"""
Router — Intent detection and module dispatch.

Improvements:
- Session history injected into RAG queries
- history / clear history commands
- All built-ins use Rich output
"""

from __future__ import annotations

import re
import importlib
import os

# ── Intent patterns ──────────────────────────────────────────────────────────
INTENTS = [
    # ── Ollama control ────────────────────────────────────────────────────
    (r"^ollama\s+on\b|^ollama\s+start\b",   None,  "ollama_on"),
    (r"^ollama\s+off\b|^ollama\s+stop\b",   None,  "ollama_off"),
    (r"^ollama\s+status\b",                  None,  "ollama_status"),

    # ── Agent mode (must precede RAG fallthrough: "do/agent ..." wins) ────
    (r"^\s*(do|agent)\b",                 "modules.agent_mode",    "run"),
    (r"^\s*undo\s*$",                     "modules.agent_mode",    "undo"),

    # ── RAG / Indexing ────────────────────────────────────────────────────
    (r"\b(index|idx)\b",                  "modules.indexer",       "run"),
    (r"\bindex\s+status\b",               "modules.indexer",       "run"),

    # ── Code Audit ────────────────────────────────────────────────────────
    (r"\baudit\b",                        "modules.code_audit",    "run"),

    # ── Command helper ────────────────────────────────────────────────────
    (r"^\s*(?:fix|kill)\s+port\s+(\d+)",  "modules.cmd_helper",    "fix_port"),
    (r"^\s*port\s+(\d+)",                 "modules.cmd_helper",    "fix_port"),

    # ── Tunnel ────────────────────────────────────────────────────────────
    (r"^\s*(?:start\s+|open\s+)?tunnel\b", "modules.tunnel_helper", "run"),
    (r"^\s*ngrok\b",                      "modules.tunnel_helper", "run"),
    (r"^\s*(?:expose|open)\s+(\d+)",      "modules.tunnel_helper", "run"),

    # ── Git ───────────────────────────────────────────────────────────────
    (r"^\s*git\s+(push|pull|rebase|fix)", "modules.git_helper",    "run"),
    (r"^\s*conflict\b",                   "modules.git_helper",    "run"),

    # ── File tools ────────────────────────────────────────────────────────
    (r"^\s*(?:rename|clean)\b",           "modules.file_tool",     "run"),

    # ── Built-ins ─────────────────────────────────────────────────────────
    (r"^model\b",                         None,                    "model_select"),
    (r"\bhelp\b|\bcommands?\b",           None,                    "show_help"),
    (r"\bplugins?\b",                     None,                    "list_plugins"),
    (r"\bstatus\b",                       None,                    "show_status"),
    (r"\bhistory\s+clear\b|\bclear\s+history\b", None,            "clear_history"),
    (r"\bhistory\b",                      None,                    "show_history"),

    # ── RAG fallthrough (broad patterns — must be LAST) ───────────────────
    (r"\bask\b.{0,60}",                   None,                    "rag_ask"),
    (r"\bwhat\b.{0,80}",                  None,                    "rag_ask"),
    (r"\bhow\b.{0,80}",                   None,                    "rag_ask"),
    (r"\bexplain\b",                      None,                    "rag_ask"),
    (r"\bbug\b|\berror\b|\bfix\b.{0,40}", None,                    "rag_ask"),
    (r"\barchitecture\b|\bstructure\b",   None,                    "rag_ask"),
]


def handle_input(text: str) -> None:
    """Match intent and dispatch to appropriate module."""
    text_stripped = text.strip()
    text_lower = text_stripped.lower()

    for pattern, module_path, func_name in INTENTS:
        if re.search(pattern, text_lower):
            if module_path is None:
                _dispatch_builtin(func_name, text_stripped)
            else:
                _dispatch_module(module_path, func_name, text_stripped)
            return

    # Plugin check → smart fallback
    if _try_plugin(text_lower):
        return

    _smart_fallback(text_stripped)


