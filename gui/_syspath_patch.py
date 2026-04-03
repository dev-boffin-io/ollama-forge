"""
_syspath_patch.py — inject system site-packages into a PyInstaller frozen binary.

Strategy: ask the real system Python interpreter for its site-packages paths,
then inject them into sys.path. This is reliable across Linux, Windows, proot,
and Termux because we bypass guessing and let Python tell us directly.
"""
import sys
import os


def _get_system_python() -> "str | None":
    """Find the real system Python interpreter (not the frozen binary)."""
    import shutil

    ver = f"{sys.version_info.major}.{sys.version_info.minor}"

    if sys.platform == "win32":
        candidates = ["py", f"python{ver}", "python3", "python"]
    else:
        candidates = [f"python{ver}", "python3", "python"]

    for name in candidates:
        path = shutil.which(name)
        if path and os.path.realpath(path) != os.path.realpath(sys.executable):
            return path

    return None


def _inject_system_site_packages() -> None:
    """Add system/user site-packages to sys.path if running frozen."""
    if not getattr(sys, "frozen", False):
        return

    import subprocess

    py = _get_system_python()
    if not py:
        return

    try:
        result = subprocess.run(
            [py, "-c",
             "import sys, site\n"
             "paths = list(sys.path)\n"
             "try: paths += site.getsitepackages()\n"
             "except: pass\n"
             "try: paths.append(site.getusersitepackages())\n"
             "except: pass\n"
             "print('\\n'.join(p for p in paths if p))"
             ],
            capture_output=True, text=True, timeout=5
        )
        added = set(sys.path)
        for line in result.stdout.splitlines():
            p = line.strip()
            if p and os.path.isdir(p) and p not in added:
                sys.path.insert(0, p)
                added.add(p)
    except Exception:
        pass


_inject_system_site_packages()
