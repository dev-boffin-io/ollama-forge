#!/usr/bin/env python3
"""
Notes Panel — create, edit, delete, search personal notes.
Accessible from the left drawer.  Talks back to OllamaGUI via signals.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)


class NoteEditorDialog(QDialog):
    """Simple create/edit dialog for a single note."""

    def __init__(self, parent=None, title: str = "", content: str = "", tags: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Edit Note")
        self.setMinimumSize(700, 500)

        v = QVBoxLayout(self)
        v.setSpacing(10)

        # Title
        v.addWidget(QLabel("Title:"))
        self.title_edit = QLineEdit(title)
        self.title_edit.setMinimumHeight(52)
        self.title_edit.setPlaceholderText("Note title…")
        v.addWidget(self.title_edit)

        # Tags
        v.addWidget(QLabel("Tags (comma-separated):"))
        self.tags_edit = QLineEdit(tags)
        self.tags_edit.setMinimumHeight(44)
        self.tags_edit.setPlaceholderText("e.g. work, ideas, personal")
        v.addWidget(self.tags_edit)

        # Content
        v.addWidget(QLabel("Content:"))
        self.content_edit = QTextEdit()
        self.content_edit.setPlaceholderText("Write your note here…")
        self.content_edit.setPlainText(content)
        self.content_edit.setMinimumHeight(280)
        v.addWidget(self.content_edit, 1)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def get_data(self) -> tuple[str, str, str]:
        return (
            self.title_edit.text().strip() or "Untitled",
            self.content_edit.toPlainText().strip(),
            self.tags_edit.text().strip(),
        )


class NotesPanel(QWidget):
    """
    Embedded notes panel shown inside the left drawer.
    Emits note_to_chat(str) so the main window can paste
    a note's content as context for the LLM.
    """
    note_to_chat = pyqtSignal(str)   # sends note text → chat input

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._notes: list[dict] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # ── Search bar ───────────────────────────────────────────────
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("🔍 Search notes…")
        self.search_edit.setMinimumHeight(50)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        search_row.addWidget(self.search_edit, 1)
        v.addLayout(search_row)

        # ── Note list ────────────────────────────────────────────────
        self.note_list = QListWidget()
        self.note_list.setMinimumHeight(200)
        self.note_list.itemDoubleClicked.connect(self._edit_selected)
        self.note_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.note_list.customContextMenuRequested.connect(self._note_menu)
        v.addWidget(self.note_list, 1)

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self.new_btn = QPushButton("➕ New Note")
        self.new_btn.setMinimumHeight(56)
        self.new_btn.clicked.connect(self._new_note)
        btn_row.addWidget(self.new_btn)

        self.send_btn = QPushButton("📤 Use in Chat")
        self.send_btn.setMinimumHeight(56)
        self.send_btn.setToolTip("Send selected note to chat as context")
        self.send_btn.clicked.connect(self._send_to_chat)
        btn_row.addWidget(self.send_btn)

        v.addLayout(btn_row)

        self.refresh()

    # ── Data ────────────────────────────────────────────────────────

    def refresh(self):
        self._notes = self.db.list_notes()
        self._render(self._notes)

    def _render(self, notes: list[dict]):
        self.note_list.clear()
        for n in notes:
            tags = f"  [{n['tags']}]" if n.get("tags") else ""
            date = (n.get("updated_at") or "")[:10]
            item = QListWidgetItem(f"📝 {n['title']}{tags}  —  {date}")
            item.setData(Qt.ItemDataRole.UserRole, n["id"])
            self.note_list.addItem(item)

    def _on_search(self, text: str):
        if text.strip():
            self._render(self.db.search_notes(text.strip()))
        else:
            self._render(self._notes)

    # ── Actions ─────────────────────────────────────────────────────

    def _new_note(self):
        dlg = NoteEditorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            title, content, tags = dlg.get_data()
            self.db.create_note(title, content, tags)
            self.refresh()

    def _edit_selected(self, item: QListWidgetItem | None = None):
        note_id = self._selected_id()
        if note_id is None:
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        dlg = NoteEditorDialog(self, note["title"], note["content"], note["tags"] or "")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            title, content, tags = dlg.get_data()
            self.db.update_note(note_id, title, content, tags)
            self.refresh()

    def _delete_selected(self):
        note_id = self._selected_id()
        if note_id is None:
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        if QMessageBox.question(
            self, "Delete Note",
            f"Delete «{note['title']}»?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.db.delete_note(note_id)
            self.refresh()

    def _send_to_chat(self):
        note_id = self._selected_id()
        if note_id is None:
            QMessageBox.information(self, "Notes", "Select a note first.")
            return
        note = self.db.get_note(note_id)
        if not note:
            return
        text = f"[Note: {note['title']}]\n{note['content']}"
        self.note_to_chat.emit(text)

    def _note_menu(self, pos):
        item = self.note_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.addAction("✏️ Edit",   self._edit_selected)
        menu.addAction("📤 Use in Chat", self._send_to_chat)
        menu.addSeparator()
        menu.addAction("🗑 Delete", self._delete_selected)
        menu.exec(self.note_list.mapToGlobal(pos))

    def _selected_id(self) -> int | None:
        item = self.note_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)
