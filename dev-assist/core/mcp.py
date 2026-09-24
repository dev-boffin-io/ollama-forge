"""
MCP client — spawn Model Context Protocol servers over stdio and expose
their tools to the agent (opencode parity).

The MCP "stdio" transport is newline-delimited JSON-RPC: each message is a
complete JSON object on its own line, no Content-Length framing. This module
speaks just enough of the 2024-11-05/2025-03-26 protocol to be useful to the
agent loop:

  - `initialize` + `notifications/initialized` handshake,
  - `tools/list` to discover a server's tools,
  - `tools/call` to invoke one,
  - `shutdown`/`exit` on cleanup.

Configured servers live in settings.json:

    "mcp_servers": {
      "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
      "github":     {"command": "uvx", "args": ["mcp-server-github"]}
    }

Tools are exposed to the model as `mcp__{server}__{tool}` — schema names
come from the server's own inputSchema, so any MCP server drops straight in.
Connections are established lazily (first tools/list), cached per process,
and closed at exit. Broken servers are remembered so a session doesn't keep
respawning a failing process on every tool discovery.
"""

from __future__ import annotations

import atexit
import json
import os
import select
import subprocess

_INITIALIZE_PROTOCOL = "2024-11-05"
_STDIO_TIMEOUT = 30

_client_by_server: dict[str, McpClient] = {}
_broken: set[str] = set()


class McpServerError(RuntimeError):
    """Raised when an MCP server is unreachable or misbehaves."""


class McpClient:
    """A single stdio MCP server process, speaking newline-JSON-RPC."""

    def __init__(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self._proc: subprocess.Popen | None = None
        self._req_id = 0

    # ── Process lifecycle ───────────────────────────────────────────────

    def _merge_env(self) -> dict[str, str]:
        merged = dict(os.environ)
        merged.update({k: str(v) for k, v in self.env.items()})
        return merged

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self._merge_env(),
            )
        except OSError as exc:
            raise McpServerError(
                f"MCP server {self.name!r} failed to start: {exc}"
            ) from exc

    def _proc_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self._send_nowait({"jsonrpc": "2.0", "method": "shutdown"})
                self._send_nowait({"jsonrpc": "2.0", "method": "exit"})
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc = None

    # ── Low-level stdio ──────────────────────────────────────────────────

    def _send_nowait(self, payload: dict) -> None:
        self._ensure_started()
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _read_line(self, timeout: float = _STDIO_TIMEOUT) -> str:
        self._ensure_started()
        assert self._proc and self._proc.stdout
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            raise McpServerError(f"MCP server {self.name!r} timed out (no response in {timeout}s)")
        if self._proc.poll() is not None:
            raise McpServerError(f"MCP server {self.name!r} exited unexpectedly")
        line = self._proc.stdout.readline()
        if not line:
            raise McpServerError(f"MCP server {self.name!r} closed its stdout")
        return line.strip()

    def _request(self, method: str, params: dict, timeout: float = _STDIO_TIMEOUT) -> dict:
        if not self._proc_alive():
            self._ensure_started()
        self._req_id += 1
        rid = self._req_id
        self._send_nowait({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            line = self._read_line(timeout)
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # ignore stray non-JSON lines
            if not isinstance(msg, dict) or msg.get("id") != rid:
                continue  # notification or another request's response
            if msg.get("error") is not None:
                err = msg["error"]
                raise McpServerError(
                    f"MCP {method} error: {err.get('message') or err}"
                )
            return msg.get("result") or {}

    def _notify(self, method: str, params: dict) -> None:
        self._send_nowait({"jsonrpc": "2.0", "method": method, "params": params})

    # ── Protocol methods ─────────────────────────────────────────────────

    def initialize(self) -> dict:
        result = self._request("initialize", {
            "protocolVersion": _INITIALIZE_PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dev-assist", "version": "1.2"},
        })
        try:
            self._notify("notifications/initialized", {})
        except Exception:
            pass
        return result

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        return result.get("tools") or []

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        content = result.get("content") or []
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "resource":
                    parts.append(str(item.get("text", "")))
        if result.get("isError"):
            raise McpServerError("MCP tool error: " + ("\n".join(parts) if parts else "unknown"))
        return "\n".join(parts)


def _configured_servers() -> dict[str, dict]:
    """Raw `mcp_servers` config from settings.json (never raises)."""
    try:
        from core import config as _config
        cfg = _config.load_config()
        if hasattr(cfg, "mcp_servers"):
            servers = cfg.mcp_servers or {}
            return dict(servers)
        if isinstance(cfg, dict):
            raw = cfg.get("mcp_servers") or {}
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _ensure_client(name: str, spec: dict) -> McpClient:
    """Connect to one server, caching + remembering failures."""
    global _client_by_server, _broken
    if name in _client_by_server:
        return _client_by_server[name]
    if name in _broken:
        raise McpServerError(f"MCP server {name!r} already failed to connect")

    command = str(spec.get("command") or "")
    if not command:
        raise McpServerError(f"MCP server {name!r} has no 'command' configured")
    client = McpClient(
        name, command,
        spec.get("args") or [],
        spec.get("env") or {},
    )
    try:
        client.initialize()
        tools = client.list_tools()
    except Exception:
        client.close()
        _broken.add(name)
        raise

    # Cache enabled tools onto the client for schema building.
    client._tools = tools  # type: ignore[attr-defined]
    _client_by_server[name] = client
    return client


def server_tools() -> list[tuple[str, str, dict]]:
    """
    Discover every (server, tool_name, tool_schema) from configured servers,
    connecting lazily. Best-effort: broken servers are skipped, not fatal.
    """
    discovered: list[tuple[str, str, dict]] = []
    for name, spec in _configured_servers().items():
        try:
            client = _ensure_client(name, spec)
        except Exception:
            continue
        for tool in getattr(client, "_tools", []) or []:
            tool_name = str(tool.get("name") or "")
            if tool_name:
                discovered.append((name, tool_name, tool))
    return discovered


def installed_schemas() -> list[dict]:
    """OpenAI-style function schemas for every MCP tool (`mcp__s__t`)."""
    schemas: list[dict] = []
    for server, tool_name, tool in server_tools():
        fn = {
            "name": f"mcp__{server}__{tool_name}",
            "description": (
                str(tool.get("description") or "")
                or f"MCP tool {tool_name!r} exposed by server {server!r}."
                + " Use it like any other tool. Arguments follow the tool's inputSchema."
            ),
            "parameters": tool.get("inputSchema")
            or {"type": "object", "properties": {}},
        }
        schemas.append({"type": "function", "function": fn})
    return schemas


def call_tool(name: str, args: dict, _workdir: str | None = None) -> str:
    """
    Execute an `mcp__{server}__{tool}` call. Returns the tool's text output,
    or an Error:... message (never raises for the agent loop).
    """
    parts = name.split("__", 2)
    if len(parts) != 3 or parts[0] != "mcp":
        return f"Error: {name!r} is not an MCP tool call"
    server, tool_name = parts[1], parts[2]
    try:
        client = _ensure_client(server, _configured_servers().get(server) or {})
        return client.call_tool(tool_name, args or {})
    except Exception as exc:
        return f"Error: MCP tool {tool_name!r} on server {server!r} failed: {exc}"


def shutdown_all() -> None:
    for client in _client_by_server.values():
        try:
            client.close()
        except Exception:
            pass
    _client_by_server.clear()


def reset_for_tests() -> None:
    shutdown_all()
    global _broken
    _broken = set()


atexit.register(shutdown_all)
