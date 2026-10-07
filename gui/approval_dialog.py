#!/usr/bin/env python3
"""
approval_dialog.py — approve / always / deny a destructive agent tool call.

Shown by the main window when AgentWorker requests approval. It never blocks
the Qt event loop: the window uses `show()` and the dialog emits `decided`
when a button is pressed, which unblocks the worker.
"""
import html

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

import agent_bridge

_DARK = {
    "add": "#7ee787", "del": "#ff7b72", "ctx": "#c9d1d9",
    "meta": "#8b949e", "bg": "#161b22", "fg": "#e6edf3",
    "border": "#30363d",
}
_LIGHT = {
    "add": "#1a7f37", "del": "#cf222e", "ctx": "#1f2328",
    "meta": "#57606a", "bg": "#ffffff", "fg": "#1f2328",
    "border": "#d0d7de",
}


def diff_to_html(diff: str, dark: bool = True) -> str:
    """Colour a unified diff for a QPlainTextEdit/QTextEdit (no JS needed)."""
    c = _DARK if dark else _LIGHT
    rows = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            color = c["add"]
        elif line.startswith("-") and not line.startswith("---"):
            color = c["del"]
        elif line.startswith(("@@", "+++", "---")):
            color = c["meta"]
        else:
            color = c["ctx"]
        rows.append(
            f'<span style="color:{color};white-space:pre;">'
            f'{html.escape(line)}</span>'
        )
    return (
        f'<pre style="background:{c["bg"]};color:{c["fg"]};'
        f'margin:0;font-family:monospace;">' + "\n".join(rows) + "</pre>"
    )


class ApprovalDialog(QDialog):
    """Non-blocking approval prompt for a pending destructive tool call."""

    decided = pyqtSignal(bool, bool, str)   # approve, always, reason

    def __init__(self, name: str, args: dict, workdir: str, *,
                 dark: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Approve: {name}")
        self.setModal(True)
        self._name = name
        self._dark = dark
        self._resolved = False

        info = agent_bridge.pending_change(name, args, workdir)

        root = QVBoxLayout(self)

        head = QLabel(f"<b>{html.escape(name)}</b>")
        root.addWidget(head)

        if info.get("path"):
            root.addWidget(QLabel(f"<span>path: {html.escape(info['path'])}</span>"))

        if info["kind"] == "command":
            view = QTextEdit(info["command"])
            view.setReadOnly(True)
            view.setFont(QFont("monospace", 11))
            view.setMinimumHeight(80)
            root.addWidget(view)
        else:
            view = QTextEdit()
            view.setReadOnly(True)
            view.setFont(QFont("monospace", 11))
            view.setMinimumHeight(240)
            view.setHtml(diff_to_html(info["diff"] or "(no changes previewed)", dark))
            root.addWidget(view)

        if info.get("note"):
            root.addWidget(QLabel(f"<i>{html.escape(info['note'])}</i>"))

        reason_row = QHBoxLayout()
        reason_row.addWidget(QLabel("Reason (optional, for Deny):"))
        self.reason_edit = QLineEdit()
        reason_row.addWidget(self.reason_edit)
        root.addLayout(reason_row)

        buttons = QHBoxLayout()
        self.approve_btn = QPushButton("Approve")
        self.always_btn = QPushButton("Always")
        self.deny_btn = QPushButton("Deny")
        self.approve_btn.clicked.connect(self._approve)
        self.always_btn.clicked.connect(self._always)
        self.deny_btn.clicked.connect(self._deny)
        buttons.addWidget(self.approve_btn)
        buttons.addWidget(self.always_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.deny_btn)
        root.addLayout(buttons)

    # ── slots (called by the buttons, or directly by tests) ──
    def _finish(self, approve: bool, always: bool) -> None:
        if self._resolved:
            return
        self._resolved = True
        self.decided.emit(bool(approve), bool(always), self.reason_edit.text().strip())
        self.accept() if approve else self.reject()
        self.close()

    def _approve(self) -> None:
        self._finish(True, False)

    def _always(self) -> None:
        self._finish(True, True)

    def _deny(self) -> None:
        self._finish(False, False)
