"""
Doc-drift test — every dev-assist integration the README advertises for the
GUI (and the CLI slash set) must still exist in the real runtime registries.
Keeps README claims from silently going stale as the code moves.
"""

import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
_README = os.path.join(_ROOT, "README.md")

sys.path.insert(0, os.path.join(_ROOT, "gui"))
import agent_bridge  # noqa: E402  (puts dev-assist/ first)

sys.path.insert(0, os.path.join(_ROOT, "gui"))  # gui/ back on top

_SLASH_RE = re.compile(r"`/([a-z][a-z0-9_-]*)`")
_FENCE_SLASH_RE = re.compile(r"/([a-z][a-z0-9_-]*)")


def _slash_tokens(section: str, *, backticked: bool = True) -> set:
    pat = _SLASH_RE if backticked else _FENCE_SLASH_RE
    return set(pat.findall(section))


def _readme() -> str:
    with open(_README, encoding="utf-8") as f:
        return f.read()


def _section(text: str, header: str) -> str:
    start = text.index(header)
    tail = text[start + len(header):]
    nxt = re.search(r"\n#+ ", tail)
    end = start + len(header) + (nxt.start() if nxt else len(tail))
    return text[start:end]


def test_readme_gui_agent_slash_commands_exist_in_real_registry():
    text = _readme()
    section = _section(text, "### GUI Agent Mode — dev-assist integration")
    documented = _slash_tokens(section) - {"audit"}
    assert documented, "no slash commands found in the GUI agent section"
    from modules.slash_commands import command_names
    real = set(command_names(""))
    missing = documented - real
    assert not missing, f"README slash commands missing from registry: {missing}"


def test_readme_gui_audit_invokes_real_code_audit():
    text = _readme()
    section = _section(text, "### GUI Agent Mode — dev-assist integration")
    assert "audit" in _slash_tokens(section)
    from modules import code_audit
    assert callable(code_audit.run)


def test_readme_cli_slash_commands_exist_in_real_registry():
    text = _readme()
    section = _section(text, "# Slash commands & persistent sessions")
    documented = _slash_tokens(section, backticked=False)
    assert documented, "no slash commands found in the CLI slash section"
    from modules.slash_commands import command_names
    real = set(command_names(""))
    missing = documented - real
    assert not missing, f"README slash commands missing from registry: {missing}"


def test_readme_agents_exist_in_real_registry():
    text = _readme()
    section = _section(text, "### GUI Agent Mode — dev-assist integration")
    names = set(re.findall(r"`([a-z]+)`", section)) & \
        {"build", "coder", "reviewer", "explore", "general"}
    assert names, "expected agent names missing from the README"
    from core.agents import routable_agents
    real = set(routable_agents().keys())
    assert names <= real, f"documented agents not in registry: {names - real}"


def test_gui_picker_matches_agent_registry():
    from core.agents import routable_agents
    real = set(routable_agents().keys())
    picked = {a["id"] for a in agent_bridge.available_agents()}
    assert picked and picked <= real, f"picker ids {picked} not in registry {real}"

def test_bridge_prefers_bundled_dev_assist_when_frozen(tmp_path, monkeypatch):
    bundled = tmp_path / "_MEIPASS" / "dev-assist"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEIPASS"),
                        raising=False)
    paths = agent_bridge._dev_assist_paths()
    assert paths and os.path.normpath(paths[0]) == os.path.normpath(str(bundled))
    assert os.path.normpath(paths[-1]).endswith("dev-assist")
