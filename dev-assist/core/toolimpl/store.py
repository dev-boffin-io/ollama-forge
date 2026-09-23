"""
Session state shared by the ported tools.

Each tool that needs durable state for the lifetime of a REPL keeps it here
instead of the registry (core/tools.py stays stateless and testable):

  - _todos:           per-workdir todowrite lists.
  - _subagent_memory: per-workdir {task_id: [messages]} so the `task` tool
                      can resume a prior sub-agent from its task_id.
  - question handler: a pluggable callback so the CLI/TUI/web front-end can
                      render questions; falls back to a plain stdin prompt.
  - auto mode:        when set, questions are skipped (returned as
                      "Unanswered") instead of interrupting an unattended run.
"""

from __future__ import annotations

from collections.abc import Callable

# todowrite: workdir -> list of {"content", "status", "priority"?}
_todos: dict[str, list[dict]] = {}

# task tool: workdir -> {task_id -> list[messages]} for resuming.
_subagent_memory: dict[str, dict[str, list[dict]]] = {}

# Counter for fresh task_ids.
_next_task_id: int = 0

# Depth of nested task tool calls; mirrors opencode's subagent depth guard.
ACTIVE_SUBAGENT_DEPTH = 0
MAX_SUBAGENT_DEPTH = 1

# question handler hook. Signature: handler(questions) -> list[list[str] | None]
#   questions: [{"question", "header"?, "options": [{"label","description"}],
#                "multiple"?, "custom"?}]  (option labels are str)
# Returns one list of chosen labels per question (empty list = none picked),
# or None for a whole question when the user skipped/unanswered it.
QuestionHandler = Callable[[list[dict]], list[list[str] | None]]

_question_handler: QuestionHandler | None = None

# When True the default question handler returns "unanswered" immediately
# (used by --auto/--yolo runs so questions never block headless execution).
_question_auto_skip = False


def set_todos(workdir: str, todos: list[dict]) -> list[dict]:
    """Replace the todo list for a working directory. Returns the stored list."""
    _todos[workdir] = todos
    return _todos[workdir]


def get_todos(workdir: str) -> list[dict]:
    return _todos.get(workdir, [])


def clear_todos(workdir: str) -> None:
    _todos.pop(workdir, None)


def new_task_id() -> str:
    global _next_task_id
    _next_task_id += 1
    return f"task_{_next_task_id}"


def save_subagent_sessions(workdir: str, task_id: str, messages: list[dict]) -> None:
    _subagent_memory.setdefault(workdir, {})[task_id] = messages


def load_subagent_sessions(workdir: str, task_id: str) -> list[dict] | None:
    return (_subagent_memory.get(workdir) or {}).get(task_id)


def set_question_handler(handler: QuestionHandler | None) -> None:
    """Install a custom question renderer (CLI/TUI/web). None resets to default."""
    global _question_handler
    _question_handler = handler


def get_question_handler() -> QuestionHandler | None:
    return _question_handler


def set_question_auto_skip(enabled: bool) -> None:
    global _question_auto_skip
    _question_auto_skip = enabled


def question_auto_skip() -> bool:
    return _question_auto_skip


def guess_workdir(workdir: str | None) -> str:
    import os

    return os.path.abspath(workdir or os.getcwd())
