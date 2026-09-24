"""
LSP integration — a minimal stdio language-server client for diagnostics.

opencode runs a language server per detected project language and feeds
diagnostics back with file reads. This module ports the essentials: it
spawns an LSP server over stdio (Content-Length framed JSON-RPC), holds an
`initialize` handshake, opens files with `textDocument/didOpen`, pulls
diagnostics (3.17 `textDocument/diagnostic`, falling back to
`textDocument/publishDiagnostics` notifications), and exposes them through
a `lsp_diagnostics` agent tool plus optional read_file enrichment.

Servers are configured per language in settings.json:

    "lsp": {
      "python":     {"command": "jedi-language-server", "args": []},
      "typescript": {"command": "pyright-langserver",   "args": ["--stdio"]}
    }

When no server is configured/available for a file's language, both entry
points degrade gracefully (to "no diagnostics" text), never raising.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import time
from typing import Any

_DIAG_TIMEOUT = 12.0  # seconds to wait for a diagnostics round-trip

# Extension → language key used to look up the `lsp` config section.
_EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "typescript", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
}

# LSP DiagnosticSeverity: 1=Error 2=Warning 3=Information 4=Hint
_SEVERITY = {1: "error", 2: "warning", 3: "info", 4: "hint"}

_lsp_by_lang: dict[str, LspClient | None] = {}


class LspError(RuntimeError):
    pass


class LspClient:
    """A single stdio language server with Content-Length JSON-RPC framing."""

    def __init__(self, lang: str, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        self.lang = lang
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self._proc: subprocess.Popen | None = None
        self._req_id = 0
        self._buf = b""

    # ── Lifecycle ────────────────────────────────────────────────────────

    def _merge_env(self) -> dict[str, str]:
        merged = dict(os.environ)
        merged.update({k: str(v) for k, v in self.env.items()})
        return merged

    def ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self._merge_env(),
            )
        except OSError as exc:
            raise LspError(f"LSP server for {self.lang} failed to start: {exc}") from exc

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                self._write_msg({"jsonrpc": "2.0", "method": "shutdown"})
                self._write_msg({"jsonrpc": "2.0", "method": "exit"})
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

    # ── Framing ──────────────────────────────────────────────────────────

    def _write_msg(self, payload: dict) -> None:
        self.ensure_started()
        assert self._proc and self._proc.stdin
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._proc.stdin.write(
            f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n" + body
        )
        self._proc.stdin.flush()

    def _read_raw(self, timeout: float) -> bytes:
        """Read one framed message body from a server that speaks LSP."""
        self.ensure_started()
        assert self._proc and self._proc.stdout
        while b"\r\n\r\n" not in self._buf:
            ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
            if not ready:
                raise LspError(f"LSP server for {self.lang} timed out")
            if self._proc.poll() is not None:
                raise LspError(f"LSP server for {self.lang} exited unexpectedly")
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                raise LspError(f"LSP server for {self.lang} closed its stdout")
            self._buf += chunk
        header, self._buf = self._buf.split(b"\r\n\r\n", 1)
        length = 0
        for line in header.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                try:
                    length = int(line.split(b":", 1)[1].strip())
                except ValueError:
                    pass
        while len(self._buf) < length:
            ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
            if not ready:
                raise LspError(f"LSP server for {self.lang} timed out reading body")
            chunk = self._proc.stdout.read(4096)
            if not chunk:
                raise LspError(f"LSP server for {self.lang} closed its stdout")
            self._buf += chunk
        body, self._buf = self._buf[:length], self._buf[length:]
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise LspError(f"LSP server for {self.lang} sent a malformed message") from None

    def _request(self, method: str, params: dict, timeout: float = 15.0) -> dict:
        self.ensure_started()
        self._req_id += 1
        rid = self._req_id
        self._write_msg({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            msg = self._read_raw(timeout)
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == rid:
                if msg.get("error") is not None:
                    raise LspError(f"LSP {method} failed: {msg['error']}")
                return msg.get("result") or {}
            # Ignore other requests/responses; collect nothing yet.

    def initialize(self, root_uri: str) -> None:
        self._request("initialize", {
            "processId": None,
            "rootUri": root_uri,
            "capabilities": {},
        })
        self._write_msg({"jsonrpc": "2.0", "method": "initialized", "params": {}})


def _configured_lsp() -> dict[str, Any]:
    """Raw `lsp` config section from settings.json (never raises)."""
    try:
        from core import config as _config
        cfg = _config.load_config()
        if hasattr(cfg, "lsp"):
            return dict(cfg.lsp or {})
        if isinstance(cfg, dict):
            raw = cfg.get("lsp") or {}
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _lang_for(path: str) -> str | None:
    return _EXT_LANG.get(os.path.splitext(path)[1].lower())


def _get_client(lang: str):
    """Cached (or None) client for a language — best effort, never raises."""
    if lang in _lsp_by_lang:
        return _lsp_by_lang[lang]
    spec = _configured_lsp().get(lang)
    command = str(spec.get("command") or "") if isinstance(spec, dict) else ""
    if not command or not os.path.basename(command.split()[0]) and " " not in command:
        if not command:
            _lsp_by_lang[lang] = None
            return None
    client = LspClient(
        lang, command,
        spec.get("args") or [],
        spec.get("env") or {},
    )
    try:
        client.ensure_started()
        client.initialize(root_uri=os.getcwd())
    except Exception:
        try:
            client.close()
        except Exception:
            pass
        _lsp_by_lang[lang] = None
        return None
    _lsp_by_lang[lang] = client
    return client


def _diagnostics_for(client: LspClient, path: str, timeout: float = _DIAG_TIMEOUT) -> list[dict]:
    """Pull + push diagnostics for one file; returns a list of SEG-agnostic dicts."""
    uri = pathlib_uri(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []

    try:
        client._write_msg({
            "jsonrpc": "2.0", "method": "textDocument/didOpen",
            "params": {
                "textDocument": {"uri": uri, "languageId": client.lang,
                                 "version": 1, "text": text},
            },
        })
    except Exception:
        return []

    found: list[dict] = []
    rid = client._req_id
    client._req_id += 1
    try:
        client._write_msg({
            "jsonrpc": "2.0", "id": rid,
            "method": "textDocument/diagnostic",
            "params": {"textDocument": {"uri": uri}},
        })
    except Exception:
        return []

    deadline = time.monotonic() + timeout
    got_response = False
    while time.monotonic() < deadline and not got_response:
        try:
            msg = client._read_raw(deadline - time.monotonic())
        except Exception:
            break
        if not isinstance(msg, dict):
            continue
        if msg.get("id") == rid:
            # Response to our diagnostic request — consume and stop.
            result = msg.get("result") or {}
            items = (result.get("items") or []) if isinstance(result, dict) else []
            if isinstance(items, list):
                found.extend(items)
            got_response = True
            continue
        if isinstance(msg.get("result"), dict):
            items = (msg["result"].get("items") or []) if isinstance(msg["result"].get("items"), list) else []
            if items:
                found.extend(items)
            continue
        params = msg.get("params") or {}
        if msg.get("method") in ("textDocument/publishDiagnostics", "textDocument/diagnostic"):
            diags = (params.get("diagnostics") or []) if isinstance(params, dict) else []
            if isinstance(diags, list):
                found.extend(diags)
            if msg.get("method") == "textDocument/diagnostic" and not params.get("kind"):
                got_response = True
    return found


def pathlib_uri(path: str) -> str:
    """File URI (file://...) for an absolute path."""
    from pathlib import Path
    return Path(path).resolve().as_uri()


def _format(diag: dict, path: str) -> str:
    range_ = diag.get("range") or {}
    start = range_.get("start") or {}
    line = int(start.get("line") or 0) + 1
    severity = _SEVERITY.get(int(diag.get("severity") or 0), "info")
    message = str(diag.get("message") or "")
    code = diag.get("code")
    code_str = f" [{code}]" if code is not None else ""
    return f"  {severity:<7} line {line}: {message}{code_str}"


def lsp_schema() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "lsp_diagnostics",
            "description": (
                "Run the language server on a file to get compiler/type diagnostics "
                "(errors, warnings, info) with line numbers. Returns the file's "
                "diagnostics, or 'no diagnostics' when the file is clean or no LSP "
                "server is configured for the language."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute path to the file to diagnose.",
                    },
                },
                "required": ["path"],
            },
        },
    }


def lsp_executor(args: dict, workdir: str) -> str:
    """Executor for the `lsp_diagnostics` tool."""
    raw_path = str(args.get("path") or "")
    if not raw_path:
        return "Error: lsp_diagnostics requires a 'path' argument."
    path = raw_path if os.path.isabs(raw_path) else os.path.join(workdir, raw_path)
    path = os.path.normpath(path)
    return diagnostics_block(path)


def diagnostics_block(path: str) -> str:
    """Human-readable diagnostics for one file ("" when nothing/unsupported)."""
    if not os.path.isfile(path):
        return ""
    lang = _lang_for(path)
    if lang is None:
        return ""
    client = _get_client(lang)
    if client is None:
        return ""
    try:
        diags = _diagnostics_for(client, path)
    except Exception:
        return ""
    if not diags:
        return "## LSP diagnostics\nNo diagnostics reported."
    lines = ["## LSP diagnostics"]
    for d in diags:
        lines.append(_format(d, path))
    return "\n".join(lines)


def shutdown_all() -> None:
    for client in _lsp_by_lang.values():
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    _lsp_by_lang.clear()


def reset_for_tests() -> None:
    shutdown_all()
