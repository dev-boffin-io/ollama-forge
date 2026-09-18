"""
Tool registry — the actions an agent is allowed to take.

Mirrors the toolset OpenCode-style agents expose: read/write/edit files,
explore the tree, search content, and run shell commands. Each tool is
declared in the JSON-Schema shape both Ollama's native tool calling and
OpenAI-compatible APIs (Groq) accept, so the same registry drives both
engines.

Execution is deliberately separate from declaration: `TOOL_SCHEMAS` is
what we send to the model, `execute_tool()` is what actually runs. Any
tool marked `destructive` routes through an approval callback first, so
Phase 2 (diff preview + approve) can hook in without touching the loop.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from typing import Any, Callable

# Tools that modify the filesystem or run arbitrary code. The agent loop
# asks for approval before executing any of these.
DESTRUCTIVE_TOOLS = frozenset({"write_file", "edit_file", "bash"})

MAX_READ_BYTES = 200_000
MAX_GREP_RESULTS = 60
MAX_GLOB_RESULTS = 200
BASH_TIMEOUT = 120

# Directories never worth walking into.
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", "bin",
})


# ─────────────────────────────────────────────────────────────────────
# Schemas sent to the model
# ─────────────────────────────────────────────────────────────────────
TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a file's contents. Returns the text with 1-based line "
                "numbers prefixed, which you need in order to call edit_file "
                "accurately. Always read a file before editing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, absolute or relative to the working directory.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Optional 1-based line to start reading from.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional max number of lines to read.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and subdirectories of a directory (non-recursive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path. Defaults to the working directory.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Find files by name pattern, recursively. Use this to locate "
                "files when you know roughly what they're called, "
                "e.g. '**/*.py' or '**/test_*.py'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search under. Defaults to the working directory.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents with a regular expression and return "
                "matching lines with their file paths and line numbers. Use "
                "this to find where something is defined or used."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search under. Defaults to the working directory.",
                    },
                    "include": {
                        "type": "string",
                        "description": "Optional filename glob to restrict the search, e.g. '*.py'.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a new file or overwrite an existing one with the given "
                "content. For changing part of an existing file, prefer "
                "edit_file — it is safer and shows a smaller diff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write."},
                    "content": {"type": "string", "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact string in a file with another string. "
                "old_string must appear EXACTLY once in the file and must "
                "match the raw file text (no line-number prefixes). Read the "
                "file first so you can quote it exactly. Include enough "
                "surrounding context to make the match unique."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit."},
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to replace; must occur exactly once.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement text. Use an empty string to delete.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command in the working directory and return its "
                "stdout/stderr. Use for builds, tests, git, and other tooling. "
                "Do not use it to read or edit files — use the file tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "timeout": {
                        "type": "integer",
                        "description": f"Optional timeout in seconds (default {BASH_TIMEOUT}).",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _resolve(path: str | None, workdir: str) -> str:
    if not path:
        return workdir
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(workdir, path)
    return os.path.normpath(path)


def resolve_path(path: str | None, workdir: str) -> str:
    """Public wrapper so callers (e.g. the agent loop, for change tracking)
    can resolve a tool's path argument the same way the tools themselves do."""
    return _resolve(path, workdir)


