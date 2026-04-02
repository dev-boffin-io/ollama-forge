"""
Shell Runner — Safe subprocess execution with user-friendly error messages.

All subprocess calls in dev-assist should go through this module.
Provides:
- Structured result objects
- User-friendly error messages
- Timeout support
- Rich/plain output
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from typing import Sequence


@dataclass
class RunResult:
    """Result of a shell command execution."""
    returncode: int
    stdout: str
    stderr: str
    command: list[str]
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr, stripped."""
        combined = "\n".join(filter(None, [self.stdout.strip(), self.stderr.strip()]))
        return combined.strip()

    def friendly_error(self) -> str:
        """Return a human-readable error description."""
        cmd_str = " ".join(self.command)

        if self.timed_out:
            return f"⏱ Command timed out: `{cmd_str}`"

        err = self.stderr.strip() or self.stdout.strip() or "(no output)"

        # Known error patterns → friendly messages
        patterns = [
            ("Permission denied",
             "🔒 Permission denied. Try running with sudo, or check file permissions."),
            ("command not found",
             f"📦 Command not found: `{self.command[0]}`. "
             f"Install it first or check your PATH."),
            ("No such file or directory",
             f"📂 File or directory not found. "
             f"Check the path and make sure it exists."),
            ("Connection refused",
             "🔌 Connection refused. "
             "Is the service running? Check with: systemctl status <service>"),
            ("Address already in use",
             "⚡ Port is already in use. "
             "Use `fix port <N>` to find and kill the process."),
            ("fatal: not a git repository",
             "📁 Not inside a git repository. Run `git init` or cd into your project."),
            ("fatal: Authentication failed",
             "🔑 Git authentication failed. "
             "Check your credentials or SSH key setup."),
            ("error: failed to push",
             "🚀 Git push failed. "
             "Try: `git pull --rebase origin <branch>` then push again."),
            ("CONFLICT",
             "⚔️ Merge conflict detected. "
             "Use `git conflict` for resolution help."),
            ("Could not resolve host",
             "🌐 Network error: can't reach the remote host. Check your internet connection."),
            ("Killed",
             "💀 Process was killed (likely out of memory or OOM killer)."),
        ]

        for keyword, friendly in patterns:
            if keyword.lower() in err.lower():
                return (
                    f"{friendly}\n"
                    f"   Raw output: {err[:200]}"
                )

        return (
            f"❌ Command failed (exit {self.returncode}): `{cmd_str}`\n"
            f"   {err[:300]}"
        )


def run(
    cmd: str | Sequence[str],
    *,
    timeout: int = 30,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> RunResult:
    """
    Run a shell command safely and return a RunResult.

    Args:
        cmd:      Command as list ['git', 'status'] or string 'git status'
        timeout:  Seconds before timeout (default 30)
        cwd:      Working directory
        env:      Extra environment variables (merged with current env)
        capture:  Capture stdout/stderr (True) or let it flow to terminal (False)
    """
    if isinstance(cmd, str):
        cmd_list = cmd.split()
    else:
        cmd_list = list(cmd)

    # Check binary exists
    binary = cmd_list[0]
    if not shutil.which(binary):
        return RunResult(
            returncode=127,
            stdout="",
            stderr=f"{binary}: command not found",
            command=cmd_list,
        )

    import os
    merged_env = dict(os.environ)
    if env:
        merged_env.update(env)

    try:
        result = subprocess.run(
            cmd_list,
            capture_output=capture,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=merged_env,
        )
        return RunResult(
            returncode=result.returncode,
            stdout=result.stdout if capture else "",
            stderr=result.stderr if capture else "",
            command=cmd_list,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            returncode=-1,
            stdout="",
            stderr=f"Timed out after {timeout}s",
            command=cmd_list,
            timed_out=True,
        )
    except FileNotFoundError:
        return RunResult(
            returncode=127,
            stdout="",
            stderr=f"{binary}: not found in PATH",
            command=cmd_list,
        )
    except PermissionError as exc:
        return RunResult(
            returncode=126,
            stdout="",
            stderr=str(exc),
            command=cmd_list,
        )
    except Exception as exc:
        return RunResult(
            returncode=-1,
            stdout="",
            stderr=str(exc),
            command=cmd_list,
        )


def run_git(*args: str, cwd: str | None = None) -> RunResult:
    """Convenience wrapper for git commands."""
    import os
    return run(["git"] + list(args), cwd=cwd or os.getcwd(), timeout=15)


def check_binary(name: str) -> bool:
    """Return True if binary is available in PATH."""
    return shutil.which(name) is not None


def print_result(result: RunResult, *, verbose: bool = False) -> None:
    """Print a RunResult to the terminal with optional Rich formatting."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        console = Console()

        if result.ok:
            if verbose and result.output:
                console.print(result.output)
        else:
            console.print(result.friendly_error(), style="yellow")
    except ImportError:
        if result.ok:
            if verbose and result.output:
                print(result.output)
        else:
            print(result.friendly_error())
