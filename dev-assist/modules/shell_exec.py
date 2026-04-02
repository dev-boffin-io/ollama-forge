"""
Shell Exec — Run arbitrary Linux commands directly from the dev-assist CLI.

When the user types a line that looks like a shell command (not a known
dev-assist command), this module runs it interactively in the current
terminal and streams output in real time.

Design decisions:
- Uses subprocess with shell=True so pipelines, redirects, builtins all work
- stdin is inherited (interactive commands like vim, less, htop work)
- cwd follows the session's tracked working directory
- 'cd <dir>' is handled specially to update the session cwd
- Output is streamed live (no capture), so large output scrolls naturally
- Timeout: None by default (let the user Ctrl+C)
"""

from __future__ import annotations

import os
import subprocess
import sys

# Per-process tracked cwd (updated by 'cd' commands)
_session_cwd: str = os.path.expanduser("~")
_prev_cwd: str = ""  # for 'cd -' support


def get_cwd() -> str:
    """Return the current tracked working directory."""
    return _session_cwd


def set_cwd(path: str) -> None:
    """Explicitly set the tracked working directory."""
    global _session_cwd
    _session_cwd = path


def run_shell_command(command: str) -> None:
    """
    Execute a raw shell command interactively.

    Handles:
    - 'cd <dir>'  → updates tracked cwd
    - 'cd'        → goes to $HOME
    - 'cd -'      → goes to previous dir (best-effort)
    - everything else → subprocess.run with shell=True, live I/O
    """
    global _session_cwd

    cmd = command.strip()

    if not cmd:
        return

    # ── Handle 'cd' specially ────────────────────────────────────────────────
    if cmd == "cd" or cmd.startswith("cd ") or cmd.startswith("cd\t"):
        _handle_cd(cmd)
        return

    # ── Run everything else via bash ─────────────────────────────────────────
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=_session_cwd,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            executable=_find_shell(),
        )
        # Non-zero exit: show exit code (like a real shell)
        if result.returncode != 0:
            # Only print if it's not a signal termination (Ctrl+C = 130)
            if result.returncode not in (130, -2):
                _eprint(f"[exit {result.returncode}]")
    except KeyboardInterrupt:
        # User pressed Ctrl+C — just swallow and continue
        print()
    except FileNotFoundError:
        _eprint(f"❌ Shell not found. Tried: {_find_shell()}")
    except Exception as exc:
        _eprint(f"❌ Shell error: {exc}")


def _handle_cd(cmd: str) -> None:
    """Handle the 'cd' builtin by updating _session_cwd."""
    global _session_cwd, _prev_cwd

    parts = cmd.split(None, 1)
    if len(parts) == 1:
        # bare 'cd' → $HOME
        target = os.path.expanduser("~")
    else:
        arg = parts[1].strip()
        if arg == "-":
            if _prev_cwd:
                target = _prev_cwd
                print(target)  # like real bash: prints prev dir
            else:
                _eprint("cd: OLDPWD not set")
                return
        else:
            target = os.path.expandvars(os.path.expanduser(arg))
            if not os.path.isabs(target):
                target = os.path.join(_session_cwd, target)
            target = os.path.normpath(target)

    if not os.path.isdir(target):
        arg_str = parts[1].strip() if len(parts) > 1 else "~"
        _eprint(f"cd: {arg_str}: No such file or directory")
        return

    _prev_cwd = _session_cwd
    _session_cwd = target


def _find_shell() -> str:
    """Find the best available shell."""
    for sh in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.isfile(sh):
            return sh
    # Termux / non-standard paths
    import shutil
    for name in ("bash", "sh"):
        found = shutil.which(name)
        if found:
            return found
    return "/bin/sh"


def _eprint(msg: str) -> None:
    """Print to stderr with optional Rich formatting."""
    try:
        from rich.console import Console
        Console(stderr=True).print(msg)
    except ImportError:
        print(msg, file=sys.stderr)


# Shell builtins that are never on PATH but must always be treated as commands
_SHELL_BUILTINS: frozenset[str] = frozenset({
    "cd", "pwd", "echo", "export", "unset", "source", ".",
    "alias", "unalias", "set", "unset", "exec", "eval",
    "history", "jobs", "fg", "bg", "wait", "umask", "ulimit",
    "pushd", "popd", "dirs",
})

# Common English words that happen to be binaries — don't run them as commands
_NATURAL_LANG_WORDS: frozenset[str] = frozenset({
    "find", "type", "sort", "test", "read", "help",
    "kill", "exit", "clear", "reset", "true", "false",
    "head", "tail", "date", "time", "which",
})


def looks_like_shell_command(text: str) -> bool:
    """
    Heuristic: should this input be treated as a shell command?

    Priority order:
    1. Starts with '!' → always shell
    2. Shell builtins (cd, pwd, echo, export …) → always shell
    3. Shell metacharacters (pipe, redirect, $VAR, ./) → always shell
    4. First word is executable on PATH AND not a natural-language trigger
    5. Otherwise → not a shell command
    """
    import shutil
    import re

    text = text.strip()

    if not text:
        return False

    # 1. Explicit escape
    if text.startswith("!"):
        return True

    first_word = text.split()[0].rstrip(";:()")

    # 2. Shell builtins (not on PATH but always commands)
    if first_word in _SHELL_BUILTINS:
        return True

    # 3. Shell metacharacters
    if re.search(r"(^\./|^\../|\||\s*>\s|\s*>>\s|\$[\w(]|\`)", text):
        return True

    # 4. Executable on PATH — but skip natural-language words
    if first_word not in _NATURAL_LANG_WORDS and shutil.which(first_word):
        return True

    return False
