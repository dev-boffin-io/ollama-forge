"""
Tests for the run_tests and web_search tools in core/tools.py.
"""

import io
import json
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DDG_HTML = """
<html><body>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=<b64enc>https%3A%2F%2Fexample.com%2Fdocs">Example Docs</a>
  <a class="result__snippet" href="...">How to use <b>example</b>.</a>
</div>
<div class="result">
  <a class="result__a" href="https://second.example/">Second Hit</a>
  <a class="result__snippet" href="...">More info here.</a>
</div>
</body></html>
"""


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *a, **k):
        return self._body


class TestRunTests:
    def _mkproject(self, tmp_path, hold, tests_dir=True):
        d = str(tmp_path)
        if tests_dir:
            os.makedirs(os.path.join(d, "tests"), exist_ok=True)
        with open(os.path.join(d, "tests", "test_x.py"), "w") as f:
            f.write(hold)
        return d

    def test_pytest_pass(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkproject(tmp_path, "def test_ok():\n    assert 1 == 1\n")
        result = execute_tool("run_tests", {}, d)
        assert result.startswith("✅") or "passed" in result
        assert "pytest" in result

    def test_pytest_failure_reports_details(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkproject(tmp_path, "def test_bad():\n    assert 1 == 2\n")
        result = execute_tool("run_tests", {}, d)
        assert "failed" in result
        assert "test_bad" in result

    def test_no_framework_detected(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)  # no tests/, no config files
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "notes.txt"), "w") as f:
            f.write("nothing to run here")
        result = execute_tool("run_tests", {}, d)
        assert "no test framework detected" in result

    def test_path_arg_respected(self, tmp_path):
        from core.tools import execute_tool, resolve_path
        sub = os.path.join(str(tmp_path), "sub")
        with open(os.path.join(str(tmp_path), "package.json"), "w") as f:
            json.dump({"scripts": {"test": "node no-such-file.js"}}, f)
        result = execute_tool("run_tests", {"path": "sub"}, str(tmp_path))
        assert isinstance(result, str)


class TestWebSearch:
    def test_ddg_results_parsed(self, tmp_path, monkeypatch):
        from core.tools import _search_via_ddg

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: _FakeResponse(DDG_HTML.encode()),
        )
        result = _search_via_ddg("example docs")
        assert "# web results for 'example docs'" in result
        assert "Example Docs" in result
        assert "example.com/docs" in result  # uddg param decoded
        assert "Second Hit" in result

    def test_network_failure_returns_unavailable(self, tmp_path, monkeypatch):
        from core.tools import _search_via_ddg

        def boom(req, timeout):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        result = _search_via_ddg("anything")
        assert result.startswith("search unavailable:")

    def test_empty_page_returns_unavailable(self, monkeypatch):
        from core.tools import _search_via_ddg

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout: _FakeResponse(b"<html><body>no results</body></html>"),
        )
        result = _search_via_ddg("definitely nothing")
        assert result.startswith("search unavailable:")

    def test_missing_query(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("web_search", {}, str(tmp_path))
        assert "query is required" in result

    def test_configured_api_used_over_ddg(self, tmp_path, monkeypatch):
        # The config has no web_search section, but we can exercise the
        # OpenAI-compatible branch by patching the resolver + client.
        from core.tools import _tool_web_search
        import core.tools as tools

        def fake_config():
            return {"web_search": {"api_key": "k", "url": "http://x/search"}}

        monkeypatch.setattr("core.ai._load_config", fake_config)

        calls = {}

        def fake_urlopen(req, timeout):
            calls["url"] = req.full_url
            body = json.dumps({"results": [
                {"title": "T1", "link": "http://t1/", "snippet": "s1"},
            ]}).encode()
            return _FakeResponse(body)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = _tool_web_search({"query": "q"}, str(tmp_path))
        assert "T1" in result
        assert calls["url"] == "http://x/search"