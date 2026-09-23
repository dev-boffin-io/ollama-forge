#!/usr/bin/env python3
"""
dev-assist — Personal AI DevOps Assistant with RAG

Usage:
  python main.py                    →  Terminal REPL (CLI mode)
  python main.py --web              →  Web UI mode (FastAPI, in-process)
  python main.py --web --port 8080
  python main.py --resume           →  resume the most recent session
  python main.py --session <id>     →  resume a specific session
  python main.py --help             →  Show this help

CLI input modes:
  <message>           →  chat with AI / built-in commands / slash (/...) commands
  !<cmd>              →  run shell command  (e.g. !ls -la)
  !run <cmd>          →  force shell run    (e.g. !run find . -name *.py)

Slash commands:
  /init /review /new /sessions /resume /rename /delete /help
  (custom /<name> commands + skills; see: /help)

Keyboard shortcuts:
  ctrl+p  command palette     ctrl+c  clear input (empty: quit)
  ctrl+x  leader key → n new session · l list sessions · s status ·
          m models · a commands · h help · q quit
  Shift+Enter  newline         Tab     complete / submit completions

Environment variables:
  DEV_ASSIST_API_KEY   →  API key for external AI (instead of settings.json)
  DEV_ASSIST_DATA_DIR  →  where sessions.db lives (default ~/.config/dev-assist)
"""

from __future__ import annotations

import os
import sys


