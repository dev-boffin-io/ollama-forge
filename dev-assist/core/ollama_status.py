"""
Ollama Status Manager — Check, start, and stop ollama service.

Provides:
- get_status()     → "running" | "stopped"
- get_status_line() → colored status line for prompt display
- start_ollama()   → start ollama serve in background
- stop_ollama()    → stop ollama serve
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time

# Singleton background process handle
_ollama_proc: subprocess.Popen | None = None
_lock = threading.Lock()


def _is_ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _check_running_via_ps() -> bool:
    """Check if any 'ollama serve' process is alive using ps."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ollama serve"],
            capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        pass

    # Fallback: ps aux
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True, text=True, timeout=3
        )
        return "ollama serve" in result.stdout or (
            "ollama" in result.stdout and "serve" in result.stdout
        )
    except Exception:
        return False


def _check_running_via_api() -> bool:
    """Try hitting ollama's local API (port 11434)."""
    try:
        import urllib.request
        req = urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=1
        )
        return req.status == 200
    except Exception:
        return False


def get_status() -> str:
    """Return 'running' or 'stopped'."""
    if not _is_ollama_installed():
        return "not_installed"

    # Fast check via API port first
    if _check_running_via_api():
        return "running"

    # Fallback: check process list
    if _check_running_via_ps():
        return "running"

    return "stopped"


def get_status_line() -> str:
    """
    Return a colored one-line status string for display above the prompt.
    Uses Rich markup if available, plain ANSI otherwise.
    """
    status = get_status()

    if status == "running":
        icon = "🟢"
        label = "running"
        try:
            from rich.console import Console
            from io import StringIO
            buf = StringIO()
            Console(file=buf, highlight=False, force_terminal=True).print(
                f"{icon} ollama [bold green]{label}[/bold green]  "
                f"[dim](type [bold]ollama off[/bold] to stop)[/dim]"
            )
            return buf.getvalue().rstrip()
        except ImportError:
            return f"{icon} ollama {label}  (type 'ollama off' to stop)"

    elif status == "stopped":
        icon = "🔴"
        label = "stopped"
        try:
            from rich.console import Console
            from io import StringIO
            buf = StringIO()
            Console(file=buf, highlight=False, force_terminal=True).print(
                f"{icon} ollama [bold red]{label}[/bold red]  "
                f"[dim](type [bold]ollama on[/bold] to start)[/dim]"
            )
            return buf.getvalue().rstrip()
        except ImportError:
            return f"{icon} ollama {label}  (type 'ollama on' to start)"

    else:  # not_installed
        icon = "⚫"
        try:
            from rich.console import Console
            from io import StringIO
            buf = StringIO()
            Console(file=buf, highlight=False, force_terminal=True).print(
                f"{icon} ollama [dim]not installed[/dim]"
            )
            return buf.getvalue().rstrip()
        except ImportError:
            return f"{icon} ollama not installed"


def start_ollama() -> str:
    """
    Start 'ollama serve' in the background.
    Returns a user-facing status message.
    """
    global _ollama_proc

    if not _is_ollama_installed():
        return "⚠️  ollama is not installed. Visit: https://ollama.com/download"

    if get_status() == "running":
        return "✅ ollama is already running."

    with _lock:
        try:
            _ollama_proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,   # detach from terminal (POSIX)
            )
        except Exception as exc:
            return f"❌ Failed to start ollama: {exc}"

    # Wait up to 4 seconds for API to respond
    for _ in range(8):
        time.sleep(0.5)
        if _check_running_via_api():
            return "✅ ollama started successfully. (🟢 running)"

    return "⚠️  ollama started but API not responding yet — try again in a moment."


def stop_ollama() -> str:
    """
    Stop ollama serve (kills all matching processes).
    Returns a user-facing status message.
    """
    global _ollama_proc

    if get_status() == "stopped":
        return "ℹ️  ollama is already stopped."

    killed = False

    # First try: kill our own spawned process
    with _lock:
        if _ollama_proc is not None:
            try:
                _ollama_proc.terminate()
                try:
                    _ollama_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _ollama_proc.kill()
                _ollama_proc = None
                killed = True
            except Exception:
                pass

    # Second: kill any other ollama serve processes
    try:
        result = subprocess.run(
            ["pkill", "-f", "ollama serve"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            killed = True
    except Exception:
        pass

    # Fallback: kill by port 11434
    if not killed:
        try:
            result = subprocess.run(
                ["fuser", "-k", "11434/tcp"],
                capture_output=True, text=True, timeout=5
            )
            killed = result.returncode == 0
        except Exception:
            pass

    # Give it a moment to die
    time.sleep(0.8)

    final = get_status()
    if final == "stopped":
        return "✅ ollama stopped. (🔴 stopped)"
    else:
        return "⚠️  Could not stop ollama. Try: pkill -f 'ollama serve'"
