import os
import sys

try:
    # Add dev-assist to path for source runs
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _DA = os.path.join(_ROOT, "dev-assist")
    if _DA not in sys.path:
        sys.path.insert(0, _DA)
    from core import agent as da_agent
except Exception as e:
    da_agent = None
    _IMPORT_ERROR = str(e)
else:
    _IMPORT_ERROR = None


def is_available() -> bool:
    return da_agent is not None


def get_error() -> str | None:
    return _IMPORT_ERROR


def run_agent(task: str, *, workdir: str, approver=None, on_event=None,
              max_steps: int = 24, agent: str = "build", extra_context: str = "") -> str:
    if da_agent is None:
        raise RuntimeError(f"dev-assist not available: {_IMPORT_ERROR}")
    return da_agent.run_agent(
        task,
        workdir=workdir,
        approver=approver,
        on_event=on_event,
        max_steps=max_steps,
        agent=agent,
        extra_context=extra_context,
    )


# ── Approvals ────────────────────────────────────────────────────────────────
def load_permission_rules() -> dict:
    """Permission rules from the dev-assist settings (same source as the CLI)."""
    try:
        from modules.agent_mode import _load_permission_rules
        return _load_permission_rules()
    except Exception:
        return {}


def make_approver(workdir: str, ask, *, auto_yes: bool = False,
                  rules: dict | None = None, always: set | None = None):
    """Build the CLI Approver, injecting `ask` as the decision callback.

    `ask(name, args) -> {"approve": bool, "always": bool, "reason": str|None}`.
    All other semantics (permission rules, `always`, `last_reason`) are the
    CLI's own `modules.agent_mode.Approver`.
    """
    from modules.agent_mode import make_approver as _make
    approver = _make(workdir, auto_yes=auto_yes, rules=rules, ask=ask)
    if always:
        approver.always.update(always)
    return approver


def _unified_diff(before: str, after: str, path: str) -> str:
    from modules.agent_mode import _unified_diff as _ud
    return _ud(before, after, path)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def pending_change(name: str, args: dict, workdir: str) -> dict:
    """Describe an approval request for the Qt dialog.

    Returns {"kind": "diff"|"command"|"files", "path": str,
             "diff": str, "command": str, "note": str}. Pure — never raises.
    """
    info = {"kind": "files", "path": "", "diff": "", "command": "", "note": ""}
    try:
        if name == "bash":
            info["kind"] = "command"
            info["command"] = str(args.get("command", ""))
            return info

        if name == "apply_patch":
            info["kind"] = "diff"
            info["path"] = "patch"
            patch_text = str(args.get("patch_text", ""))
            info["diff"] = "\n".join(
                ln for ln in patch_text.splitlines()
                if ln.startswith((" ", "+", "-", "@@"))
                and not ln.startswith(("+++", "---"))
            )
            if not info["diff"]:
                info["note"] = "apply_patch (no preview available)"
            return info

        path = str(args.get("path", ""))
        info["path"] = path
        full = path if os.path.isabs(path) else os.path.join(workdir, path)
        info["kind"] = "diff"

        if name == "edit_file":
            old, new = str(args.get("old_string", "")), str(args.get("new_string", ""))
            before = _read(full)
            if before is None or before.count(old) != 1:
                info["diff"] = (
                    "-" * 3 + f" intended change in {path} " + "-" * 3 + "\n"
                    + "".join(f"-{ln}\n" for ln in old.splitlines())
                    + "".join(f"+{ln}\n" for ln in new.splitlines())
                )
                info["note"] = "file missing or edit target not unique"
            else:
                info["diff"] = _unified_diff(before, before.replace(old, new, 1), path)
            return info

        if name == "write_file":
            new = str(args.get("content", ""))
            before = _read(full)
            if before is None:
                info["note"] = f"new file ({len(new.splitlines())} lines)"
                info["diff"] = "\n".join(f"+{ln}" for ln in new.splitlines())
            else:
                info["diff"] = _unified_diff(before, new, path)
            return info
    except Exception as exc:                       # pragma: no cover - defensive
        info["note"] = f"(preview failed: {exc})"
    return info


# ── Change tracker (undo) ────────────────────────────────────────────────────
def _tracker():
    from core.change_tracker import get_tracker
    return get_tracker()


def has_changes() -> bool:
    try:
        tracker = _tracker()
        return bool(tracker and tracker.has_changes())
    except Exception:
        return False


def diffstat() -> str:
    try:
        tracker = _tracker()
        return tracker.diffstat() if tracker else "No files changed."
    except Exception:
        return "No files changed."


def touched_paths() -> list:
    try:
        tracker = _tracker()
        return list(tracker.touched_paths()) if tracker else []
    except Exception:
        return []


def undo_changes() -> list:
    """Revert every file changed in the last run (same `tracker.undo()` as the CLI)."""
    tracker = _tracker()
    if tracker is None or not tracker.has_changes():
        return []
    return list(tracker.undo())
