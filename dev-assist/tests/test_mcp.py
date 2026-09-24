"""
Tests for core/mcp.py — stdio MCP client against a fake NDJSON server.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import mcp

FAKE_SERVER = '''\
import json
import sys

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method", "")
    resp = None
    if method == "initialize":
        resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                "result": {"protocolVersion": "2024-11-05",
                           "capabilities": {},
                           "serverInfo": {"name": "fake", "version": "1"}}}
    elif method == "tools/list":
        resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"tools": [
            {"name": "echo", "description": "Echo text back.",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]}},
            {"name": "fail", "description": "Always fails.",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}}
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {}) or {}
        if name == "echo":
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}]}}
        else:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {
                "content": [{"type": "text", "text": "forced failure"}],
                "isError": True}}
    elif method.startswith("notifications/"):
        continue
    else:
        resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
    if resp is not None:
        print(json.dumps(resp), flush=True)
'''


@pytest.fixture
def fake_script(tmp_path):
    path = tmp_path / "fake_mcp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return str(path)


@pytest.fixture
def fake_cfg(fake_script, monkeypatch):
    """Point _configured_servers at our fake in-process server."""

    def _configured():
        return {"fake": {"command": sys.executable, "args": [fake_script]}}

    monkeypatch.setattr(mcp, "_configured_servers", _configured)
    mcp.reset_for_tests()
    yield
    mcp.reset_for_tests()


class TestClientProtocol:
    def test_initialize_handshake(self, fake_script):
        client = mcp.McpClient("fake", sys.executable, [fake_script])
        try:
            info = client.initialize()
            assert info.get("protocolVersion") == "2024-11-05"
            assert info.get("serverInfo", {}).get("name") == "fake"
        finally:
            client.close()

    def test_list_tools(self, fake_script):
        client = mcp.McpClient("fake", sys.executable, [fake_script])
        try:
            client.initialize()
            tools = client.list_tools()
            names = [t["name"] for t in tools]
            assert "echo" in names and "fail" in names
        finally:
            client.close()

    def test_call_tool(self, fake_script):
        client = mcp.McpClient("fake", sys.executable, [fake_script])
        try:
            client.initialize()
            client.list_tools()
            out = client.call_tool("echo", {"text": "hello world"})
            assert out == "echo:hello world"
        finally:
            client.close()

    def test_call_error_tool_raises(self, fake_script):
        client = mcp.McpClient("fake", sys.executable, [fake_script])
        try:
            client.initialize()
            client.list_tools()
            with pytest.raises(mcp.McpServerError):
                client.call_tool("fail", {})
        finally:
            client.close()


class TestInstalledSchemas:
    def test_schemas_marked_mcp(self, fake_cfg):
        schemas = mcp.installed_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "mcp__fake__echo" in names
        assert "mcp__fake__fail" in names
        schema = next(s for s in schemas if s["function"]["name"] == "mcp__fake__echo")
        assert schema["function"]["parameters"]["required"] == ["text"]

    def test_call_tool_via_schema_name(self, fake_cfg):
        assert mcp.installed_schemas()
        assert mcp.call_tool("mcp__fake__echo", {"text": "hi"}, "/tmp") == "echo:hi"

    def test_non_mcp_name_rejected(self, fake_cfg):
        out = mcp.call_tool("read_file", {}, "/tmp")
        assert out.startswith("Error:")

    def test_unconfigured_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mcp, "_configured_servers", lambda: {})
        mcp.reset_for_tests()
        assert mcp.installed_schemas() == []
        mcp.reset_for_tests()


class TestBrokenServer:
    def test_broken_server_skipped_not_fatal(self, fake_script, monkeypatch):
        def _configured():
            return {
                "broken": {"command": "/nonexistent/binary", "args": []},
                "fake": {"command": sys.executable, "args": [fake_script]},
            }

        monkeypatch.setattr(mcp, "_configured_servers", _configured)
        mcp.reset_for_tests()
        schemas = mcp.installed_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "mcp__fake__echo" in names
        mcp.reset_for_tests()

    def test_missing_command_raises(self, fake_cfg):
        mcp.reset_for_tests()
        with pytest.raises(mcp.McpServerError):
            mcp._ensure_client("nope", {"args": []})


class TestReset:
    def test_reset_clears_connections(self, fake_cfg, fake_script):
        assert mcp.installed_schemas()
        mcp.reset_for_tests()
        assert mcp._client_by_server == {} and mcp._broken == set()
