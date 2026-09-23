"""
Tool registry — the actions an agent is allowed to take.

The toolset is a Python port of the OpenCode-style agent tools (MIT-licensed
`opencode`, https://github.com/sst/opencode, Copyright (c) 2025 opencode;
see core/toolimpl/editors.py and core/toolimpl/patch.py for the ported
file-edit and apply_patch internals): read/write/edit files, explore the
tree, search content, run shell commands, fetch and search the web, launch
sub-agents, keep a todo list, ask the user questions, apply patches, and
load skills.

Each tool is declared in the JSON-Schema shape both Ollama's native tool
calling and OpenAI-compatible APIs accept, so the same registry drives every
backend in core.providers.

Execution is deliberately separate from declaration: `TOOL_SCHEMAS` is what
we send to the model, `execute_tool()` is what actually runs. Tools marked
`destructive` route through an approval callback first, so the diff-preview
+ approve layer can hook in without touching the loop.
"""

from __future__ import annotations

import fnmatch
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from core.toolimpl import editors, html2md, store
from core.toolimpl import patch as patchlib

# Tools that modify the filesystem, run arbitrary code, or ask the user for
# permission to change things. The agent loop asks for approval before any of
# these. (question is interactive rather than destructive, so it is not gated.)
DESTRUCTIVE_TOOLS = frozenset({"write_file", "edit_file", "bash", "apply_patch"})

DEFAULT_READ_LIMIT = 2000
MAX_LINE_LENGTH = 2000
MAX_LINE_SUFFIX = f"... (line truncated to {MAX_LINE_LENGTH} chars)"
MAX_READ_BYTES = 50 * 1024
MAX_READ_BYTES_LABEL = f"{MAX_READ_BYTES // 1024} KB"
SAMPLE_BYTES = 4096
MAX_GLOB_RESULTS = 100
MAX_GREP_RESULTS = 100
BASH_TIMEOUT = 120
TEST_TIMEOUT = 600
WEB_TIMEOUT = 15
WEB_TOP_K = 6
WEB_FETCH_TIMEOUT = 30
WEB_FETCH_MAX_TIMEOUT = 120
WEB_FETCH_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Directories never worth walking into.
_SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", "bin",
})

