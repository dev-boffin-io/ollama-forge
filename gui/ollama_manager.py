#!/usr/bin/env python3
"""
Ollama Manager — standalone window for managing the Ollama service,
models, auth, and the ollama binary itself.

Architecture:
  • All blocking work runs in daemon threads or QThreads
  • UI state changes only happen on the main thread via pyqtSignal
  • Auth, server, and install states are fully independent
"""
import _syspath_patch  # noqa: F401 — must be first, injects system site-packages into frozen binary
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import requests
from PyQt5.QtCore    import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Background workers
# ─────────────────────────────────────────────────────────────────────────────
class _ServerCheckWorker(QThread):
    result = pyqtSignal(bool)
    def run(self):
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=4)
            self.result.emit(r.status_code == 200)
        except Exception:
            self.result.emit(False)


class _SubprocWorker(QThread):
    line    = pyqtSignal(str)
    percent = pyqtSignal(int)
    done    = pyqtSignal(int)

    def __init__(self, cmd, env=None):
        super().__init__()
        self.cmd   = cmd
        self.env   = env
        self._proc = None

    def run(self):
        try:
            self._proc = subprocess.Popen(
                self.cmd, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
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

    def abort(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()


# ─────────────────────────────────────────────────────────────────────────────
#  Pure helpers (no Qt)
# ─────────────────────────────────────────────────────────────────────────────
def _safe_remove(path):
    try:
        os.remove(path)
    except Exception:
        pass


def _read_ollama_username():
    for path in [os.path.expanduser("~/.ollama/config"),
                 os.path.expanduser("~/.config/ollama/config")]:
        if os.path.exists(path):
            try:
                d = json.loads(open(path, encoding="utf-8").read())
                u = d.get("username") or d.get("user") or d.get("name")
                if u:
                    return str(u)
            except Exception:
                pass
    return None


def _parse_signin_username(line):
    for pat in [
        r"signed in as user\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"already signed in as\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"logged in as\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"welcome[,\s]+['\"]?([A-Za-z0-9_\-]+)['\"]?",
    ]:
        m = re.search(pat, line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _fmt_size(b):
    if not b:
        return "?"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


# ─────────────────────────────────────────────────────────────────────────────
#  Main window
# ─────────────────────────────────────────────────────────────────────────────
class OllamaManager(QMainWindow):
    # Signals (emitted from any thread, handled on main thread)
    _log          = pyqtSignal(str)
    _progress     = pyqtSignal(int)
    _prog_vis     = pyqtSignal(bool)
    _sig_server   = pyqtSignal(bool)
    _sig_auth     = pyqtSignal(bool, str)          # signed_in, username

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🦙 Ollama Manager")
        self.resize(1600, 900)

        self._server_running = False
        self._signed_in      = False
        self._current_model  = None
        self._active_worker  = None
        self._serve_proc     = None
        self._signin_proc    = None
        self._poll_tmr       = None

        self._build_ui()

        self._log        .connect(self._on_log)
        self._progress   .connect(self.bar.setValue)
        self._prog_vis   .connect(self.bar.setVisible)
        self._sig_server .connect(self._on_server)
        self._sig_auth   .connect(self._on_auth)

        # Independent startup checks — no shared state
        QTimer.singleShot(100, self._chk_server)
        QTimer.singleShot(150, self._chk_auth)

        # Periodic server heartbeat — detects external start/stop
        self._heartbeat_tmr = QTimer(self)
        self._heartbeat_tmr.timeout.connect(self._heartbeat)
        self._heartbeat_tmr.start(5000)   # every 5 s

    # ── UI build ─────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setSpacing(28)
        h.setContentsMargins(12, 12, 12, 12)
        h.addWidget(self._left_panel(), 0)
        h.addWidget(self._right_panel(), 1)
        self._theme()

    def _left_panel(self):
        w = QWidget()
        w.setFixedWidth(560)
        v = QVBoxLayout(w)
        v.setSpacing(14)

        # Server
        self.lbl_srv = QLabel("🟡 Ollama: Checking…")
        self.lbl_srv.setObjectName("srvLabel")
        self.lbl_srv.setAlignment(Qt.AlignCenter)
        self.lbl_srv.setWordWrap(True)
        v.addWidget(self.lbl_srv)

        self.btn_serve = QPushButton("▶ Start Ollama Serve")
        self.btn_serve.setMinimumHeight(88)
        self.btn_serve.clicked.connect(self._toggle_serve)
        v.addWidget(self.btn_serve)

        self.btn_refresh = QPushButton("↻ Refresh Model List")
        self.btn_refresh.setMinimumHeight(80)
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.clicked.connect(self._load_models)
        v.addWidget(self.btn_refresh)

        v.addWidget(self._sep())

        # Auth
        self.lbl_auth = QLabel("🔴 Not signed in to ollama.com")
        self.lbl_auth.setObjectName("authLabel")
        self.lbl_auth.setWordWrap(False)
        v.addWidget(self.lbl_auth)

        r = QHBoxLayout(); r.setSpacing(16)
        self.btn_signin  = QPushButton("🔑 Sign In")
        self.btn_signout = QPushButton("🚪 Sign Out")
        self.btn_signin .setMinimumHeight(80)
        self.btn_signout.setMinimumHeight(80)
        self.btn_signout.setEnabled(False)
        self.btn_signin .clicked.connect(self._do_signin)
        self.btn_signout.clicked.connect(self._do_signout)
        r.addWidget(self.btn_signin)
        r.addWidget(self.btn_signout)
        v.addLayout(r)

        v.addWidget(self._sep())

        # Pull — with model search / autocomplete
        r3 = QHBoxLayout(); r3.setSpacing(16)
        self.inp_pull = QLineEdit()
        self.inp_pull.setPlaceholderText("Search or type model name…")
        self.inp_pull.setMinimumHeight(80)

        _POPULAR = [
            "llama3.2",           "llama3.2:1b",        "llama3.2:3b",
            "llama3.1",           "llama3.1:8b",        "llama3.1:70b",
            "llama3",             "llama2",              "llama2:13b",
            "mistral",            "mistral:7b",          "mistral-nemo",
            "mixtral",            "mixtral:8x7b",
            "gemma3",             "gemma3:1b",           "gemma3:4b",
            "gemma3:12b",         "gemma3:27b",
            "gemma2",             "gemma2:2b",           "gemma2:9b",
            "gemma2:27b",
            "qwen2.5",            "qwen2.5:0.5b",        "qwen2.5:1.5b",
            "qwen2.5:3b",         "qwen2.5:7b",          "qwen2.5:14b",
            "qwen2.5:32b",        "qwen2.5:72b",
            "qwen2.5-coder",      "qwen2.5-coder:1.5b",  "qwen2.5-coder:7b",
            "qwen2.5-coder:14b",  "qwen2.5-coder:32b",
            "deepseek-r1",        "deepseek-r1:1.5b",    "deepseek-r1:7b",
            "deepseek-r1:8b",     "deepseek-r1:14b",     "deepseek-r1:32b",
            "deepseek-r1:70b",    "deepseek-r1:671b",
            "deepseek-coder-v2",  "deepseek-v3",
            "phi4",               "phi4-mini",            "phi3",
            "phi3:mini",          "phi3:medium",          "phi3.5",
            "codellama",          "codellama:7b",         "codellama:13b",
            "codellama:34b",      "codellama:70b",
            "starcoder2",         "starcoder2:3b",        "starcoder2:7b",
            "starcoder2:15b",
            "dolphin3",           "dolphin-mistral",      "dolphin-llama3",
            "nous-hermes2",       "openhermes",           "wizardlm2",
            "vicuna",             "orca-mini",            "solar",
            "command-r",          "command-r-plus",       "zephyr",
            "llava",              "llava:13b",            "llava:34b",
            "llava-llama3",       "moondream",            "bakllava",
            "nomic-embed-text",   "mxbai-embed-large",
            "all-minilm",         "snowflake-arctic-embed",
            "bge-m3",             "bge-large",
        ]
        from PyQt5.QtCore import QStringListModel
        from PyQt5.QtWidgets import QCompleter
        _mdl  = QStringListModel(_POPULAR, self)
        _comp = QCompleter(_mdl, self.inp_pull)
        _comp.setCaseSensitivity(Qt.CaseInsensitive)
        _comp.setFilterMode(Qt.MatchContains)
        _comp.setMaxVisibleItems(14)
        _comp.popup().setStyleSheet(
            "background:#161b22; color:#c9d1d9;"
            "border:1px solid #388bfd; border-radius:8px;"
            "font-size:26px; padding:6px;"
            "selection-background-color:#264f78;"
            "selection-color:white;"
        )
        self.inp_pull.setCompleter(_comp)

        self.btn_pull   = QPushButton("⬇ Pull")
        self.btn_cancel = QPushButton("⛔ Cancel")
        self.btn_pull  .setMinimumHeight(80)
        self.btn_cancel.setMinimumHeight(80)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setObjectName("cancelBtn")
        self.btn_pull  .clicked.connect(self._do_pull)
        self.btn_cancel.clicked.connect(self._cancel_worker)
        r3.addWidget(self.inp_pull, 3)
        r3.addWidget(self.btn_pull, 1)
        r3.addWidget(self.btn_cancel, 1)
        v.addLayout(r3)

        # Create
        r4 = QHBoxLayout(); r4.setSpacing(16)
        self.inp_create = QLineEdit()
        self.inp_create.setPlaceholderText("Model name (user/my-model)")
        self.inp_create.setMinimumHeight(80)
        self.inp_create.textChanged.connect(self._upd_create)
        self.btn_create = QPushButton("🛠 Create")
        self.btn_create.setMinimumHeight(80)
        self.btn_create.setEnabled(False)
        self.btn_create.clicked.connect(self._do_create)
        r4.addWidget(self.inp_create, 3)
        r4.addWidget(self.btn_create, 1)
        v.addLayout(r4)

        # Modelfile
        r5 = QHBoxLayout(); r5.setSpacing(16)
        self.lbl_mf = QLabel("No Modelfile selected")
        self.lbl_mf.setObjectName("smallLabel")
        self.lbl_mf.setWordWrap(True)
        self.btn_browse = QPushButton("📂 Browse")
        self.btn_browse.setMinimumHeight(72)
        self.btn_browse.clicked.connect(self._browse_mf)
        r5.addWidget(self.lbl_mf, 2)
        r5.addWidget(self.btn_browse, 1)
        v.addLayout(r5)

        lbl_mfc = QLabel("📄 Modelfile Content:")
        lbl_mfc.setObjectName("secLabel")
        v.addWidget(lbl_mfc)
        self.edit_mf = QTextEdit()
        self.edit_mf.setPlaceholderText(
            "# Example Modelfile\n"
            "FROM llama3.2\n"
            "PARAMETER temperature 0.8\n"
            "SYSTEM You are a helpful assistant."
        )
        self.edit_mf.textChanged.connect(self._upd_create)
        v.addWidget(self.edit_mf, 1)
        return w

    def _right_panel(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(16)

        lbl = QLabel("📋 Available Models")
        lbl.setObjectName("secLabel")
        v.addWidget(lbl, 0, Qt.AlignCenter)
        self.lst = QListWidget()
        self.lst.itemClicked.connect(self._on_model_click)
        v.addWidget(self.lst, 2)

        frm = QFrame(); frm.setObjectName("detFrm")
        fl  = QVBoxLayout(frm)
        lbl2 = QLabel("📄 Model Details"); lbl2.setObjectName("secLabel")
        fl.addWidget(lbl2, 0, Qt.AlignCenter)
        self.txt_det = QTextEdit()
        self.txt_det.setReadOnly(True)
        self.txt_det.setMinimumHeight(200)
        self.txt_det.setPlaceholderText("Select a model to see details…")
        fl.addWidget(self.txt_det)
        v.addWidget(frm, 1)

        lbl3 = QLabel("📜 Output Log"); lbl3.setObjectName("secLabel")
        v.addWidget(lbl3, 0, Qt.AlignCenter)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setObjectName("logArea")
        v.addWidget(self.txt_log, 2)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(False)
        v.addWidget(self.bar)

        r = QHBoxLayout(); r.setSpacing(20)
        self.lbl_sel  = QLabel("No model selected")
        self.btn_rm   = QPushButton("🗑 Remove")
        self.btn_push = QPushButton("⬆ Push to ollama.com")
        self.btn_rm  .setMinimumHeight(80)
        self.btn_push.setMinimumHeight(80)
        self.btn_rm  .setEnabled(False)
        self.btn_push.setEnabled(False)
        self.btn_rm  .clicked.connect(self._do_remove)
        self.btn_push.clicked.connect(self._do_push)
        r.addWidget(self.lbl_sel)
        r.addWidget(self.btn_rm)
        r.addWidget(self.btn_push)
        v.addLayout(r)
        return w

    @staticmethod
    def _sep():
        f = QFrame()
        f.setFrameShape(QFrame.HLine)
        f.setObjectName("sepLine")
        return f

    def _theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#0d1117; color:#c9d1d9; }
            QLabel { color:#c9d1d9; font-size:28px; }
            QLabel#srvLabel  { font-size:32px; font-weight:bold; padding:4px; }
            QLabel#authLabel { font-size:28px; font-weight:bold; padding:3px 2px; }
            QLabel#secLabel  { font-size:28px; font-weight:bold; }
            QLabel#smallLabel{ font-size:24px; color:#8b949e; }
            QPushButton {
                background:#21262d; color:#c9d1d9;
                border:1px solid #30363d; border-radius:7px;
                padding:5px 11px; font-size:26px;
            }
            QPushButton:hover   { background:#30363d; }
            QPushButton:pressed { background:#444c56; }
            QPushButton:disabled{ background:#161b22; color:#484f58; border-color:#21262d; }
            QPushButton#cancelBtn{
                background:#6e1717; color:#ffa198; border-color:#8b1a1a;
            }
            QPushButton#cancelBtn:hover{ background:#8b1a1a; }
            QLineEdit, QTextEdit {
                background:#161b22; color:#c9d1d9;
                border:1px solid #30363d; border-radius:7px;
                padding:5px 9px; font-size:26px;
            }
            QListWidget {
                background:#161b22; color:#c9d1d9;
                border:1px solid #30363d; border-radius:7px; font-size:26px;
            }
            QListWidget::item{ padding:9px 11px; border-bottom:1px solid #21262d; }
            QListWidget::item:selected{ background:#264f78; color:white; font-weight:bold; }
            QProgressBar{
                border:1px solid #30363d; border-radius:5px;
                background:#161b22; text-align:center;
                font-size:24px; min-height:20px;
            }
            QProgressBar::chunk{ background:#238636; border-radius:5px; }
            QFrame#detFrm{
                background:#161b22; border:1px solid #30363d;
                border-radius:9px; padding:5px;
            }
            QFrame#sepLine{ color:#30363d; }
            QTextEdit#logArea{
                background:#0d1117; color:#79c0ff;
                font-family:Consolas,Monaco,monospace; font-size:24px;
                border:1px solid #21262d;
            }
        """)

    # ── Signal handlers (main thread) ────────────────────────
    def _on_log(self, text):
        self.txt_log.append(text)
        self.txt_log.ensureCursorVisible()

    def _on_server(self, running):
        changed = running != self._server_running
        self._server_running = running
        if running:
            self.lbl_srv.setText("🟢 Ollama Server: Running")
            self.lbl_srv.setStyleSheet(
                "font-size:32px;font-weight:bold;color:#3fb950;padding:4px;")
            self.btn_serve.setText("⏹ Stop Ollama Serve")
            self.btn_refresh.setEnabled(True)
            if changed:
                self._load_models()
        else:
            self.lbl_srv.setText("🔴 Ollama Server: Stopped")
            self.lbl_srv.setStyleSheet(
                "font-size:32px;font-weight:bold;color:#f85149;padding:4px;")
            self.btn_serve.setText("▶ Start Ollama Serve")
            self.btn_refresh.setEnabled(False)
            if changed:
                self.lst.clear()
                self.txt_det.clear()
                self.lbl_sel.setText("No model selected")
                self.btn_rm.setEnabled(False)
                self.btn_push.setEnabled(False)
        self._upd_push()

    def _on_auth(self, signed_in, username):
        self._signed_in = signed_in
        self.btn_signin .setEnabled(not signed_in)
        self.btn_signout.setEnabled(signed_in)
        if signed_in:
            self.lbl_auth.setText(f"🟢 Signed in as {username}")
            self.lbl_auth.setStyleSheet(
                "QLabel#authLabel{font-size:28px;font-weight:bold;"
                "color:#3fb950;padding:3px 2px;}")
        else:
            self.lbl_auth.setText("🔴 Not signed in to ollama.com")
            self.lbl_auth.setStyleSheet(
                "QLabel#authLabel{font-size:28px;font-weight:bold;"
                "color:#f85149;padding:3px 2px;}")
        self._upd_push()

    # ── Startup checks ───────────────────────────────────────
    def _chk_server(self):
        w = _ServerCheckWorker(self)
        w.result.connect(lambda ok: self._sig_server.emit(ok))
        w.start()
        self._chk_w = w

    def _chk_auth(self):
        def _bg():
            u = _read_ollama_username()
            if u:
                self._log.emit(f"✅ Signed in as: {u}")
                self._sig_auth.emit(True, u)
            else:
                self._sig_auth.emit(False, "")
        threading.Thread(target=_bg, daemon=True).start()

    def _heartbeat(self):
        """Runs every 5 s on the main thread via QTimer.
        Fires a background check; only emits _sig_server when state
        actually changes — avoids redundant _load_models() calls."""
        def _bg():
            try:
                ok = requests.get(
                    "http://localhost:11434/api/tags", timeout=2
                ).status_code == 200
            except Exception:
                ok = False
            if ok != self._server_running:
                self._sig_server.emit(ok)
        threading.Thread(target=_bg, daemon=True).start()

    def _toggle_serve(self):
        if self._server_running:
            self._stop_serve()
        else:
            self._start_serve()

    def _stop_serve(self):
        def _bg():
            if self._serve_proc and self._serve_proc.poll() is None:
                self._serve_proc.terminate()
                try:
                    self._serve_proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    self._serve_proc.kill()
                self._serve_proc = None
            else:
                subprocess.run(["pkill", "-TERM", "-x", "ollama"],
                               capture_output=True)
                for _ in range(12):
                    time.sleep(0.4)
                    try:
                        requests.get("http://localhost:11434/api/tags", timeout=1)
                    except Exception:
                        break
            self._log.emit("🛑 Ollama stopped.")
            QTimer.singleShot(600, self._chk_server)
        threading.Thread(target=_bg, daemon=True).start()

    def _start_serve(self):
        def _bg():
            self._log.emit("▶ Starting ollama serve…")
            try:
                self._serve_proc = subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                self._log.emit("❌ 'ollama' not found in PATH.")
                return
            for _ in range(30):
                time.sleep(1)
                try:
                    if requests.get("http://localhost:11434/api/tags",
                                    timeout=2).status_code == 200:
                        self._log.emit("🟢 Server ready!")
                        QTimer.singleShot(0, self._chk_server)
                        return
                except Exception:
                    pass
            self._log.emit("⚠️ Server may be up but not responding — try Refresh.")
            QTimer.singleShot(0, self._chk_server)
        threading.Thread(target=_bg, daemon=True).start()

    # ── Models ───────────────────────────────────────────────
    def _load_models(self):
        try:
            models = requests.get(
                "http://localhost:11434/api/tags", timeout=5
            ).json().get("models", [])
        except Exception as e:
            self._log.emit(f"⚠️ Could not load models: {e}")
            return
        self.lst.clear()
        for m in models:
            name = m.get("name", "?")
            size = _fmt_size(m.get("size", 0))
            date = (m.get("modified_at") or "?")[:10]
            self.lst.addItem(f"{name}  |  {size}  |  {date}")
        self._log.emit(f"✅ {len(models)} models loaded.")

    def _on_model_click(self, item):
        self._current_model = item.text().split("  |  ")[0].strip()
        self.lbl_sel.setText(f"Selected: {self._current_model}")
        self.btn_rm.setEnabled(True)
        self._upd_push()
        self._show_details(self._current_model)

    def _show_details(self, name):
        try:
            r = requests.post("http://localhost:11434/api/show",
                              json={"name": name}, timeout=8)
            if r.status_code != 200:
                self.txt_det.setText("Failed to load details.")
                return
            info = r.json()
            mf   = info.get("modelfile", "")
            base = next(
                (ln.strip()[5:].strip() for ln in mf.splitlines()
                 if ln.strip().upper().startswith("FROM ")), "Unknown")
            det  = info.get("details", {})
            self.txt_det.setHtml(
                f"<b>Base:</b> {base}<br>"
                f"<b>Format:</b> {det.get('format','?')}<br>"
                f"<b>Family:</b> {det.get('family','?')}<br>"
                f"<b>Quantization:</b> {det.get('quantization_level','?')}<br>"
                f"<b>Size:</b> {_fmt_size(info.get('size',0))}<br>"
                f"<b>Digest:</b> {(info.get('digest','') or '')[:24]}…"
            )
        except Exception as e:
            self.txt_det.setText(f"Error: {e}")

    def _upd_push(self):
        if hasattr(self, "btn_push"):
            can = (bool(self._current_model)
                   and "/" in (self._current_model or "")
                   and self._signed_in)
            self.btn_push.setEnabled(can)

    # ── Pull / Push / Create / Remove ────────────────────────
    def _do_pull(self):
        model = self.inp_pull.text().strip()
        if not model:
            QMessageBox.warning(self, "Input", "Enter a model name.")
            return
        if not self._server_running:
            QMessageBox.warning(self, "Server", "Start the server first.")
            return
        self._log.emit(f"⬇ Pulling {model}…")
        self._run_worker(["ollama", "pull", model], on_success=self._load_models)

    def _do_push(self):
        if not self._current_model or "/" not in self._current_model:
            QMessageBox.warning(self, "Push",
                                "Model name must contain username/ prefix.")
            return
        self._log.emit(f"⬆ Pushing {self._current_model}…")
        self._run_worker(["ollama", "push", self._current_model])

    def _browse_mf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Modelfile", "",
            "Modelfile (Modelfile *);;All Files (*)")
        if not path:
            return
        try:
            self.edit_mf.setPlainText(open(path, encoding="utf-8").read())
            self.lbl_mf.setText(os.path.basename(path))
            self._log.emit(f"📂 Loaded: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _upd_create(self):
        ok = bool(self.inp_create.text().strip()
                  and self.edit_mf.toPlainText().strip())
        self.btn_create.setEnabled(ok)

    def _do_create(self):
        name    = self.inp_create.text().strip()
        content = self.edit_mf.toPlainText().strip()
        if not name or not content:
            return
        if not self._server_running:
            QMessageBox.warning(self, "Server", "Start the server first.")
            return
        fd, tmp = tempfile.mkstemp(suffix=".Modelfile", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            self._log.emit(f"🛠 Creating '{name}'…")
            self._run_worker(["ollama", "create", name, "-f", tmp],
                             on_success=self._load_models,
                             cleanup=lambda: _safe_remove(tmp))
        except Exception as e:
            _safe_remove(tmp)
            self._log.emit(f"❌ {e}")

    def _do_remove(self):
        if not self._current_model:
            return
        if QMessageBox.question(
            self, "Confirm",
            f"Permanently delete '{self._current_model}'?"
        ) != QMessageBox.Yes:
            return
        try:
            subprocess.run(["ollama", "rm", self._current_model],
                           check=True, capture_output=True)
            self._log.emit(f"🗑 Removed {self._current_model}")
            self._current_model = None
            self.lbl_sel.setText("No model selected")
            self.btn_rm.setEnabled(False)
            self.btn_push.setEnabled(False)
            self.txt_det.clear()
            self._load_models()
        except subprocess.CalledProcessError as e:
            self._log.emit(f"❌ Remove failed: {e.stderr.decode()}")

    # ── Worker helper ─────────────────────────────────────────
    def _run_worker(self, cmd, on_success=None, cleanup=None, env=None):
        w = _SubprocWorker(cmd, env=env)
        self._active_worker = w
        w.line   .connect(self._log)
        w.percent.connect(self._progress)

        def _done(rc):
            self._busy(False)
            if cleanup:
                cleanup()
            if rc == 0:
                self._log.emit("✅ Done.")
                if on_success:
                    on_success()
            else:
                self._log.emit(f"❌ Failed (exit {rc})")
            self._active_worker = None

        w.done.connect(_done)
        self._busy(True)
        w.start()

    def _busy(self, busy):
        self.btn_pull  .setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self._prog_vis.emit(busy)
        if busy:
            self._progress.emit(0)
        self._upd_push()

    def _cancel_worker(self):
        if self._active_worker and self._active_worker.isRunning():
            self._active_worker.abort()
            self._log.emit("⛔ Cancelled.")

    # ── Auth ──────────────────────────────────────────────────
    def _do_signin(self):
        u = _read_ollama_username()
        if u:
            self._log.emit(f"✅ Already signed in as: {u}")
            self._sig_auth.emit(True, u)
            return

        self.btn_signin.setEnabled(False)
        self._log.emit("🔑 Running ollama signin…")
        self._stop_poll()

        def _bg():
            try:
                proc = subprocess.Popen(
                    ["ollama", "signin"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1)
                self._signin_proc = proc
                url_done = False

                for raw in proc.stdout:
                    line = raw.strip()
                    if not line:
                        continue
                    self._log.emit(f"  {line}")
                    u = _parse_signin_username(line)
                    if u:
                        proc.terminate()
                        self._signin_proc = None
                        self._log.emit(f"✅ Signed in as: {u}")
                        self._sig_auth.emit(True, u)
                        return
                    url_m = re.search(r"https?://\S+", line)
                    if url_m and not url_done:
                        url = url_m.group(0).rstrip(".")
                        url_done = True
                        try:
                            subprocess.Popen(["xdg-open", url],
                                             stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                        except Exception:
                            self._log.emit(
                                "⚠️ Browser open failed — copy URL above.")
                        QTimer.singleShot(0, self._start_poll)

                proc.wait()
                self._signin_proc = None
                QTimer.singleShot(600, self._recheck_auth)
            except Exception as e:
                self._log.emit(f"❌ signin error: {e}")
                QTimer.singleShot(0, lambda: self.btn_signin.setEnabled(True))

        threading.Thread(target=_bg, daemon=True).start()

    def _start_poll(self):
        self._stop_poll()
        self._poll_n = 0
        self._poll_tmr = QTimer(self)
        self._poll_tmr.timeout.connect(self._poll_tick)
        self._poll_tmr.start(2000)

    def _stop_poll(self):
        if self._poll_tmr:
            self._poll_tmr.stop()
            self._poll_tmr = None

    def _poll_tick(self):
        self._poll_n = getattr(self, "_poll_n", 0) + 1
        if self._poll_n > 90:
            self._stop_poll()
            self._log.emit("⚠️ Auth poll timed out — try Sign In again.")
            self.btn_signin.setEnabled(True)
            return
        u = _read_ollama_username()
        if u:
            self._stop_poll()
            self._log.emit(f"✅ Signed in as: {u}")
            self._sig_auth.emit(True, u)

    def _recheck_auth(self):
        u = _read_ollama_username()
        if u:
            self._stop_poll()
            self._log.emit(f"✅ Signed in as: {u}")
            self._sig_auth.emit(True, u)
        else:
            self._log.emit("🔴 Not signed in — try again.")
            self._sig_auth.emit(False, "")

    def _do_signout(self):
        if QMessageBox.question(
            self, "Confirm", "Sign out from ollama.com?"
        ) != QMessageBox.Yes:
            return

        def _bg():
            ok = False
            try:
                ok = subprocess.run(
                    ["ollama", "signout"],
                    capture_output=True, text=True, timeout=8
                ).returncode == 0
            except Exception as e:
                self._log.emit(f"⚠️ signout: {e}")

            if not ok:
                for path in [os.path.expanduser("~/.ollama/config"),
                             os.path.expanduser("~/.config/ollama/config")]:
                    if os.path.exists(path):
                        try:
                            d = json.loads(open(path, encoding="utf-8").read())
                            for k in ("username", "user", "name", "token"):
                                d.pop(k, None)
                            open(path, "w", encoding="utf-8").write(
                                json.dumps(d))
                            ok = True
                        except Exception:
                            pass

            if ok:
                self._log.emit("🚪 Signed out.")
                self._sig_auth.emit(False, "")
            else:
                self._log.emit("❌ Could not sign out.")

        threading.Thread(target=_bg, daemon=True).start()

    # ── Ollama binary install / update ────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = OllamaManager()
    win.show()
    sys.exit(app.exec_())
