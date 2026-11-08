"""System tray application: icon, menu, periodic cycles, notification handling, dialogs. Qt only lives here."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from datetime import datetime
from typing import Callable, Iterable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar,
                               QPushButton, QSystemTrayIcon, QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .actions import effective_mode, needs_confirmation
from .checks import run_all
from .config import REPO_ROOT, Config, check_env, set_action_mode
from .cycle import Cycle, CycleResult, Pending, merge_findings
from .catalog import MIN_TERM, Result, SearchResult, normalize, search
from .inventory import PROTECTED_REASON, Installed, Inventory, collect
from .model import Action, ActionResult, Finding, Summary
from .notify import BUTTONS_ASK, URGENCY_CRITICAL, URGENCY_NORMAL, Notifier, result_popup
from .single_instance import InstanceLock
from .snapshots import snapshot_info
from .state import State
from .summarizer import template_summary

ASSETS = REPO_ROOT / "assets"
ICONS = {"idle": "tray.svg", "attention": "tray-attention.svg", "busy": "tray-busy.svg"}
NOTIFY_ICON = str(ASSETS / "tray.svg")
EXAMPLE_CONFIG = ASSETS / "config.example.toml"
BUTTON_ACTIONS: list[tuple[str, str]] = [("actions", "Show actions")]
# Updates are never run straight from a notification: the button opens the Updates tab
BUTTONS_UPDATES: list[tuple[str, str]] = [("updates", "Show updates"), ("no", "Later")]
AUTO_LABEL = "Next time run automatically, just notify"

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


def format_item(item: dict) -> str:
    """One line per finding item: 'name  old → new  [source]' for package-like items (⚠ = critical),
    otherwise the item's key=value pairs."""
    if {"name", "new"} <= item.keys():
        mark = "⚠ " if item.get("critical") else "  "
        source = f"  [{item['source']}]" if item.get("source") else ""
        return f"{mark}{item['name']}  {item.get('old') or '?'} → {item['new']}{source}"
    return ", ".join(f"{k}={v}" for k, v in item.items())


def _item_order(item: dict) -> tuple:
    return (not item.get("critical"), str(item.get("source", "")), str(item.get("name", "")))


class PendingDialog(StyledDialog):
    """One card per open action: label (+ reason), 'next time automatically' box, Details, Run, No.

    The window stays open while an action runs: the card shows a busy bar, disappears on success
    and gets its buttons back on failure (the result itself is reported by the usual notification).
    """

    def __init__(self, pending: list[Pending], on_run: Callable[[Pending, bool], None],
                 on_decline: Callable[[Pending], None], on_updates: Callable[[Pending], None] | None = None,
                 parent=None):
        super().__init__("Arch Assistant – pending actions", parent)
        self.on_run, self.on_decline = on_run, on_decline
        self.on_updates = on_updates   # Details of the `update` action open the Updates tab instead of the item list
        self.rows: dict[tuple, dict] = {}
        self.labels: list[QLabel] = []
        self.auto_boxes: list[QCheckBox] = []
        self.run_buttons: list[QPushButton] = []
        self.decline_buttons: list[QPushButton] = []
        self.details_buttons: list[QPushButton] = []
        self.details_window: ItemsWindow | None = None
        self.subtitle = self.add_header("Pending actions")
        self.empty_label = QLabel("No pending actions.")
        self.empty_label.setObjectName("subtitle")
        self.layout_.addWidget(self.empty_label)
        self.empty_label.setVisible(not pending)
        for p in pending:
            self._add_row(p)
        self._update_subtitle()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        self.add_buttons(close)
        self.resize(700, 150 + 110 * len(pending))

    def _update_subtitle(self) -> None:
        n = len(self.rows)
        self.subtitle.setText(f"{n} waiting for your decision" if n else "Everything is done")

    def _add_row(self, p: Pending) -> None:
        container = QFrame()
        container.setObjectName("card")
        row = QHBoxLayout(container)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(8)
        label = QLabel(p.label + (f"\n⚠ {p.reason}" if p.reason else ""))
        label.setObjectName("rowTitle")
        label.setWordWrap(True)
        auto = QCheckBox(AUTO_LABEL)
        auto.setToolTip("Sets this action to `auto`: it runs without asking and the result pops up in the tray.")
        busy = QProgressBar()
        busy.setRange(0, 0)
        busy.setFixedWidth(90)
        busy.setTextVisible(False)
        busy.hide()
        run = QPushButton("Run")
        run.setObjectName("primary")
        no = QPushButton("No")
        run.clicked.connect(lambda _c=False, p=p, auto=auto: self._run_clicked(p, auto.isChecked()))
        no.clicked.connect(lambda _c=False, p=p: self._decline_clicked(p))
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(label)
        texts.addWidget(auto)
        row.addLayout(texts, 1)
        row.addWidget(busy)
        self.rows[p.key] = {"widget": container, "label": label, "auto": auto, "busy": busy,
                            "run": run, "no": no}
        # The system update is reviewed and started in the Updates tab: the card only opens it
        opens_updates = p.action.id == "update" and self.on_updates is not None
        if opens_updates:
            run.setText("Show updates")
            run.setToolTip("Review the pending packages and pick what to update.")
            auto.hide()
        if p.finding.items and not opens_updates:
            details = QPushButton("Details")
            details.setToolTip(f"Show the {len(p.finding.items)} items this action affects.")
            details.clicked.connect(lambda _c=False, p=p: self._show_details(p))
            row.addWidget(details)
            self.rows[p.key]["details"] = details
            self.details_buttons.append(details)
        row.addWidget(run)
        row.addWidget(no)
        match_heights(run, no, *([self.rows[p.key]["details"]] if "details" in self.rows[p.key] else []))
        self.layout_.addWidget(container)
        self.labels.append(label)
        self.auto_boxes.append(auto)
        self.run_buttons.append(run)
        self.decline_buttons.append(no)

    def _show_details(self, p: Pending) -> None:
        if p.action.id == "update" and self.on_updates is not None:
            self.on_updates(p)
            return
        self.details_window = ItemsWindow(p, self)
        self.details_window.show()

    def _run_clicked(self, p: Pending, auto: bool) -> None:
        if p.action.id == "update" and self.on_updates is not None:
            self.on_updates(p)
            return
        self.set_running(p)
        self.on_run(p, auto)

    def _decline_clicked(self, p: Pending) -> None:
        self.on_decline(p)
        self._remove_row(p)

    def set_running(self, p: Pending) -> None:
        row = self.rows.get(p.key)
        if row:
            row["busy"].show()
            for key in ("run", "no", "auto"):
                row[key].setEnabled(False)

    def finish_row(self, p: Pending, ok: bool) -> None:
        row = self.rows.get(p.key)
        if not row:
            return
        if ok:
            self._remove_row(p)
            return
        row["busy"].hide()
        for key in ("run", "no", "auto"):
            row[key].setEnabled(True)
        row["label"].setText(row["label"].text().split("\n✗")[0] + "\n✗ last run failed, see the notification")

    def remove_action_rows(self, action_id: str) -> None:
        """Drop every card of this action (e.g. all `update` cards once any update succeeded)."""
        for key in [k for k in self.rows if k[1] == action_id]:
            self._remove_key(key)

    def _remove_row(self, p: Pending) -> None:
        self._remove_key(p.key)

    def _remove_key(self, key: tuple) -> None:
        row = self.rows.pop(key, None)
        if row:
            row["widget"].hide()
            row["widget"].deleteLater()
        self.empty_label.setVisible(not self.rows)
        self._update_subtitle()