_BINARY_EXTS = frozenset({
    ".zip", ".tar", ".gz", ".exe", ".dll", ".so", ".class", ".jar", ".war",
    ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods",
    ".odp", ".bin", ".dat", ".obj", ".o", ".a", ".lib", ".wasm", ".pyc",
    ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
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
                "Read a file or directory from the local filesystem. If the path does not "
                "exist, an error is returned.\n"
                "Usage:\n"
                "- The path parameter should be an absolute path.\n"
                "- By default, this tool returns up to 2000 lines from the start of the file.\n"
                "- The offset parameter is the line number to start from (1-indexed).\n"
                "- To read later sections, call this tool again with a larger offset.\n"
                "- Use the grep tool to find specific content in large files.\n"
                "- Contents are returned with each line prefixed by its line number as "
                "'<line>: <content>'. For directories, entries are returned one per line with "
                "a trailing '/' for subdirectories.\n"
                "- Any line longer than 2000 characters is truncated.\n"
                "- Call this tool in parallel when you know there are multiple files to read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The absolute path to the file or directory to read.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "The line number to start reading from (1-indexed).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "The maximum number of lines to read (defaults to 2000).",
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
            "description": "List files and subdirectories of a directory (non-recursive)",
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
                "Fast file pattern matching tool that works with any codebase size.\n"
                "- Supports glob patterns like '**/*.py' or 'src/**/*.ts'\n"
                "- Returns matching absolute file paths\n"
                "- Use this tool when you need to find files by name patterns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The glob pattern to match files against, e.g. '**/*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "The directory to search in. If not specified, the current working "
                            "directory will be used. Must be a valid directory path if provided."
                        ),
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
                "Fast content search tool that works with any codebase size.\n"
                "- Searches file contents using regular expressions\n"
                "- Supports full regex syntax (e.g. 'log.*Error', 'function\\s+\\w+')\n"
                "- Filter files by pattern with the include parameter (e.g. '*.py', '*.{ts,tsx}')\n"
                "- Returns file paths and line numbers with matching lines\n"
                "- Use this tool when you need to find files containing specific patterns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The regex pattern to search for in file contents.",
                    },
                    "path": {
                        "type": "string",
                        "description": "The directory to search in. Defaults to the current working directory.",
                    },
                    "include": {
                        "type": "string",
                        "description": 'File pattern to include in the search (e.g. "*.py", "*.{ts,tsx}").',
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
                "Writes a file to the local filesystem.\n"
                "- This tool will overwrite the existing file if there is one at the provided path.\n"
                "- If this is an existing file, you should read the file first.\n"
                "- ALWAYS prefer editing existing files. NEVER write new files unless explicitly "
                "required. Never proactively create documentation files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write, absolute or relative."},
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
                "Replace text in a file. old_string must appear EXACTLY ONCE in the file, but "
                "the matcher tolerates minor whitespace/indentation differences; still, read the "
                "file first so you can quote it as accurately as possible.\n"
                "- preserve the exact indentation (tabs/spaces) as it appears AFTER the line-number "
                "prefix in read_file output. Never include the prefix itself.\n"
                "- ALWAYS prefer editing existing files in the codebase.\n"
                "- The edit will fail if old_string is not found, or if it matches ambiguously. "
                "Provide more surrounding context to make the match unique, or use replace_all "
                "to change every occurrence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit."},
                    "old_string": {
                        "type": "string",
                        "description": "The text to replace; must not be empty for an existing file.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The text to replace it with (must be different from old_string).",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences of old_string (default false).",
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
                "Run a shell command and return its stdout/stderr. Use for builds, tests, git, "
                "and other tooling. Do NOT use it to read or edit files — use the file tools. "
                "This tool is for terminal operations like git, npm, docker, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run."},
                    "workdir": {
                        "type": "string",
                        "description": "Optional directory to run the command in (absolute or relative).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Optional timeout in seconds (default {BASH_TIMEOUT}).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": (
                "Detect the project's test framework (pytest, npm test, go test, cargo test, "
                "make test) and run the test suite. Returns a pass/fail summary plus the tail of "
                "the test output. Use this to verify your work after changing code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional directory or subproject to test. Defaults to the working directory.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web — performs real-time web searches and returns the top results "
                "(title, URL, snippet). Use for current events, API documentation, unfamiliar "
                "error messages, library versions. Falls back to a short unavailability message "
                "rather than failing.\n"
                f"The current year is {__import__('datetime').datetime.now().year}. Use this year "
                "when searching for recent information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise search query.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of search results to return (default 6, max 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetches content from a specified URL and returns it converted to the requested "
                "format (markdown by default).\n"
                "- Use when you need to retrieve and analyze web content.\n"
                "- The URL must be a fully-formed http(s) URL.\n"
                "- Format options: 'markdown' (default), 'text', or 'html'.\n"
                "- This tool is read-only and does not modify any files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to fetch content from."},
                    "format": {
                        "type": "string",
                        "enum": ["text", "markdown", "html"],
                        "description": "The format to return the content in (default: markdown).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds (max 120).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todowrite",
            "description": (
                "Create and maintain a structured task list for the current session. Tracks "
                "progress, organizes multi-step work.\n"
                "Use proactively when the task requires 3+ distinct steps. States: pending, "
                "in_progress, completed, cancelled. Keep exactly one 'in_progress' at a time. "
                "Mark completed only after the work is actually done and verified. Items should "
                "be specific and actionable.\n"
                "Call it with the full updated todo list each time (this tool replaces the whole list)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The updated todo list",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "What needs to be done."},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed", "cancelled"],
                                },
                                "priority": {
                                    "type": "string",
                                    "description": "Optional priority (low/medium/high).",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "question",
            "description": (
                "Use this tool when you need to ask the user questions during execution. This "
                "allows you to:\n"
                "1. Gather user preferences or requirements\n"
                "2. Clarify ambiguous instructions\n"
                "3. Get decisions on implementation choices as you work\n"
                "When 'custom' is enabled (default) a 'type your own answer' option is added. "
                "Answers are returned as arrays of labels."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "Questions to ask",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "The question to ask."},
                                "header": {
                                    "type": "string",
                                    "description": "Short label for the question (max ~30 chars).",
                                },
                                "multiple": {
                                    "type": "boolean",
                                    "description": "Allow selecting more than one answer (default false).",
                                },
                                "custom": {
                                    "type": "boolean",
                                    "description": "Allow a typed custom answer (default true).",
                                },
                                "options": {
                                    "type": "array",
                                    "description": "Answer options.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "1-5 word label."},
                                            "description": {"type": "string", "description": "Explanation of the choice."},
                                        },
                                        "required": ["label"],
                                    },
                                },
                            },
                            "required": ["question", "options"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task",
            "description": (
                "Launch a sub-agent to handle a complex, multistep task autonomously. You must "
                "specify subagent_type.\n"
                "When NOT to use: to read a file, use read_file/glob; to search class "
                "definitions, use grep; to search within 2-3 files, use read_file.\n"
                "The sub-agent's output is not visible to the user — you report a concise "
                "summary to the user. Pass a detailed, self-contained prompt; the sub-agent "
                "starts with a fresh context. The result includes a task_id you can reuse "
                "later to resume the same sub-agent session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "A short (3-5 words) description of the task.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The self-contained task for the sub-agent to perform.",
                    },
                    "subagent_type": {
                        "type": "string",
                        "description": (
                            "The type of specialized agent to use. dev-assist currently offers "
                            "one type: 'general' (the standard agent)."
                        ),
                    },
                    "task_id": {
                        "type": "string",
                        "description": (
                            "Set only if you mean to resume a previous task — the sub-agent "
                            "session continues instead of starting fresh."
                        ),
                    },
                },
                "required": ["description", "prompt", "subagent_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill",
            "description": (
                "Load a specialized skill when the task at hand matches one of the skills listed "
                "in the system prompt. Use this tool to inject the skill's instructions and "
                "resources into the conversation. The skill name must match a skill available in "
                "the project or user config."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the skill to load.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_patch",
            "description": (
                "Use apply_patch to edit files with a stripped-down, file-oriented diff format. "
                "The envelope is:\n"
                "*** Begin Patch\n"
                "[Add/Update/Delete File sections]\n"
                "*** End Patch\n\n"
                "Headers: '*** Add File: <path>' (each content line prefixed with '+'), "
                "'*** Delete File: <path>', '*** Update File: <path>' (optionally followed by "
                "'*** Move to: <path>'). Update chunks start with '@@ <context hint>' and contain "
                "lines prefixed with ' ' (context), '-' (removed) and '+' (added)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "patch_text": {
                        "type": "string",
                        "description": "The full patch text that describes all changes to be made.",
                    },
                },
                "required": ["patch_text"],
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


def _is_binary_file(filepath: str, sample: bytes) -> bool:
    """Port of opencode's read.ts binary detection: extension allowlist plus a
    printable-character ratio check on a sample of the file."""
    if os.path.splitext(filepath)[1].lower() in _BINARY_EXTS:
        return True
    if not sample:
        return False
    non_printable = 0
    for byte in sample:
        if byte == 0:
            return True
        if byte < 9 or (byte > 13 and byte < 32):
            non_printable += 1
    return non_printable / len(sample) > 0.3


def _read_sample(filepath: str, size: int) -> bytes:
    try:
        with open(filepath, "rb") as f:
            return f.read(size)
    except Exception:
        return b""


def _miss_suggestion(filepath: str) -> str:
    """When a path isn't found, offer nearby similarly-named files (read.ts)."""
    directory = os.path.dirname(filepath)
    base = os.path.basename(filepath).lower()
    try:
        items = os.listdir(directory)
    except Exception:
        return ""
    suggests = []
    for item in sorted(items):
        low = item.lower()
        if low == base:
            continue
        if base in low or low in base:
            suggests.append(os.path.join(directory, item))
            if len(suggests) >= 3:
                break
    if not suggests:
        return ""
    return "\n\nDid you mean one of these?\n" + "\n".join(suggests)


def _entry_rows(directory: str) -> list[str]:
    """Sorted directory entry display names, dirs with a trailing '/'."""
    try:
        names = sorted(os.listdir(directory))
    except Exception as exc:
        return [f"Error listing {directory}: {exc}"]
    rows = []
    for name in names:
        if name in _SKIP_DIRS:
            continue
        full = os.path.join(directory, name)
        rows.append(name + ("/" if os.path.isdir(full) else ""))
    return rows


def _include_match(basename: str, include: str) -> bool:
    """fnmatch with `{a,b}` brace support for include filters like '*.{ts,tsx}'."""
    m = re.match(r"^(.*)\{(.*)\}(.*)$", include)
    if m:
        pre, opts, post = m.group(1), m.group(2), m.group(3)
        return any(fnmatch.fnmatch(basename, pre + opt + post) for opt in opts.split(","))
    return fnmatch.fnmatch(basename, include)


def _trunc_output(text: str, max_chars: int = MAX_READ_BYTES) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + (
        f"\n\n(Output truncated at {max_chars} chars. Use offset / narrower queries "
        "to continue.)"
    )


# ─────────────────────────────────────────────────────────────────────
# Question handler — pluggable so CLI/TUI/web front-ends can render prompts
# ─────────────────────────────────────────────────────────────────────
def set_question_handler(handler: store.QuestionHandler | None) -> None:
    """Install a custom question renderer. The default is a plain stdin prompt,
    skipped automatically when stdin is not a TTY or auto mode is on."""
    store.set_question_handler(handler)


def set_question_auto_skip(enabled: bool) -> None:
    """When enabled, the question tool returns 'Unanswered' instead of prompting."""
    store.set_question_auto_skip(enabled)


def _default_question_handler(questions: list[dict]) -> list[list[str] | None]:
    if store.question_auto_skip() or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return [None] * len(questions)

    answers: list[list[str] | None] = []
    for q in questions:
        try:
            header = q.get("header") or "Answer required"
            print(f"\n{header}")
            print(q.get("question", ""))
            options = q.get("options") or []
            for i, opt in enumerate(options, 1):
                desc = opt.get("description") or ""
                print(f"  {i}. {opt['label']}" + (f" — {desc}" if desc else ""))
            custom = bool(q.get("custom", True))
            if custom:
                print(f"  {len(options) + 1}. (type your own answer)")

            mode = "pick numbers (comma-separated)" if q.get("multiple") else (
                "pick a number or type an answer"
            )
            raw = input(f"> {mode}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            answers.append(None)
            continue

        picked: list[str] = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token.isdigit():
                n = int(token)
                if 1 <= n <= len(options):
                    picked.append(options[n - 1]["label"])
                elif custom and n == len(options) + 1:
                    try:
                        typed = input("> your answer: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        typed = ""
                    if typed:
                        picked.append(typed)
            else:
                picked.append(token)
        answers.append(picked)
    return answers


# ─────────────────────────────────────────────────────────────────────
# Tool implementations — read/write/edit/search
# ─────────────────────────────────────────────────────────────────────
def _tool_read_file(args: dict, workdir: str) -> str:
    filepath = _resolve(args.get("path"), workdir)
    offset = max(1, int(args.get("offset") or 1))
    limit = int(args.get("limit") or DEFAULT_READ_LIMIT)
    if limit < 1:
        limit = DEFAULT_READ_LIMIT

    if not os.path.exists(filepath):
        return f"File not found: {filepath}" + _miss_suggestion(filepath)

    if os.path.isdir(filepath):
        items = _entry_rows(filepath)
        start = offset - 1
        sliced = items[start:start + limit]
        truncated = start + len(sliced) < len(items)
        lines = [
            f"<path>{filepath}</path>",
            "<type>directory</type>",
            "<entries>",
            *sliced,
            (
                f"\n(Showing {len(sliced)} of {len(items)} entries. Use 'offset' parameter to "
                f"read beyond entry {offset + len(sliced)})"
                if truncated
                else f"\n({len(items)} entries)"
            ),
            "</entries>",
        ]
        return "\n".join(lines)

    if not os.path.isfile(filepath):
        return f"File not found: {filepath}"

    sample = _read_sample(filepath, SAMPLE_BYTES)
    if _is_binary_file(filepath, sample):
        return f"Cannot read binary file: {filepath}"

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as exc:
        return f"Error reading {filepath}: {exc}"

    text = text.rstrip("\n")
    raw_lines = text.split("\n")
    total = len(raw_lines) if text else 0
    if total == 0 and text == "":
        raw_lines = [""]
        total = 0

    start = offset - 1
    if offset > 1 and offset > (total or 1):
        return f"Error: Offset {offset} is out of range for this file ({total} lines)"

    out_lines: list[str] = []
    used_bytes = 0
    cut = False
    more = False
    line_no = offset
    i = start

    if total == 0 and text == "":
        return (
            f"<path>{filepath}</path>\n<type>file</type>\n<content>\n"
            f"(End of file - total 0 lines)\n</content>"
        )

    i = start
    while i < len(raw_lines) and len(out_lines) < limit:
        line = raw_lines[i]
        rendered = line
        if len(rendered) > MAX_LINE_LENGTH:
            rendered = rendered[:MAX_LINE_LENGTH] + MAX_LINE_SUFFIX
        size = len(rendered.encode("utf-8", "replace")) + (1 if out_lines else 0)
        if used_bytes + size > MAX_READ_BYTES:
            cut = True
            more = True
            break
        out_lines.append(f"{line_no}: {rendered}")
        used_bytes += size
        line_no += 1
        i += 1

    if i < len(raw_lines):
        more = True

    last = offset + len(out_lines) - 1
    next_line = last + 1
    output = [f"<path>{filepath}</path>", "<type>file</type>", "<content>"]
    output.append("\n".join(out_lines))

    if cut:
        output.append(
            f"\n(Output capped at {MAX_READ_BYTES_LABEL}. Showing lines {offset}-{last}. "
            f"Use offset={next_line} to continue.)"
        )
    elif more:
        output.append(
            f"\n(Showing lines {offset}-{last} of {total}. Use offset={next_line} to continue.)"
        )
    else:
        output.append(f"\n(End of file - total {total} lines)")
    output.append("</content>")
    return "\n".join(output)


def _tool_list_dir(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    if not os.path.isdir(path):
        return f"Error: not a directory: {path}"
    rows = _entry_rows(path)
    return f"# {path}\n" + ("\n".join(rows) if rows else "(empty)")


def _tool_glob(args: dict, workdir: str) -> str:
    import glob as _glob

    pattern = args.get("pattern") or "*"
    root = _resolve(args.get("path"), workdir)
    if not os.path.isdir(root):
        return f"Error: glob path must be a directory: {root}"

    full_pattern = os.path.join(root, pattern)
    hits = []
    for full in _glob.glob(full_pattern, recursive=True):
        if os.path.isdir(full):
            continue
        hits.append(os.path.abspath(full))
        if len(hits) >= MAX_GLOB_RESULTS:
            break

    if not hits:
        return "No files found"
    parts = list(hits)
    if len(hits) >= MAX_GLOB_RESULTS:
        parts.append("")
        parts.append(
            f"(Results are truncated: showing first {MAX_GLOB_RESULTS} results. "
            "Consider using a more specific path or pattern.)"
        )
    return "\n".join(parts)


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

    matches: list[tuple[str, int, str]] = []  # (path, line, text)
    for full in _walk(root):
        if include and not _include_match(os.path.basename(full), include):
            continue
        if not _is_probably_text(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                for lineno, line in enumerate(f, 1):
                    if rx.search(line):
                        matches.append((os.path.abspath(full), lineno, line.rstrip("\n")[:200]))
                        if len(matches) >= MAX_GREP_RESULTS:
                            break
        except Exception:
            continue
        if len(matches) >= MAX_GREP_RESULTS:
            break

    if not matches:
        return "No files found"

    total = len(matches)
    truncated = total >= MAX_GREP_RESULTS
    out = [f"Found {total} matches" + (" (more matches available)" if truncated else "")]
    current = ""
    for path, lineno, text in matches:
        if path != current:
            if current:
                out.append("")
            current = path
            out.append(f"{path}:")
        out.append(f"  Line {lineno}: {text}")
    if truncated:
        out.append("")
        out.append("(Results truncated. Consider using a more specific path or pattern.)")
    return "\n".join(out)


def _tool_write_file(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    content = args.get("content")
    if content is None:
        return "Error: content is required."
    # Preserve an existing BOM, or keep one supplied in the new content.
    had_bom = False
    if os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                had_bom = f.read(3) == b"\xef\xbb\xbf"
        except Exception:
            had_bom = False
    _bom, body = editors.split_bom(content)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        existed = os.path.isfile(path)
        with open(path, "wb") as f:
            f.write(b"\xef\xbb\xbf" if (had_bom or _bom) else b"")
            f.write(body.encode("utf-8"))
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {path} ({len(body.splitlines())} lines)."
    except Exception as exc:
        return f"Error writing {path}: {exc}"


def _tool_edit_file(args: dict, workdir: str) -> str:
    path = _resolve(args.get("path"), workdir)
    old = args.get("old_string")
    new = args.get("new_string")
    replace_all = bool(args.get("replace_all"))

    if not old:
        return (
            "Error: old_string cannot be empty when editing an existing file. Provide the exact "
            "text to replace, or use write_file for an intentional full-file replacement."
        )
    if old == new:
        return "Error: No changes to apply: oldString and newString are identical."
    if not os.path.exists(path):
        return f"Error: File {path} not found"
    if os.path.isdir(path):
        return f"Error: Path is a directory, not a file: {path}"

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return f"Error reading {path}: {exc}"

    _bom, content_body = editors.split_bom(content)
    ending = editors.detect_line_ending(content_body)
    target_old = editors.convert_to_line_ending(editors.normalize_line_endings(old), ending)
    target_new = editors.convert_to_line_ending(editors.normalize_line_endings(new), ending)

    try:
        updated = editors.replace(content_body, target_old, target_new, replace_all=replace_all)
    except editors.EditError as exc:
        return f"Error: {exc}"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(editors.join_bom(updated, _bom))
    except Exception as exc:
        return f"Error writing {path}: {exc}"
    return "Edit applied successfully."


def _tool_bash(args: dict, workdir: str) -> str:
    command = args.get("command")
    if not command:
        return "Error: command is required."
    try:
        timeout = int(args.get("timeout") or BASH_TIMEOUT)
    except (TypeError, ValueError):
        timeout = BASH_TIMEOUT
    if timeout < 0:
        return f"Error: Invalid timeout value: {timeout}. Timeout must be a positive number."
    timeout = timeout if timeout > 0 else BASH_TIMEOUT

    cwd = _resolve(args.get("workdir"), workdir) if args.get("workdir") else workdir
    if not os.path.isdir(cwd):
        return f"Error: workdir is not a directory: {cwd}"

    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True,
            text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            f"Error: shell tool terminated command after exceeding timeout {timeout}s. If this "
            "command is expected to take longer and is not waiting for interactive input, retry "
            "with a larger timeout value in seconds."
        )
    except Exception as exc:
        return f"Error running command: {exc}"

    parts = [f"(exit {proc.returncode})"]
    if proc.stdout.strip():
        parts.append(proc.stdout.strip()[:MAX_READ_BYTES])
    if proc.stderr.strip():
        parts.append("stderr:\n" + proc.stderr.strip()[:8000])
    if not proc.stdout.strip() and not proc.stderr.strip():
        parts.append("(no output)")
    return "\n".join(parts)


def _detect_test_command(folder: str) -> tuple[list[str], str] | None:
    """Pick a runnable test command from the project's config files."""
    def _has(name: str, isdir: bool = False) -> bool:
        p = os.path.join(folder, name)
        return os.path.isdir(p) if isdir else os.path.isfile(p)

    if _has("package.json"):
        try:
            with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts") or {}
            if isinstance(scripts, dict) and scripts.get("test"):
                return (["npm", "test"], "npm test")
        except Exception:
            pass

    if _has("go.mod"):
        return (["go", "test", "./..."], "go test")
    if _has("Cargo.toml"):
        return (["cargo", "test"], "cargo test")

    py_markers = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini",
                  "conftest.py")
    if any(_has(m) for m in py_markers) or _has("tests", isdir=True):
        return ([sys.executable, "-m", "pytest", "-q"], "pytest")

    if _has("Makefile"):
        try:
            with open(os.path.join(folder, "Makefile"), encoding="utf-8",
                      errors="replace") as f:
                if re.search(r"(?m)^test\s*:", f.read()):
                    return (["make", "test"], "make test")
        except Exception:
            pass

    return None


def _run_tests_in(folder: str, cmd: list[str], label: str) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=folder, capture_output=True, text=True, timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Error: {label} timed out after {TEST_TIMEOUT}s."
    except Exception as exc:
        return f"Error running {label}: {exc}"

    tail = proc.stdout.strip()[-8000:]
    stderr = proc.stderr.strip()[-4000:]

    if label == "pytest" and proc.returncode != 0 and "No module named 'pytest'" in (
        proc.stdout + proc.stderr
    ):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover"],
                cwd=folder, capture_output=True, text=True, timeout=TEST_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"Error: unittest fallback timed out after {TEST_TIMEOUT}s."
        except Exception as exc:
            return f"Error running unittest: {exc}"
        label = "unittest"
        tail = proc.stdout.strip()[-8000:]
        stderr = proc.stderr.strip()[-4000:]

    if proc.returncode == 0:
        head = f"✅ {label} passed (exit 0)"
    else:
        head = f"❌ {label} failed (exit {proc.returncode})"

    parts = [f"{head}  [{' '.join(cmd)}]"]
    if tail:
        parts.append("output:\n" + tail)
    if stderr:
        parts.append("stderr:\n" + stderr)
    return "\n".join(parts)


def _tool_run_tests(args: dict, workdir: str) -> str:
    folder = _resolve(args.get("path"), workdir)
    if not os.path.isdir(folder):
        return f"Error: not a directory: {folder}"

    detected = _detect_test_command(folder)
    if detected is None:
        return (
            f"Error: no test framework detected in {folder}. Checked pytest.ini, "
            "pyproject.toml, setup.cfg, tox.ini, conftest.py, a tests/ directory, "
            "package.json, go.mod, Cargo.toml, and a Makefile with a test target. "
            "If tests exist anyway, run them yourself via bash."
        )
    cmd, label = detected
    return _run_tests_in(folder, cmd, label)


def _search_via_ddg(query: str, num: int = WEB_TOP_K) -> str:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) dev-assist/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return f"search unavailable: {exc}"

    titles = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', page, re.S)
    urls = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]*)"', page)
    snips = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', page, re.S)

    def _clean(s: str) -> str:
        s = re.sub(r"<[^>]+>", "", s)
        return html.unescape(re.sub(r"\s+", " ", s)).strip()

    take = min(len(titles), len(urls), num)
    if take == 0:
        return "search unavailable: no results parsed from the DuckDuckGo endpoint"

    rows = []
    for i in range(take):
        u = html.unescape(urls[i])
        parsed = urllib.parse.urlparse(u)
        qs = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qs:
            u = qs["uddg"][0]
        title = _clean(titles[i])
        snip = _clean(snips[i]) if i < len(snips) else ""
        rows.append(f"{i + 1}. {title}\n   {u}\n   {snip}" if snip
                    else f"{i + 1}. {title}\n   {u}")
    return f"# web results for {query!r}\n" + "\n".join(rows)


def _search_via_api(query: str, section: dict, num: int = WEB_TOP_K) -> str:
    key = section.get("api_key") or ""
    try:
        if section.get("url"):
            payload = json.dumps({"query": query}).encode()
            req = urllib.request.Request(
                section["url"], data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                },
            )
            with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
                data = json.loads(resp.read())
            results = data.get("results") or data.get("data") or []
            rows = []
            for i, r in enumerate(results[:num]):
                title = r.get("title") or r.get("name") or "?"
                link = (r.get("link") or r.get("url") or r.get("href") or "")
                snip = (r.get("snippet") or r.get("text") or "").strip()
                rows.append(f"{i + 1}. {title}\n   {link}\n   {snip}" if snip
                            else f"{i + 1}. {title}\n   {link}")
            if not rows:
                return "search unavailable: no results from configured API"
            return f"# web results for {query!r}\n" + "\n".join(rows)

        api_url = ("https://serpapi.com/search.json?" +
                   urllib.parse.urlencode({
                       "engine": section.get("engine") or "duckduckgo",
                       "q": query, "api_key": key,
                   }))
        with urllib.request.urlopen(api_url, timeout=WEB_TIMEOUT) as resp:
            data = json.loads(resp.read())
        organic = data.get("organic_results") or []
        rows = []
        for i, r in enumerate(organic[:num]):
            snip = (r.get("snippet") or "").strip()
            rows.append(f"{i + 1}. {r.get('title', '?')}\n   {r.get('link', '')}\n   {snip}"
                        if snip else f"{i + 1}. {r.get('title', '?')}\n   {r.get('link', '')}")
        if not rows:
            return "search unavailable: no results from the configured search API"
        return f"# web results for {query!r}\n" + "\n".join(rows)
    except Exception as exc:
        return f"search unavailable: {exc}"