def _dispatch_builtin(func_name: str, text: str) -> None:
    handlers = {
        "model_select":  lambda: _model_select(text),
        "show_help":     _show_help,
        "list_plugins":  _list_plugins,
        "show_status":   _show_status,
        "show_history":  _show_history,
        "clear_history": _clear_history,
        "rag_ask":       lambda: _rag_ask(text),
        "ollama_on":     _ollama_on,
        "ollama_off":    _ollama_off,
        "ollama_status": _ollama_status_cmd,
    }
    fn = handlers.get(func_name)
    if fn:
        fn()


def _dispatch_module(module_path: str, func_name: str, text: str) -> None:
    try:
        mod = importlib.import_module(module_path)
        func = getattr(mod, func_name)
        func(text)
    except ImportError as exc:
        _print(f"⚠️  Module load error: {exc}")
    except Exception as exc:
        _print(f"⚠️  Error: {exc}")


# ── RAG / AI dispatch ────────────────────────────────────────────────────────

def _rag_ask(text: str) -> None:
    from core.vector_store import get_stats
    stats = get_stats()
    if stats["total_files"] > 0:
        from core.rag_engine import ask_with_context
        ask_with_context(text, use_history=True)
    else:
        _plain_ai_with_history(text)


def _smart_fallback(text: str) -> None:
    try:
        from core.vector_store import get_stats
        if get_stats()["total_files"] > 0:
            from core.rag_engine import ask_with_context
            ask_with_context(text, use_history=True)
            return
    except Exception:
        pass
    _plain_ai_with_history(text)


def _plain_ai_with_history(text: str) -> None:
    """Send to AI, injecting conversation history."""
    try:
        from core.session import get_session
        from core.ai import ask_ai
        sess = get_session()
        sess.add_user(text)
        response = ask_ai(text, capture_output=True)
        if response:
            sess.add_assistant(response)
    except Exception as exc:
        _print(f"🤖 AI unavailable: {exc}")
        _print("   Run: pip install ollama")
        _print("   Then: ollama pull qwen2.5-coder:7b && ollama serve")


def _ollama_on() -> None:
    from core.ollama_status import start_ollama
    _print(start_ollama())


def _ollama_off() -> None:
    from core.ollama_status import stop_ollama
    _print(stop_ollama())


def _ollama_status_cmd() -> None:
    from core.ollama_status import get_status_line
    _print(get_status_line())


# ── History commands ─────────────────────────────────────────────────────────

def _show_history() -> None:
    from core.session import get_session
    sess = get_session()
    _print(f"\n📜 Session: {sess.history_summary()}\n")
    for turn in sess.get_history()[-10:]:
        icon = "👤" if turn.role == "user" else "🤖"
        preview = turn.content[:120].replace("\n", " ")
        _print(f"  {icon} [dim]{preview}[/dim]")
    _print("")


def _clear_history() -> None:
    from core.session import get_session
    get_session().clear_history()
    _print("✅ Conversation history cleared.")


# ── Plugin dispatch ───────────────────────────────────────────────────────────

def _get_plugins_dir() -> str:
    """
    Return the plugins directory path — works both in normal and frozen (PyInstaller) mode.
    In a frozen onefile binary sys._MEIPASS is the extract tmpdir; plugins/ is bundled there.
    """
    import sys as _sys
    if getattr(_sys, "frozen", False):
        meipass = getattr(_sys, "_MEIPASS", "")
        if meipass:
            return os.path.join(meipass, "plugins")
    # Normal / source mode
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))


def _try_plugin(text: str) -> bool:
    plugins_dir = _get_plugins_dir()
    if not os.path.isdir(plugins_dir):
        return False
    try:
        entries = os.listdir(plugins_dir)
    except OSError:
        return False
    for fname in entries:
        if fname.endswith(".py") and not fname.startswith("_"):
            plugin_name = fname[:-3]
            if plugin_name in text:
                try:
                    mod = importlib.import_module(f"plugins.{plugin_name}")
                    mod.run(text)
                    return True
                except Exception as exc:
                    _print(f"⚠️  Plugin error ({plugin_name}): {exc}")
                    return True
    return False


