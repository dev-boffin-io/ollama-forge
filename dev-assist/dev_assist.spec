# -*- mode: python ; coding: utf-8 -*-
"""
dev-assist PyInstaller spec
Build:  pyinstaller dev_assist.spec
Output: dist/dev-assist   (single binary)
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(SPECPATH)

def _find_venv_site_packages() -> list:
    """Return site-packages from .venv if it exists alongside the spec."""
    import glob
    venv = ROOT / ".venv"
    if not venv.is_dir():
        return []
    # e.g. .venv/lib/python3.13/site-packages
    matches = glob.glob(str(venv / "lib" / "python3*" / "site-packages"))
    return matches

def _opt(*pkgs_and_mods):
    """Include module only if its top-level package is installed."""
    result = []
    for pkg, mods in pkgs_and_mods:
        if importlib.util.find_spec(pkg):
            result.extend(mods)
    return result

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)] + _find_venv_site_packages(),
    binaries=[],
    datas=[
        (str(ROOT / "config" / "settings.json"), "config"),
        (str(ROOT / "public"),                   "public"),
        (str(ROOT / ".chainlit"),                ".chainlit"),
        (str(ROOT / "chainlit.md"),              "."),
        # web_chat.py is NOT imported — chainlit runs it as a script via subprocess.
        # It must land at the root of _MEIPASS so main.py can copy it to tmpdir.
        (str(ROOT / "web_chat.py"),              "."),
        # plugins dir — bundled as data so _try_plugin can find .py files at runtime
        (str(ROOT / "plugins"),                  "plugins"),
        # core/ and modules/ bundled as raw .py data so main.py can copy them
        # into the chainlit subprocess tmpdir — the subprocess cannot access PYZ.
        (str(ROOT / "core"),                     "core"),
        (str(ROOT / "modules"),                  "modules"),
    ],
    hiddenimports=[
        # ── First-party (always present) ──────────────────────────────────
        "core.ai",
        "core.banner",
        "core.config",
        "core.prompts",
        "core.rag_engine",
        "core.router",
        "core.session",
        "core.shell",
        "core.ollama_status",
        "core.vector_store",
        "modules.code_audit",
        "modules.shell_exec",
        "modules.cmd_helper",
        "modules.file_tool",
        "modules.git_helper",
        "modules.indexer",
        "modules.tunnel_helper",
        "plugins.makefile",
        "plugins.telegram",
        # ── Optional third-party (only if installed) ──────────────────────
        *_opt(
            ("rich",          ["rich", "rich.console", "rich.table", "rich.progress"]),
            ("prompt_toolkit",["prompt_toolkit", "prompt_toolkit.history",
                               "prompt_toolkit.auto_suggest", "prompt_toolkit.completion"]),
            ("pydantic",      ["pydantic"]),
            ("jinja2",        ["jinja2"]),
            ("ollama",        ["ollama"]),
        ),
    ],
    excludes=[
        "torch", "transformers", "tensorflow", "numpy",
        "chainlit",
        "pytest", "ruff", "black",
    ],
    hookspath=[str(ROOT / "build_hooks")],
    runtime_hooks=[str(ROOT / "build_hooks" / "runtime_hook_paths.py")],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="dev-assist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