def _tool_web_search(args: dict, workdir: str) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "Error: query is required."
    query = query[:500]

    try:
        num = max(1, min(int(args.get("num_results") or 0), 10))
    except (TypeError, ValueError):
        num = WEB_TOP_K

    section = None
    try:
        from core.ai import _load_config
        cfg = _load_config()
        if isinstance(cfg, dict):
            section = cfg.get("web_search")
        else:
            section = getattr(cfg, "web_search", None)
        if not isinstance(section, dict) or not section.get("api_key"):
            section = None
    except Exception:
        section = None

    if section:
        return _search_via_api(query, section, num)
    return _search_via_ddg(query, num)


# ─────────────────────────────────────────────────────────────────────
# Tool implementations — ported Web/planning/interactive tools
# ─────────────────────────────────────────────────────────────────────
def _tool_web_fetch(args: dict, workdir: str) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return "Error: url is required."
    if not url.startswith("http://") and not url.startswith("https://"):
        return "Error: URL must start with http:// or https://"

    fmt = args.get("format") or "markdown"
    if fmt not in ("text", "markdown", "html"):
        fmt = "markdown"

    try:
        timeout = max(1, min(int(args.get("timeout") or WEB_FETCH_TIMEOUT), WEB_FETCH_MAX_TIMEOUT))
    except (TypeError, ValueError):
        timeout = WEB_FETCH_TIMEOUT

    accept = {
        "markdown": "text/markdown;q=1.0, text/x-markdown;q=0.9, text/plain;q=0.8, text/html;q=0.7, */*;q=0.1",
        "text": "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1",
        "html": "text/html;q=1.0, application/xhtml+xml;q=0.9, text/plain;q=0.8, */*;q=0.1",
    }[fmt]

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
            ),
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "") or ""
            content_length = resp.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > WEB_FETCH_MAX_BYTES:
                return "Error: Response too large (exceeds 5MB limit)"
            body = resp.read(WEB_FETCH_MAX_BYTES + 1)
    except Exception as exc:
        return f"Error fetching {url}: {exc}"

    if len(body) > WEB_FETCH_MAX_BYTES:
        return "Error: Response too large (exceeds 5MB limit)"

    mime = content_type.split(";")[0].strip().lower()
    if mime.startswith("image/"):
        return f"Image fetched successfully ({url}, {mime})"

    try:
        content = body.decode("utf-8", errors="replace")
    except Exception:
        content = body.decode("latin-1", errors="replace")

    is_html = "html" in mime or content.lstrip().lower().startswith("<!doctype html") \
        or content.lstrip().lower().startswith("<html")
    if fmt == "html":
        out = content
    elif fmt == "text":
        out = html2md.html_to_text(content) if is_html else content
    else:
        out = html2md.html_to_markdown(content) if is_html else content
    if not out.strip():
        out = "(no content retrieved)"
    return _trunc_output(out.strip())


