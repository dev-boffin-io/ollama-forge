"""
Command Helper — Fix common terminal errors.

Improvements:
- Uses core.shell for robust subprocess handling
- Rich table output for port info
- AI-assisted error explanation
"""

from __future__ import annotations

import re
import os
from core.shell import run, RunResult


def run_cmd(text: str = "") -> None:
    """General command helper dispatcher."""
    port_match = re.search(r"\d+", text)
    if port_match:
        fix_port(text)
    else:
        _print("💡 Usage examples:")
        _print("   fix port 3000")
        _print("   kill port 8080")


# Keep old name for router compat
run = run_cmd  # type: ignore[assignment]


def fix_port(text: str = "") -> None:
    """Find and kill process using a specific port."""
    from core.shell import run as shell_run, check_binary

    match = re.search(r"(\d+)", text)
    if not match:
        _print("⚠️  Please specify a port number. Example: fix port 3000")
        return

    port = match.group(1)
    _print(f"🔍 Checking port [cyan]{port}[/cyan]...\n")

    system = os.uname().sysname.lower() if hasattr(os, "uname") else "linux"

    if "darwin" in system and check_binary("lsof"):
        res = shell_run(["lsof", "-i", f":{port}", "-t"], timeout=10)
    elif check_binary("fuser"):
        res = shell_run(["fuser", f"{port}/tcp"], timeout=10)
    elif check_binary("ss"):
        res = shell_run(["ss", "-tlnp", f"sport = :{port}"], timeout=10)
        _print(res.output or f"✅ Port {port} is free.")
        return
    else:
        _print(f"📋 Run manually:")
        _print(f"   lsof -i :{port}   OR   fuser {port}/tcp")
        return

    pids = [p for p in res.stdout.strip().split() if p.isdigit()]

    if not pids:
        _print(f"✅ Port [green]{port}[/green] is free — nothing running there.")
        return

    _print(f"⚡ Port [yellow]{port}[/yellow] used by PID(s): [bold]{', '.join(pids)}[/bold]")

    # Show process info for each PID
    for pid in pids:
        info = shell_run(["ps", "-p", pid, "-o", "comm="], timeout=5)
        if info.ok and info.stdout.strip():
            _print(f"   PID {pid} → [dim]{info.stdout.strip()}[/dim]")

    choice = input("\n❓ Kill now? [y/N]: ").strip().lower()
    if choice == "y":
        for pid in pids:
            res = shell_run(["kill", "-9", pid], timeout=5)
            if res.ok:
                _print(f"   ✅ Killed PID {pid}")
            else:
                _print(res.friendly_error())


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re as _re
        print(_re.sub(r"\[/?[^\]]*\]", "", msg))
