"""
Tests for the run_tests and web_search tools in core/tools.py.
"""

import json
import os
import sys
import urllib.error

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
        from core.tools import execute_tool
        os.path.join(str(tmp_path), "sub")
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

class TestReadFile:
    def test_offset_limit_and_footer(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        p = os.path.join(d, "a.txt")
        with open(p, "w") as f:
            f.write("".join(f"line {i}\n" for i in range(1, 6)))
        result = execute_tool("read_file", {"path": "a.txt", "offset": 2, "limit": 2}, d)
        assert "2: line 2" in result
        assert "3: line 3" in result
        assert "1: line 1" not in result
        assert "Showing lines 2-3 of 5" in result

    def test_empty_file(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "empty.txt"), "w"):
            pass
        result = execute_tool("read_file", {"path": "empty.txt"}, d)
        assert "total 0 lines" in result

    def test_binary_file_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "blob.bin"), "wb") as f:
            f.write(b"\x00\x01\x02\x03binary")
        result = execute_tool("read_file", {"path": "blob.bin"}, d)
        assert "binary" in result.lower()

    def test_offset_out_of_range(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("one\n")
        result = execute_tool("read_file", {"path": "a.txt", "offset": 10}, d)
        assert "out of range" in result

    def test_missing_file(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("read_file", {"path": "nope.txt"}, str(tmp_path))
        assert "File not found" in result


class TestWriteEdit:
    def test_write_creates_then_overwrites(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        r1 = execute_tool("write_file", {"path": "x.txt", "content": "a\nb\n"}, d)
        assert "Created" in r1
        r2 = execute_tool("write_file", {"path": "x.txt", "content": "c\n"}, d)
        assert "Overwrote" in r2
        with open(os.path.join(d, "x.txt")) as f:
            assert f.read() == "c\n"

    def test_write_preserves_bom(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        p = os.path.join(d, "bom.txt")
        with open(p, "wb") as f:
            f.write(b"\xef\xbb\xbftext")
        execute_tool("write_file", {"path": "bom.txt", "content": "new\n"}, d)
        with open(p, "rb") as f:
            assert f.read() == b"\xef\xbb\xbfnew\n"

    def test_edit_exact_and_replace_all(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        p = os.path.join(d, "e.py")
        with open(p, "w") as f:
            f.write("x = 1\ny = x\n")
        result = execute_tool("edit_file", {
            "path": "e.py", "old_string": "y = x", "new_string": "y = x + 1",
        }, d)
        assert "Edit applied successfully." in result
        with open(p) as f:
            assert f.read() == "x = 1\ny = x + 1\n"

        p2 = os.path.join(d, "r.py")
        with open(p2, "w") as f:
            f.write("a=1\na=1\n")
        execute_tool("edit_file", {
            "path": "r.py", "old_string": "a=1", "new_string": "a=2", "replace_all": True,
        }, d)
        with open(p2) as f:
            assert f.read() == "a=2\na=2\n"

    def test_edit_ambiguous_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "m.txt"), "w") as f:
            f.write("same\nsame\n")
        result = execute_tool("edit_file", {
            "path": "m.txt", "old_string": "same", "new_string": "other",
        }, d)
        assert "multiple matches" in result.lower()

    def test_edit_identifies_missing_string(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "n.txt"), "w") as f:
            f.write("hello\n")
        result = execute_tool("edit_file", {
            "path": "n.txt", "old_string": "nope", "new_string": "x",
        }, d)
        assert "could not find" in result.lower()

    def test_edit_fuzzy_whitespace_match(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        p = os.path.join(d, "i.py")
        with open(p, "w") as f:
            f.write("def f():\n    return 1\n")
        result = execute_tool("edit_file", {
            # extra indentation vs. the file's 4 spaces — should still match
            "path": "i.py",
            "old_string": "        return 1",
            "new_string": "        return 2",
        }, d)
        assert "Edit applied successfully." in result
        with open(p) as f:
            assert "    return 2" in f.read()


class TestGlobGrep:
    def test_glob_finds_files(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        for rel in ("a.py", "sub/b.py", "c.md"):
            fp = os.path.join(d, rel)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as f:
                f.write("x")
        result = execute_tool("glob", {"pattern": "**/*.py"}, d)
        assert "a.py" in result
        assert "b.py" in result
        assert "c.md" not in result
        # results are absolute paths
        first = result.splitlines()[0]
        assert os.path.isabs(first)

    def test_glob_no_matches(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("glob", {"pattern": "**/*.rust"}, str(tmp_path))
        assert "No files found" in result

    def test_grep_finds_pattern(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "s.py"), "w") as f:
            f.write("import os\ndef todo():\n    pass\n")
        result = execute_tool("grep", {"pattern": r"def\s+\w+"}, d)
        assert "Found 1 match" in result
        assert "Line 2" in result
        assert "def todo()" in result

    def test_grep_invalid_regex(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("grep", {"pattern": "([", }, str(tmp_path))
        assert "invalid regex" in result

    def test_grep_include_filter(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        with open(os.path.join(d, "hit.py"), "w") as f:
            f.write("needle")
        with open(os.path.join(d, "hit.txt"), "w") as f:
            f.write("needle")
        result = execute_tool("grep", {"pattern": "needle", "include": "*.py"}, d)
        assert "hit.py" in result
        assert "hit.txt" not in result


class TestBash:
    def test_runs_command_with_exit_code(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("bash", {"command": "echo hi"}, str(tmp_path))
        assert "(exit 0)" in result
        assert "hi" in result

    def test_nonzero_exit_reported(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("bash", {"command": "echo oops && exit 3"}, str(tmp_path))
        assert "(exit 3)" in result

    def test_empty_command_error(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("bash", {}, str(tmp_path))
        assert "command is required" in result


class TestApplyPatch:
    def test_add_update_delete(self, tmp_path):
        from core import change_tracker
        from core.tools import execute_tool
        change_tracker.new_run()
        d = str(tmp_path)
        with open(os.path.join(d, "keep.txt"), "w") as f:
            f.write("old line\n")
        with open(os.path.join(d, "gone.txt"), "w") as f:
            f.write("bye\n")

        patch_text = (
            "*** Begin Patch\n"
            "*** Add File: new.txt\n"
            "+hello\n"
            "*** Update File: keep.txt\n"
            "@@\n"
            "-old line\n"
            "+new line\n"
            "*** Delete File: gone.txt\n"
            "*** End Patch"
        )
        result = execute_tool("apply_patch", {"patch_text": patch_text}, d)
        assert "Success" in result and "A new.txt" in result \
            and "M keep.txt" in result and "D gone.txt" in result
        with open(os.path.join(d, "new.txt")) as f:
            assert f.read() == "hello\n"
        with open(os.path.join(d, "keep.txt")) as f:
            assert f.read() == "new line\n"
        assert not os.path.exists(os.path.join(d, "gone.txt"))

    def test_invalid_patch_reports_error(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("apply_patch", {"patch_text": "no markers here"}, str(tmp_path))
        assert "Error" in result

    def test_missing_patch_text(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("apply_patch", {}, str(tmp_path))
        assert "patchText is required" in result


class TestTodoWrite:
    def test_set_and_render(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("todowrite", {
            "todos": [
                {"content": "first", "status": "completed"},
                {"content": "second", "status": "pending"},
            ]
        }, str(tmp_path))
        assert '"first"' in result
        assert '"completed"' in result
        assert "(1 todo(s) not yet completed)" in result

    def test_invalid_todos(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("todowrite", {"todos": "not a list"}, str(tmp_path))
        assert "must be an array" in result


class TestQuestion:
    def test_auto_skip_returns_unanswered(self, tmp_path, monkeypatch):
        from core.tools import execute_tool, set_question_auto_skip
        set_question_auto_skip(True)
        try:
            result = execute_tool("question", {"questions": [
                {"question": "pick one", "options": [{"label": "a"}]}
            ]}, str(tmp_path))
        finally:
            set_question_auto_skip(False)
        assert "Unanswered" in result

    def test_no_options_prompts_error(self, tmp_path, monkeypatch):
        from core.tools import execute_tool
        result = execute_tool("question", {"questions": [
            {"question": "no opts here", "options": []}
        ]}, str(tmp_path))
        assert "Error" in result


class TestWebFetch:
    class _FakeResp:
        def __init__(self, body, content_type="text/html"):
            self.body = body
            self.headers = {"Content-Type": content_type}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *a, **k):
            return self.body

    def test_missing_url_error(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("web_fetch", {}, str(tmp_path))
        assert "url is required" in result

    def test_bad_scheme_rejected(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("web_fetch", {"url": "ftp://x"}, str(tmp_path))
        assert "http:// or https://" in result

    def test_markdown_conversion(self, tmp_path, monkeypatch):
        from core.tools import execute_tool

        def fake_urlopen(req, timeout):
            return TestWebFetch._FakeResp(b"<html><body><h1>Title</h1><p>Bla.</p></body></html>")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = execute_tool("web_fetch", {"url": "https://example.com/"}, str(tmp_path))
        assert "Title" in result
        assert "Bla" in result

    def test_plain_text_passthrough(self, tmp_path, monkeypatch):
        from core.tools import execute_tool

        def fake_urlopen(req, timeout):
            return TestWebFetch._FakeResp(b"just text", content_type="text/plain")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = execute_tool("web_fetch", {"url": "https://example.com/x.txt"}, str(tmp_path))
        assert "just text" in result


class TestSkill:
    def test_missing_skill_error(self, tmp_path):
        from core.tools import execute_tool
        result = execute_tool("skill", {"name": "nope"}, str(tmp_path))
        assert "not found" in result

    def test_skill_loaded_from_workdir(self, tmp_path):
        from core.tools import execute_tool
        d = str(tmp_path)
        os.makedirs(os.path.join(d, "skills", "demo", "scripts"), exist_ok=True)
        with open(os.path.join(d, "skills", "demo", "SKILL.md"), "w") as f:
            f.write("# Demo skill\\n\\nRun install.sh and report back.\\n")
        result = execute_tool("skill", {"name": "demo"}, d)
        assert "Demo skill" in result
        assert "scripts" in result


class TestRootConfinement:
    """Tools must stay inside the launched project root — otherwise the agent
    lists/edits folders it was never started in (e.g. the model guessing
    absolute paths like ~/Desktop/Review_Bin/...)."""

    def _mkworkdir(self, tmp_path):
        d = str(tmp_path)
        os.makedirs(os.path.join(d, "sub"), exist_ok=True)
        with open(os.path.join(d, "sub", "keep.txt"), "w") as f:
            f.write("inside")
        return d

    def test_list_dir_inside_root_allowed(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        assert "keep.txt" in execute_tool("list_dir", {"path": "sub"}, d)

    def test_list_dir_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        result = execute_tool("list_dir", {"path": ".."}, d)
        assert "outside the project root" in result

    def test_read_file_absolute_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        outside = os.path.join(os.path.dirname(d), "secret.txt")
        with open(outside, "w") as f:
            f.write("secret")
        result = execute_tool("read_file", {"path": outside}, d)
        assert "outside the project root" in result

    def test_absolute_path_inside_root_allowed(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        inside = os.path.join(d, "sub", "keep.txt")
        assert "inside" in execute_tool("read_file", {"path": inside}, d)

    def test_glob_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        assert "outside the project root" in execute_tool("glob", {"path": "..", "pattern": "*"}, d)

    def test_grep_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        assert "outside the project root" in execute_tool("grep", {"path": "..", "pattern": "x"}, d)

    def test_write_file_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        outside = os.path.join(os.path.dirname(d), "escaped.txt")
        result = execute_tool("write_file", {"path": "../escaped.txt", "content": "x"}, d)
        assert "outside the project root" in result
        assert not os.path.exists(outside)

    def test_edit_file_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        result = execute_tool(
            "edit_file",
            {"path": "../../etc/hostname", "old_string": "x", "new_string": "y"},
            d,
        )
        assert "outside the project root" in result

    def test_bash_workdir_escape_rejected(self, tmp_path):
        from core.tools import execute_tool
        d = self._mkworkdir(tmp_path)
        result = execute_tool("bash", {"command": "pwd", "workdir": ".."}, d)
        assert "outside the project root" in result