def _tool_todowrite(args: dict, workdir: str) -> str:
    todos = args.get("todos")
    if not isinstance(todos, list):
        return "Error: todos must be an array."

    cleaned = []
    for item in todos:
        if isinstance(item, str):
            entry = {"content": item, "status": "pending"}
        elif isinstance(item, dict):
            entry = {
                "content": str(item.get("content") or ""),
                "status": str(item.get("status") or "pending"),
                "priority": str(item["priority"]) if item.get("priority") else None,
            }
            if not entry["content"]:
                entry["content"] = str(item)[:80]
        else:
            continue
        if entry["status"] not in ("pending", "in_progress", "completed", "cancelled"):
            entry["status"] = "pending"
        if entry.get("priority") is None:
            entry.pop("priority", None)
        cleaned.append(entry)

    workdir_key = store.guess_workdir(workdir)
    store.set_todos(workdir_key, cleaned)
    remaining = sum(1 for t in cleaned if t["status"] != "completed")
    return json.dumps(cleaned, indent=2) + f"\n\n({remaining} todo(s) not yet completed)"


def _tool_question(args: dict, workdir: str) -> str:
    raw = args.get("questions")
    if not isinstance(raw, list) or not raw:
        return "Error: questions must be a non-empty array."

    questions = []
    for q in raw:
        if not isinstance(q, dict):
            continue
        options = []
        for opt in q.get("options") or []:
            if isinstance(opt, str):
                options.append({"label": opt, "description": ""})
            elif isinstance(opt, dict):
                options.append({
                    "label": str(opt.get("label") or opt.get("label") or "?"),
                    "description": str(opt.get("description") or ""),
                })
        if not options:
            continue
        questions.append({
            "question": str(q.get("question") or "?"),
            "header": str(q.get("header") or "") or None,
            "multiple": bool(q.get("multiple")),
            "custom": q.get("custom", True),
            "options": options,
        })
    if not questions:
        return "Error: questions must include valid options."

    handler = store.get_question_handler() or _default_question_handler
    answers = handler(questions)

    formatted = []
    for i, q in enumerate(questions):
        picked = answers[i] if i < len(answers) else None
        if not picked:
            formatted.append(f'"{q["question"]}"="Unanswered"')
        else:
            formatted.append(f'"{q["question"]}"="{", ".join(picked)}"')
    return "User has answered your questions: " + ", ".join(formatted) + \
        ". You can now continue with the user's answers in mind."


