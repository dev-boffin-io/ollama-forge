#!/usr/bin/env python3
"""
dev-assist — Personal AI DevOps Assistant with RAG

Usage:
  python main.py                    →  Terminal REPL (CLI mode)
  python main.py --web              →  Web UI mode (FastAPI, in-process)
  python main.py --web --port 8080
  python main.py --help             →  Show this help

CLI input modes:
  <message>           →  chat with AI / built-in commands
  !<cmd>              →  run shell command  (e.g. !ls -la)
  !run <cmd>          →  force shell run    (e.g. !run find . -name *.py)

Environment variables:
  DEV_ASSIST_API_KEY   →  API key for external AI (instead of settings.json)
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

def _start_cli() -> None:
    """Launch terminal REPL mode with history + completion."""
    from core.router import handle_input
    from core.banner import show_banner

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

    # Setup readline / prompt_toolkit for history + completion
    prompt_fn = _build_prompt_fn()

    while True:
        try:
            # ── Show ollama status line above prompt ───────────────────────
            _print_ollama_status()
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

        # Save user input to CLI history, then handle
        try:
            from core.cli_history import save as _cli_save
            _cli_save("user", raw)
        except Exception:
            pass

        import io as _io, sys as _sys
        _buf = _io.StringIO()
        _old_stdout = _sys.stdout
        _sys.stdout = _buf
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


def _print_ollama_status() -> None:
    """Print ollama status line above the prompt (no trailing newline — prompt follows)."""
    try:
        from core.ollama_status import get_status_line
        line = get_status_line()
        # sys.stdout.write keeps it on the same visual block as the prompt below
        import sys as _sys
        _sys.stdout.write(line + "\n")
        _sys.stdout.flush()
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


def _build_prompt_fn():
    """
    Return an input function with readline history + tab completion if available.
    Falls back to plain input(). Prompt is dynamic (shows cwd).
    """

    # Try prompt_toolkit first (best UX)
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.completion import WordCompleter

        COMPLETIONS = [
            "index", "index status", "index clear",
            "audit", "help", "status", "plugins", "exit", "quit",
            "model", "model list", "model set", "model engine ollama", "model engine api",
            "ollama on", "ollama off", "ollama status",
            "history", "clear history",
            # shell shortcuts
            "!ls", "!ls -la", "!pwd", "!cat", "!grep", "!ps aux",
            "!df -h", "!free -h", "!top", "!htop", "!ping", "!curl",
            "!git status", "!git log", "!git diff",
            "!run find . -name",
        ]

        session = PromptSession(
            history=InMemoryHistory(),
            auto_suggest=AutoSuggestFromHistory(),
            completer=WordCompleter(COMPLETIONS, ignore_case=True),
        )

        def _pt_prompt() -> str:
            return session.prompt(_get_prompt_str()).strip()

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


def _parse_args() -> tuple[bool, str, int]:
    """Parse --web, --host, --port from sys.argv without external deps."""
    args = sys.argv[1:]
    web = "--web" in args
    host = "0.0.0.0"
    port = 8000

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

    if "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    return web, host, port


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))


def main() -> None:
    web, host, port = _parse_args()

    if web:
        _start_web(host, port)
    else:
        _start_cli()


if __name__ == "__main__":
    main()