class ItemsWindow(StyledDialog):
    """Read-only list of what a pending action affects (e.g. every pending update: name, old → new, source)."""

    def __init__(self, p: Pending, parent=None):
        super().__init__(f"Details – {p.finding.title}", parent)
        self.add_header(p.finding.title, f"{len(p.finding.items)} items")
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.text.setFontFamily("monospace")
        self.text.setPlainText("\n".join(format_item(i) for i in sorted(p.finding.items, key=_item_order)))
        self.layout_.addWidget(self.text)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        self.add_buttons(copy, close)
        self.resize(640, 480)


SOURCE_LABELS = {"repo": "Arch", "aur": "AUR", "flatpak": "Flatpak"}
SOURCE_ORDER = ("repo", "aur", "flatpak")
SOURCE_FILTERS = (("all", "All"), ("repo", "Arch"), ("aur", "AUR"), ("flatpak", "Flatpak"))
REPO_NOTE = "Arch repo packages are updated together: partial upgrades are not supported on Arch."


def package_key(item: dict) -> tuple[str, str]:
    return (str(item.get("source", "")), str(item.get("name", "")))


def _update_order(item: dict) -> tuple:
    """Critical packages first, then repo, AUR, Flatpak, then by name."""
    source = str(item.get("source", ""))
    rank = SOURCE_ORDER.index(source) if source in SOURCE_ORDER else len(SOURCE_ORDER)
    return (not item.get("critical"), rank, str(item.get("name", "")).lower())


def update_args(items: Iterable[dict], selected: set[tuple[str, str]]) -> tuple[str, ...] | None:
    """Args of actions/update.sh for the selected (source, name) keys.

    Everything selected = no args (the full update); repo packages always go as one `repo`, since Arch
    does not support partial upgrades; AUR packages and Flatpaks are named one by one. None = nothing selected.
    """
    keys = [package_key(i) for i in items]
    chosen = [k for k in keys if k in selected]
    if not chosen:
        return None
    if len(chosen) == len(keys):
        return ()
    repo = ("repo",) if any(source == "repo" for source, _ in chosen) else ()
    return repo + tuple(f"{source}:{name}" for source, name in chosen if source != "repo")


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


class PackagesTab(QWidget):
    """Common frame of the Packages window's tabs: a one-line summary for the window header, a bottom row
    with a busy bar, a status text, Close and the tab's primary button."""

    summary_changed = Signal(str)

    def __init__(self, on_close: Callable[[], None], parent=None):
        super().__init__(parent)
        self.summary = ""
        self.running = False
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 10, 0, 0)
        self.layout_.setSpacing(10)
        self.busy = make_busy()
        self.status = dim_label()
        self.status.setWordWrap(False)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(lambda: on_close())

    def add_bottom_row(self, primary: QPushButton, *others: QPushButton) -> None:
        primary.setObjectName("primary")
        row = QHBoxLayout()
        row.addWidget(self.busy)
        row.addWidget(self.status)
        row.addStretch(1)
        row.addWidget(self.close_button)
        row.addWidget(primary)
        self.layout_.addLayout(row)
        match_heights(self.close_button, primary, *others)

    def set_summary(self, text: str) -> None:
        self.summary = text
        self.summary_changed.emit(text)

    def show_status(self, text: str) -> None:
        self.status.setText(text)


