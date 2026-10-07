"""FlowLayout — a wrapping QLayout (port of Qt's Flow Layout example).

Items are laid out left-to-right and wrap onto the next line when there is
not enough width, so a toolbar/top bar never overflows the window.
minimumSize() is the widest single item (plus margins), never the sum of all
items — that is what keeps the layout from forcing the window wider than the
screen.
"""

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QStyle


class FlowLayout(QLayout):
    def __init__(self, parent=None, h_spacing: int = -1,
                 v_spacing: int = -1, margins: tuple[int, int, int, int] | None = None):
        super().__init__(parent)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items = []
        if margins is not None:
            self.setContentsMargins(*margins)

    def __del__(self):
        while self._items:
            item = self._items.pop()
            if item.widget():
                item.widget().deleteLater()

    def addItem(self, item):  # noqa: D102
        self._items.append(item)

    def count(self):  # noqa: D102
        return len(self._items)

    def itemAt(self, index):  # noqa: D102
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):  # noqa: D102
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: D102
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self):  # noqa: D102
        return True

    def heightForWidth(self, width):  # noqa: D102
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):  # noqa: D102
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):  # noqa: D102
        return self.minimumSize()

    def minimumSize(self):  # noqa: D102
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def setHorizontalSpacing(self, px: int):  # noqa: D102
        self._h_space = px

    def setVerticalSpacing(self, px: int):  # noqa: D102
        self._v_space = px

    def horizontal_spacing(self) -> int:
        if self._h_space >= 0:
            return self._h_space
        return self._smart_spacing(
            QStyle.PixelMetric.PM_LayoutHorizontalSpacing)

    def vertical_spacing(self) -> int:
        if self._v_space >= 0:
            return self._v_space
        return self._smart_spacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)

    def _smart_spacing(self, metric: QStyle.PixelMetric) -> int:
        parent = self.parentWidget()
        if parent is None:
            return -1
        try:
            return parent.style().pixelMetric(metric, None, parent)
        except RuntimeError:      # parent destroyed mid-teardown
            return -1

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        h_space = max(self.horizontal_spacing(), 0)
        v_space = max(self.vertical_spacing(), 0)
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + h_space
            if (next_x - h_space > effective.right() + 1 and line_height > 0):
                x = effective.x()
                y += line_height + v_space
                next_x = x + hint.width() + h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()