# ── Built-in handlers ────────────────────────────────────────────────────────

def _model_select(text: str) -> None:
    from core.ai import _load_config, save_config, get_current_model
    from core import providers as _providers

    parts = text.strip().split()
    cfg = _load_config()

    # Normalize — work with dict for compat
    if hasattr(cfg, "model_dump"):
        cfg_dict = cfg.model_dump()
    else:
        cfg_dict = dict(cfg)

    active = cfg_dict.get("active_provider", "ollama")

    if len(parts) == 1:
        _print(f"""
🤖 Current model   : [green]{get_current_model()}[/green]

   Usage:
   [bold]model list[/bold]                     → list all providers & their models
   [bold]model set[/bold] <name>               → set model for the active provider
   [bold]model provider[/bold] <id>            → switch provider (ollama, openai,
                                    anthropic, groq, openrouter,
                                    mistral, azure, custom)
   [bold]model engine[/bold] ollama|api        → legacy alias for provider switch
""")
        return

    sub = parts[1].lower()

    if sub == "list":
        _print("\n📦 [bold]AI providers:[/bold]")
        for pid, p in _providers.PROVIDERS.items():
            prof = cfg_dict.get("providers", {}).get(pid, {})
            label = prof.get("label") or p.get("label", pid)
            kind = prof.get("kind") or p.get("kind", "")
            default = prof.get("default_model") or p.get("default_model", "")
            marker = " ✅  [dim]← active[/dim]" if pid == active else ""
            _print(f"\n• [bold]{label}[/bold] [dim]({pid})[/dim]{marker}  [dim]{kind}[/dim]")
            models = prof.get("models") or p.get("models", [])
            for m in models:
                m_active = " ← active" if (pid == active and m == default) else ""
                _print(f"    {m}{m_active}")
        _print(f"\n💡 Active provider: [cyan]{active}[/cyan]  |  "
               f"[bold]model provider[/bold] <id>  ·  [bold]model set[/bold] <name>\n")
        return

    if sub == "provider" and len(parts) >= 3:
        pid = parts[2].lower()
        if pid not in _providers.PROVIDERS:
            _print("⚠️  Valid providers: " + ", ".join(_providers.PROVIDER_ORDER))
            return
        cfg_dict["active_provider"] = pid
        cfg_dict["ai_engine"] = "ollama" if pid == "ollama" else "api"  # legacy mirror
        save_config(cfg_dict)
        _print(f"✅ Provider → [green]{_providers.PROVIDERS[pid]['label']}[/green]  "
               f"(model: {get_current_model()})")
        return

    if sub == "set" and len(parts) >= 3:
        model_name = parts[2]
        prof = cfg_dict.setdefault("providers", {}).get(active, {})
        prof["default_model"] = model_name
        cfg_dict["providers"][active] = prof
        save_config(cfg_dict)
        _print(f"✅ Model → [green]{active}/{model_name}[/green]")
        return

    if sub == "engine" and len(parts) >= 3:
        new_engine = parts[2].lower()
        mapping = {"ollama": "ollama", "api": "groq"}
        if new_engine not in mapping:
            _print("⚠️  Valid engines: [bold]ollama[/bold]  or  [bold]api[/bold] "
                   "(api maps to the Groq provider)")
            return
        cfg_dict["active_provider"] = mapping[new_engine]
        cfg_dict["ai_engine"] = new_engine
        save_config(cfg_dict)
        _print(f"✅ Engine → [green]{mapping[new_engine]}[/green]  (model: {get_current_model()})")
        return

    _print("⚠️  Unknown command. Type [bold]help[/bold] for available commands.")


