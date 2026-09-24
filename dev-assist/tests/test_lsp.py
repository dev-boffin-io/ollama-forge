"""
Tests for core/lsp.py — Content-Length-framed diagnostics against a fake server.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import lsp

FAKE_SERVER = '''\
import json
import os
import sys

def write_body(obj):
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

buf = b""

def read_msg():
    global buf
    while b"\\r\\n\\r\\n" not in buf:
        chunk = os.read(0, 4096)
        if not chunk:
            return None
        buf += chunk
    header, buf2 = buf.split(b"\\r\\n\\r\\n", 1)
    buf = buf2
    length = 0
    for line in header.split(b"\\r\\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(buf) < length:
        chunk = os.read(0, 4096)
        if not chunk:
            return None
        buf += chunk
    body, buf2 = buf[:length], buf[length:]
    buf = buf2
    return json.loads(body)

while True:
    msg = read_msg()
    if msg is None:
        break
    method = msg.get("method", "")
    if method == "initialize":
        write_body({"jsonrpc": "2.0", "id": msg.get("id"),
                    "result": {"capabilities": {"textDocumentSync": 1}}})
    elif method == "initialized":
        continue
    elif method == "textDocument/didOpen":
        uri = msg["params"]["textDocument"]["uri"]
        write_body({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
                    "params": {"uri": uri, "diagnostics": [
                        {"range": {"start": {"line": 0, "character": 0},
                                   "end": {"line": 0, "character": 5}},
                         "severity": 1, "message": "sync error"}]}})
    elif method == "textDocument/diagnostic":
        uri = msg["params"]["textDocument"]["uri"]
        write_body({"jsonrpc": "2.0", "id": msg.get("id"),
                    "result": {"kind": "full", "items": [
                        {"range": {"start": {"line": 2, "character": 0},
                                   "end": {"line": 2, "character": 4}},
                         "severity": 2, "message": "unused variable here"}]}})
    else:
        write_body({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}})
'''


@pytest.fixture
def fake_script(tmp_path):
    path = tmp_path / "fake_lsp.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return str(path)


@pytest.fixture
def cfg_python(fake_script, monkeypatch):
    def _configured():
        return {"python": {"command": sys.executable, "args": [fake_script]}}

    monkeypatch.setattr(lsp, "_configured_lsp", _configured)
    lsp.reset_for_tests()
    yield
    lsp.reset_for_tests()


class TestClientFraming:
    def test_initialize_roundtrip(self, fake_script):
        client = lsp.LspClient("python", sys.executable, [fake_script])
        try:
            client.ensure_started()
            client.initialize(root_uri="file:///tmp")
        finally:
            client.close()


class TestDiagnostics:
    def test_executor_returns_diagnostics(self, cfg_python, tmp_path):
        target = tmp_path / "sample.py"
        target.write_text("x = 1\n\ny = 2\n", encoding="utf-8")
        out = lsp.lsp_executor({"path": str(target)}, str(tmp_path))
        assert "## LSP diagnostics" in out
        assert "unused variable here" in out
        assert "line 3" in out
        assert "warning" in out

    def test_publish_diagnostics_notifications_collected(self, cfg_python, tmp_path):
        target = tmp_path / "sample.py"
        target.write_text("a = 1\n", encoding="utf-8")
        out = lsp.diagnostics_block(str(target))
        assert "sync error" in out

    def test_unsupported_language_returns_empty(self, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("hi", encoding="utf-8")
        assert lsp.diagnostics_block(str(target)) == ""

    def test_nonexistent_file_returns_empty(self, cfg_python, tmp_path):
        assert lsp.diagnostics_block(str(tmp_path / "missing.py")) == ""

    def test_no_server_configured_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(lsp, "_configured_lsp", lambda: {})
        lsp.reset_for_tests()
        target = tmp_path / "sample.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert lsp.diagnostics_block(str(target)) == ""
        lsp.reset_for_tests()

    def test_relative_path_resolved_against_workdir(self, cfg_python, tmp_path):
        (tmp_path / "mod.py").write_text("z = 0\n", encoding="utf-8")
        out = lsp.lsp_executor({"path": "mod.py"}, str(tmp_path))
        assert "## LSP diagnostics" in out

    def test_missing_path_argument_errors(self, cfg_python, tmp_path):
        out = lsp.lsp_executor({}, str(tmp_path))
        assert out.startswith("Error:")
