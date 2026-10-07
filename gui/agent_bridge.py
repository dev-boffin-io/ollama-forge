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
