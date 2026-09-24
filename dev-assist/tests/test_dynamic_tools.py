"""
Tests for core/tools.py dynamic registry — register_tool, all_tool_schemas,
the mcp__ dispatch, and lsp_diagnostics wiring.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.tools as tools
from core import lsp, mcp


@pytest.fixture
def clean_registry(monkeypatch):
    """Empty config (no mcp/lsp) + wipe the dynamic registry each test."""
    from core import config as core_config
    monkeypatch.setattr(core_config, "load_config", lambda: {})
    tools.reset_dynamic_tools()
    yield
    tools.reset_dynamic_tools()


class TestRegisterTool:
    def test_registered_schema_appears_after_builtins(self, clean_registry):
        assert len(tools.all_tool_schemas()) == len(tools.TOOL_SCHEMAS)
        tools.register_tool(
            {"type": "function", "function": {
                "name": "my_custom_tool",
                "description": "custom tool",
                "parameters": {"type": "object", "properties": {}},
            }},
            executor=lambda args, workdir: "custom ran",
        )
        names = [s["function"]["name"] for s in tools.all_tool_schemas()]
        assert names[-1] == "my_custom_tool"
        assert "read_file" in names

    def test_register_by_name_with_synthetic_schema(self, clean_registry):
        tools.register_tool("skin_tool", executor=lambda args, workdir: "ok")
        names = [s["function"]["name"] for s in tools.all_tool_schemas()]
        assert "skin_tool" in names

    def test_dedupe_same_name(self, clean_registry):
        def fn(args, workdir):
            return "ok"

        tools.register_tool("dup", executor=fn)
        tools.register_tool("dup", executor=fn)
        names = [s["function"]["name"] for s in tools.all_tool_schemas()]
        assert names.count("dup") == 1

    def test_schema_requires_function_name(self, clean_registry):
        with pytest.raises(ValueError):
            tools.register_tool({"function": {}}, executor=lambda a, w: "")

    def test_dynamic_executor_dispatched(self, clean_registry):
        tools.register_tool(
            "custom", executor=lambda args, workdir: f"ran {args.get('x')} @ {workdir}",
        )
        assert tools.execute_tool("custom", {"x": 5}, "/tmp") == "ran 5 @ /tmp"

    def test_unknown_tool_error_message(self, clean_registry):
        out = tools.execute_tool("nothing_here", {}, "/tmp")
        assert out.startswith("Error: unknown tool")


class TestMcpDispatch:
    def test_mcp_tool_call(self, clean_registry, monkeypatch):
        from core import mcp as mcp_mod

        def fake_call(name, args, workdir):
            return f"mcp-ran:{name}"

        monkeypatch.setattr(mcp_mod, "call_tool", fake_call)
        out = tools.execute_tool("mcp__server__thing", {"k": 1}, "/tmp")
        assert out == "mcp-ran:mcp__server__thing"

    def test_mcp_bad_args(self, clean_registry):
        out = tools.execute_tool("mcp__server__thing", "not-a-dict", "/tmp")
        assert out.startswith("Error:")


class TestConfigDrivenRegistration:
    def test_no_config_adds_nothing(self, clean_registry):
        schemas = tools.all_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "lsp_diagnostics" not in names
        assert all(not n.startswith("mcp__") for n in names)

    def test_lsp_tool_registered_when_configured(self, clean_registry, monkeypatch):
        from core import config as core_config
        monkeypatch.setattr(core_config, "load_config", lambda: {"lsp": {"python": {"command": "jedi"}}})
        names = [s["function"]["name"] for s in tools.all_tool_schemas()]
        assert "lsp_diagnostics" in names
        # executor is wired through the real lsp module
        out = tools.execute_tool("lsp_diagnostics", {"path": "nonexistent.py"}, "/tmp")
        assert out == ""

    def test_mcp_tools_registered_when_configured(self, clean_registry, monkeypatch):
        from core import config as core_config
        monkeypatch.setattr(core_config, "load_config", lambda: {
            "mcp_servers": {"broken": {"command": "/definitely/missing", "args": []}},
        })
        schemas = tools.all_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        # broken server is skipped, never fatal, nothing registered
        assert all(not n.startswith("mcp__") for n in names)


class TestReset:
    def test_reset_clears_everything(self, clean_registry):
        tools.register_tool("temp_tool", executor=lambda args, workdir: "x")
        assert any(s["function"]["name"] == "temp_tool" for s in tools.all_tool_schemas())
        tools.reset_dynamic_tools()
        names = [s["function"]["name"] for s in tools.all_tool_schemas()]
        assert "temp_tool" not in names
        assert mcp._client_by_server == {} and mcp._broken == set()
        assert lsp._lsp_by_lang == {}


class TestAgentUsesDynamicSchemas:
    def test_subagent_uses_all_tool_schemas(self):
        import core.toolimpl.subagent as sub
        source = open(sub.__file__, encoding="utf-8").read()
        assert "all_tool_schemas" in source
        assert "TOOL_SCHEMAS" not in source
