#!/usr/bin/env python3
"""
manager_entry.py — entry point for the Ollama-ai-manager binary.
PyInstaller targets this file with --name Ollama-ai-manager.
Fully isolated: no system site-packages injection.
"""
import sys

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from ollama_manager import OllamaManager


def main() -> None:
    app = QApplication(sys.argv)

    _families = set(QFontDatabase.families())
    _BENGALI_CHAIN = [
        "Noto Sans Bengali", "Noto Serif Bengali",
        "Kalpurush", "SolaimanLipi", "Lohit Bengali",
        "FreeSans", "FreeSerif", "Unifont", "DejaVu Sans",
    ]
    _primary  = next((f for f in _BENGALI_CHAIN if f in _families), "")
    _app_font = QFont(_primary, 14)
    _app_font.setStyleHint(QFont.StyleHint.SansSerif)
    try:
        _app_font.setFamilies(
            [f for f in _BENGALI_CHAIN if f in _families] or ["Sans Serif"]
        )
    except AttributeError:
        pass
    app.setFont(_app_font)

    win = OllamaManager()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