def _tool_task(args: dict, workdir: str) -> str:
    from core.toolimpl import subagent
    from core.toolimpl.store import ACTIVE_SUBAGENT_DEPTH

    description = (args.get("description") or "Sub-task").strip()
    prompt = args.get("prompt")
    if not prompt:
        return "Error: prompt is required."
    args.get("subagent_type") or "general"
    task_id = args.get("task_id") or ""

    if ACTIVE_SUBAGENT_DEPTH >= store.MAX_SUBAGENT_DEPTH:
        return (
            f"<task id=\"{task_id or 'task'}\" state=\"error\">\n"
            "<task_error>\n"
            f"Subagent depth limit reached ({store.MAX_SUBAGENT_DEPTH}). Increase "
            f"\"core.toolimpl.store.MAX_SUBAGENT_DEPTH\" to allow nested sub-agents.\n"
            "</task_error>\n</task>"
        )

    workdir_key = store.guess_workdir(workdir)
    if task_id and store.load_subagent_sessions(workdir_key, task_id) is not None:
        session_id = task_id
        resume_messages = store.load_subagent_sessions(workdir_key, task_id)
    else:
        session_id = store.new_task_id()
        resume_messages = None

    result = subagent.run_task(
        prompt, description, workdir,
        messages=resume_messages, for_id=session_id,
    )

    if result["messages"]:
        store.save_subagent_sessions(workdir_key, session_id, result["messages"])

    state, text = result["state"], result["text"]
    if state == "completed":
        return subagent._render_output(session_id, "completed", text,
                                       summary=f"Task completed: {description}")
    return subagent._render_output(session_id, "error", text,
                                   summary=f"Task failed: {description}")