def _show_status() -> None:
    from core.vector_store import get_stats
    from core.session import get_session
    stats = get_stats()
    sess = get_session()
    _print(f"""
📊 [bold]dev-assist status[/bold]
   Indexed files  : [cyan]{stats['total_files']}[/cyan]
   Indexed chunks : [cyan]{stats['total_chunks']}[/cyan]
   Session        : {sess.history_summary()}
""")


def _show_help() -> None:
    try:
        from rich.panel import Panel
        from rich.console import Console
        from rich.text import Text
        console = Console()
        help_text = """\
[bold cyan]Agent Mode (edits your code):[/bold cyan]
  do <task>                →  agent reads/edits files & runs commands
  agent <task>             →  same as 'do'
  do <task> --yes          →  skip approval prompts
  do <task> --verbose      →  show full tool output
  undo                     →  revert every file the last run changed
  [dim]e.g. do fix the failing test in tests/[/dim]
  [dim]Needs a tool-capable model (qwen2.5-coder:7b, llama3.1:8b, or API mode)[/dim]

[bold cyan]RAG / Code Analysis:[/bold cyan]
  index /path/to/project   →  index a local folder
  index status             →  show indexed files
  index clear              →  clear the index
  <any question>           →  ask AI about indexed code

[bold cyan]Dev Tools:[/bold cyan]
  audit                    →  AI code review (git diff)
  audit --no-sensitive     →  skip sensitive files
  fix port 3000            →  kill process on port
  tunnel [port]            →  start cloudflared tunnel
  git push fix             →  fix git push errors
  git conflict             →  resolve merge conflicts
  rename / clean           →  file operations
  clean --dry-run          →  preview only (no delete)

[bold cyan]Shell Commands:[/bold cyan]
  !<command>               →  run shell command   (e.g. !ls -la)
  !run <command>           →  force shell run     (e.g. !run find . -name *.py)
  cd /path                 →  change directory (updates prompt)

[bold cyan]Ollama Control:[/bold cyan]
  ollama on                →  start ollama serve (background)
  ollama off               →  stop ollama serve
  ollama status            →  show running/stopped status

[bold cyan]History:[/bold cyan]
  history                  →  show conversation history
  history clear            →  clear session history

[bold cyan]AI Model:[/bold cyan]
  model                    →  বর্তমান model দেখাও
  model list               →  সব provider ও model
  model set <নাম>          →  active provider-এর model বদলাও
  model provider <id>      →  provider বদলাও (ollama, groq, openai, anthropic, openrouter, mistral, azure, custom)

[bold cyan]Other:[/bold cyan]
  status                   →  show index + session status
  plugins                  →  list installed plugins
  exit / quit              →  quit

[dim]💡 Tip: Index your project first, then ask anything!
   > index /home/user/my-project
   > main.py তে কী হচ্ছে?

   Shell: just type commands directly!
   > ls -la
   > git status
   > ps aux | grep python[/dim]"""
        console.print(Panel(help_text, title="dev-assist — Commands", border_style="blue"))
    except ImportError:
        print("""
┌──────────────────────────────────────────────┐
│           dev-assist — Commands              │
├──────────────────────────────────────────────┤
│  index /path    index status                 │
│  audit          fix port N  tunnel [port]    │
│  git push fix   git conflict                 │
│  rename  clean  clean --dry-run              │
│  history        history clear                │
│  model          model list   model set <N>   │
│  status         plugins      exit            │
└──────────────────────────────────────────────┘
""")


def _list_plugins() -> None:
    plugins_dir = _get_plugins_dir()
    plugins = []
    if os.path.isdir(plugins_dir):
        try:
            plugins = [
                f[:-3] for f in os.listdir(plugins_dir)
                if f.endswith(".py") and not f.startswith("_")
            ]
        except OSError:
            pass
    if plugins:
        _print("🔌 [bold]Installed plugins:[/bold]")
        for p in plugins:
            _print(f"   • {p}")
    else:
        _print("🔌 No plugins installed. Add .py files to [dim]plugins/[/dim] folder.")


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))
