"""The look shared by every window: the panel-like style sheet, the dialog frame and small widget factories."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..config import REPO_ROOT

ASSETS = REPO_ROOT / "assets"
NOTIFY_ICON = str(ASSETS / "tray.svg")

# Look of the desktop panel's popups: compact rounded cards on the window colour, the accent only on the main action.
# Colours come from the palette only (the desktop's colour scheme through the Qt platform theme), never hard-coded.
STYLE = """
QFrame#card { background: palette(button); border-radius: 12px; }
QLabel#title { font-size: 14pt; font-weight: 600; }
QLabel#subtitle, QLabel#reason { color: palette(placeholder-text); }
QLabel#rowTitle { font-weight: 600; }
QPushButton#primary { background: palette(highlight); color: palette(highlighted-text); border: none;
                      border-radius: 8px; padding: 5px 16px; font-weight: 600; }
QPushButton#primary:disabled { background: palette(mid); }
QTextEdit { background: palette(base); border: none; border-radius: 12px; padding: 8px; }
QFrame#segments { background: palette(button); border-radius: 9px; }
QPushButton#segment { background: transparent; border: none; border-radius: 7px; padding: 4px 12px; }
QPushButton#segment:checked { background: palette(base); font-weight: 600; }
QTreeWidget { background: palette(base); border: none; border-radius: 12px; padding: 4px 6px; }
QTreeWidget::item { padding: 3px 0; }
QHeaderView::section { background: palette(base); color: palette(placeholder-text); border: none;
                       padding: 4px 6px; font-weight: 600; }
"""


class StyledDialog(QDialog):
    """Common frame of the app's windows: stays on top, panel-like style, margins and spacing of the popups."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(STYLE)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 16, 16, 16)
        self.layout_.setSpacing(10)

    def add_header(self, title: str, subtitle: str = "") -> QLabel:
        """Icon, title and a dimmed subtitle (returned, so it can be updated)."""
        header = QHBoxLayout()
        header.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(QIcon(NOTIFY_ICON).pixmap(28, 28))
        header.addWidget(icon)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        title_label = QLabel(title)
        title_label.setObjectName("title")
        sub = QLabel(subtitle)
        sub.setObjectName("subtitle")
        texts.addWidget(title_label)
        texts.addWidget(sub)
        header.addLayout(texts, 1)
        self.layout_.addLayout(header)
        return sub

    def add_buttons(self, *buttons: QPushButton) -> None:
        """Buttons at the bottom right."""
        row = QHBoxLayout()
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.layout_.addLayout(row)
        match_heights(*buttons)


def match_heights(*buttons: QPushButton) -> None:
    """The accent button has its own padding; keep it as tall as the native buttons next to it."""
    height = max(b.sizeHint().height() for b in buttons)
    for b in buttons:
        b.setMinimumHeight(height)


def make_segments(options: Iterable[tuple[str, str]], on_pick: Callable[[str], None],
                  parent: QWidget) -> tuple[QFrame, dict[str, QPushButton]]:
    """A segmented control (exclusive checkable buttons in a rounded frame); the first option starts checked."""
    frame = QFrame()
    frame.setObjectName("segments")
    row = QHBoxLayout(frame)
    row.setContentsMargins(2, 2, 2, 2)
    row.setSpacing(2)
    group = QButtonGroup(parent)
    buttons: dict[str, QPushButton] = {}
    for i, (key, label) in enumerate(options):
        b = QPushButton(label)
        b.setObjectName("segment")
        b.setCheckable(True)
        b.setChecked(i == 0)
        b.clicked.connect(lambda _c=False, key=key: on_pick(key))
        group.addButton(b)
        buttons[key] = b
        row.addWidget(b)
    return frame, buttons


def make_tree(columns: Iterable[str]) -> QTreeWidget:
    """Flat list look: no selection, the first column stretches, the others fit their content."""
    columns = list(columns)
    tree = QTreeWidget()
    tree.setColumnCount(len(columns))
    tree.setHeaderLabels(columns)
    tree.setRootIsDecorated(False)
    tree.setUniformRowHeights(True)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setSectionsMovable(False)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for col in range(1, len(columns)):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    toggle_on_row_click(tree)
    return tree


def toggle_on_row_click(tree: QTreeWidget) -> None:
    """A click anywhere on a checkable row toggles its box, not only on the small box itself. The state is
    remembered on press: when Qt already toggled it (the click hit the box), the row is left alone."""
    pressed: dict[str, object] = {}

    def on_pressed(item: QTreeWidgetItem, _column: int) -> None:
        pressed["item"], pressed["state"] = item, item.checkState(0)

    def on_clicked(item: QTreeWidgetItem, _column: int) -> None:
        checkable = bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.flags() & Qt.ItemFlag.ItemIsEnabled)
        if not checkable or pressed.get("item") is not item or item.checkState(0) != pressed.get("state"):
            return
        checked = item.checkState(0) == Qt.CheckState.Checked
        item.setCheckState(0, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)

    tree.itemPressed.connect(on_pressed)
    tree.itemClicked.connect(on_clicked)


def make_busy() -> QProgressBar:
    """An indeterminate busy bar, hidden until something runs."""
    busy = QProgressBar()
    busy.setRange(0, 0)
    busy.setFixedWidth(90)
    busy.setTextVisible(False)
    busy.hide()
    return busy


def dim_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("subtitle")
    label.setWordWrap(True)
    return label