def _tool_skill(args: dict, workdir: str) -> str:
    from core.toolimpl import skills

    name = (args.get("name") or "").strip()
    if not name:
        return "Error: name is required."
    info = skills.load_skill(name, workdir)
    if info is None:
        available = skills.list_skills(workdir)
        listing = ", ".join(available) if available else "(none configured)"
        return (
            f"Error: skill {name!r} not found. Available skills: {listing}. "
            "Skills live in <workdir>/skills/<name>/SKILL.md, "
            "<workdir>/.dev-assist/skills/<name>/SKILL.md, or "
            "~/.config/dev-assist/skills/<name>/SKILL.md."
        )

    files = "\n".join(f"<file>{f}</file>" for f in info["files"])
    return "\n".join([
        f'<skill_content name="{info["name"]}">',
        f"# Skill: {info['name']}",
        "",
        info["content"].strip(),
        "",
        f'Base directory for this skill: {info["dir"]}',
        "Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.",
        "Note: file list is sampled.",
        "",
        "<skill_files>",
        files if files else "(no extra files)",
        "</skill_files>",
        "</skill_content>",
    ])


def _tool_apply_patch(args: dict, workdir: str) -> str:
    patch_text = args.get("patch_text")
    if not patch_text:
        return "Error: patchText is required."

    workdir = store.guess_workdir(workdir)

    from core import change_tracker

    tracker = change_tracker.get_tracker()
    # Snapshot every file the patch touches up front so the whole run stays undoable,
    # mirroring how the agent snapshots write_file/edit_file before execution.
    if tracker is not None:
        for rel in patchlib.hunk_paths(patch_text):
            full = os.path.normpath(os.path.join(workdir, rel))
            tracker.snapshot(full)

    try:
        summary = patchlib.apply_patch(patch_text, workdir)
    except patchlib.PatchError as exc:
        return f"Error: {exc}"
    except Exception as exc:
        return f"Error: apply_patch failed: {exc}"

    if tracker is not None:
        for path in summary["added"] + summary["modified"]:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    tracker.record(path, f.read())
            except Exception:
                pass
        for path in summary["deleted"]:
            tracker.record(path, "")

    lines = []
    for path in summary["added"]:
        lines.append(f"A {os.path.relpath(path, workdir)}")
    for path in summary["modified"]:
        prefix = "M"
        if any(os.path.abspath(p) == os.path.abspath(path) for p in summary["deleted"]):
            prefix = "D"
        lines.append(f"{prefix} {os.path.relpath(path, workdir)}")
    for path in summary["deleted"]:
        if not any(os.path.abspath(p) == os.path.abspath(path) for p in summary["added"] + summary["modified"]):
            lines.append(f"D {os.path.relpath(path, workdir)}")

    if not lines:
        return "Error: No files were modified."
    return "Success. Updated the following files:\n" + "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────