class UpdatesTab(PackagesTab):
    """bauh-like list of pending updates: name, installed → new version, source; selective update.

    Repo packages are all-or-nothing (toggling one toggles all of them), AUR packages and Flatpaks can be
    picked one by one; everything starts checked. The search and the source filter only change what is
    shown, never what is selected. A dimmed line below tells when the last Timeshift snapshot was taken.
    """

    FILTERS = SOURCE_FILTERS
    COLUMNS = ("Name", "Installed", "", "New", "Source")

    def __init__(self, on_update: Callable[[tuple[str, ...]], None], on_refresh: Callable[[], None],
                 on_close: Callable[[], None], parent=None):
        super().__init__(on_close, parent)
        self.on_update, self.on_refresh = on_update, on_refresh
        self.pending: Pending | None = None
        self.items: list[dict] = []
        self.unchecked: set[tuple[str, str]] = set()
        self.source_filter = "all"
        self.summary = "Checking for updates…"

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search packages")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        toolbar.addWidget(self.search, 1)
        segments, self.filter_buttons = make_segments(self.FILTERS, self.set_source_filter, self)
        toolbar.addWidget(segments)
        self.refresh_button = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        self.refresh_button.setToolTip("Check for updates again (repo, AUR and Flatpak).")
        self.refresh_button.clicked.connect(lambda: self.on_refresh())
        toolbar.addWidget(self.refresh_button)
        self.layout_.addLayout(toolbar)

        self.tree = make_tree(self.COLUMNS)
        self.tree.itemChanged.connect(self._item_changed)
        self.layout_.addWidget(self.tree, 1)

        self.repo_note = dim_label(REPO_NOTE)
        self.layout_.addWidget(self.repo_note)
        self.snapshot_label = dim_label()
        self.snapshot_label.setToolTip("From `timeshift --list` and /etc/timeshift-autosnap.conf.")
        self.snapshot_label.hide()
        self.layout_.addWidget(self.snapshot_label)
        self.reason_label = QLabel()
        self.reason_label.setObjectName("rowTitle")
        self.reason_label.setWordWrap(True)
        self.layout_.addWidget(self.reason_label)

        self.update_button = QPushButton()
        self.update_button.clicked.connect(self._update_clicked)
        self.add_bottom_row(self.update_button, self.refresh_button)
        self._refresh_view()

    # --- content -------------------------------------------------------------

    def set_pending(self, pending: Pending | None, error: str | None = None) -> None:
        """Show the current updates finding (None = nothing to update); an `error` keeps the old list."""
        if error is not None:
            self.set_summary(f"Update check failed: {error}")
            return
        self.pending = pending
        self.items = sorted(pending.finding.items if pending else (), key=_update_order)
        self.unchecked &= {package_key(i) for i in self.items}   # new packages start checked
        self.tree.blockSignals(True)
        self.tree.clear()
        dim = self.palette().placeholderText()
        for item in self.items:
            critical = bool(item.get("critical"))
            name = str(item.get("name", ""))
            source = str(item.get("source", ""))
            row = QTreeWidgetItem([f"⚠ {name}" if critical else name, str(item.get("old") or "?"), "→",
                                   str(item.get("new", "")), SOURCE_LABELS.get(source, source)])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setData(0, Qt.ItemDataRole.UserRole, package_key(item))
            for col in (1, 2, 4):
                row.setForeground(col, dim)
            bold = row.font(3)
            bold.setBold(True)
            row.setFont(3, bold)
            row.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            if critical:
                row.setToolTip(0, "Critical package: read the update notes before rebooting.")
            self.tree.addTopLevelItem(row)
        self.tree.blockSignals(False)
        self._sync_checks()
        self.set_summary(f"{len(self.items)} updates available" if self.items else "System is up to date")
        reason = pending.reason if pending else None
        self.reason_label.setText(f"⚠ {reason}" if reason else "")
        self._refresh_view()

    def set_snapshot(self, lines: list[str] | None) -> None:
        """The last Timeshift snapshot and the timeshift-autosnap note; None = unknown, the line is hidden."""
        self.snapshot_label.setText("\n".join(lines or ()))
        self.snapshot_label.setVisible(bool(lines))

    def _refresh_view(self) -> None:
        counts = {key: sum(1 for i in self.items if key == "all" or i.get("source") == key) for key, _ in self.FILTERS}
        for key, label in self.FILTERS:
            self.filter_buttons[key].setText(f"{label}  {counts[key]}")
        self.repo_note.setVisible(counts["repo"] > 0)
        self.reason_label.setVisible(bool(self.reason_label.text()))
        self._apply_filter()
        self._update_button()

    def rows(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    # --- selection -------------------------------------------------------------

    def selected(self) -> set[tuple[str, str]]:
        return {package_key(i) for i in self.items} - self.unchecked

    def set_checked(self, key: tuple[str, str], checked: bool) -> None:
        """Check or uncheck a package; any repo package stands for all of them."""
        keys = {package_key(i) for i in self.items if i.get("source") == "repo"} if key[0] == "repo" else {key}
        self.unchecked = self.unchecked - keys if checked else self.unchecked | keys
        self._sync_checks()
        self._update_button()

    def _item_changed(self, row: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self.set_checked(row.data(0, Qt.ItemDataRole.UserRole), row.checkState(0) == Qt.CheckState.Checked)

    def _sync_checks(self) -> None:
        self.tree.blockSignals(True)
        for row in self.rows():
            key = row.data(0, Qt.ItemDataRole.UserRole)
            row.setCheckState(0, Qt.CheckState.Unchecked if key in self.unchecked else Qt.CheckState.Checked)
        self.tree.blockSignals(False)

    def update_args(self) -> tuple[str, ...] | None:
        return update_args(self.items, self.selected())

    def _update_button(self) -> None:
        n = len(self.selected())
        self.update_button.setText(f"Update selected ({n})")
        self.update_button.setEnabled(n > 0 and not self.running)

    def _update_clicked(self) -> None:
        args = self.update_args()
        if args is not None and not self.running:
            self.on_update(args)

    # --- filter ------------------------------------------------------------------

    def set_source_filter(self, key: str) -> None:
        self.source_filter = key
        self.filter_buttons[key].setChecked(True)
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        for row, item in zip(self.rows(), self.items):
            source_ok = self.source_filter == "all" or item.get("source") == self.source_filter
            row.setHidden(not (source_ok and text in str(item.get("name", "")).lower()))

    def visible_names(self) -> list[str]:
        return [str(item.get("name")) for row, item in zip(self.rows(), self.items) if not row.isHidden()]

    # --- running -----------------------------------------------------------------

    def set_running(self, running: bool, text: str = "") -> None:
        """Busy indicator while a check or an update runs; the list and the buttons wait meanwhile."""
        self.running = running
        self.busy.setVisible(running)
        self.status.setText(text)
        self.refresh_button.setEnabled(not running)
        self.tree.setEnabled(not running)
        self._update_button()


CATEGORY_LABELS = {"apps": "Apps", "utilities": "Utilities", "system": "System"}
CATEGORY_FILTERS = (("all", "All"), ("apps", "Apps"), ("utilities", "Utilities"), ("system", "System"))
GENERIC_ICONS = {"apps": "application-x-executable", "utilities": "package-x-generic",
                 "system": "package-x-generic"}
MAX_CONFIRM_LINES = 15


def row_icon(row: Installed) -> QIcon:
    """The .desktop / Flatpak icon from the icon theme (or its absolute path), else a generic package icon."""
    icon = QIcon(row.icon) if row.icon.startswith("/") else QIcon.fromTheme(row.icon) if row.icon else QIcon()
    return icon if not icon.isNull() else QIcon.fromTheme(GENERIC_ICONS.get(row.category, "package-x-generic"))


def confirm_packages(verb: str, rows: list, note: str, parent: QWidget | None = None) -> bool:
    """Ask before anything is installed or removed, listing every package (rows have name, id and source)."""
    names = [f"• {r.name}" + (f" ({r.id})" if r.name != r.id else "") + f" – {SOURCE_LABELS.get(r.source, r.source)}"
             for r in rows]
    shown = names[:MAX_CONFIRM_LINES] + ([f"… and {len(names) - MAX_CONFIRM_LINES} more"]
                                         if len(names) > MAX_CONFIRM_LINES else [])
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if verb == "Uninstall" else QMessageBox.Icon.Question)
    box.setWindowTitle(f"Arch Assistant – {verb.lower()}")
    box.setText(f"{verb} {len(rows)} {'package' if len(rows) == 1 else 'packages'}?")
    box.setInformativeText("\n".join(shown) + f"\n\n{note}")
    if len(names) > MAX_CONFIRM_LINES:
        box.setDetailedText("\n".join(names))
    yes = box.addButton(verb, QMessageBox.ButtonRole.DestructiveRole if verb == "Uninstall"
                        else QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is yes


UNINSTALL_NOTE = ("Packages are removed with pacman -Rns, together with the dependencies nothing else needs. "
                  "Flatpak app data in ~/.var/app is kept.")
INSTALL_NOTE = ("Arch packages come from the official repos, Flatpaks from Flathub (system-wide). "
                "AUR packages are made by users and built from their PKGBUILD on this machine.")
AUR_NOTE = ("AUR packages are made by users, not by Arch, and are built from a PKGBUILD on this machine: "
            "check the package page and its votes before installing.")


class InstalledTab(PackagesTab):
    """What is installed and what can go: explicit Arch packages, AUR packages and Flatpak apps grouped into
    Apps, Utilities and System. System rows are protected (no checkbox); the rest can be checked and uninstalled
    after a confirmation that lists them. Like the Updates tab, filters only change what is shown."""

    COLUMNS = ("Name", "Package", "Version", "Source")

    def __init__(self, on_uninstall: Callable[[tuple[str, ...]], None], on_refresh: Callable[[], None],
                 on_close: Callable[[], None], confirm: Callable[[list[Installed]], bool] | None = None, parent=None):
        super().__init__(on_close, parent)
        self.on_uninstall, self.on_refresh = on_uninstall, on_refresh
        self.confirm = confirm or (lambda rows: confirm_packages("Uninstall", rows, UNINSTALL_NOTE, self.window()))
        self.loaded = False
        self.data: list[Installed] = []
        self.checked: set[tuple[str, str]] = set()
        self.category_filter = "all"
        self.source_filter = "all"
        self.summary = "Not loaded yet"
        self.headers: dict[str, QTreeWidgetItem] = {}
        self.items: list[tuple[QTreeWidgetItem, Installed]] = []

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name or package")
        self.search.setMinimumWidth(240)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._apply_filter())
        toolbar.addWidget(self.search, 1)
        categories, self.category_buttons = make_segments(CATEGORY_FILTERS, self.set_category_filter, self)
        toolbar.addWidget(categories)
        sources, self.source_buttons = make_segments(SOURCE_FILTERS, self.set_source_filter, self)
        toolbar.addWidget(sources)
        self.refresh_button = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        self.refresh_button.setToolTip("Read the installed packages and Flatpaks again.")
        self.refresh_button.clicked.connect(lambda: self.on_refresh())
        toolbar.addWidget(self.refresh_button)
        self.layout_.addLayout(toolbar)

        self.tree = make_tree(self.COLUMNS)
        self.tree.setUniformRowHeights(False)   # section headers are taller than the rows
        self.tree.setItemsExpandable(False)
        self.tree.setIndentation(0)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.layout_.addWidget(self.tree, 1)
        self.note = dim_label("Click a row to select it, right-click to uninstall a single package. "
                              "Only explicitly installed packages are listed, not their dependencies. "
                              "System packages are protected and cannot be uninstalled here.")
        self.layout_.addWidget(self.note)

        self.uninstall_button = QPushButton()
        self.uninstall_button.clicked.connect(self._uninstall_clicked)
        self.add_bottom_row(self.uninstall_button, self.refresh_button)
        self._refresh_view()

    # --- content -------------------------------------------------------------

    def set_inventory(self, inv: Inventory | None, error: str | None = None) -> None:
        """Show what is installed; an `error` keeps the old list."""
        if error is not None or inv is None:
            self.set_summary(f"Reading installed packages failed: {error or 'unknown error'}")
            return
        self.loaded = True
        self.data = list(inv.rows)
        self.checked &= {r.key for r in self.data}
        self.tree.blockSignals(True)
        self.tree.clear()
        self.headers, self.items = {}, []
        dim = self.palette().placeholderText()
        for category in CATEGORY_LABELS:
            rows = [r for r in self.data if r.category == category]
            if not rows:
                continue
            header = QTreeWidgetItem([CATEGORY_LABELS[category]])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            header.setForeground(0, dim)
            self.tree.addTopLevelItem(header)
            header.setFirstColumnSpanned(True)
            self.headers[category] = header
            for r in rows:
                item = QTreeWidgetItem([r.name, r.id, r.version, SOURCE_LABELS.get(r.source, r.source)])
                item.setIcon(0, row_icon(r))
                item.setData(0, Qt.ItemDataRole.UserRole, r.key)
                for col in (1, 2, 3):
                    item.setForeground(col, dim)
                if r.protected:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    for col in range(len(self.COLUMNS)):
                        item.setToolTip(col, PROTECTED_REASON)
                else:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                    if r.source == "flatpak":
                        item.setToolTip(0, f"Flatpak app ({r.installation} installation)")
                item.setCheckState(0, Qt.CheckState.Unchecked)
                header.addChild(item)
                self.items.append((item, r))
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._sync_checks()
        counts = {c: sum(1 for r in self.data if r.category == c) for c in CATEGORY_LABELS}
        self.set_summary(f"{len(self.data)} installed: {counts['apps']} apps, {counts['utilities']} utilities, "
                         f"{counts['system']} system")
        problems = ", ".join(inv.errors)
        self.show_status(f"Could not read: {problems}" if problems else "")
        self._refresh_view()

    def _refresh_view(self) -> None:
        for key, label in CATEGORY_FILTERS:
            n = sum(1 for r in self.data if key == "all" or r.category == key)
            self.category_buttons[key].setText(f"{label}  {n}")
        for key, label in SOURCE_FILTERS:
            n = sum(1 for r in self.data if key == "all" or r.source == key)
            self.source_buttons[key].setText(f"{label}  {n}")
        self._apply_filter()
        self._update_button()

    # --- selection -------------------------------------------------------------

    def selected_rows(self) -> list[Installed]:
        return [r for r in self.data if r.key in self.checked and not r.protected]

    def uninstall_args(self) -> tuple[str, ...] | None:
        rows = self.selected_rows()
        return tuple(r.uninstall_arg for r in rows) if rows else None

    def set_checked(self, key: tuple[str, str], checked: bool) -> None:
        if any(r.key == key and r.protected for r in self.data):
            return
        self.checked = self.checked | {key} if checked else self.checked - {key}
        self._sync_checks()
        self._update_button()

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if column == 0 and key is not None:
            self.set_checked(tuple(key), item.checkState(0) == Qt.CheckState.Checked)

    def _sync_checks(self) -> None:
        self.tree.blockSignals(True)
        for item, r in self.items:
            item.setCheckState(0, Qt.CheckState.Checked if r.key in self.checked else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def _update_button(self) -> None:
        n = len(self.selected_rows())
        self.uninstall_button.setText(f"Uninstall selected ({n})")
        self.uninstall_button.setEnabled(n > 0 and not self.running)

    def _uninstall_clicked(self) -> None:
        self.uninstall(self.selected_rows())

    def uninstall(self, rows: list[Installed]) -> None:
        """Confirm, then hand the packages to the action; protected rows never get here."""
        rows = [r for r in rows if not r.protected]
        if rows and not self.running and self.confirm(rows):
            self.on_uninstall(tuple(r.uninstall_arg for r in rows))

    def row_at(self, pos) -> Installed | None:
        item = self.tree.itemAt(pos)
        return next((r for i, r in self.items if i is item), None)

    def _context_menu(self, pos) -> None:
        r = self.row_at(pos)
        if r is None:
            return
        menu = QMenu(self.tree)
        act = menu.addAction(QIcon.fromTheme("edit-delete"), f"Uninstall {r.name}…")
        act.setEnabled(not r.protected and not self.running)
        if r.protected:
            act.setToolTip(PROTECTED_REASON)
        act.triggered.connect(lambda _c=False, r=r: self.uninstall([r]))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- filter ------------------------------------------------------------------

    def set_category_filter(self, key: str) -> None:
        self.category_filter = key
        self.category_buttons[key].setChecked(True)
        self._apply_filter()

    def set_source_filter(self, key: str) -> None:
        self.source_filter = key
        self.source_buttons[key].setChecked(True)
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        shown: dict[str, int] = {}
        for item, r in self.items:
            ok = ((self.category_filter == "all" or r.category == self.category_filter)
                  and (self.source_filter == "all" or r.source == self.source_filter)
                  and (text in r.name.lower() or text in r.id.lower()))
            item.setHidden(not ok)
            shown[r.category] = shown.get(r.category, 0) + ok
        for category, header in self.headers.items():
            header.setHidden(not shown.get(category))
            header.setText(0, f"{CATEGORY_LABELS[category]}  ·  {shown.get(category, 0)}")

    def visible_ids(self) -> list[str]:
        return [r.id for item, r in self.items if not item.isHidden()]

    # --- running -----------------------------------------------------------------

    def set_running(self, running: bool, text: str = "") -> None:
        """Busy indicator while the list loads or an uninstall runs; the list and the buttons wait meanwhile."""
        self.running = running
        self.busy.setVisible(running)
        self.show_status(text)
        self.refresh_button.setEnabled(not running)
        self.tree.setEnabled(not running)
        self._update_button()


SEARCH_DEBOUNCE_MS = 500


class BrowseTab(PackagesTab):
    """Find and install new software: one search over the Arch repos, the AUR and Flathub (in a worker, debounced,
    at least MIN_TERM characters, Enter searches at once). Installed results are marked and cannot be picked; the
    rest can be checked and installed after a confirmation that lists them."""

    COLUMNS = ("Name", "Description", "Version", "Source", "")

    def __init__(self, on_install: Callable[[tuple[str, ...]], None], on_search: Callable[[str], None],
                 on_close: Callable[[], None], confirm: Callable[[list[Result]], bool] | None = None, parent=None):
        super().__init__(on_close, parent)
        self.on_install, self.on_search = on_install, on_search
        self.confirm = confirm or (lambda rows: confirm_packages("Install", rows, INSTALL_NOTE, self.window()))
        self.term = ""            # what the shown results are for
        self.searching = ""       # the term a search is running for ("" = none)
        self.data: list[Result] = []
        self.checked: dict[tuple[str, str], Result] = {}   # kept across searches, so picks from several searches add up
        self.source_filter = "all"
        self.summary = "Search the Arch repos, the AUR and Flathub"
        self.items: list[tuple[QTreeWidgetItem, Result]] = []

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Search apps and packages (at least {MIN_TERM} characters, Enter to search now)")
        self.search.setClearButtonEnabled(True)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self.debounce.timeout.connect(self.start_search)
        self.search.textChanged.connect(lambda _t: self.debounce.start())
        self.search.returnPressed.connect(self.start_search)
        toolbar.addWidget(self.search, 1)
        sources, self.source_buttons = make_segments(SOURCE_FILTERS, self.set_source_filter, self)
        toolbar.addWidget(sources)
        self.layout_.addLayout(toolbar)

        self.tree = make_tree(self.COLUMNS)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 260)
        self.tree.itemChanged.connect(self._item_changed)
        self.layout_.addWidget(self.tree, 1)
        self.aur_note = dim_label(AUR_NOTE)
        self.aur_note.hide()
        self.layout_.addWidget(self.aur_note)

        self.install_button = QPushButton()
        self.install_button.clicked.connect(self._install_clicked)
        self.add_bottom_row(self.install_button)
        self._refresh_view()

    # --- search --------------------------------------------------------------

    def start_search(self) -> None:
        """Search now (Enter or the debounce timer); too short a term or the same one again does nothing."""
        self.debounce.stop()
        term = normalize(self.search.text())
        if len(term) < MIN_TERM or term in (self.searching, self.term if not self.searching else None):
            return
        self.searching = term
        self.busy.show()
        self.show_status(f"Searching for “{term}”…")
        self.on_search(term)

    def research(self) -> None:
        """Run the shown search again (after an install the Installed marks change)."""
        if self.term and not self.searching:
            self.searching = self.term
            self.on_search(self.term)

    def set_results(self, found: SearchResult | None, error: str | None = None) -> None:
        """Results of a search; an answer for an older term than the one now typed is dropped."""
        if found is not None and found.term != self.searching:
            return
        self.searching = ""
        self.busy.setVisible(self.running)
        if found is None:
            self.show_status(f"Search failed: {error or 'unknown error'}")
            return
        self.term, self.data = found.term, list(found.results)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = []
        dim = self.palette().placeholderText()
        for r in self.data:
            item = QTreeWidgetItem([r.name, r.description, r.version, SOURCE_LABELS.get(r.source, r.source),
                                    "Installed" if r.installed else ""])
            item.setData(0, Qt.ItemDataRole.UserRole, r.key)
            for col in (1, 2, 3, 4):
                item.setForeground(col, dim)
            tip = f"{r.id}" + (f" · {r.origin}" if r.origin else "")
            if r.source == "aur":
                tip += f" · {r.votes} votes, popularity {r.popularity:.2f}"
            item.setToolTip(0, tip)
            item.setToolTip(1, r.description)
            if r.installed:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setToolTip(4, "Already installed")
            else:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(item)
            self.items.append((item, r))
        self.tree.blockSignals(False)
        self._sync_checks()
        self.set_summary(f"{len(self.data)} results for “{found.term}”" if self.data else f"Nothing found for “{found.term}”")
        self.show_status("Not searched: " + "; ".join(found.errors) if found.errors else "")
        self._refresh_view()

    def _refresh_view(self) -> None:
        for key, label in SOURCE_FILTERS:
            n = sum(1 for r in self.data if key == "all" or r.source == key)
            self.source_buttons[key].setText(f"{label}  {n}")
        self._apply_filter()
        self._update_button()

    # --- selection -------------------------------------------------------------

    def selected_rows(self) -> list[Result]:
        return list(self.checked.values())

    def install_args(self) -> tuple[str, ...] | None:
        rows = self.selected_rows()
        return tuple(r.install_arg for r in rows) if rows else None

    def set_checked(self, key: tuple[str, str], checked: bool) -> None:
        row = next((r for r in self.data if r.key == key), None)
        if row is None or row.installed:
            return
        self.checked = {**self.checked, key: row} if checked else {k: v for k, v in self.checked.items() if k != key}
        self._sync_checks()
        self._update_button()

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self.set_checked(tuple(item.data(0, Qt.ItemDataRole.UserRole)), item.checkState(0) == Qt.CheckState.Checked)

    def _sync_checks(self) -> None:
        self.tree.blockSignals(True)
        for item, r in self.items:
            item.setCheckState(0, Qt.CheckState.Checked if r.key in self.checked else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def clear_selection(self) -> None:
        self.checked = {}
        self._sync_checks()
        self._update_button()

    def _update_button(self) -> None:
        n = len(self.checked)
        self.install_button.setText(f"Install selected ({n})")
        self.install_button.setEnabled(n > 0 and not self.running)
        self.aur_note.setVisible(any(r.source == "aur" for r in self.checked.values())
                                 or any(r.source == "aur" and not item.isHidden() for item, r in self.items))

    def _install_clicked(self) -> None:
        rows = self.selected_rows()
        if rows and not self.running and self.confirm(rows):
            self.on_install(tuple(r.install_arg for r in rows))

    # --- filter ------------------------------------------------------------------

    def set_source_filter(self, key: str) -> None:
        self.source_filter = key
        self.source_buttons[key].setChecked(True)
        self._apply_filter()
        self._update_button()

    def _apply_filter(self) -> None:
        for item, r in self.items:
            item.setHidden(self.source_filter != "all" and r.source != self.source_filter)

    def visible_ids(self) -> list[str]:
        return [r.id for item, r in self.items if not item.isHidden()]

    # --- running -----------------------------------------------------------------

    def set_running(self, running: bool, text: str = "") -> None:
        """Busy indicator while an install runs; the list and the button wait meanwhile."""
        self.running = running
        self.busy.setVisible(running or bool(self.searching))
        self.show_status(text)
        self.tree.setEnabled(not running)
        self._update_button()


class PackagesWindow(StyledDialog):
    """One window for packages: Updates (what can be updated), Installed (what is there and what can be
    uninstalled) and Browse (find and install new software). The header subtitle follows the current tab's
    summary; the Installed tab asks for its list the first time it is opened."""

    TABS = ("updates", "installed", "browse")

    def __init__(self, pending: Pending | None, on_update: Callable[[tuple[str, ...]], None],
                 on_refresh: Callable[[], None], on_load_installed: Callable[[], None] = lambda: None,
                 on_uninstall: Callable[[tuple[str, ...]], None] = lambda args: None,
                 on_search: Callable[[str], None] = lambda term: None,
                 on_install: Callable[[tuple[str, ...]], None] = lambda args: None, loaded: bool = True,
                 confirm: Callable[[list], bool] | None = None, parent=None):
        super().__init__("Arch Assistant – packages", parent)
        self.on_load_installed = on_load_installed
        self.subtitle = self.add_header("Packages", "")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.updates_tab = UpdatesTab(on_update, on_refresh, self.close)
        self.installed_tab = InstalledTab(on_uninstall, on_load_installed, self.close, confirm)
        self.browse_tab = BrowseTab(on_install, on_search, self.close, confirm)
        self.tabs.addTab(self.updates_tab, QIcon.fromTheme("update-none"), "Updates")
        self.tabs.addTab(self.installed_tab, QIcon.fromTheme("package-x-generic"), "Installed")
        self.tabs.addTab(self.browse_tab, QIcon.fromTheme("edit-find"), "Browse")
        for tab in self.all_tabs():
            tab.summary_changed.connect(lambda _t: self._sync_subtitle())
        self.tabs.currentChanged.connect(self._tab_changed)
        self.layout_.addWidget(self.tabs, 1)
        if loaded:
            self.updates_tab.set_pending(pending)
        self._sync_subtitle()
        self.resize(1060, 680)

    def all_tabs(self) -> tuple[PackagesTab, ...]:
        return (self.updates_tab, self.installed_tab, self.browse_tab)

    def current(self) -> str:
        return self.TABS[self.tabs.currentIndex()]

    def show_tab(self, name: str) -> None:
        index = self.TABS.index(name)
        if index == self.tabs.currentIndex():
            self._tab_changed()   # no currentChanged signal for the tab that is already shown
        self.tabs.setCurrentIndex(index)

    def _tab_changed(self, _index: int = 0) -> None:
        self._sync_subtitle()
        tab = self.installed_tab
        if self.current() == "installed" and not tab.loaded and not tab.running:
            self.on_load_installed()
        if self.current() == "browse":
            self.browse_tab.search.setFocus()

    def _sync_subtitle(self) -> None:
        self.subtitle.setText(self.all_tabs()[self.tabs.currentIndex()].summary)


class TrayApp(QObject):
    cycle_finished = Signal(object)                   # CycleResult | None
    action_finished = Signal(object, object, object)  # Pending, ActionResult, list[Finding]
    inventory_loaded = Signal(object, object)         # Inventory | None, error str | None
    snapshot_loaded = Signal(object)                  # list[str] | None
    search_finished = Signal(object, object)          # SearchResult | None, error str | None
    updates_refreshed = Signal(object, object, object)  # Finding | None, Pending | None, error str | None

    def __init__(self, cfg: Config, state: State, log: logging.Logger,
                 notifier: Notifier | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg, self.log = cfg, log
        self.cycle = Cycle(cfg, state, log)
        self.notifier = notifier or Notifier(icon=NOTIFY_ICON)
        self.notifier.icon = NOTIFY_ICON
        self.pending: list[Pending] = []
        self.findings: list[Finding] = []      # of the last full cycle (+ refreshed updates): the update guards read them
        self.updates: Pending | None = None    # what the Updates tab shows; None = nothing to update
        self.updates_loaded = False            # False until the first cycle or refresh finished
        self.updates_note = ""                 # shown in the Updates tab after the refresh that follows a failed update
        self.busy = False
        self.dialog: PendingDialog | None = None
        self.packages_window: PackagesWindow | None = None
        self.inventory_loading = False         # the Installed list is being read (independent of `busy`)
        self.snapshot_loading = False

        self.tray = QSystemTrayIcon(self._icon("idle"), self)
        self.tray.setToolTip("Arch Assistant")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._rebuild_menu()
        self.tray.show()

        self.cycle_finished.connect(self._on_cycle_finished)
        self.action_finished.connect(self._on_action_finished)
        self.inventory_loaded.connect(self._on_inventory_loaded)
        self.snapshot_loaded.connect(self._on_snapshot_loaded)
        self.search_finished.connect(self._on_search_finished)
        self.updates_refreshed.connect(self._on_updates_refreshed)
        self.timer = QTimer(self)
        self.timer.setInterval(int(cfg.interval_hours * 3600 * 1000))
        self.timer.timeout.connect(self.check_now)
        self.timer.start()
        if cfg.config_error:
            self.log.error("config error, defaults in use: %s", cfg.config_error)
            QTimer.singleShot(0, lambda: self.notifier.send(
                Summary("Arch Assistant: config.toml has a syntax error, defaults are in use", cfg.config_error, []),
                [], lambda _k: None, urgency=URGENCY_CRITICAL))
        QTimer.singleShot(0, self.check_now)

    # --- icon & menu -------------------------------------------------------

    def _icon(self, kind: str) -> QIcon:
        return QIcon(str(ASSETS / ICONS[kind]))

    def _refresh_icon(self) -> None:
        kind = "busy" if self.busy else ("attention" if self.pending else "idle")
        self.tray.setIcon(self._icon(kind))
        self.tray.setToolTip("Arch Assistant" + (f" – {len(self.pending)} pending" if self.pending else ""))

    def _rebuild_menu(self) -> None:
        self.menu.clear()
        if self.pending:
            for p in self.pending:
                act = QAction(p.label + (f"  (confirm: {p.reason})" if p.reason else ""), self.menu)
                if p.reason:
                    act.setToolTip(p.reason)
                act.triggered.connect(lambda _c=False, p=p: self.open_pending(p))
                self.menu.addAction(act)
            self.menu.addSeparator()
            self.menu.addAction("Show actions…", self.show_actions)
        else:
            self.menu.addAction("No pending actions").setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Packages…", self.show_updates)
        self.menu.addAction("Check now", self.check_now)
        self.menu.addAction("Open log", self.open_log)
        self.menu.addAction("Settings", self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit", QApplication.instance().quit)
        self._refresh_icon()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_actions()

    def show_actions(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
        self.dialog = PendingDialog(self.pending, on_run=self.run_pending, on_decline=self.decline,
                                    on_updates=lambda _p: self.show_updates())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    # --- packages window ---------------------------------------------------

    def _window(self) -> PackagesWindow:
        if self.packages_window is None:
            self.packages_window = PackagesWindow(
                self.updates, on_update=self._update_selected, on_refresh=self.refresh_updates,
                on_load_installed=self.load_installed, on_uninstall=self._uninstall_selected,
                on_search=self.search_packages, on_install=self._install_selected, loaded=self.updates_loaded)
        return self.packages_window

    def show_packages(self, tab: str = "updates") -> None:
        """Open the Packages window on a tab; the Updates tab also re-reads the last Timeshift snapshot."""
        w = self._window()
        if self.busy:
            w.updates_tab.set_running(True, "Checking…")
        elif not self.updates_loaded:
            self.refresh_updates()
        w.show_tab(tab)
        if tab == "updates":
            self.load_snapshot()
        w.show()
        w.raise_()
        w.activateWindow()

    def show_updates(self) -> None:
        self.show_packages("updates")

    def show_installed(self) -> None:
        self.show_packages("installed")

    def _set_updates_idle(self) -> None:
        if self.packages_window is not None:
            self.packages_window.updates_tab.set_running(False)

    def _updates_from(self, findings: list[Finding], pending: list[Pending]) -> Pending | None:
        """The updates entry for the Updates tab: the queued one, or (declined, snoozed, mode `never`) a new one."""
        queued = next((p for p in pending if p.finding.id == "updates" and p.action.id == "update"), None)
        if queued is not None:
            return queued
        f = next((f for f in findings if f.id == "updates" and not f.error and not f.is_empty), None)
        action = next((a for a in f.actions if a.id == "update"), None) if f else None
        if action is None:
            return None
        # not queued means not checked against the VM state: fail closed without calling virsh on the GUI thread
        return Pending(f, action, needs_confirmation(action, f, findings, running_vms=lambda: None))

    def refresh_updates(self) -> None:
        """Run only the `updates` check in a worker thread; no full cycle, no notification."""
        if self.busy:
            self.log.info("updates refresh requested while busy; ignored")
            if self.packages_window is not None and not self.packages_window.updates_tab.running:
                self.packages_window.updates_tab.show_status("Arch Assistant is busy, try again in a moment")
            return
        self.busy = True
        self._refresh_icon()
        if self.packages_window is not None:
            self.packages_window.updates_tab.set_running(True, "Checking for updates…")
        findings = list(self.findings)

        def work():
            try:
                found = run_all(self.cfg.checks_dir, check_env(self.cfg), only=["updates"])
                f = found[0] if found else None
                if f is not None and f.error:
                    self.updates_refreshed.emit(f, None, f.summary)
                    return
                action = next((a for a in f.actions if a.id == "update"), None) if f and not f.is_empty else None
                p = None
                if action is not None:
                    reason = needs_confirmation(action, f, merge_findings(findings, [f]), self.cycle.running_vms)
                    p = Pending(f, action, reason)
                self.updates_refreshed.emit(f, p, None)
            except Exception as e:   # a crashed refresh must not leave the tray busy forever
                self.log.exception("updates refresh crashed")
                self.updates_refreshed.emit(None, None, str(e))

        threading.Thread(target=work, name="aa-updates", daemon=True).start()

    def _on_updates_refreshed(self, f: Finding | None, p: Pending | None, error: str | None) -> None:
        self.busy = False
        if error is None:
            if f is not None:
                self.findings = merge_findings(self.findings, [f])
            self.updates, self.updates_loaded = p, True
            queue = (p is not None and effective_mode("update", self.cfg) != "never"
                     and not self.cycle.state.is_snoozed(p.finding, datetime.now()))
            old = [i for i, q in enumerate(self.pending) if q.finding.id == "updates"]
            rest = [q for q in self.pending if q.finding.id != "updates"]
            at = old[0] if old else len(rest)
            self.pending = rest[:at] + ([p] if queue else []) + rest[at:]
            self.log.info("updates refreshed: %d available", len(p.finding.items) if p else 0)
        else:
            self.log.warning("updates refresh failed: %s", error)
        self._rebuild_menu()
        if self.packages_window is not None:
            tab = self.packages_window.updates_tab
            tab.set_running(False)
            tab.set_pending(self.updates, error)
            tab.show_status(self.updates_note)

    def _update_selected(self, args: tuple[str, ...]) -> None:
        """The Updates tab's "Update selected": the usual run_pending path (terminal progress, guards, popups)."""
        base = self.updates
        if base is None:
            return
        p = Pending(base.finding, Action("update", base.action.label, tuple(args)), base.reason)
        self._run_from_tab(p, self._window().updates_tab, "Updating…")

    def _uninstall_selected(self, args: tuple[str, ...]) -> None:
        """The Installed tab's "Uninstall selected" (already confirmed): actions/uninstall.sh in the terminal."""
        p = Pending(Finding("installed", "none", "Installed packages", ""), Action("uninstall", "Uninstall packages", args))
        self._run_from_tab(p, self._window().installed_tab, "Uninstalling…")

    def _install_selected(self, args: tuple[str, ...]) -> None:
        """The Browse tab's "Install selected" (already confirmed): actions/install.sh in the terminal."""
        p = Pending(Finding("browse", "none", "New packages", ""), Action("install", "Install packages", args))
        self._run_from_tab(p, self._window().browse_tab, "Installing…")

    def _run_from_tab(self, p: Pending, tab: PackagesTab, text: str) -> None:
        if self.busy:
            tab.show_status("Arch Assistant is busy, try again in a moment")
            return
        self.log.info("%s selected from the Packages window: args=%s", p.action.id, list(p.action.args))
        tab.set_running(True, text)
        self.run_pending(p)

    # --- installed list, snapshot info and search (read only, independent of `busy`) ---------

    def load_installed(self) -> None:
        """Read the installed packages in a worker thread for the Installed tab."""
        if self.inventory_loading:
            return
        self.inventory_loading = True
        self._window().installed_tab.set_running(True, "Reading installed packages…")

        def work():
            try:
                self.inventory_loaded.emit(collect(), None)
            except Exception as e:   # a crashed read must not leave the tab busy forever
                self.log.exception("reading installed packages crashed")
                self.inventory_loaded.emit(None, str(e))

        threading.Thread(target=work, name="aa-inventory", daemon=True).start()

    def _on_inventory_loaded(self, inv: Inventory | None, error: str | None) -> None:
        self.inventory_loading = False
        if self.packages_window is None:
            return
        tab = self.packages_window.installed_tab
        tab.set_running(False)
        tab.set_inventory(inv, error)
        if inv is not None:
            self.log.info("installed packages read: %d rows", len(inv.rows))

    def load_snapshot(self) -> None:
        """The last Timeshift snapshot for the Updates tab, read in a worker thread (timeshift mounts, slow)."""
        if self.snapshot_loading:
            return
        self.snapshot_loading = True

        def work():
            try:
                lines = snapshot_info()
            except Exception:
                self.log.exception("reading the snapshot info crashed")
                lines = None
            self.snapshot_loaded.emit(lines)

        threading.Thread(target=work, name="aa-snapshot", daemon=True).start()

    def _on_snapshot_loaded(self, lines: list[str] | None) -> None:
        self.snapshot_loading = False
        if self.packages_window is not None:
            self.packages_window.updates_tab.set_snapshot(lines)

    def search_packages(self, term: str) -> None:
        """Search the repos, the AUR and Flathub in a worker thread for the Browse tab."""
        def work():
            try:
                self.search_finished.emit(search(term), None)
            except Exception as e:
                self.log.exception("package search crashed")
                self.search_finished.emit(None, str(e))

        threading.Thread(target=work, name="aa-search", daemon=True).start()

    def _on_search_finished(self, found: SearchResult | None, error: str | None) -> None:
        if self.packages_window is not None:
            self.packages_window.browse_tab.set_results(found, error)

    # --- cycle -------------------------------------------------------------

    def check_now(self) -> None:
        if self.busy:
            self.log.info("check requested while busy; ignored")
            return
        self.busy = True
        self._refresh_icon()

        def work():
            try:
                result = self.cycle.run()
            except Exception:  # a crashed cycle must not leave the tray busy forever
                self.log.exception("cycle crashed")
                result = None
            self.cycle_finished.emit(result)

        threading.Thread(target=work, name="aa-cycle", daemon=True).start()

    def _on_cycle_finished(self, result: CycleResult | None) -> None:
        self.busy = False
        if result is None:
            self._rebuild_menu()
            self._set_updates_idle()
            return
        for res in result.auto_run:
            self._popup_result(res, self._label_for(res, result.findings), [])
        self.pending = list(result.pending)   # replaced wholesale: stale entries disappear, surviving ones stay
        self.findings = list(result.findings)
        self.updates, self.updates_loaded = self._updates_from(self.findings, self.pending), True
        self._rebuild_menu()
        if self.packages_window is not None:
            self.packages_window.updates_tab.set_running(False)
            self.packages_window.updates_tab.set_pending(self.updates)
        if result.summary:
            self._notify_summary(result.summary, result.new_pending, result.new)

    def _notify_summary(self, summary: Summary, new_pending: list[Pending], new: list[Finding]) -> None:
        urgency = URGENCY_CRITICAL if any(f.severity == "critical" for f in new) else URGENCY_NORMAL
        self.log.info("notify: %s (%d new pending)", summary.headline, len(new_pending))
        if len(new_pending) == 1:
            p = new_pending[0]
            body = summary.body + (f"\n\n{p.reason}" if p.reason else "")
            buttons = BUTTONS_UPDATES if p.action.id == "update" else BUTTONS_ASK
            self.notifier.send(Summary(summary.headline, body, summary.risks), buttons,
                               lambda key, p=p: self._on_button(key, p),
                               on_closed=lambda: self.log.info("notification closed, %s stays pending", p.action.id),
                               urgency=urgency)
        elif new_pending:
            self.notifier.send(summary, BUTTON_ACTIONS, lambda key: self.show_actions(), urgency=urgency)
        else:
            self.notifier.send(summary, [], lambda _k: None, urgency=urgency)

    def _on_button(self, key: str, p: Pending) -> None:
        if key == "updates":
            self.show_updates()
        elif key == "no":
            self.decline(p)
        elif key in ("yes", "yes_auto"):
            self.run_pending(p, auto=(key == "yes_auto"))

    # --- actions -----------------------------------------------------------

    def open_pending(self, p: Pending) -> None:
        """A click on a queued action: the system update opens the Updates tab, anything else runs."""
        if p.action.id == "update":
            self.show_updates()
        else:
            self.run_pending(p)

    def decline(self, p: Pending) -> None:
        self.cycle.decline(p)
        self._remove_pending(p)
        self.log.info("declined %s for %s", p.action.id, p.finding.id)

    def run_pending(self, p: Pending, auto: bool = False) -> None:
        if auto:
            set_action_mode(self.cfg.config_dir, p.action.id, "auto")
            self.cfg.actions[p.action.id] = "auto"
            self.log.info("action %s set to auto", p.action.id)
        if self.busy:
            self.log.info("action %s requested while busy; stays pending", p.action.id)
            if self.dialog is not None:
                self.dialog.finish_row(p, ok=False)
            self.notifier.send(Summary("Arch Assistant is busy", f"{p.action.label} stays in the menu, try again later.", []),
                               [], lambda _k: None)
            return
        self.busy = True
        self._refresh_icon()
        if self.dialog is not None:
            self.dialog.set_running(p)

        def work():
            try:
                res, extra = self.cycle.run_pending(p)
            except Exception as e:  # a crashed action must not leave the tray busy forever
                self.log.exception("action %s crashed", p.action.id)
                res, extra = ActionResult(p.action.id, False, f"{p.action.label} crashed: {e}", "", 0.0), []
            self.action_finished.emit(p, res, extra)

        threading.Thread(target=work, name="aa-action", daemon=True).start()

    def _on_action_finished(self, p: Pending, res: ActionResult, extra: list[Finding]) -> None:
        self.busy = False
        is_update = p.action.id == "update"
        if res.ok and is_update:
            # a full or selective update supersedes every queued update entry; the refresh below re-adds what is left
            self.pending = [q for q in self.pending if q.action.id != "update"]
            if self.dialog is not None:
                self.dialog.remove_action_rows("update")
        elif res.ok:
            self._remove_pending(p)
        self._rebuild_menu()
        if self.dialog is not None:
            self.dialog.finish_row(p, res.ok)
        self._set_updates_idle()
        self.updates_note = "" if res.ok or not is_update else "Last update failed, see the notification"
        self._popup_result(res, p.action.label, extra)
        changes_packages = p.action.id in ("uninstall", "install")
        if changes_packages and self.packages_window is not None:
            tab = self.packages_window.installed_tab if p.action.id == "uninstall" else self.packages_window.browse_tab
            tab.set_running(False, "" if res.ok else f"Last {p.action.id} failed, see the notification")
            if res.ok and p.action.id == "install":
                self.packages_window.browse_tab.clear_selection()
            if self.packages_window.installed_tab.loaded or p.action.id == "uninstall":
                self.load_installed()
            self.packages_window.browse_tab.research()
        if is_update or changes_packages:
            self.refresh_updates()

    def _popup_result(self, res: ActionResult, label: str, extra: list[Finding]) -> None:
        notes = [f"{f.title}" + (f" – {f.summary}" if f.summary else "") for f in extra if not f.is_empty]
        if notes:
            res = ActionResult(res.action_id, res.ok, res.output + "\n" + "\n".join(notes), res.detail, res.duration_s)
        result_popup(self.notifier, res, label)
        crit = [f for f in extra if f.severity == "critical"]
        if crit:
            self.notifier.send(template_summary(crit), [], lambda _k: None, urgency=URGENCY_CRITICAL)

    def _remove_pending(self, p: Pending) -> None:
        self.pending = [q for q in self.pending if q.key != p.key]
        self._rebuild_menu()

    @staticmethod
    def _label_for(res: ActionResult, findings: list[Finding]) -> str:
        for f in findings:
            for a in f.actions:
                if a.id == res.action_id:
                    return a.label
        return res.action_id

    # --- menu helpers --------------------------------------------------------

    def open_log(self) -> None:
        subprocess.Popen(["xdg-open", str(self.cfg.state_dir / "log" / "assistant.log")])

    def open_settings(self) -> None:
        path = self.cfg.config_dir / "config.toml"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(EXAMPLE_CONFIG.read_text() if EXAMPLE_CONFIG.exists() else "# Arch Assistant config\n")
        subprocess.Popen(["xdg-open", str(path)])


def run_tray(cfg: Config, state: State, log: logging.Logger) -> int:
    lock = InstanceLock(cfg.state_dir / "tray.lock")
    other = lock.acquire()
    if other is not None:
        # launched from the menu while the tray already runs: ask that instance to show its actions
        log.info("tray already running (pid %s); asking it to show actions", other)
        if other > 0:
            try:
                os.kill(other, signal.SIGUSR1)
            except OSError:
                pass
        return 0
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("arch-assistant")
    app.setDesktopFileName("arch-assistant")   # KWin looks up arch-assistant.desktop for the window icon
    app.setWindowIcon(QIcon(NOTIFY_ICON))
    tray = TrayApp(cfg, state, log)
    wanted = {"show": False}
    signal.signal(signal.SIGUSR1, lambda *_a: wanted.__setitem__("show", True))
    poll = QTimer()   # Python signal handlers only run while bytecode executes: poll from the Qt loop
    poll.setInterval(250)

    def check_signal():
        if wanted["show"]:
            wanted["show"] = False
            tray.show_actions()

    poll.timeout.connect(check_signal)
    poll.start()
    try:
        return app.exec()
    finally:
        lock.release()
