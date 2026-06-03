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
import _syspath_patch  # noqa: F401 — must be first, injects system site-packages into frozen binary
import base64
import copy
import glob
import json
import os
import subprocess
import sys
import threading
import time

from PyQt5.QtCore import Qt, QMutex, QMutexLocker, QTimer, QUrl
from PyQt5.QtGui import QFont, QFontDatabase, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFrame,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QTextEdit, QTextBrowser, QVBoxLayout, QWidget,
)

from database import DB_CLASS
from ollama_client import OllamaClient
from workers import (
    DirectChatWorker, CrewChatWorker, RAGBuildWorker,
    GroqChatWorker, SmartChatWorker,
)
from groq_client import GroqClient
from chat_renderer import chat_html
from crew_dialogs import CrewConfigDialog, CREW_TEMPLATES


# Persistent settings file — stores theme and Groq API key
_CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".ollama_gui")
_SETTINGS_FILE = os.path.join(_CONFIG_DIR, "settings.json")


# Fallback embedding model names shown when Ollama has no embed models installed
_EMBED_FALLBACKS = [
    "nomic-embed-text:latest",
    "mxbai-embed-large:latest",
]


def _truncate_text_blocks(
    blocks: list[tuple[str, str]], max_chars: int
) -> list[tuple[str, str]]:
    """Trim text blocks so total character count stays within max_chars."""
    out   = []
    total = 0
    for fname, content in blocks:
        remaining = max_chars - total
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n…[truncated]"
        out.append((fname, content))
        total += len(content)
    return out


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

        # Attachment state — cleared after each send
        self.attached_path   = None
        self.attached_images: list[str] = []
        self.attached_texts:  list[tuple[str, str]] = []
        self.attached_label  = ""

        # Project ZIP session — persists across multiple messages
        self.project_zip_path: str | None = None   # active zip path
        self.project_zip_tree: str        = ""      # formatted tree string
        self.project_zip_entries: list[str] = []    # all file paths inside zip

        # ── API mode (Groq) ──────────────────────────────────────────
        self.api_mode     = False
        self.groq_api_key = os.environ.get("GROQ_API_KEY", "")
        self._saved_model  = ""   # restored by _load_settings below

        self._load_settings()   # overwrite defaults with persisted values

        # ── Chat rendering ───────────────────────────────────────────
        self._chat_log:    list[dict] = []   # {type, content, label?}
        self._code_store:  list[str]  = []   # code blocks for copy
        self._is_streaming     = False
        self._streaming_ai_idx = -1           # index of the AI message being streamed

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
        root.setContentsMargins(0, 0, 0, 0)

        # Build left panel widgets (needed for RAG/Crews), then wrap in a
        # popup drawer — toggled by the ☰ button in the chat selector row.
        self._left_panel = self._build_left_panel()
        self._left_panel.setWindowFlags(Qt.Popup)
        self._left_panel.setMinimumWidth(540)
        self._left_panel.setMaximumWidth(620)
        self._left_panel.hide()

        root.addLayout(self._build_right_panel(), 1)

        self._apply_theme()

    # ---- Left panel (drawer) ------------------------------------ #
    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setMinimumWidth(480)
        w.setMaximumWidth(680)
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.setContentsMargins(10, 14, 10, 14)

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

        self.mgr_btn = QPushButton("🦙 Ollama Manager")
        self.mgr_btn.setMinimumHeight(70)
        self.mgr_btn.clicked.connect(self._open_manager)
        self.mgr_btn.setStyleSheet(
            "QPushButton {"
            "background:#1a5f1a;color:white;font-weight:bold;"
            "padding:8px;border-radius:8px;font-size:28px;}"
            "QPushButton:disabled {"
            "background:#1a2e1a;color:#4a6b4a;"
            "border:1px solid #2a3f2a;}"
        )
        v.addWidget(self.mgr_btn)

        return w

    # ---- Right panel -------------------------------------------- #
    def _build_right_panel(self) -> QHBoxLayout:
        v = QVBoxLayout()

        # ── Chat selector row ────────────────────────────────────────
        chat_sel_row = QHBoxLayout()
        chat_sel_row.setSpacing(6)

        self.drawer_btn = QPushButton("☰")
        self.drawer_btn.setFixedWidth(72)
        self.drawer_btn.setMinimumHeight(60)
        self.drawer_btn.setObjectName("drawerBtn")
        self.drawer_btn.setToolTip("RAG · Crews · Ollama Manager")
        self.drawer_btn.clicked.connect(self._toggle_drawer)
        chat_sel_row.addWidget(self.drawer_btn)

        self.new_chat_btn = QPushButton("➕ New Chat")
        self.new_chat_btn.setMinimumHeight(60)
        self.new_chat_btn.setMinimumWidth(200)
        self.new_chat_btn.clicked.connect(self._new_chat)
        chat_sel_row.addWidget(self.new_chat_btn)

        self.chat_title_btn = QPushButton("💬  —")
        self.chat_title_btn.setMinimumHeight(60)
        self.chat_title_btn.setObjectName("chatTitleBtn")
        self.chat_title_btn.clicked.connect(self._toggle_chat_popup)
        chat_sel_row.addWidget(self.chat_title_btn, 1)
        v.addLayout(chat_sel_row)

        # ── Chat list popup (hidden by default) ──────────────────────
        self._chat_popup = QFrame()
        self._chat_popup.setObjectName("chatPopup")
        self._chat_popup.setWindowFlags(Qt.Popup)
        self._chat_popup.setFixedWidth(640)
        popup_v = QVBoxLayout(self._chat_popup)
        popup_v.setContentsMargins(6, 6, 6, 6)
        popup_v.setSpacing(4)
        # Search inside popup
        self.chat_search = QLineEdit()
        self.chat_search.setPlaceholderText("🔍 Search…")
        self.chat_search.setClearButtonEnabled(True)
        self.chat_search.setMinimumHeight(52)
        self.chat_search.textChanged.connect(self._filter_convs)
        popup_v.addWidget(self.chat_search)
        self.conv_list = QListWidget()
        self.conv_list.setMinimumHeight(500)
        self.conv_list.setMaximumHeight(800)
        self.conv_list.itemClicked.connect(self._on_popup_item_clicked)
        self.conv_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._conv_menu)
        popup_v.addWidget(self.conv_list)
        self._chat_popup.hide()

        # Top bar
        top = QHBoxLayout()
        top.addWidget(QLabel("Model:"))
        self.model_box = QComboBox()
        self.model_box.setMinimumWidth(500)
        self.model_box.setMinimumHeight(60)
        self.model_box.currentIndexChanged.connect(self._update_attach_btn)
        self.model_box.currentIndexChanged.connect(self._on_model_changed)
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

        # ── Local / Groq API toggle ──────────────────────────────────
        self.api_toggle_btn = QPushButton("🖥️ Local")
        self.api_toggle_btn.setObjectName("apiToggleBtn")
        self.api_toggle_btn.setMinimumHeight(60)
        self.api_toggle_btn.setMinimumWidth(180)
        self.api_toggle_btn.clicked.connect(self._toggle_api_mode)
        top.addWidget(self.api_toggle_btn)

        self.server_btn = QPushButton("🟢 Server: ON")
        self.server_btn.setObjectName("srvBtn")
        self.server_btn.setMinimumHeight(60)
        self.server_btn.setMinimumWidth(220)
        self.server_btn.clicked.connect(self._toggle_server)
        top.addWidget(self.server_btn)
        v.addLayout(top)

        # ── Groq API key row (hidden by default) ─────────────────────
        self.groq_row = QWidget()
        groq_layout = QHBoxLayout(self.groq_row)
        groq_layout.setContentsMargins(0, 4, 0, 4)
        groq_layout.addWidget(QLabel("🔑 Groq API Key:"))
        self.groq_key_input = QLineEdit()
        self.groq_key_input.setPlaceholderText("gsk_… (paste your Groq API key)")
        self.groq_key_input.setEchoMode(QLineEdit.Password)
        self.groq_key_input.setMinimumHeight(56)
        if self.groq_api_key:
            self.groq_key_input.setText(self.groq_api_key)
        groq_layout.addWidget(self.groq_key_input, 1)
        self.groq_save_btn = QPushButton("✔ Apply")
        self.groq_save_btn.setMinimumHeight(56)
        self.groq_save_btn.setMinimumWidth(140)
        self.groq_save_btn.clicked.connect(self._apply_groq_key)
        groq_layout.addWidget(self.groq_save_btn)
        self.groq_clear_btn = QPushButton("🗑 Clear")
        self.groq_clear_btn.setMinimumHeight(56)
        self.groq_clear_btn.setMinimumWidth(120)
        self.groq_clear_btn.setToolTip("Remove saved Groq API key")
        self.groq_clear_btn.clicked.connect(self._clear_groq_key)
        groq_layout.addWidget(self.groq_clear_btn)
        self.groq_row.setVisible(False)
        v.addWidget(self.groq_row)

        # Chat display — QTextBrowser renders HTML (markdown, code blocks, tables)
        self.chat = QTextBrowser()
        self.chat.setReadOnly(True)
        self.chat.setOpenLinks(False)
        # Font is controlled entirely by the QSS stylesheet (_apply_theme).
        # Do NOT call setFont() here — it overrides Qt's glyph-fallback
        # mechanism and breaks non-Latin (e.g. Bengali) input rendering.
        self.chat.anchorClicked.connect(self._on_anchor_clicked)
        v.addWidget(self.chat, 1)

        # Input row
        inp_row = QHBoxLayout()
        self.attach_btn = QPushButton("📎")
        self.attach_btn.setFixedWidth(80)
        self.attach_btn.setMinimumHeight(80)
        self.attach_btn.setToolTip("Attach file(s) — images, code, zip (project)…")
        self.attach_btn.clicked.connect(self._attach_files)
        inp_row.addWidget(self.attach_btn)

        self.close_proj_btn = QPushButton("✖ Project")
        self.close_proj_btn.setFixedWidth(90)
        self.close_proj_btn.setMinimumHeight(80)
        self.close_proj_btn.setToolTip("Close active project ZIP")
        self.close_proj_btn.clicked.connect(self._clear_project)
        self.close_proj_btn.setVisible(False)
        inp_row.addWidget(self.close_proj_btn)

        self.input = QTextEdit()
        self.input.setFixedHeight(180)
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
        clr_btn.clicked.connect(self._clear_chat_display)
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
    _FONT_UI   = 32   # buttons, labels, combos, list items
    _FONT_CHAT = 44   # chat display
    _FONT_INPUT= 40   # message input

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
            QTextBrowser {{ font-size: {self._FONT_CHAT}px; border-radius: 8px; }}
            QTextEdit    {{ font-size: {self._FONT_INPUT}px; border-radius: 8px; }}
            QProgressBar {{ border-radius: 6px; min-height: 20px; }}
        """
        if self.dark:
            self.setStyleSheet(base + """
                QMainWindow, QWidget { background: #121212; color: #e0e0e0; }
                QTextBrowser, QTextEdit { background: #1e1e1e; color: #f0f0f0;
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
                QPushButton#apiToggleBtn { background:#1a4f8f; color:white; font-weight:bold; }
                QPushButton#apiToggleBtn:hover { background:#153b6e; }
                QPushButton#apiToggleBtn[api="true"] { background:#7b3fa0; color:white; font-weight:bold; }
                QPushButton#apiToggleBtn[api="true"]:hover { background:#5e2e7a; }
                QPushButton#chatTitleBtn { background:#1e2736; color:#a8c4e8;
                    border:1px solid #2a3a52; text-align:left; padding-left:14px; }
                QPushButton#chatTitleBtn:hover { background:#253040; }
                QPushButton#drawerBtn { background:#2a2d36; color:#e0e0e0;
                    font-size:28px; border:1px solid #3a3d48; border-radius:8px; }
                QPushButton#drawerBtn:hover { background:#363a45; }
                QFrame#chatPopup { background:#1a1e2a; border:1px solid #2a3a52;
                    border-radius:8px; }
                        """)
        else:
            self.setStyleSheet(base + f"""
                QMainWindow, QWidget {{ background: #f0f2f5; color: #1a1a2e; }}
                QTextBrowser, QTextEdit {{ background: #ffffff; color: #1a1a2e;
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
                QPushButton#apiToggleBtn {{ background:#1a4f8f; color:white; font-weight:bold; }}
                QPushButton#apiToggleBtn:hover {{ background:#153b6e; }}
                QPushButton#apiToggleBtn[api="true"] {{ background:#7b3fa0; color:white; font-weight:bold; }}
                QPushButton#apiToggleBtn[api="true"]:hover {{ background:#5e2e7a; }}
                QPushButton#chatTitleBtn {{ background:#e8f0fe; color:#1a4f8f;
                    border:1px solid #b8cef8; text-align:left; padding-left:14px; }}
                QPushButton#chatTitleBtn:hover {{ background:#d2e3fc; }}
                QPushButton#drawerBtn {{ background:#e8edf5; color:#1a1a2e;
                    font-size:28px; border:1px solid #c8cdd4; border-radius:8px; }}
                QPushButton#drawerBtn:hover {{ background:#d8e0ec; }}
                QFrame#chatPopup {{ background:#ffffff; border:1px solid #c8cdd4;
                    border-radius:8px; }}
                        """)

    def _toggle_theme(self):
        self.dark = not self.dark
        self._save_settings()
        self._apply_theme()
        # Font size is set via QSS above — no setFont() needed.
        # Re-render chat with new theme colors
        self._render_chat()

    # ================================================================ #
    #  MODELS                                                            #
    # ================================================================ #
    def _load_models(self):
        self.models = []
        self.model_box.clear()

        # ── Groq API mode ────────────────────────────────────────────
        if self.api_mode:
            groq = GroqClient(api_key=self.groq_api_key)
            groq_models = groq.list_models()
            self.models = groq_models   # each dict already has "name" + "vision"
            for m in groq_models:
                self.model_box.addItem(m["name"])
            # For RAG in API mode, keep sentence-transformers embed options
            self.embed_box.clear()
            self.embed_box.addItems(_EMBED_FALLBACKS)
            self._log(f"☁️ Groq API mode — {len(groq_models)} models loaded.")
            self._restore_saved_model()
            return

        # ── Local Ollama mode ────────────────────────────────────────
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
        self._restore_saved_model()

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
        # Keep title button in sync with current conversation
        if self.current_conv_id:
            for i in range(self.conv_list.count()):
                item = self.conv_list.item(i)
                if item.data(Qt.UserRole) == self.current_conv_id:
                    title = item.text().lstrip("📌 ").strip()
                    self.chat_title_btn.setText(f"💬  {title}")
                    break

    def _toggle_drawer(self):
        """Show/hide the left-panel drawer (RAG, Crews, Ollama Manager)."""
        if self._left_panel.isVisible():
            self._left_panel.hide()
            return
        btn = self.drawer_btn
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._left_panel.move(pos)
        self._left_panel.adjustSize()
        self._left_panel.show()
        self._left_panel.raise_()

    def _toggle_chat_popup(self):
        """Show/hide the scrollable chat list popup below the title button."""
        if self._chat_popup.isVisible():
            self._chat_popup.hide()
            return
        # Position popup below the title button
        btn = self.chat_title_btn
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        self._chat_popup.move(pos)
        self._chat_popup.adjustSize()
        self._chat_popup.show()
        self._chat_popup.raise_()

    def _on_popup_item_clicked(self, item: QListWidgetItem):
        self._chat_popup.hide()
        self._load_conversation(item)

    def _new_chat(self):
        self.current_conv_id = None
        self.last_prompt = ""
        self._chat_log = []
        self._code_store = []
        self._is_streaming = False
        self.chat.clear()
        self.chat_title_btn.setText("💬  New Chat")
        self._update_stop_btn(False)

    def _load_conversation(self, item):
        self.current_conv_id = item.data(Qt.UserRole)
        title = item.text().lstrip("📌 ").strip()
        self.chat_title_btn.setText(f"💬  {title}")
        self._chat_log = []
        self._code_store = []
        self._is_streaming = False
        with QMutexLocker(self.db_mutex):
            msgs = self.db.get_messages(self.current_conv_id)
        for m in msgs:
            if m["role"] == "user":
                self._chat_log.append({'type': 'user', 'content': m['content']})
            else:
                self._chat_log.append({'type': 'ai', 'content': m['content'], 'label': '🤖 AI'})
        self._render_chat()
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
    #  ATTACHMENTS                                                       #
    # ================================================================ #
    def _attach_files(self):
        """
        Universal file picker.
        • Images / code / text  → inject content as before
        • ZIP                   → load as project session (tree only injected)
        """
        from attachment_handler import (
            process_attachment, build_zip_tree, list_zip_entries,
        )

        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach file(s)", "",
            "All Files (*);;"
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.ico);;"
            "Code (*.py *.js *.ts *.sh *.bash *.c *.cpp *.h *.hpp *.go *.rs "
            "*.java *.kt *.rb *.php *.pl *.lua *.r *.cs *.swift *.zig);;"
            "Text / Data (*.txt *.md *.json *.yaml *.yml *.toml *.ini *.csv "
            "*.xml *.html *.css);;"
            "Archives (*.zip)"
        )
        if not paths:
            return

        self.attached_images = []
        self.attached_texts  = []
        labels = []

        for path in paths:
            ext = os.path.splitext(path)[1].lower()

            if ext == '.zip':
                self._load_project_zip(path)
                labels.append(f"📦 Project: {os.path.basename(path)}")
                continue

            result = process_attachment(path)
            self.attached_images.extend(result.images)
            self.attached_texts.extend(result.text_blocks)
            labels.append(result.summary)

        # Enforce 60 KB cap on plain text attachments
        total = sum(len(c) for _, c in self.attached_texts)
        if total > 60_000:
            self.attached_texts = _truncate_text_blocks(self.attached_texts, 60_000)
            labels.append("⚠️ Content trimmed to fit context limit")

        if labels:
            self.attached_label = " | ".join(labels)
            self.attach_btn.setText("📎✓")
            self._log(f"📎 {self.attached_label}")

    def _load_project_zip(self, zip_path: str):
        """
        Load a zip as the active project session.
        Stores the tree; actual file contents fetched on demand.
        """
        from attachment_handler import list_zip_entries, build_zip_tree

        entries = list_zip_entries(zip_path)
        if not entries:
            self._log("⚠️ ZIP is empty or invalid.")
            return

        self.project_zip_path    = zip_path
        self.project_zip_entries = entries
        self.project_zip_tree    = build_zip_tree(entries)
        self.close_proj_btn.setVisible(True)

        n    = len(entries)
        name = os.path.basename(zip_path)
        info = (
            f"📦 **Project loaded: `{name}`** — {n} files\n\n"
            f"```\n{self.project_zip_tree}\n```\n\n"
            f"ফাইল দেখতে বলো যেমন: *\"main.py দেখাও\"* বা "
            f"*\"{name} এর gui/ ফোল্ডার analyze করো\"*"
        )
        self._chat_log.append({'type': 'ai', 'content': info, 'label': '📦 Project'})
        self._render_chat()
        self._log(f"📦 Project loaded: {name} ({n} files)")

    def _resolve_project_files(self, prompt: str) -> list[tuple[str, str]]:
        """
        Given the user's prompt, find matching files from the project zip
        and return their contents (max 3 files, max 60 KB total).
        Called from _send() before building the message.
        """
        if not self.project_zip_path or not self.project_zip_entries:
            return []

        from attachment_handler import fetch_zip_files

        prompt_lower = prompt.lower()

        # Score each entry by how well it matches the prompt
        scored: list[tuple[float, str]] = []
        for entry in self.project_zip_entries:
            basename = os.path.basename(entry).lower()
            score = 0.0
            if basename in prompt_lower:
                score += 10.0
            elif any(basename.startswith(w) for w in prompt_lower.split()):
                score += 5.0
            # directory match
            parts = entry.lower().replace('\\', '/').split('/')
            for part in parts[:-1]:
                if part and part in prompt_lower:
                    score += 3.0
            # extension match
            ext = os.path.splitext(basename)[1].lstrip('.')
            if ext and ext in prompt_lower:
                score += 2.0
            if score > 0:
                scored.append((score, entry))

        # Take top 3 matches, cap at 60 KB total
        scored.sort(key=lambda x: -x[0])
        top = [e for _, e in scored[:3]]

        if not top:
            return []

        blocks = fetch_zip_files(self.project_zip_path, top)
        return _truncate_text_blocks(blocks, 60_000)

    def _clear_attachments(self):
        self.attached_images = []
        self.attached_texts  = []
        self.attached_label  = ""
        self.attach_btn.setText("📎")

    def _clear_project(self):
        """Unload the active project zip session."""
        if self.project_zip_path:
            name = os.path.basename(self.project_zip_path)
            self.project_zip_path    = None
            self.project_zip_tree    = ""
            self.project_zip_entries = []
            self.close_proj_btn.setVisible(False)
            self._log(f"📦 Project closed: {name}")

    def _update_attach_btn(self):
        pass   # no-op — attach button always visible

    def _current_model_vision(self) -> bool:
        name = self.model_box.currentText()
        for m in self.models:
            if m["name"] == name:
                return m.get("vision", False)
        lo = name.lower()
        return any(k in lo for k in (
            "llava", "bakllava", "vision", "moondream", "phi3-v", "minicpm-v",
            "llama-4", "llama4", "scout", "maverick",
        ))

    # ================================================================ #
    #  SEND                                                              #
    # ================================================================ #
    def _send(self):
        from attachment_handler import AttachmentResult

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
            self.chat_title_btn.setText(f"💬  {title}")
            self._refresh_conversations()

        with QMutexLocker(self.db_mutex):
            self.db.add_message(self.current_conv_id, "user", prompt)

        self._add_user_msg(prompt)

        mode_tag = (
            f" (Crew: {len(self.current_crew_cfg)} agents)"
            if self.crew_mode and self.current_crew_cfg else ""
        )
        ai_label = f"🤖 AI{mode_tag}"
        self._start_ai_msg(ai_label)
        self._update_stop_btn(True)

        # Build history
        max_hist = 20 if self.crew_mode else 10
        with QMutexLocker(self.db_mutex):
            history = self.db.get_messages(self.current_conv_id)[-max_hist:]

        ollama_msgs = [
            {"role": m["role"], "content": m["content"]}
            for m in history[:-1]
        ]

        # RAG injection
        rag_ctx = self._rag_context(prompt)
        if rag_ctx:
            sys_rag = (
                "Use ONLY these facts if relevant. "
                "If unsure, say 'not found in knowledge base':\n\n" + rag_ctx
            )
            ollama_msgs.insert(0, {"role": "system", "content": sys_rag})

        ollama_msgs.append({"role": "user", "content": prompt})

        # Grab attachment data, then clear for next message
        images        = list(self.attached_images)
        text_blocks   = list(self.attached_texts)
        self._clear_attachments()

        # Build text injection from plain attached files
        text_injection = ""
        if text_blocks:
            import os as _os
            parts = []
            for fname, content in text_blocks:
                raw_ext = _os.path.splitext(fname)[1].lstrip('.').lower()
                lang = raw_ext or 'text'
                parts.append(f"### `{fname}`\n```{lang}\n{content}\n```")
            text_injection = "\n\n".join(parts)

        # Project ZIP: inject tree (always) + auto-fetch relevant files
        if self.project_zip_path:
            relevant = self._resolve_project_files(prompt)
            zip_name = os.path.basename(self.project_zip_path)
            zip_ctx  = f"**Project: `{zip_name}`**\n\nFile tree:\n```\n{self.project_zip_tree}\n```"
            if relevant:
                file_parts = []
                for fname, content in relevant:
                    ext  = os.path.splitext(fname)[1].lstrip('.').lower()
                    file_parts.append(f"### `{fname}`\n```{ext or 'text'}\n{content}\n```")
                zip_ctx += "\n\nRelevant files:\n\n" + "\n\n".join(file_parts)
            text_injection = (zip_ctx + "\n\n" + text_injection).strip()

        # ── Crew mode ────────────────────────────────────────────────
        if self.crew_mode:
            if not self.current_crew_cfg:
                self._log("❌ No crew selected!")
                self._update_stop_btn(False)
                return
            if self.api_mode and not self.groq_api_key.strip():
                self._log("❌ Groq API key not set!")
                self._update_stop_btn(False)
                return
            crew_cfg = copy.deepcopy(self.current_crew_cfg)
            if rag_ctx:
                crew_cfg[0]["system_prompt"] = (
                    crew_cfg[0].get("system_prompt", "") + "\n\n" + sys_rag
                ).strip()
            self.thread = CrewChatWorker(
                prompt, crew_cfg, history,
                api_key=self.groq_api_key if self.api_mode else "",
                api_model_override=self.model_box.currentText() if self.api_mode else "",
            )
            self.thread.token.connect(self._append_token)
            self.thread.finished.connect(self._on_done)
            self.thread.error.connect(self._on_error)
            self.thread.start()
            QTimer.singleShot(600_000, lambda: self.thread and self.thread.stop())
            return

        # ── Smart chat (single model, with attachments) ───────────────
        if self.api_mode and not self.groq_api_key.strip():
            self._log("❌ Groq API key not set! Switch to API mode and enter your key.")
            self._update_stop_btn(False)
            return

        self.thread = SmartChatWorker(
            model            = self.model_box.currentText(),
            messages         = ollama_msgs,
            images           = images,
            text_injection   = text_injection,
            api_mode         = self.api_mode,
            api_key          = self.groq_api_key,
            available_models = self.models,
        )
        self.thread.token.connect(self._append_token)
        self.thread.finished.connect(self._on_done)
        self.thread.error.connect(self._on_error)
        self.thread.status.connect(self._add_status)
        self.thread.start()
        QTimer.singleShot(600_000, lambda: self.thread and self.thread.stop())

    def _append_token(self, text: str):
        """Fast path: accumulate token in the tracked AI message slot."""
        if not self._is_streaming:
            return   # stale signal after cleanup — ignore
        idx = self._streaming_ai_idx
        if 0 <= idx < len(self._chat_log):
            self._chat_log[idx]['content'] += text
        # Append plain text to display (fast — no full re-render during streaming)
        doc = self.chat.document()
        if doc.isEmpty():
            return   # document was cleared; cursor position would be invalid
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
            # Use tracked index — safe even if status items were appended after
            idx = self._streaming_ai_idx
            if 0 <= idx < len(self._chat_log):
                self._chat_log[idx]['content'] = response

        if elapsed > 0 and chunks:
            speed = len(response) / elapsed
            self._add_status(f"📊 {len(response)} chars | {speed:.0f} ch/s | {elapsed:.1f}s")

        self._streaming_ai_idx = -1
        self._is_streaming = False
        self._render_chat()
        self._update_stop_btn(False)
        self._cleanup_thread()

    def _on_error(self, err: str):
        self._is_streaming = False
        self._log(f"❌ Error: {err}")
        self._render_chat()
        QMessageBox.critical(self, "Error", err)
        self._update_stop_btn(False)
        self._cleanup_thread()

    def _stop_or_reload(self):
        if self.thread and self.thread.isRunning():
            self._is_streaming = False
            self._log("⚠️ Stopped.")
            self._render_chat()
            self._update_stop_btn(False)
            self._cleanup_thread()   # stop + wait + disconnect + deleteLater
        elif self.last_prompt and self.current_conv_id:
            self.input.setPlainText(self.last_prompt)
            self._send()
        else:
            self._log("ℹ️ Nothing to reload.")

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
            t = self.thread
            self.thread = None          # clear ref first so late signals are ignored
            # Disconnect all signals to prevent stale callbacks after cleanup
            try: t.token.disconnect()
            except Exception: pass
            try: t.finished.disconnect()
            except Exception: pass
            try: t.error.disconnect()
            except Exception: pass
            try: t.status.disconnect()
            except Exception: pass
            # Signal the thread to stop, then wait up to 3 s before giving up
            t.stop()
            if not t.wait(3000):        # 3 second graceful timeout
                t.terminate()
                t.wait(1000)
            t.deleteLater()

    # ================================================================ #
    #  GROQ API TOGGLE                                                   #
    # ================================================================ #
    # ================================================================ #
    #  PERSISTENT SETTINGS                                               #
    # ================================================================ #
    def _load_settings(self) -> None:
        """Load persisted settings from ~/.ollama_gui/settings.json."""
        if not os.path.isfile(_SETTINGS_FILE):
            return
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "dark" in data:
                self.dark = bool(data["dark"])
            if data.get("groq_api_key"):
                self.groq_api_key = data["groq_api_key"]
            self._saved_model = data.get("selected_model", "")
        except Exception:
            pass  # corrupt file — keep defaults, will be overwritten on next save

    def _restore_saved_model(self) -> None:
        """After populating model_box, select the last-used model if present."""
        if not self._saved_model:
            return
        idx = self.model_box.findText(self._saved_model)
        if idx >= 0:
            self.model_box.blockSignals(True)
            self.model_box.setCurrentIndex(idx)
            self.model_box.blockSignals(False)

    def _on_model_changed(self) -> None:
        """Persist the newly selected model immediately."""
        self._save_settings()

    def _save_settings(self) -> None:
        """Persist current theme and Groq API key to ~/.ollama_gui/settings.json."""
        os.makedirs(_CONFIG_DIR, exist_ok=True)
        data = {
            "dark": self.dark,
            "groq_api_key": self.groq_api_key,
            "selected_model": self.model_box.currentText(),
        }
        try:
            with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            self._log(f"⚠️ Could not save settings: {exc}\n")

    def _clear_groq_key(self) -> None:
        """Clear the saved Groq API key from memory and disk."""
        self.groq_api_key = ""
        self.groq_key_input.clear()
        self._save_settings()
        self._log("🗑 Groq API key cleared.\n")

    def _toggle_api_mode(self):
        """Switch between Local Ollama and Groq API mode."""
        self.api_mode = not self.api_mode

        # Update toggle button appearance
        self.api_toggle_btn.setProperty("api", "true" if self.api_mode else "false")
        self.api_toggle_btn.style().unpolish(self.api_toggle_btn)
        self.api_toggle_btn.style().polish(self.api_toggle_btn)

        if self.api_mode:
            self.api_toggle_btn.setText("☁️ Groq API")
            self.groq_row.setVisible(True)
            # Hide server button in API mode — not needed
            self.server_btn.setVisible(False)
            self.mgr_btn.setVisible(False)
            self._log("☁️ Switched to Groq API mode.\n")
        else:
            self.api_toggle_btn.setText("🖥️ Local")
            self.groq_row.setVisible(False)
            self.server_btn.setVisible(True)
            self.mgr_btn.setVisible(True)
            self._log("🖥️ Switched to Local Ollama mode.\n")

        self._load_models()
        self._check_server_state()

    def _apply_groq_key(self):
        """Validate and save Groq API key, then reload model list."""
        key = self.groq_key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "Groq API Key", "Please enter your Groq API key.")
            return

        self.groq_save_btn.setEnabled(False)
        self.groq_save_btn.setText("⏳ Checking…")
        self._log("🔑 Validating Groq API key…\n")

        # Force the UI to repaint before the blocking HTTP call
        QApplication.processEvents()

        try:
            ok, err = GroqClient(api_key=key).validate_key()
        except Exception as e:
            ok, err = False, str(e)

        self.groq_save_btn.setEnabled(True)
        self.groq_save_btn.setText("✔ Apply")

        if ok:
            self.groq_api_key = key
            self._save_settings()
            self._log("✅ Groq API key valid — saved and models reloaded.\n")
            self._load_models()
        else:
            self._log(f"❌ Groq key error: {err}\n")
            QMessageBox.critical(self, "Groq API Key Error", err)

    # ================================================================ #
    #  OLLAMA SERVER TOGGLE                                              #
    # ================================================================ #
    def _check_server_state(self):
        """Check if Ollama is running and update button + UI accordingly."""
        if self.api_mode:
            # In API mode server state doesn't matter for chat
            self._set_ui_server_state(True)
            return
        running = self._client.is_running()
        self._server_running = running
        self._update_server_btn()
        self._set_ui_server_state(running)

    def _set_ui_server_state(self, running: bool):
        """Enable/disable ALL interactive widgets based on server state."""
        # In API mode always treat as "running" for chat widgets
        effective = running or self.api_mode

        # Right panel — chat
        self.send_btn.setEnabled(effective)
        self.stop_btn.setEnabled(effective and bool(self.last_prompt))
        self.input.setEnabled(effective)
        self.model_box.setEnabled(effective)
        self.mode_btn.setEnabled(effective)
        self.crew_btn.setEnabled(effective)
        self.attach_btn.setEnabled(effective)   # always enabled when server is up
        if not effective:
            self.input.setPlaceholderText(
                "⏸ Ollama server stopped — press [Server: OFF] to start"
            )
        elif self.api_mode:
            self.input.setPlaceholderText("Type your message… (Groq API — Ctrl+Enter to send)")
        else:
            self.input.setPlaceholderText("Type your message… (Ctrl+Enter to send)")

        # Left panel — RAG
        # Embedding: Ollama models (contain ':') need local server.
        # sentence-transformers models work without Ollama → allow in API mode.
        embed_model = self.embed_box.currentText() if self.embed_box.count() else ""
        embed_needs_server = ":" in embed_model   # Ollama model names contain ':'
        rag_available = running or (self.api_mode and not embed_needs_server)
        self.rag_file_btn.setEnabled(rag_available)
        self.rag_folder_btn.setEnabled(rag_available)
        self.embed_box.setEnabled(rag_available)
        has_index = self._rag_has_data()
        self.clear_rag_btn.setEnabled(rag_available and has_index)
        self.rm_doc_btn.setEnabled(rag_available and has_index)

        # Left panel — Crews & manager
        self.new_crew_btn.setEnabled(effective)
        self.tmpl_btn.setEnabled(effective)
        self.mgr_btn.setEnabled(running)

    def _poll_server(self):
        """
        Called every 5s by QTimer.
        Runs is_running() in a background thread so the UI never freezes.
        On state change: updates button, reloads models, toggles chat UI.
        """
        if self.api_mode:
            return   # no Ollama polling needed in API mode
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
    # ================================================================ #
    #  CHAT DISPLAY MANAGEMENT                                          #
    # ================================================================ #
    def _add_user_msg(self, prompt: str):
        """Add a user message bubble and re-render."""
        self._chat_log.append({'type': 'user', 'content': prompt})
        self._render_chat()

    def _start_ai_msg(self, label: str = "🤖 AI"):
        """Add an empty AI bubble and record its index for streaming."""
        self._is_streaming = True
        self._streaming_ai_idx = len(self._chat_log)
        self._chat_log.append({'type': 'ai', 'content': '', 'label': label})
        self._render_chat()

    def _add_status(self, text: str):
        """Add a status / info line (small italic)."""
        clean = text.strip()
        if not clean:
            return
        self._chat_log.append({'type': 'status', 'content': clean})
        if not self._is_streaming:
            self._render_chat()

    def _log(self, text: str):
        """Legacy helper — routes to _add_status."""
        self._add_status(text)

    def _render_chat(self):
        """Full HTML re-render of the entire chat log."""
        self._code_store = []
        html = chat_html(self._chat_log, self._code_store, self.dark)
        sb = self.chat.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 40
        self.chat.setHtml(html)
        if at_bottom or self._is_streaming:
            self.chat.verticalScrollBar().setValue(
                self.chat.verticalScrollBar().maximum()
            )

    def _on_anchor_clicked(self, url: QUrl):
        """Handle copy:N and run:N anchor clicks from code block buttons."""
        s = url.toString()

        if s.startswith('copy:'):
            try:
                idx = int(s[5:])
                if 0 <= idx < len(self._code_store):
                    QApplication.clipboard().setText(self._code_store[idx])
                    self._add_status("📋 Copied to clipboard")
                    self._render_chat()
            except ValueError:
                pass

    def _clear_chat_display(self):
        """Clear visible chat while keeping conversation in DB."""
        self._chat_log = []
        self._code_store = []
        self._is_streaming = False
        self.chat.clear()

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

    # Set application-level font with Bengali fallback chain.
    # Using QApplication.setFont() is the only reliable way to enable
    # non-Latin glyph rendering in all widgets (including QTextEdit input).
    _db = QFontDatabase()
    _families = set(_db.families())
    _BENGALI_CHAIN = [
        "Noto Sans Bengali", "Noto Serif Bengali",
        "Kalpurush", "SolaimanLipi", "Lohit Bengali",
        "FreeSans", "FreeSerif", "Unifont",
        "DejaVu Sans",
    ]
    _primary = next((f for f in _BENGALI_CHAIN if f in _families), "")
    _app_font = QFont(_primary, 14)   # size overridden by QSS per-widget
    _app_font.setStyleHint(QFont.SansSerif)
    try:
        _app_font.setFamilies([f for f in _BENGALI_CHAIN if f in _families] or ["Sans Serif"])
    except AttributeError:
        pass
    app.setFont(_app_font)

    win = OllamaGUI()
    win.show()
    sys.exit(app.exec_())