_EXECUTORS: dict[str, Callable[[dict, str], str]] = {
    "read_file":   _tool_read_file,
    "list_dir":    _tool_list_dir,
    "glob":        _tool_glob,
    "grep":        _tool_grep,
    "write_file":  _tool_write_file,
    "edit_file":   _tool_edit_file,
    "bash":        _tool_bash,
    "run_tests":   _tool_run_tests,
    "web_search":  _tool_web_search,
    "web_fetch":   _tool_web_fetch,
    "todowrite":   _tool_todowrite,
    "question":    _tool_question,
    "task":        _tool_task,
    "skill":       _tool_skill,
    "apply_patch": _tool_apply_patch,
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
    if name == "run_tests":
        return f"run_tests: {args.get('path') or '.'}"
    if name == "web_search":
        return f"web_search: {(args.get('query') or '')[:60]}"
    if name == "web_fetch":
        return f"web_fetch: {(args.get('url') or '')[:80]}"
    if name == "todowrite":
        todos = args.get("todos")
        n = len(todos) if isinstance(todos, list) else 0
        return f"todowrite: {n} todo(s)"
    if name == "question":
        questions = args.get("questions")
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            return f"question: {str(questions[0].get('question', ''))[:60]}"
        return "question: (ask the user)"
    if name == "task":
        return f"task: {args.get('description', '')[:60]}"
    if name == "skill":
        return f"skill: {args.get('name', '')}"
    if name == "apply_patch":
        if "patch_text" in args and args["patch_text"]:
            for ln in str(args["patch_text"]).splitlines():
                if ln.startswith("*** "):
                    return f"apply_patch: {ln.strip()[:70]}"
        return f"apply_patch: {args}"
    return f"{name}: {args}"
