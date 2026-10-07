"""Unit tests for gui/flow_layout.py (wrapping QLayout)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtWidgets import QApplication, QWidget

from flow_layout import FlowLayout

_APP = None


def _app():
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
    return app or _APP


def _parented_flow():
    """A FlowLayout on a container widget, like the real top bar."""
    _app()
    host = QWidget()
    fl = FlowLayout(host)
    fl.setContentsMargins(0, 0, 0, 0)
    fl.setHorizontalSpacing(0)
    fl.setVerticalSpacing(0)
    return host, fl


def test_has_height_for_width():
    _, fl = _parented_flow()
    assert fl.hasHeightForWidth()


def test_minimum_size_is_widest_single_item_not_sum():
    host, fl = _parented_flow()
    wide = QWidget(host)
    wide.setFixedSize(400, 40)
    narrow = QWidget(host)
    narrow.setFixedSize(100, 40)
    fl.addWidget(wide)
    fl.addWidget(narrow)
    width = fl.minimumSize().width()
    assert width >= 400              # widest single item
    assert width < 500               # NOT the sum of all items


def test_height_for_width_wraps_to_more_rows():
    host, fl = _parented_flow()
    for _ in range(4):
        w = QWidget(host)
        w.setFixedSize(120, 40)
        fl.addWidget(w)
    single_row = fl.heightForWidth(600)      # all four on one row
    two_row = fl.heightForWidth(150)         # 120+120 > 150 → wraps
    assert single_row <= 40
    assert two_row > single_row


def test_set_geometry_wraps_items_onto_new_rows():
    host, fl = _parented_flow()
    ws = []
    for _ in range(3):
        w = QWidget(host)
        w.setFixedSize(120, 40)
        fl.addWidget(w)
        ws.append(w)
    host.resize(150, 100)
    fl.setGeometry(host.rect())
    assert ws[0].geometry().x() == 0 and ws[0].geometry().y() == 0
    # 120+120 = 240 > 150 → second item wraps to row 2
    assert ws[1].geometry().y() > ws[0].geometry().y()
    assert ws[2].geometry().y() > ws[0].geometry().y()
