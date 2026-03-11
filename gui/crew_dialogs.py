#!/usr/bin/env python3
"""
Crew configuration dialog + built-in templates.
"""
import json

from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QTextEdit, QVBoxLayout, QWidget,
)

CREW_TEMPLATES = [
    {"name": "Research Crew", "config": [
        {"role": "Researcher",  "model": "llama3.2:latest",
         "system_prompt": "You are an expert researcher.",
         "input_prompt": "Research this topic thoroughly:\n{previous}"},
        {"role": "Analyst",     "model": "llama3.2:latest",
         "system_prompt": "Analyse the findings critically.",
         "input_prompt": "{previous}"},
        {"role": "Report Writer", "model": "llama3.2:latest",
         "system_prompt": "Write a clear, professional report.",
         "input_prompt": "{previous}"},
    ]},
    {"name": "Coding Crew", "config": [
        {"role": "Architect", "model": "llama3.2:latest",
         "system_prompt": "Design a clean solution architecture.",
         "input_prompt": "Design a plan for:\n{previous}"},
        {"role": "Coder",    "model": "llama3.2:latest",
         "system_prompt": "Write clean, efficient code.",
         "input_prompt": "{previous}"},
        {"role": "Reviewer", "model": "llama3.2:latest",
         "system_prompt": "Review code and suggest improvements.",
         "input_prompt": "{previous}"},
    ]},
    {"name": "Writing Crew", "config": [
        {"role": "Outliner", "model": "llama3.2:latest",
         "system_prompt": "Create a detailed outline.",
         "input_prompt": "Outline: {previous}"},
        {"role": "Drafter",  "model": "llama3.2:latest",
         "system_prompt": "Write the first draft.",
         "input_prompt": "{previous}"},
        {"role": "Editor",   "model": "llama3.2:latest",
         "system_prompt": "Edit for clarity and style.",
         "input_prompt": "{previous}"},
    ]},
]


class CrewConfigDialog(QDialog):
    def __init__(self, models: list[str], config=None,
                 crew_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Crew" if config else "Create Crew")
        self.setMinimumSize(900, 600)
        self.models = models
        self._agent_widgets: list[dict] = []

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Crew Name:</b>"))
        self.name_edit = QLineEdit(crew_name)
        layout.addWidget(self.name_edit)

        add_btn = QPushButton("➕ Add Agent")
        add_btn.clicked.connect(lambda: self.add_agent())
        layout.addWidget(add_btn)

        scroll = QScrollArea()
        self._scroll_widget = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_widget)
        scroll.setWidget(self._scroll_widget)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        for agent in (config or [{}]):
            self.add_agent(agent)

    def add_agent(self, preset: dict | None = None):
        frame = QWidget()
        frame.setStyleSheet(
            "background:#222;border:1px solid #555;"
            "border-radius:8px;padding:10px;margin:6px;"
        )
        form = QFormLayout(frame)

        role   = QLineEdit()
        role.setPlaceholderText("e.g. Researcher")
        model  = QComboBox()
        model.addItems(self.models or ["llama3.2:latest"])
        sys_p  = QTextEdit(); sys_p.setFixedHeight(70)
        inp    = QTextEdit(); inp.setFixedHeight(110)
        inp.setPlaceholderText("Use {previous} to pass prior output")

        if preset:
            role.setText(preset.get("role", ""))
            txt = preset.get("model", "")
            if txt:
                model.setCurrentText(txt)
            sys_p.setPlainText(preset.get("system_prompt", ""))
            inp.setPlainText(preset.get("input_prompt", "{previous}"))

        form.addRow("Role:",           role)
        form.addRow("Model:",          model)
        form.addRow("System Prompt:",  sys_p)
        form.addRow("Input Template:", inp)

        # Remove button
        rm_btn = QPushButton("🗑 Remove Agent")
        rm_btn.setFixedHeight(32)

        def _remove():
            self._agent_widgets = [w for w in self._agent_widgets if w["frame"] is not frame]
            frame.setParent(None)
            frame.deleteLater()

        rm_btn.clicked.connect(_remove)
        form.addRow("", rm_btn)

        rec = {"role": role, "model": model, "sys": sys_p,
               "inp": inp, "frame": frame}
        self._agent_widgets.append(rec)
        self._scroll_layout.addWidget(frame)

    def get_crew_data(self) -> tuple[str | None, list | None]:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Crew name is required.")
            return None, None

        config = []
        for w in self._agent_widgets:
            role = w["role"].text().strip()
            inp  = w["inp"].toPlainText().strip()
            if not role:
                QMessageBox.warning(self, "Error", "Every agent needs a role.")
                return None, None
            if "{previous}" not in inp:
                r = QMessageBox.question(
                    self, "Warning",
                    f"Agent '{role}' has no {{previous}} placeholder. Continue?"
                )
                if r == QMessageBox.No:
                    return None, None
            config.append({
                "role":          role,
                "model":         w["model"].currentText(),
                "system_prompt": w["sys"].toPlainText().strip(),
                "input_prompt":  inp,
            })
        return name, config