def _walk(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            yield os.path.join(dirpath, fn)


def _is_probably_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(2048)
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────
def _tool_read_file(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    if not os.path.isfile(path):
        return f"Error: no such file: {path}"
    if not _is_probably_text(path):
        return f"Error: {path} looks like a binary file; not reading it as text."

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    offset = max(1, int(args.get("offset") or 1))
    limit = args.get("limit")
    start = offset - 1
    end = start + int(limit) if limit else len(lines)
    chunk = lines[start:end]

    if not chunk:
        return f"(no lines in range; file has {len(lines)} lines)"

    out, size = [], 0
    for i, line in enumerate(chunk, start=offset):
        rendered = f"{i:6d}\t{line.rstrip(chr(10))}"
        size += len(rendered)
        if size > MAX_READ_BYTES:
            out.append(f"... (truncated at line {i}; file has {len(lines)} lines)")
            break
        out.append(rendered)

    header = f"# {path} (lines {offset}-{min(end, len(lines))} of {len(lines)})"
    return header + "\n" + "\n".join(out)


def _tool_list_dir(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    if not os.path.isdir(path):
        return f"Error: not a directory: {path}"
    try:
        entries = sorted(os.listdir(path))
    except Exception as exc:
        return f"Error listing {path}: {exc}"

    rows = []
    for name in entries:
        if name in _SKIP_DIRS:
            continue
        full = os.path.join(path, name)
        rows.append(f"{name}/" if os.path.isdir(full) else name)
    return f"# {path}\n" + ("\n".join(rows) if rows else "(empty)")


def _tool_glob(args: dict, workdir: str) -> str:
    pattern = args.get("pattern") or "*"
    root = _resolve(args.get("path"), workdir)
    if not os.path.isdir(root):
        return f"Error: not a directory: {root}"

    # fnmatch doesn't treat '**' specially, so normalise it to a plain
    # substring match against the repo-relative path.
    simple = pattern.replace("**/", "")
    hits = []
    for full in _walk(root):
        rel = os.path.relpath(full, root)
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(os.path.basename(full), simple):
            hits.append(rel)
            if len(hits) >= MAX_GLOB_RESULTS:
                break

    if not hits:
        return f"No files matching {pattern!r} under {root}"
    return f"# {len(hits)} match(es) for {pattern!r}\n" + "\n".join(sorted(hits))


def _tool_grep(args: dict, workdir: str) -> str:
    pattern = args.get("pattern")
    if not pattern:
        return "Error: pattern is required."
    root = _resolve(args.get("path"), workdir)
    include = args.get("include")

    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"Error: invalid regex {pattern!r}: {exc}"

    hits = []
    for full in _walk(root):
        if include and not fnmatch.fnmatch(os.path.basename(full), include):
            continue
        if not _is_probably_text(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if rx.search(line):
                        rel = os.path.relpath(full, root)
                        hits.append(f"{rel}:{lineno}: {line.rstrip()[:200]}")
                        if len(hits) >= MAX_GREP_RESULTS:
                            break
        except Exception:
            continue
        if len(hits) >= MAX_GREP_RESULTS:
            break

    if not hits:
        return f"No matches for {pattern!r} under {root}"
    capped = " (capped)" if len(hits) >= MAX_GREP_RESULTS else ""
    return f"# {len(hits)} match(es){capped}\n" + "\n".join(hits)


def _tool_write_file(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    content = args.get("content")
    if content is None:
        return "Error: content is required."
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        existed = os.path.isfile(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(content.splitlines())} lines)."
    except Exception as exc:
        return f"Error writing {path}: {exc}"


def _tool_edit_file(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    old = args.get("old_string")
    new = args.get("new_string")
    if old is None or new is None:
        return "Error: old_string and new_string are both required."
    if not os.path.isfile(path):
        return f"Error: no such file: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    count = content.count(old)
    if count == 0:
        return (
            f"Error: old_string not found in {path}. It must match the raw file "
            f"text exactly — check whitespace, and do not include the line-number "
            f"prefixes that read_file adds."
        )
    if count > 1:
        return (
            f"Error: old_string appears {count} times in {path}; it must be unique. "
            f"Include more surrounding context to disambiguate."
        )

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.replace(old, new, 1))
    except Exception as exc:
        return f"Error writing {path}: {exc}"
    return f"Edited {path}."


def _tool_bash(args: dict, workdir: str) -> str:
    command = args.get("command")
    if not command:
        return "Error: command is required."
    timeout = int(args.get("timeout") or BASH_TIMEOUT)
    try:
        proc = subprocess.run(
            command, shell=True, cwd=workdir, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s."
    except Exception as exc:
        return f"Error running command: {exc}"

    parts = [f"(exit {proc.returncode})"]
    if proc.stdout.strip():
        parts.append(proc.stdout.strip()[:MAX_READ_BYTES])
    if proc.stderr.strip():
        parts.append("stderr:\n" + proc.stderr.strip()[:8000])
    return "\n".join(parts)


_EXECUTORS: dict[str, Callable[[dict, str], str]] = {
    "read_file":  _tool_read_file,
    "list_dir":   _tool_list_dir,
    "glob":       _tool_glob,
    "grep":       _tool_grep,
    "write_file": _tool_write_file,
    "edit_file":  _tool_edit_file,
    "bash":       _tool_bash,
}


def execute_tool(name: str, args: dict[str, Any], workdir: str) -> str:
    """Run a tool by name. Always returns a string for the model to read."""
    fn = _EXECUTORS.get(name)
    if fn is None:
        return f"Error: unknown tool {name!r}. Available: {', '.join(sorted(_EXECUTORS))}"
    if not isinstance(args, dict):
        return f"Error: arguments for {name} must be an object."
    try:
        return fn(args, workdir)
    except Exception as exc:
        return f"Error in {name}: {exc}"


def describe_call(name: str, args: dict) -> str:
    """One-line human-readable summary of a pending tool call."""
    if name == "bash":
        return f"bash: {args.get('command', '')}"
    if name in ("read_file", "write_file", "edit_file"):
        return f"{name}: {args.get('path', '')}"
    if name == "glob":
        return f"glob: {args.get('pattern', '')}"
    if name == "grep":
        return f"grep: {args.get('pattern', '')}"
    if name == "list_dir":
        return f"list_dir: {args.get('path', '.')}"
    return f"{name}: {args}"