def _start_web(host: str, port: int) -> None:
    """
    Launch the FastAPI web interface -- runs fully in-process (no subprocess,
    no external CLI, no dependency on a Python interpreter being on PATH).
    This is what makes onefile PyInstaller packaging simple: uvicorn/FastAPI
    are bundled straight into the frozen binary and served directly.
    """
    _frozen = getattr(sys, "frozen", False)

    # -- Make sure this directory (and its bundled data) is importable ------
    _base_dir = (
        getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        if _frozen else os.path.dirname(os.path.abspath(__file__))
    )
    if _base_dir not in sys.path:
        sys.path.insert(0, _base_dir)

    try:
        import uvicorn
    except ImportError:
        _print("[!] uvicorn not found. Install it:")
        _print("   pip install fastapi uvicorn python-multipart")
        sys.exit(1)

    try:
        from web_app import app as _fastapi_app
    except Exception as exc:
        _print(f"[!] Could not load web app: {exc}")
        sys.exit(1)

    _print("[bold]dev-assist[/bold] -- Web UI starting...")
    _display_host = host if host != "0.0.0.0" else "127.0.0.1"
    _print(f"""
  URL  : [link]http://{_display_host}:{port}[/link]

  [green]Starting server...[/green] Open the URL above in your browser once ready.
  Press [bold]Ctrl+C[/bold] to stop.
""")

    try:
        uvicorn.run(_fastapi_app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        _print("\n Web UI stopped.")

def _start_cli(resume_spec: bool | str | None = None) -> None:
    """Launch terminal REPL mode with history + completion."""
    from core.banner import show_banner
    from core.router import handle_input

    try:
        from prompt_toolkit.patch_stdout import patch_stdout
    except ImportError:
        import contextlib
        patch_stdout = contextlib.nullcontext  # no-op if prompt_toolkit isn't installed

    show_banner()

    # Show index status on startup
    try:
        from core.vector_store import get_stats
        stats = get_stats()
        if stats["total_files"] > 0:
            _print(f"📚 Index loaded: [cyan]{stats['total_files']} files[/cyan], "
                   f"[cyan]{stats['total_chunks']} chunks[/cyan] ready.\n")
        else:
            _print("💡 No project indexed yet. Run: [bold]index /path/to/your/project[/bold]\n")
    except Exception:
        pass

    try:
        from core.ai import get_current_model
        _print(f"🤖 Active model : [green]{get_current_model()}[/green]  "
               f"([dim]change: model set[/dim])\n")
    except Exception:
        pass

    _print("Type [bold]help[/bold] for commands · [bold]!cmd[/bold] for shell · [bold]!run cmd[/bold] for force-run · [bold]exit[/bold] to quit.\n")

    # Boot the persistent session store (opencode-style sessions).
    try:
        from core.session_store import bootstrap
        session = bootstrap(project=os.getcwd(), resume=resume_spec or False)
        _print(f"💾 [cyan]session {session.id[:8]}[/cyan]  ·  [bold]/new[/bold] fresh · "
               f"[bold]/sessions[/bold] list · [bold]/resume[/bold] switch · [bold]ctrl+p[/bold] palette\n")
    except Exception as exc:
        _print(f"[dim](sessions store unavailable: {exc})[/dim]\n")

    # Setup readline / prompt_toolkit for history + completion
    prompt_fn = _build_prompt_fn()

    with patch_stdout():
        while True:
            try:
                user_input = prompt_fn()
            except (KeyboardInterrupt, EOFError):
                _print("\n\n👋 Bye!")
                sys.exit(0)

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                _print("👋 Bye!")
                sys.exit(0)

            # ── Ollama on/off commands ─────────────────────────────────────────
            cmd_lower = user_input.strip().lower()
            if cmd_lower in ("ollama on", "ollama start"):
                from core.ollama_status import start_ollama
                _print(start_ollama())
                continue
            if cmd_lower in ("ollama off", "ollama stop"):
                from core.ollama_status import stop_ollama
                _print(stop_ollama())
                continue
            if cmd_lower in ("ollama status",):
                from core.ollama_status import get_status_line
                _print(get_status_line())
                continue

            # ── Special CLI history commands ───────────────────────────────────
            raw     = user_input.strip()
            cmd_raw = raw.lower()
            if cmd_raw in ("history", "/history"):
                from core.cli_history import show
                show()
                continue
            if cmd_raw in ("clear history", "/clear"):
                from core.cli_history import clear
                clear()
                _print("🗑️  CLI history cleared.")
                continue

            # ── Shell command execution ──────────────────────────────────────
            # !run <cmd>  → explicit force-run (e.g. !run find . -name *.py)
            # !<cmd>      → shell shortcut  (e.g. !ls -la)
            # anything else → AI / router
            if raw.startswith("!run "):
                from modules.shell_exec import run_shell_command
                run_shell_command(raw[5:].strip())
                continue
            if raw.startswith("!"):
                from modules.shell_exec import run_shell_command
                run_shell_command(raw[1:].strip())
                continue

            # ── Slash commands (opencode-style /init, /review, sessions …) ──
            if raw.startswith("/"):
                try:
                    from modules.slash_commands import run as run_slash
                    run_slash(raw)
                except Exception as exc:
                    _print(f"⚠️  {exc}")
                continue

            # Save user input to CLI history, then handle
            try:
                from core.cli_history import save as _cli_save
                _cli_save("user", raw)
            except Exception:
                pass

            import io as _io
            import sys as _sys
            _buf = _io.StringIO()

            class _Tee:
                def __init__(self, stream, buf):
                    self._stream = stream
                    self._buf = buf
                def write(self, data):
                    self._stream.write(data)
                    self._buf.write(data)
                    self._stream.flush()
                    return len(data)
                def flush(self):
                    self._stream.flush()
                    self._buf.flush()

            _old_stdout = _sys.stdout
            _sys.stdout = _Tee(_old_stdout, _buf)
            try:
                handle_input(user_input)
            finally:
                _sys.stdout = _old_stdout
            _out = _buf.getvalue()
            if _out:
                print(_out, end="")
                try:
                    from core.cli_history import save as _cli_save
                    _cli_save("assistant", _out.strip())
                except Exception:
                    pass


def _get_prompt_str() -> str:
    """Build dynamic prompt: ⚡ dev-assist > /current/path$"""
    import os as _os
    try:
        from modules.shell_exec import get_cwd
        cwd = get_cwd()
    except Exception:
        cwd = _os.getcwd()

    # Shorten home dir to ~
    home = _os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    return f"⚡ dev-assist > {cwd}$ "


_LEADER_ROUTER = {
    "status": "status",
    "models": "model list",
    "slash_help": "help",
}


def _build_key_bindings():
    """
    Prompt key bindings — a port of opencode's keymap onto prompt_toolkit:

      ctrl+c          clear current input; press again (or on empty) to quit
      ctrl+p          open the command palette (/...)
      ctrl+x          leader key →  n new session · l list sessions · s status
                      · m models · a commands · h help · q quit
      Enter           submit (or accept a highlighted completion)
      Shift+Enter     insert a newline (multiline input)

    Because prompt_toolkit merges caller bindings after the defaults and
    resolves in reverse order, these handlers take precedence automatically.
    """
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()
    state = {"leader": False}
    leader_active = Condition(lambda: state["leader"])

    @kb.add("c-c")
    def _ctrl_c(event):
        buf = event.current_buffer
        if buf.text:
            buf.reset()  # opencode behaviour: first ctrl+c clears the input
        else:
            event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-p")
    def _command_palette(event):
        event.app.exit(result=("palette", event.current_buffer.text))

    @kb.add("c-x")
    def _leader(event):
        state["leader"] = True

    @kb.add("q", filter=leader_active)
    def _leader_quit(event):
        state["leader"] = False
        event.app.exit(exception=KeyboardInterrupt())

    def _leader_action(action):
        def handler(event):
            state["leader"] = False
            event.app.exit(result=(action, ""))
        return handler

    for key, action in (
        ("n", "new_session"),
        ("l", "list_sessions"),
        ("s", "status"),
        ("m", "models"),
        ("a", "slash_help"),
        ("h", "slash_help"),
    ):
        kb.add(key, filter=leader_active)(_leader_action(action))

    @kb.add("<any>", filter=leader_active)
    def _leader_cancel(event):
        """Any other key after ctrl+x cancels the leader (and is consumed)."""
        state["leader"] = False

    @kb.add("enter")
    def _enter(event):
        buf = event.current_buffer
        if buf.complete_state and buf.complete_state.current_completion:
            # Enter with a highlighted completion inserts it; Enter again submits.
            buf.apply_completion(buf.complete_state.current_completion)
            return
        buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _alt_enter(event):
        event.current_buffer.insert_text("\n")

    return kb


def _build_prompt_fn():
    """
    Return an input function with readline history + tab completion if available.
    Falls back to plain input(). Prompt is dynamic (shows cwd).
    """

    # Try prompt_toolkit first (best UX)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import InMemoryHistory

        COMPLETIONS = [
            "index", "index status", "index clear",
            "audit", "help", "status", "plugins", "exit", "quit",
            "model", "model list", "model set", "model engine ollama", "model engine api",
            "ollama on", "ollama off", "ollama status",
            "history", "clear history", "/history", "/clear",
            # agent mode
            "do ", "agent ", "undo",
            # slash commands (opencode-style)
            "/init", "/review", "/new", "/sessions", "/resume", "/rename", "/delete", "/help",
            # shell shortcuts
            "!ls", "!ls -la", "!pwd", "!cat", "!grep", "!ps aux",
            "!df -h", "!free -h", "!top", "!htop", "!ping", "!curl",
            "!git status", "!git log", "!git diff",
            "!run find . -name",
        ]
        try:
            from modules.slash_commands import command_names
            extra = ["/" + n for n in command_names()]
            COMPLETIONS = list(dict.fromkeys(COMPLETIONS + extra))
        except Exception:
            pass

        from prompt_toolkit.styles import Style

        from core import tui_status

        toolbar_style = Style.from_dict({
            "toolbar": "bg:#333333 #ffffff",
            "toolbar.activity": "bg:#333333 #ffcc00",
        })

        session = PromptSession(
            history=InMemoryHistory(),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(COMPLETIONS, ignore_case=True),
            style=toolbar_style,
            key_bindings=_build_key_bindings(),
            multiline=True,
            prompt_continuation=lambda _w, _ln, is_soft_wrap: "… " if not is_soft_wrap else "· ",
        )

        def _pt_prompt(default: str = "") -> str:
            """Prompt, returning submitted text. Inline `ctrl+p`/leader actions
            are executed here so the main loop stays a plain read loop."""
            while True:
                result = session.prompt(
                    _get_prompt_str(),
                    default=default,
                    bottom_toolbar=tui_status.render_bottom_toolbar,
                    refresh_interval=1.0,  # keeps the status bar live while idle
                )
                if not isinstance(result, tuple):
                    return result.strip()
                action, payload = result
                if action == "palette":
                    from modules.slash_commands import palette
                    if palette(prefill=payload):
                        return ""
                    default = payload  # cancelled → restore what the user had typed
                    continue
                if action in ("new_session", "list_sessions"):
                    from modules.slash_commands import run as run_slash
                    run_slash("/" + ("new" if action == "new_session" else "sessions"))
                elif action in ("status", "models", "slash_help"):
                    from core.router import handle_input
                    handle_input(_LEADER_ROUTER[action])
                return ""

        return _pt_prompt

    except ImportError:
        pass

    # Try readline (stdlib, no pip needed)
    try:
        import readline

        COMMANDS = [
            "index", "index status", "index clear",
            "audit", "help", "status", "plugins", "exit", "quit",
            "model", "model list", "model set",
            "ollama on", "ollama off", "ollama status",
            "history", "clear history",
            "!ls", "!ls -la", "!pwd", "!cat", "!grep", "!git status",
            "!run ",
        ]

        def _completer(text: str, state: int):
            options = [c for c in COMMANDS if c.startswith(text)]
            return options[state] if state < len(options) else None

        readline.set_completer(_completer)
        readline.parse_and_bind("tab: complete")

        def _readline_prompt() -> str:
            return input(_get_prompt_str()).strip()

        return _readline_prompt

    except ImportError:
        pass

    # Plain fallback
    def _plain_prompt() -> str:
        return input(_get_prompt_str()).strip()

    return _plain_prompt


def _parse_args() -> tuple[bool, str, int, bool | str | None]:
    """Parse --web, --host, --port, --resume, --session from sys.argv."""
    args = sys.argv[1:]
    web = "--web" in args
    host = "127.0.0.1"  # loopback by default — pass --host 0.0.0.0 to expose
    port = 8000
    resume_spec: bool | str | None = None

    if "--host" in args:
        idx = args.index("--host")
        if idx + 1 < len(args):
            host = args[idx + 1]

    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            try:
                port = int(args[idx + 1])
            except ValueError:
                print(f"⚠️  Invalid port: {args[idx+1]}", file=sys.stderr)
                sys.exit(1)

    if "--resume" in args:
        resume_spec = True

    if "--session" in args:
        idx = args.index("--session")
        if idx + 1 < len(args):
            resume_spec = str(args[idx + 1])

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    return web, host, port, resume_spec


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))


def main() -> None:
    web, host, port, resume_spec = _parse_args()

    if web:
        _start_web(host, port)
    else:
        _start_cli(resume_spec)


if __name__ == "__main__":
    main()
