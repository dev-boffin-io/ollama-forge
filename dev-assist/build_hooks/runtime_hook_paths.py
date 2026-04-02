"""
runtime_hook_paths.py — executes inside the frozen binary before any app code.

Problem:  core/config.py resolves CONFIG_PATH relative to __file__:
              CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.json"
          Inside a PyInstaller onefile bundle __file__ points into a temp
          extraction dir (sys._MEIPASS), which is correct — but only if the
          bundled assets land there too.  This hook ensures the env var
          DEV_ASSIST_CONFIG_DIR is set so config.py can prefer it.

Additionally, when running frozen we redirect writable config/vector-store
paths to ~/.config/dev-assist/ so the user's settings persist across runs
(the _MEIPASS dir is wiped on every exit).
"""

import os
import sys
from pathlib import Path

# ── Persistent user-data dir ──────────────────────────────────────────────────
_USER_DATA = Path.home() / ".config" / "dev-assist"
_USER_DATA.mkdir(parents=True, exist_ok=True)

# Tell app code where to find / write config
os.environ.setdefault("DEV_ASSIST_CONFIG_DIR",  str(_USER_DATA))
os.environ.setdefault("DEV_ASSIST_DATA_DIR",    str(_USER_DATA))

# ── Seed settings.json on first run ──────────────────────────────────────────
_user_settings = _USER_DATA / "settings.json"
if not _user_settings.exists() and hasattr(sys, "_MEIPASS"):
    _bundled = Path(sys._MEIPASS) / "config" / "settings.json"
    if _bundled.exists():
        import shutil
        shutil.copy2(_bundled, _user_settings)
