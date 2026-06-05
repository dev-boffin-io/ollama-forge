"""
ollama_manager/workers.py
QThread workers — server check, subprocess streaming, manage (install/upgrade/uninstall).
All PyQt6.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from .helpers import OLLAMA_PATHS


# ─────────────────────────────────────────────────────────────────────────────
class ServerCheckWorker(QThread):
    result = pyqtSignal(bool)

    def run(self) -> None:
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=4)
            self.result.emit(r.status_code == 200)
        except Exception:
            self.result.emit(False)


# ─────────────────────────────────────────────────────────────────────────────
class SubprocWorker(QThread):
    line    = pyqtSignal(str)
    percent = pyqtSignal(int)
    done    = pyqtSignal(int)

    def __init__(self, cmd: list[str], env: dict | None = None) -> None:
        super().__init__()
        self.cmd   = cmd
        self.env   = env
        self._proc = None

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for raw in self._proc.stdout:
                t = raw.rstrip()
                if t:
                    self.line.emit(t)
                    m = re.search(r"(\d+)%", t)
                    if m:
                        self.percent.emit(int(m.group(1)))
            self.done.emit(self._proc.wait())
        except Exception as e:
            self.line.emit(f"❌ {e}")
            self.done.emit(1)

    def abort(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


# ─────────────────────────────────────────────────────────────────────────────
class ManageWorker(QThread):
    """Install / upgrade / uninstall / version-check Ollama."""

    line        = pyqtSignal(str)
    finished_ok = pyqtSignal()

    _GITHUB_API   = "https://api.github.com/repos/ollama/ollama/releases/latest"
    _FALLBACK_API = "https://api.github.com/repos/ollama/ollama/releases"
    _INSTALL_CMD  = "curl -fsSL https://ollama.com/install.sh | sh"

    def __init__(
        self,
        command: str,
        ollama_bin: str = "ollama",
        sudo_password: str | None = None,
    ) -> None:
        super().__init__()
        self.command        = command
        self._ollama_bin    = ollama_bin
        self._cancelled     = False
        self._sudo_password = sudo_password

    def stop(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            getattr(self, f"_cmd_{self.command}")()
        except Exception as exc:
            self.line.emit(f"Error: {exc}")
        self.finished_ok.emit()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _emit(self, msg: str) -> None:
        self.line.emit(msg)

    def _sudo(self) -> list[str]:
        return [] if os.geteuid() == 0 else ["sudo", "-A"]

    def _make_sudo_env(self) -> tuple[dict, str | None]:
        """Build env with SUDO_ASKPASS pointing to a temp script."""
        env = os.environ.copy()
        if os.geteuid() == 0 or not self._sudo_password:
            return env, None
        import shlex as _shlex
        fd, tmp = tempfile.mkstemp(suffix=".sh", prefix=".ollama_askpass_")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' {_shlex.quote(self._sudo_password)}\n"
                )
            os.chmod(tmp, 0o700)
            env["SUDO_ASKPASS"] = tmp
        except Exception as e:
            self._emit(f"⚠️ askpass setup failed: {e}")
            try:
                os.remove(tmp)
            except Exception:
                pass
            return env, None
        return env, tmp

    def _stream_shell(self, cmd: str) -> None:
        env, askpass_tmp = self._make_sudo_env()
        try:
            if os.geteuid() != 0 and self._sudo_password:
                proc = subprocess.Popen(
                    ["sudo", "-kS", "sh", "-c", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                proc.stdin.write(self._sudo_password + "\n")
                proc.stdin.close()
            else:
                proc = subprocess.Popen(
                    ["sh", "-c", cmd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
            for ln in proc.stdout:
                if self._cancelled:
                    proc.terminate()
                    return
                self._emit(ln.rstrip())
            proc.wait()
        finally:
            if askpass_tmp:
                try:
                    os.remove(askpass_tmp)
                except Exception:
                    pass

    def _current_ver(self) -> str | None:
        try:
            r = subprocess.run(
                [self._ollama_bin, "--version"],
                capture_output=True,
                text=True,
            )
            m = re.search(r"(\d+\.\d+\.\d+(?:[-\w\.]+)?)", r.stdout + r.stderr)
            return m.group(1) if m else None
        except FileNotFoundError:
            return None

    def _latest_ver(self) -> str | None:
        hdrs = {"Accept": "application/vnd.github+json"}
        for url in (self._GITHUB_API, self._FALLBACK_API):
            try:
                r = requests.get(url, headers=hdrs, timeout=8)
                if r.status_code == 200:
                    data = r.json()
                    tag = (
                        data["tag_name"]
                        if isinstance(data, dict)
                        else data[0]["tag_name"]
                    )
                    return tag.lstrip("v")
            except Exception:
                pass
        return None

    # ── commands ──────────────────────────────────────────────────────────────

    def _cmd_update(self) -> None:
        cur = self._current_ver()
        lat = self._latest_ver()
        self._emit(f"Current version: {cur or 'not installed'}")
        self._emit(f"Latest version : {lat or 'unknown (network error)'}")
        if cur and lat:
            from packaging import version as _v
            if _v.parse(cur) < _v.parse(lat):
                self._emit("Update available — use Upgrade Ollama.")
            else:
                self._emit("Already up to date.")

    def _cmd_install(self) -> None:
        if self._current_ver():
            self._emit("Ollama already installed.")
            return
        self._emit("Installing via official install.sh...")
        self._stream_shell(self._INSTALL_CMD)

    def _cmd_upgrade(self) -> None:
        cur = self._current_ver()
        lat = self._latest_ver()
        if not cur:
            self._emit("Not installed, installing...")
            self._stream_shell(self._INSTALL_CMD)
            return
        if not lat:
            self._emit("Cannot reach GitHub. Aborted.")
            return
        from packaging import version as _v
        if _v.parse(cur) >= _v.parse(lat):
            self._emit(f"Already latest ({cur}).")
            return
        self._emit(f"Upgrading {cur} to {lat}...")
        self._stream_shell(self._INSTALL_CMD)

    def _cmd_uninstall(self) -> None:
        cur = self._current_ver()
        if not cur:
            self._emit("Ollama is not installed.")
            return
        env, askpass_tmp = self._make_sudo_env()
        prefix = self._sudo()
        try:
            r = subprocess.run(
                ["systemctl", "is-active", "--quiet", "ollama"],
                capture_output=True,
                env=env,
            )
            if r.returncode == 0:
                subprocess.run(prefix + ["systemctl", "stop", "ollama"], env=env)
                subprocess.run(prefix + ["systemctl", "disable", "ollama"], env=env)
                self._emit("Stopped ollama.service")
            existing = [p for p in OLLAMA_PATHS if os.path.exists(p)]
            if existing:
                subprocess.run(prefix + ["rm", "-rf"] + existing, env=env)
                self._emit(f"Removed: {', '.join(existing)}")
            if any("systemd" in p for p in existing):
                subprocess.run(
                    prefix + ["systemctl", "daemon-reload"],
                    capture_output=True,
                    env=env,
                )
            self._emit("Ollama removed.")
        finally:
            if askpass_tmp:
                try:
                    os.remove(askpass_tmp)
                except Exception:
                    pass
