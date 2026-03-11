#!/usr/bin/env python3
"""
OLLAMA GUI — refactored main window.
Clean architecture:
  ollama_client.py  — API
  database.py       — SQLite
  rag_engine.py     — FAISS RAG (no LangChain)
  workers.py        — QThread workers
  crew_dialogs.py   — Crew config UI
  main.py           — This file: GUI only
"""
import base64
import copy
import glob
import json
import os
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import Qt, QMutex, QMutexLocker, QTimer
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from database import DB_CLASS
from ollama_client import OllamaClient
from workers import DirectChatWorker, CrewChatWorker, RAGBuildWorker
from crew_dialogs import CrewConfigDialog, CREW_TEMPLATES


# Fallback embedding model names shown when Ollama has no embed models installed
_EMBED_FALLBACKS = [
    "nomic-embed-text:latest",
    "mxbai-embed-large:latest",
]


# ────────────────────────────────────────────────────────────────────
class OllamaGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("OLLAMA • Local AI")
        self.resize(1800, 900)

        self.db          = DB_CLASS()
        self.db_mutex    = QMutex()
        self._client     = OllamaClient()

        self.current_conv_id    = None
        self.thread             = None
        self.dark               = True
        self.crew_mode          = False
        self.current_crew_id    = None
        self.current_crew_name  = None
        self.current_crew_cfg   = self.db.get_default_crew_config() or []
        self.models: list[dict] = []
        self.last_prompt        = ""

        self.attached_path   = None
        self.attached_b64    = None

        # Ollama server state
        self._server_running = False
        self._server_proc    = None   # subprocess.Popen handle when we start it
        self._server_poll_busy = False  # prevent overlapping poll threads

        # RAG — lazy-loaded
        self._rag: "RAGIndex | None" = None   # noqa: F821

        self._init_ui()
        self._load_models()
        self._refresh_conversations()
        self._refresh_crews()
        self._update_crew_btn()
        self._update_attach_btn()
        self._check_server_state()   # set initial server button + all UI states
        # Restore RAG button states if persisted index exists
        if self._rag_has_data():
            self.clear_rag_btn.setEnabled(self._server_running)
            self.rm_doc_btn.setEnabled(self._server_running)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_server)
        self._poll_timer.start(5000)

    # ================================================================ #
    #  UI BUILD                                                          #
    # ================================================================ #
    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        root.addWidget(self._build_left_panel(), 0)
        root.addLayout(self._build_right_panel(), 1)

        self._apply_theme()

    # ---- Left panel -------------------------------------------- #
    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(480)
        w.setMaximumWidth(680)
        w.setFixedWidth(560)
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.setContentsMargins(10, 14, 10, 14)

        # ── Chats ──
        v.addWidget(QLabel("<b>💬 Chats</b>"))
        self.new_chat_btn = QPushButton("➕ New Chat")
        self.new_chat_btn.setMinimumHeight(64)
        self.new_chat_btn.clicked.connect(self._new_chat)
        v.addWidget(self.new_chat_btn)

        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("🔍 Search…")
        self.chat_search.setClearButtonEnabled(True)
        self.chat_search.textChanged.connect(self._filter_convs)
        v.addWidget(self.chat_search)

        self.conv_list = QListWidget()
        self.conv_list.itemClicked.connect(self._load_conversation)
        self.conv_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._conv_menu)
        v.addWidget(self.conv_list, 2)

        # ── RAG ──
        v.addWidget(QLabel("<b>📚 Knowledge (RAG)</b>"))

        rag_add_btns = QHBoxLayout()
        rag_add_btns.setSpacing(6)
        self.rag_file_btn = QPushButton("📄 Files")
        self.rag_file_btn.setMinimumHeight(64)
        self.rag_file_btn.clicked.connect(self._add_rag_files)
        self.rag_folder_btn = QPushButton("📁 Folder")
        self.rag_folder_btn.setMinimumHeight(64)
        self.rag_folder_btn.clicked.connect(self._add_rag_folder)
        rag_add_btns.addWidget(self.rag_file_btn)
        rag_add_btns.addWidget(self.rag_folder_btn)
        v.addLayout(rag_add_btns)

        self.embed_box = QComboBox()
        self.embed_box.addItems(_EMBED_FALLBACKS)
        self.embed_box.setMinimumHeight(60)
        v.addWidget(self.embed_box)

        self.rag_progress = QProgressBar()
        self.rag_progress.setVisible(False)
        v.addWidget(self.rag_progress)

        self.rag_stop_btn = QPushButton("⛔ Stop Indexing")
        self.rag_stop_btn.setMinimumHeight(56)
        self.rag_stop_btn.setVisible(False)
        self.rag_stop_btn.setStyleSheet(
            "QPushButton{background:#8b0000;color:white;font-weight:bold;"
            "font-size:26px;border-radius:6px;padding:6px;}"
            "QPushButton:hover{background:#6b0000;}"
        )
        self.rag_stop_btn.clicked.connect(self._stop_rag_indexing)
        v.addWidget(self.rag_stop_btn)

        rag_btns = QHBoxLayout()
        rag_btns.setSpacing(6)
        self.clear_rag_btn = QPushButton("🗑 Clear All")
        self.clear_rag_btn.setMinimumHeight(60)
        self.clear_rag_btn.setEnabled(False)
        self.clear_rag_btn.clicked.connect(self._clear_rag)
        self.rm_doc_btn = QPushButton("✂ Remove Doc")
        self.rm_doc_btn.setMinimumHeight(60)
        self.rm_doc_btn.setEnabled(False)
        self.rm_doc_btn.clicked.connect(self._remove_rag_doc)
        rag_btns.addWidget(self.clear_rag_btn)
        rag_btns.addWidget(self.rm_doc_btn)
        v.addLayout(rag_btns)

        # ── Crews ──
        v.addWidget(QLabel("<b>⚙️ Crews</b>"))
        crew_btns = QHBoxLayout()
        crew_btns.setSpacing(6)
        self.new_crew_btn = QPushButton("➕ New")
        self.new_crew_btn.setMinimumHeight(60)
        self.new_crew_btn.clicked.connect(self._create_crew)
        self.tmpl_btn = QPushButton("📑 Template")
        self.tmpl_btn.setMinimumHeight(60)
        self.tmpl_btn.clicked.connect(self._load_template)
        crew_btns.addWidget(self.new_crew_btn)
        crew_btns.addWidget(self.tmpl_btn)
        v.addLayout(crew_btns)

        self.crew_list = QListWidget()
        self.crew_list.itemClicked.connect(self._select_crew)
        self.crew_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.crew_list.customContextMenuRequested.connect(self._crew_menu)
        v.addWidget(self.crew_list, 2)

        self.mgr_btn = QPushButton("🦙 Model Manager")
        self.mgr_btn.setMinimumHeight(70)
        self.mgr_btn.clicked.connect(self._open_manager)
        self.mgr_btn.setStyleSheet(
            "background:#1a5f1a;color:white;font-weight:bold;"
            "padding:8px;border-radius:8px;font-size:28px;"
        )
        v.addWidget(self.mgr_btn)

        return w

    # ---- Right panel -------------------------------------------- #
    def _build_right_panel(self) -> QHBoxLayout:
        v = QVBoxLayout()

        # Top bar
        top = QHBoxLayout()
        top.addWidget(QLabel("Model:"))
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(500)
        self.model_box.setMinimumHeight(60)
        self.model_box.currentIndexChanged.connect(self._update_attach_btn)
        top.addWidget(self.model_box)
        top.addStretch()

        self.mode_btn = QPushButton("⚡ Crew Mode: OFF")
        self.mode_btn.setMinimumHeight(60)
        self.mode_btn.clicked.connect(self._toggle_crew_mode)
        top.addWidget(self.mode_btn)

        self.crew_btn = QPushButton("📋 No Crew")
        self.crew_btn.setObjectName("crewBtn")
        self.crew_btn.setMinimumHeight(60)
        self.crew_btn.clicked.connect(self._open_current_crew)
        top.addWidget(self.crew_btn)

        self.theme_btn = QPushButton("🌙")
        self.theme_btn.setFixedWidth(80)
        self.theme_btn.setMinimumHeight(60)
        self.theme_btn.clicked.connect(self._toggle_theme)
        top.addWidget(self.theme_btn)

        self.server_btn = QPushButton("🟢 Server: ON")
        self.server_btn.setObjectName("srvBtn")
        self.server_btn.setMinimumHeight(60)
        self.server_btn.setMinimumWidth(220)
        self.server_btn.clicked.connect(self._toggle_server)
        top.addWidget(self.server_btn)
        v.addLayout(top)

        # Chat display — QPlainTextEdit is faster than QTextEdit for plain text
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setFont(QFont("DejaVu Sans", 32))
        v.addWidget(self.chat, 1)

        # Input row
        inp_row = QHBoxLayout()
        self.attach_btn = QPushButton("📎")
        self.attach_btn.setFixedWidth(80)
        self.attach_btn.setMinimumHeight(80)
        self.attach_btn.clicked.connect(self._attach_image)
        inp_row.addWidget(self.attach_btn)

        self.input = QTextEdit()
        self.input.setFixedHeight(180)
        self.input.setFont(QFont("DejaVu Sans", 30))
        self.input.setPlaceholderText("Type your message… (Ctrl+Enter to send)")
        inp_row.addWidget(self.input, 1)
        v.addLayout(inp_row)

        # Action buttons
        act = QHBoxLayout()
        self.send_btn = QPushButton("Send")
        self.send_btn.setMinimumHeight(70)
        self.send_btn.clicked.connect(self._send)
        act.addWidget(self.send_btn)

        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setMinimumHeight(70)
        self.stop_btn.clicked.connect(self._stop_or_reload)
        self.stop_btn.setEnabled(False)
        act.addWidget(self.stop_btn)

        clr_btn = QPushButton("Clear")
        clr_btn.setMinimumHeight(70)
        clr_btn.clicked.connect(self.chat.clear)
        act.addWidget(clr_btn)

        exp_btn = QPushButton("Export")
        exp_btn.setMinimumHeight(70)
        exp_btn.clicked.connect(self._export_chat)
        act.addWidget(exp_btn)
        v.addLayout(act)

        return v

    # ================================================================ #
    #  THEME                                                             #
    # ================================================================ #
    # Shared font sizes — change here to affect both themes
    _FONT_UI   = 28   # buttons, labels, combos, list items
    _FONT_CHAT = 32   # chat display
    _FONT_INPUT= 30   # message input

    def _apply_theme(self):
        base = f"""
            QLabel       {{ font-size: {self._FONT_UI}px; }}
            QPushButton  {{ font-size: {self._FONT_UI}px; padding: 10px 14px;
                           border: none; border-radius: 8px; }}
            QPushButton:disabled {{ opacity: 0.45; }}
            QComboBox    {{ font-size: {self._FONT_UI}px; padding: 8px 12px;
                           border-radius: 8px; }}
            QLineEdit    {{ font-size: {self._FONT_UI}px; padding: 8px 12px;
                           border-radius: 8px; }}
            QListWidget  {{ font-size: {self._FONT_UI}px; border-radius: 8px; }}
            QListWidget::item {{ padding: 16px 10px; border-radius: 6px;
                                margin: 2px 4px; }}
            QPlainTextEdit, QTextEdit {{ font-size: {self._FONT_CHAT}px;
                                        border-radius: 8px; }}
            QProgressBar {{ border-radius: 6px; min-height: 20px; }}
        """
        if self.dark:
            self.setStyleSheet(base + """
                QMainWindow, QWidget { background: #121212; color: #e0e0e0; }
                QPlainTextEdit, QTextEdit { background: #1e1e1e; color: #f0f0f0;
                    border: none; }
                QListWidget { background: #181818; color: #ddd; border: none;
                    padding: 4px; }
                QListWidget::item:hover    { background: #2a2a2a; }
                QListWidget::item:selected { background: #1f6feb; color: white;
                    font-weight: bold; }
                QPushButton  { background: #2d2d2d; color: #e0e0e0; }
                QPushButton:hover { background: #444; }
                QPushButton:disabled { background: #1e1e1e; color: #555; }
                QComboBox, QLineEdit { background: #222; color: #e0e0e0;
                    border: 1px solid #444; }
                QComboBox::drop-down { border: none; }
                QProgressBar { background: #222; }
                QProgressBar::chunk { background: #2d8; }
                QScrollBar:vertical { background: #1a1a1a; width: 8px; }
                QScrollBar::handle:vertical { background: #444; border-radius: 4px; }
                QPushButton#srvBtn  { background:#1a7f3c; color:white; font-weight:bold; }
                QPushButton#srvBtn:hover { background:#145f2e; }
                QPushButton#srvBtn[running="false"] { background:#8b0000; }
                QPushButton#srvBtn[running="false"]:hover { background:#6b0000; }
                QPushButton#stopBtn { background:#393; color:white; font-weight:bold; }
                QPushButton#stopBtn:hover { background:#2a7a2a; }
                QPushButton#stopBtn[active="true"] { background:#c33; }
                QPushButton#stopBtn[active="true"]:hover { background:#a02020; }
                QPushButton#crewBtn { background:#2d2d2d; color:#e0e0e0; }
                QPushButton#crewBtn[active="true"] { background:#1a7f3c; color:white; font-weight:bold; }
                        """)
        else:
            self.setStyleSheet(base + f"""
                QMainWindow, QWidget {{ background: #f0f2f5; color: #1a1a2e; }}
                QPlainTextEdit, QTextEdit {{ background: #ffffff; color: #1a1a2e;
                    border: 1px solid #c8cdd4; }}
                QListWidget {{ background: #ffffff; color: #1a1a2e;
                    border: 1px solid #d0d5dd; padding: 4px; }}
                QListWidget::item:hover    {{ background: #e8f0fe; }}
                QListWidget::item:selected {{ background: #1a73e8; color: white;
                    font-weight: bold; }}
                QPushButton {{ background: #1a73e8; color: white; }}
                QPushButton:hover {{ background: #1557b0; }}
                QPushButton:disabled {{ background: #c8d8f0; color: #888; }}
                QComboBox, QLineEdit {{ background: #ffffff; color: #1a1a2e;
                    border: 1px solid #c8cdd4; }}
                QComboBox::drop-down {{ border: none; }}
                QProgressBar {{ background: #e0e7ef; }}
                QProgressBar::chunk {{ background: #1a73e8; }}
                QScrollBar:vertical {{ background: #e8eaf0; width: 8px; }}
                QScrollBar::handle:vertical {{ background: #aab; border-radius: 4px; }}
                QPushButton#srvBtn  {{ background:#1a7f3c; color:white; font-weight:bold; }}
                QPushButton#srvBtn:hover {{ background:#145f2e; }}
                QPushButton#srvBtn[running="false"] {{ background:#8b0000; color:white; }}
                QPushButton#srvBtn[running="false"]:hover {{ background:#6b0000; }}
                QPushButton#stopBtn {{ background:#2e7d32; color:white; font-weight:bold; }}
                QPushButton#stopBtn:hover {{ background:#1b5e20; }}
                QPushButton#stopBtn[active="true"] {{ background:#c62828; }}
                QPushButton#stopBtn[active="true"]:hover {{ background:#b71c1c; }}
                QPushButton#crewBtn {{ background:#1a73e8; color:white; }}
                QPushButton#crewBtn[active="true"] {{ background:#1a7f3c; color:white; font-weight:bold; }}
                        """)

    def _toggle_theme(self):
        self.dark = not self.dark
        self._apply_theme()
        self.chat.setFont(QFont("DejaVu Sans", self._FONT_CHAT))
        self.input.setFont(QFont("DejaVu Sans", self._FONT_INPUT))

    # ================================================================ #
    #  MODELS                                                            #
    # ================================================================ #
    def _load_models(self):
        self.models = []
        self.model_box.clear()
        self.embed_box.clear()
        try:
            all_models = self._client.list_models()

            # Chat models — exclude embedding-only models
            chat_models = [m for m in all_models if not m["embed"]]
            embed_models = [m for m in all_models if m["embed"]]

            self.models = chat_models
            for m in chat_models:
                self.model_box.addItem(m["name"])

            # Embedding dropdown — Ollama embed models first, then fallbacks
            if embed_models:
                for m in embed_models:
                    self.embed_box.addItem(m["name"])
            else:
                # Ollama has no embed models installed — show fallbacks
                self.embed_box.addItems(_EMBED_FALLBACKS)
                self._log(
                    "ℹ️ No Ollama embedding models found. "
                    "Pull one (e.g. nomic-embed-text) or use sentence-transformers."
                )

            if not chat_models:
                self.model_box.addItem("llama3.2:latest")
                self._log("⚠️ No chat models found. Pull a model first.")

        except Exception as e:
            self.model_box.addItem("llama3.2:latest")
            self.embed_box.addItems(_EMBED_FALLBACKS)
            # Show friendly message, not raw HTTP traceback
            err = str(e)
            if "Connection refused" in err or "Max retries" in err or "ConnectionError" in err:
                msg = "⚠️ Ollama server is not running. Press [🔴 Server: OFF] to start it."
            elif "timeout" in err.lower():
                msg = "⚠️ Ollama is not responding (timeout). Server may be starting up."
            else:
                msg = f"⚠️ Ollama not reachable — {err[:120]}"
            self._log(msg)

    def _current_model_vision(self) -> bool:
        name = self.model_box.currentText()
        # Check from loaded model list first
        for m in self.models:
            if m["name"] == name:
                return m.get("vision", False)
        # Keyword fallback if model not in list
        lo = name.lower()
        return any(k in lo for k in (
            "llava", "bakllava", "vision", "moondream", "phi3-v", "minicpm-v"
        ))

    def _update_attach_btn(self):
        is_vision = self._current_model_vision()
        self.attach_btn.setEnabled(is_vision)
        self.attach_btn.setToolTip(
            "Attach image (vision model)" if is_vision
            else "Vision not supported by selected model"
        )

    # ================================================================ #
    #  CONVERSATIONS                                                     #
    # ================================================================ #
    def _filter_convs(self):
        q = self.chat_search.text().strip().lower()
        self.conv_list.clear()
        for c in self.db.list_conversations():
            title = (c["title"] or f"Chat {c['id']}").lower()
            if q and q not in title:
                continue
            prefix = "📌 " if c["pinned"] else ""
            item = QListWidgetItem(prefix + (c["title"] or f"Chat {c['id']}"))
            item.setData(Qt.UserRole, c["id"])
            self.conv_list.addItem(item)

    def _refresh_conversations(self):
        self._filter_convs()

    def _new_chat(self):
        self.current_conv_id = None
        self.last_prompt = ""
        self.chat.clear()
        self._update_stop_btn(False)

    def _load_conversation(self, item):
        self.current_conv_id = item.data(Qt.UserRole)
        self.chat.clear()
        with QMutexLocker(self.db_mutex):
            msgs = self.db.get_messages(self.current_conv_id)
        for m in msgs:
            who = "🧑 YOU" if m["role"] == "user" else "🤖 AI"
            self._log(f"{who}:\n{m['content']}\n")
        if msgs and msgs[-1]["role"] == "user":
            self.last_prompt = msgs[-1]["content"]
        self._update_stop_btn(False)

    def _conv_menu(self, pos):
        item = self.conv_list.itemAt(pos)
        if not item:
            return
        cid = item.data(Qt.UserRole)
        menu = QMenu()
        r_act = menu.addAction("✏ Rename")
        p_act = menu.addAction("📌 Pin/Unpin")
        d_act = menu.addAction("🗑 Delete")
        action = menu.exec_(self.conv_list.mapToGlobal(pos))
        if action == r_act:
            t, ok = QInputDialog.getText(self, "Rename", "New title:")
            if ok and t.strip():
                self.db.rename_conversation(cid, t.strip())
                self._refresh_conversations()
        elif action == p_act:
            self.db.toggle_pin(cid)
            self._refresh_conversations()
        elif action == d_act:
            self.db.delete_conversation(cid)
            self._new_chat()
            self._refresh_conversations()

    def _export_chat(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", "chat.md", "Markdown (*.md);;Text (*.txt)"
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.chat.toPlainText())

    # ================================================================ #
    #  RAG                                                               #
    # ================================================================ #
    def _rag_index(self):
        """Lazy-load RAGIndex."""
        if self._rag is None:
            from rag_engine import RAGIndex
            self._rag = RAGIndex(embed_model=self.embed_box.currentText())
        return self._rag

    def _rag_has_data(self) -> bool:
        """Return True if a persisted RAG index exists on disk."""
        import os
        from rag_engine import _PERSIST
        meta = os.path.join(_PERSIST, "meta.json")
        idx  = os.path.join(_PERSIST, "index.faiss")
        if not (os.path.exists(meta) and os.path.exists(idx)):
            return False
        try:
            import json
            with open(meta, encoding="utf-8") as f:
                chunks = json.load(f)
            return len(chunks) > 0
        except Exception:
            return False

    def _add_rag_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Documents", "",
            "Documents (*.pdf *.txt *.md *.docx *.html *.htm)"
        )
        if files:
            self._start_rag_build(files)

    def _add_rag_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if not folder:
            return
        paths = glob.glob(os.path.join(folder, "**/*.*"), recursive=True)
        supported = {".pdf", ".txt", ".md", ".docx", ".html", ".htm"}
        paths = [p for p in paths if os.path.splitext(p)[1].lower() in supported]
        if not paths:
            self._log("⚠️ No supported files found in folder.")
            return
        self._start_rag_build(paths)

    def _start_rag_build(self, paths: list):
        self._set_rag_ui_busy(True)
        self.rag_progress.setValue(0)
        self._log(f"🔄 Indexing {len(paths)} file(s)…")

        self._rag_worker = RAGBuildWorker(paths, self.embed_box.currentText())
        self._rag_worker.progress.connect(
            lambda d, t: (self.rag_progress.setMaximum(t),
                          self.rag_progress.setValue(d))
        )
        self._rag_worker.message.connect(self._log)
        self._rag_worker.finished.connect(self._on_rag_done)
        self._rag_worker.error.connect(self._on_rag_error)
        self._rag_worker.start()

    def _set_rag_ui_busy(self, busy: bool):
        """Lock/unlock all interactive widgets during RAG indexing."""
        self.rag_file_btn.setEnabled(not busy)
        self.rag_folder_btn.setEnabled(not busy)
        self.embed_box.setEnabled(not busy)
        self.rag_progress.setVisible(busy)
        self.rag_stop_btn.setVisible(busy)
        # Also disable chat send so user doesn't trigger RAG search mid-index
        self.send_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        if busy:
            self.input.setPlaceholderText("⏳ Indexing knowledge base… please wait")
        else:
            self.input.setPlaceholderText("Type your message… (Ctrl+Enter to send)")

    def _stop_rag_indexing(self):
        """Gracefully stop the running RAG index worker."""
        if hasattr(self, "_rag_worker") and self._rag_worker.isRunning():
            self.rag_stop_btn.setEnabled(False)
            self.rag_stop_btn.setText("⏳ Stopping…")
            self._rag_worker.stop()

    def _on_rag_done(self):
        self._rag = None
        self.rag_stop_btn.setEnabled(True)
        self.rag_stop_btn.setText("⛔ Stop Indexing")
        self._set_rag_ui_busy(False)
        has = self._rag_has_data()
        self.clear_rag_btn.setEnabled(has)
        self.rm_doc_btn.setEnabled(has)
        self._log("✅ Knowledge base ready!")

    def _on_rag_error(self, err):
        self._set_rag_ui_busy(False)
        self._log(f"❌ RAG error: {err}")

    def _clear_rag(self):
        if QMessageBox.question(self, "Clear RAG", "Delete all knowledge?") != QMessageBox.Yes:
            return
        self._rag_index().clear()
        self._rag = None
        self.clear_rag_btn.setEnabled(False)
        self.rm_doc_btn.setEnabled(False)
        self._log("✅ Knowledge cleared.")

    def _remove_rag_doc(self):
        name, ok = QInputDialog.getText(self, "Remove Document", "Filename (e.g. report.pdf):")
        if not ok or not name.strip():
            return
        removed = self._rag_index().remove_source(name.strip())
        if removed:
            self._log(f"✅ Removed {removed} chunks from '{name}'.")
        else:
            self._log(f"⚠️ Source '{name}' not found.")

    def _rag_context(self, query: str) -> str | None:
        idx = self._rag_index()
        if idx.is_empty:
            return None
        chunks = idx.search(query, k=5)
        if not chunks:
            return None
        self._log(f"\n🔍 Retrieved {len(chunks)} knowledge chunks.\n")
        return "\n\n".join(chunks)[:4000]

    # ================================================================ #
    #  CREWS                                                             #
    # ================================================================ #
    def _refresh_crews(self):
        self.crew_list.clear()
        for crew in self.db.list_crews():
            cfg = json.loads(crew["config"])
            prefix = "⭐ " if crew["is_default"] else ""
            item = QListWidgetItem(f"{prefix}{crew['name']} ({len(cfg)} agents)")
            item.setToolTip(" | ".join(a["role"] for a in cfg))
            item.setData(Qt.UserRole, crew["id"])
            self.crew_list.addItem(item)
        self._update_crew_btn()

    def _update_crew_btn(self):
        if self.current_crew_cfg and self.current_crew_name:
            self.crew_btn.setText(
                f"📋 {self.current_crew_name} ({len(self.current_crew_cfg)} agents)"
            )
            self.crew_btn.setProperty("active", "true")
        else:
            self.crew_btn.setText("📋 No Crew")
            self.crew_btn.setProperty("active", "false")
        self.crew_btn.style().unpolish(self.crew_btn)
        self.crew_btn.style().polish(self.crew_btn)

    def _select_crew(self, item):
        cid = item.data(Qt.UserRole)
        crew = self.db.get_crew(cid)
        self.current_crew_cfg  = json.loads(crew["config"])
        self.current_crew_id   = cid
        self.current_crew_name = crew["name"]
        self._update_crew_btn()
        self._log(f"✅ Switched to crew: {crew['name']}")

    def _create_crew(self):
        dialog = CrewConfigDialog(
            [m["name"] for m in self.models], parent=self
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        name, cfg = dialog.get_crew_data()
        if name and cfg:
            nid = self.db.create_crew(name, cfg)
            self._refresh_crews()
            self.current_crew_id = nid
            self.current_crew_name = name
            self.current_crew_cfg = cfg
            self._update_crew_btn()
            self._log(f"✅ Created crew: {name}")

    def _edit_crew(self, crew_id: int):
        crew = self.db.get_crew(crew_id)
        cfg = json.loads(crew["config"])
        dialog = CrewConfigDialog(
            [m["name"] for m in self.models], cfg, crew["name"], self
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        name, new_cfg = dialog.get_crew_data()
        if name and new_cfg:
            self.db.update_crew(crew_id, name, new_cfg)
            if crew_id == self.current_crew_id:
                self.current_crew_cfg  = new_cfg
                self.current_crew_name = name
                self._update_crew_btn()
            self._refresh_crews()

    def _delete_crew(self, crew_id: int):
        crew = self.db.get_crew(crew_id)
        if QMessageBox.question(
            self, "Delete", f"Delete crew '{crew['name']}'?"
        ) == QMessageBox.Yes:
            self.db.delete_crew(crew_id)
            if crew_id == self.current_crew_id:
                self.current_crew_cfg  = []
                self.current_crew_id   = None
                self.current_crew_name = None
                self._update_crew_btn()
            self._refresh_crews()

    def _set_default_crew(self, crew_id: int):
        self.db.set_default_crew(crew_id)
        crew = self.db.get_crew(crew_id)
        self.current_crew_id   = crew_id
        self.current_crew_cfg  = json.loads(crew["config"])
        self.current_crew_name = crew["name"]
        self._update_crew_btn()
        self._refresh_crews()

    def _open_current_crew(self):
        if not self.current_crew_cfg:
            self._create_crew()
        elif self.current_crew_id:
            self._edit_crew(self.current_crew_id)

    def _crew_menu(self, pos):
        item = self.crew_list.itemAt(pos)
        menu = QMenu()
        if item:
            cid = item.data(Qt.UserRole)
            menu.addAction("⭐ Set Default").triggered.connect(
                lambda: self._set_default_crew(cid)
            )
            menu.addAction("✏ Edit").triggered.connect(
                lambda: self._edit_crew(cid)
            )
            menu.addAction("🗑 Delete").triggered.connect(
                lambda: self._delete_crew(cid)
            )
        else:
            menu.addAction("➕ Create Crew").triggered.connect(self._create_crew)
        menu.exec_(self.crew_list.mapToGlobal(pos))

    def _load_template(self):
        names = [t["name"] for t in CREW_TEMPLATES]
        name, ok = QInputDialog.getItem(
            self, "Load Template", "Choose:", names, 0, False
        )
        if not ok:
            return
        tmpl = next(t for t in CREW_TEMPLATES if t["name"] == name)
        dialog = CrewConfigDialog(
            [m["name"] for m in self.models],
            tmpl["config"], tmpl["name"], self
        )
        if dialog.exec_() == QDialog.Accepted:
            new_name, cfg = dialog.get_crew_data()
            if new_name and cfg:
                self.db.create_crew(new_name, cfg)
                self._refresh_crews()
                self._log(f"✅ Loaded template '{name}' as '{new_name}'")

    def _toggle_crew_mode(self):
        self.crew_mode = not self.crew_mode
        self.mode_btn.setText(
            f"⚡ Crew Mode: {'ON' if self.crew_mode else 'OFF'}"
        )

    # ================================================================ #
    #  VISION                                                            #
    # ================================================================ #
    def _attach_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
        )
        if not path:
            return
        with open(path, "rb") as f:
            self.attached_b64 = base64.b64encode(f.read()).decode()
        self.attached_path = path
        self._log(f"📎 Attached: {os.path.basename(path)}")

    # ================================================================ #
    #  SEND                                                              #
    # ================================================================ #
    def _send(self):
        prompt = self.input.toPlainText().strip()
        if not prompt:
            return

        self.last_prompt = prompt
        self.input.clear()

        # Create conversation if new
        if not self.current_conv_id:
            title = prompt[:40] + ("…" if len(prompt) > 40 else "")
            with QMutexLocker(self.db_mutex):
                self.current_conv_id = self.db.create_conversation(title)
            self._refresh_conversations()

        with QMutexLocker(self.db_mutex):
            self.db.add_message(self.current_conv_id, "user", prompt)

        self._log(f"\n🧑 YOU:\n{prompt}\n")
        mode_tag = (
            f" (Crew: {len(self.current_crew_cfg)} agents)"
            if self.crew_mode and self.current_crew_cfg else ""
        )
        self._log(f"🤖 AI{mode_tag}:\n")
        self._update_stop_btn(True)

        # Build history (exclude the just-added user message — we'll add it below)
        max_hist = 20 if self.crew_mode else 10
        with QMutexLocker(self.db_mutex):
            history = self.db.get_messages(self.current_conv_id)[-max_hist:]

        # History messages (without the current prompt — added separately below)
        ollama_msgs = [{"role": m["role"], "content": m["content"]}
                       for m in history[:-1]]   # exclude last (current user msg)

        # Build current user message — attach image if present
        current_msg: dict = {"role": "user", "content": prompt}
        if self.attached_b64:
            if self._current_model_vision():
                current_msg["images"] = [self.attached_b64]
                self._log(f"🖼 Image sent to model.\n")
            else:
                self._log("⚠️ Model does not support vision — image ignored.\n")
            self.attached_path = None
            self.attached_b64  = None

        ollama_msgs.append(current_msg)

        # RAG injection
        rag_ctx = self._rag_context(prompt)
        if rag_ctx:
            sys_rag = (
                "Use ONLY these facts if relevant. "
                "If unsure, say 'not found in knowledge base':\n\n" + rag_ctx
            )
            ollama_msgs.insert(0, {"role": "system", "content": sys_rag})

        # Start worker
        if self.crew_mode:
            if not self.current_crew_cfg:
                self._log("❌ No crew selected!\n")
                self._update_stop_btn(False)
                return
            crew_cfg = copy.deepcopy(self.current_crew_cfg)
            if rag_ctx:
                crew_cfg[0]["system_prompt"] = (
                    crew_cfg[0].get("system_prompt", "") + "\n\n" + sys_rag
                ).strip()
            self.thread = CrewChatWorker(prompt, crew_cfg, history)
        else:
            self.thread = DirectChatWorker(
                self.model_box.currentText(), ollama_msgs
            )

        self.thread.token.connect(self._append_token)
        self.thread.finished.connect(self._on_done)
        self.thread.error.connect(self._on_error)
        self.thread.start()

        # Safety timeout: 10 min
        QTimer.singleShot(600_000, lambda: self.thread and self.thread.stop())

    def _append_token(self, text: str):
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    def _on_done(self, response: str, elapsed: float, chunks: int):
        if response:
            stopped = (hasattr(self.thread, "is_running")
                       and not self.thread.is_running())
            if stopped:
                response += "\n\n[STOPPED BY USER]"
            with QMutexLocker(self.db_mutex):
                self.db.add_message(
                    self.current_conv_id, "assistant", response
                )
        if elapsed > 0 and chunks:
            speed = len(response) / elapsed
            self._log(
                f"\n📊 {len(response)} chars | {speed:.0f} ch/s | {elapsed:.1f}s\n"
            )
        self._update_stop_btn(False)
        self._cleanup_thread()

    def _on_error(self, err: str):
        self._log(f"\n❌ Error: {err}\n")
        QMessageBox.critical(self, "Error", err)
        self._update_stop_btn(False)
        self._cleanup_thread()

    def _stop_or_reload(self):
        if self.thread and self.thread.isRunning():
            self.thread.stop()
            self._log("\n⚠️ Stopped.\n")
            self._update_stop_btn(False)
        elif self.last_prompt and self.current_conv_id:
            self.input.setPlainText(self.last_prompt)
            self._send()
        else:
            self._log("\nℹ️ Nothing to reload.\n")

    def _update_stop_btn(self, running: bool):
        if running:
            self.stop_btn.setText("⏹ Stop")
            self.stop_btn.setProperty("active", "true")
        else:
            self.stop_btn.setText("🔄 Reload")
            self.stop_btn.setProperty("active", "false")
        self.stop_btn.style().unpolish(self.stop_btn)
        self.stop_btn.style().polish(self.stop_btn)
        self.stop_btn.setEnabled(running or bool(self.last_prompt))

    def _cleanup_thread(self):
        if self.thread:
            self.thread.deleteLater()
            self.thread = None

    # ================================================================ #
    #  OLLAMA SERVER TOGGLE                                              #
    # ================================================================ #
    def _check_server_state(self):
        """Check if Ollama is running and update button + UI accordingly."""
        running = self._client.is_running()
        self._server_running = running
        self._update_server_btn()
        self._set_ui_server_state(running)

    def _set_ui_server_state(self, running: bool):
        """Enable/disable ALL interactive widgets based on server state."""
        # Right panel — chat
        self.send_btn.setEnabled(running)
        self.stop_btn.setEnabled(running and bool(self.last_prompt))
        self.input.setEnabled(running)
        self.model_box.setEnabled(running)
        self.mode_btn.setEnabled(running)
        self.crew_btn.setEnabled(running)
        self.attach_btn.setEnabled(running and self._current_model_vision())
        if not running:
            self.input.setPlaceholderText(
                "⏸ Ollama server stopped — press [Server: OFF] to start"
            )
        else:
            self.input.setPlaceholderText("Type your message… (Ctrl+Enter to send)")

        # Left panel — RAG (needs server for Ollama embed models)
        self.rag_file_btn.setEnabled(running)
        self.rag_folder_btn.setEnabled(running)
        self.embed_box.setEnabled(running)
        # Clear/Remove only if server running AND index exists
        has_index = self._rag_has_data()
        self.clear_rag_btn.setEnabled(running and has_index)
        self.rm_doc_btn.setEnabled(running and has_index)

        # Left panel — Crews & manager
        self.new_crew_btn.setEnabled(running)
        self.tmpl_btn.setEnabled(running)
        self.mgr_btn.setEnabled(running)

    def _poll_server(self):
        """
        Called every 5s by QTimer.
        Runs is_running() in a background thread so the UI never freezes.
        On state change: updates button, reloads models, toggles chat UI.
        """
        if self._server_poll_busy:
            return
        self._server_poll_busy = True

        def _check():
            try:
                now_running = self._client.is_running()
            except Exception:
                now_running = False
            finally:
                self._server_poll_busy = False
            # Only act on actual state change
            if now_running != self._server_running:
                QTimer.singleShot(0, lambda: self._on_external_server_change(now_running))

        threading.Thread(target=_check, daemon=True).start()

    def _on_external_server_change(self, now_running: bool):
        """Called on main thread when external server state changes."""
        self._server_running = now_running
        self._update_server_btn()
        if now_running:
            self._load_models()
            self._set_ui_server_state(True)
            self._log("\n🟢 Ollama server detected — models reloaded.\n")
        else:
            # Stop any in-progress generation immediately
            if self.thread and self.thread.isRunning():
                self.thread.stop()
                self._log("\n⚠️ Generation stopped (server went down).\n")
                self._cleanup_thread()
            self._update_stop_btn(False)
            self.model_box.clear()
            self.models = []
            self._set_ui_server_state(False)
            self._log("\n🔴 Ollama server stopped externally. Chat disabled.\n")

    def _update_server_btn(self):
        if self._server_running:
            self.server_btn.setText("🟢 Server: ON")
            self.server_btn.setProperty("running", "true")
        else:
            self.server_btn.setText("🔴 Server: OFF")
            self.server_btn.setProperty("running", "false")
        # Force stylesheet re-evaluation
        self.server_btn.style().unpolish(self.server_btn)
        self.server_btn.style().polish(self.server_btn)

    def _toggle_server(self):
        if self._server_running:
            self._stop_server()
        else:
            self._start_server()

    def _stop_server(self):
        """Gracefully stop Ollama — disable chat UI, clear models."""
        self._log("\n🔴 Stopping Ollama server…\n")

        # Disable chat while server is off
        self.send_btn.setEnabled(False)
        self.input.setEnabled(False)
        self.input.setPlaceholderText("⏸ Ollama server stopped — start server to chat")
        self.model_box.setEnabled(False)

        def _do_stop():
            try:
                if self._server_proc and self._server_proc.poll() is None:
                    # We started it — terminate gracefully
                    self._server_proc.terminate()
                    try:
                        self._server_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._server_proc.kill()
                    self._server_proc = None
                else:
                    # Started externally — use pkill
                    subprocess.run(
                        ["pkill", "-TERM", "-x", "ollama"],
                        capture_output=True
                    )
                    # Wait up to 6s for it to die
                    import time as _t
                    for _ in range(12):
                        _t.sleep(0.5)
                        if not self._client.is_running():
                            break
            except Exception as e:
                pass
            # Update UI from main thread
            QTimer.singleShot(0, self._on_server_stopped)

        threading.Thread(target=_do_stop, daemon=True).start()

    def _on_server_stopped(self):
        self._server_running = False
        self._update_server_btn()
        self.model_box.clear()
        self.models = []
        self._set_ui_server_state(False)
        self._log("🔴 Ollama server stopped. Models unloaded.\n")

    def _start_server(self):
        """Start ollama serve and reload models when ready."""
        self._log("\n🟢 Starting Ollama server…\n")
        self.server_btn.setText("⏳ Starting…")
        self.server_btn.setEnabled(False)

        def _do_start():
            try:
                self._server_proc = subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                QTimer.singleShot(0, lambda: self._log(
                    "❌ 'ollama' not found in PATH.\n"
                ))
                QTimer.singleShot(0, self._on_server_failed)
                return

            # Poll until ready (max 30s)
            import time as _t
            for _ in range(60):
                _t.sleep(0.5)
                if self._client.is_running():
                    QTimer.singleShot(0, self._on_server_started)
                    return

            QTimer.singleShot(0, lambda: self._log(
                "⚠️ Server started but not responding after 30s.\n"
            ))
            QTimer.singleShot(0, self._on_server_started)

        threading.Thread(target=_do_start, daemon=True).start()

    def _on_server_started(self):
        self._server_running = True
        self._update_server_btn()
        self.server_btn.setEnabled(True)
        self._load_models()
        self._set_ui_server_state(True)
        self._log("🟢 Ollama server ready. Models loaded.\n")

    def _on_server_failed(self):
        self._server_running = False
        self._update_server_btn()
        self.server_btn.setEnabled(True)

    # ================================================================ #
    #  MODEL MANAGER                                                     #
    # ================================================================ #
    def _open_manager(self):
        """
        Launch ollama_manager.
        Supports three layouts:
          1. Frozen onefile binary  → look for 'Ollama-ai-manager' next to sys.executable
          2. Frozen onedir          → same directory as sys.executable
          3. Plain .py dev mode     → ollama_manager.py next to main.py
        """
        # ── Determine the "real" directory ──────────────────────────
        # sys.executable is always the actual binary path, even when frozen.
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))

        # ── Try compiled manager binary first ───────────────────────
        for name in ("Ollama-ai-manager", "Ollama-ai-manager.exe"):
            candidate = os.path.join(exe_dir, name)
            if os.path.isfile(candidate):
                subprocess.Popen([candidate])
                return

        # ── Fallback: .py source file (dev mode) ────────────────────
        # In dev mode __file__ works correctly.
        try:
            src_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            src_dir = exe_dir

        py_file = os.path.join(src_dir, "ollama_manager.py")
        if os.path.isfile(py_file):
            subprocess.Popen([sys.executable, py_file])
            return

        # ── Nothing found ───────────────────────────────────────────
        QMessageBox.critical(
            self, "Not Found",
            "Manager not found.\n\n"
            f"Expected one of:\n"
            f"  {os.path.join(exe_dir, 'Ollama-ai-manager')}\n"
            f"  {py_file}"
        )

    # ================================================================ #
    #  Helpers                                                           #
    # ================================================================ #
    def _log(self, text: str):
        """Append text to chat display."""
        cursor = self.chat.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.chat.setTextCursor(cursor)
        self.chat.ensureCursorVisible()

    # Ctrl+Enter sends
    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() == Qt.ControlModifier):
            self._send()
        else:
            super().keyPressEvent(event)


# ====================================================================
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    win = OllamaGUI()
    win.show()
    sys.exit(app.exec_())
