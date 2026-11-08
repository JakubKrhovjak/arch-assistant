"""The common part of the Packages window's tabs: a list of checkable package rows with a search box, filter
segments with counts, a selection that survives refreshes and filtering, a running state and a bottom row."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .widgets import dim_label, make_busy, make_segments, make_tree, match_heights

ALL = "all"
BUSY_TEXT = "Arch Assistant is busy, try again in a moment"


def filter_options(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """The segments of a filter: All, then one per value in the order of `labels`."""
    return ((ALL, "All"), *labels.items())


class PackagesTab(QWidget):
    """A tab owns its rows (`data`, any row type) and their tree items (`items`), the checked rows (`checked`,
    key → row) and the picked filter values. A subclass builds its toolbar and tree with the add_* helpers and
    says what is special about its rows through the hooks below; everything else (checkbox sync, filters, counts,
    the primary button and the running state) happens here once.

    Filtering only changes what is shown, never what is checked.
    """

    summary_changed = Signal(str)
    VERB = ""   # the primary button reads "<VERB> selected (n)"

    def __init__(self, on_action: Callable[[tuple[str, ...]], None], on_close: Callable[[], None], parent=None):
        super().__init__(parent)
        self.on_action = on_action
        self.confirm: Callable[[list], bool] = lambda _rows: True
        self.summary = ""
        self.running = False
        self.data: list = []
        self.items: list[tuple[QTreeWidgetItem, object]] = []
        self.checked: dict[tuple[str, str], object] = {}
        self.filter_values: dict[str, Callable[[object], str]] = {}
        self.filter_labels: dict[str, dict[str, str]] = {}
        self.filter_buttons: dict[str, dict[str, QPushButton]] = {}
        self.picked: dict[str, str] = {}
        self.search: QLineEdit | None = None
        self.tree: QTreeWidget | None = None
        self.locked: list[QWidget] = []          # disabled while something runs
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 10, 0, 0)
        self.layout_.setSpacing(10)
        self.toolbar = QHBoxLayout()
        self.toolbar.setSpacing(8)
        self.layout_.addLayout(self.toolbar)
        self.busy = make_busy()
        self.status = dim_label()
        self.status.setWordWrap(False)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(lambda _c=False: on_close())
        self.primary = QPushButton()
        self.primary.setObjectName("primary")
        self.primary.clicked.connect(lambda: self.submit(self.selected_rows()))

    # --- hooks ---------------------------------------------------------------------

    def row_key(self, row) -> tuple[str, str]:
        return row.key

    def checkable(self, _row) -> bool:
        return True

    def linked(self, row) -> list:
        """The rows that are checked and unchecked together with `row`."""
        return [row]

    def matches_text(self, _row, _text: str) -> bool:
        return True

    def make_item(self, row) -> QTreeWidgetItem:
        raise NotImplementedError

    def build_items(self) -> list[tuple[QTreeWidgetItem, object]]:
        """Fill the (cleared) tree; flat by default."""
        items = [(self.make_item(r), r) for r in self.data]
        for item, _r in items:
            self.tree.addTopLevelItem(item)
        return items

    def args_for(self, rows: list) -> tuple[str, ...] | None:
        """Action arguments for these rows; None = nothing to do."""
        return tuple(r.arg for r in rows) if rows else None

    def extra_busy(self) -> bool:
        """Something besides an action keeps the busy bar on (a search)."""
        return False

    def after_filter(self) -> None:
        """Called after every filter change (section headers, notes)."""

    def after_selection(self) -> None:
        """Called after every selection change."""

    # --- building ----------------------------------------------------------------

    def add_search(self, placeholder: str, filters: bool = True) -> QLineEdit:
        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        if filters:
            self.search.textChanged.connect(lambda _t: self._apply_filter())
        self.toolbar.addWidget(self.search, 1)
        return self.search

    def add_filter(self, name: str, labels: dict[str, str], value: Callable[[object], str]) -> dict[str, QPushButton]:
        """Segments All / one per label; a row is shown when value(row) is the picked one."""
        segments, buttons = make_segments(filter_options(labels), lambda key: self.set_filter(name, key), self)
        self.filter_values[name], self.filter_labels[name] = value, labels
        self.filter_buttons[name], self.picked[name] = buttons, ALL
        self.toolbar.addWidget(segments)
        return buttons

    def add_refresh(self, tooltip: str, on_refresh: Callable[[], None]) -> QPushButton:
        button = QPushButton(QIcon.fromTheme("view-refresh"), "Refresh")
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _c=False: on_refresh())
        self.toolbar.addWidget(button)
        self.locked.append(button)
        return button

    def add_tree(self, columns: Iterable[str]) -> QTreeWidget:
        self.tree = make_tree(columns)
        self.tree.itemChanged.connect(self._item_changed)
        self.layout_.addWidget(self.tree, 1)
        self.locked.append(self.tree)
        return self.tree

    def add_bottom_row(self, *others: QPushButton) -> None:
        row = QHBoxLayout()
        row.addWidget(self.busy)
        row.addWidget(self.status)
        row.addStretch(1)
        row.addWidget(self.close_button)
        row.addWidget(self.primary)
        self.layout_.addLayout(row)
        match_heights(self.close_button, self.primary, *others)

    # --- content -----------------------------------------------------------------

    def show_rows(self, rows: Iterable, new_checked: bool = False, keep_missing: bool = False) -> None:
        """Replace the rows. Checked rows stay checked while they are still listed (`keep_missing`: also when not,
        so picks from several searches add up); rows that were not listed before start checked when `new_checked`."""
        before = {self.row_key(r) for r in self.data}
        self.data = list(rows)
        self.checked = self._carried_checks(before, new_checked, keep_missing)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = self.build_items()
        for item, r in self.items:
            item.setData(0, Qt.ItemDataRole.UserRole, self.row_key(r))
        self.tree.blockSignals(False)
        self._sync_checks()
        self.refresh_view()

    def _carried_checks(self, before: set, new_checked: bool, keep_missing: bool) -> dict[tuple[str, str], object]:
        """The checked rows after `data` was replaced (fresh row objects for the rows still listed)."""
        now = {self.row_key(r): r for r in self.data if self.checkable(r)}
        kept = {k: r for k, r in self.checked.items() if keep_missing or k in now}
        fresh = {k: r for k, r in now.items() if k in kept or (new_checked and k not in before)}
        return {**kept, **fresh}

    def refresh_view(self) -> None:
        """Counts on the filter segments, then the filter and the primary button."""
        for name, labels in self.filter_labels.items():
            value = self.filter_values[name]
            for key, label in filter_options(labels):
                n = sum(1 for r in self.data if key == ALL or value(r) == key)
                self.filter_buttons[name][key].setText(f"{label}  {n}")
        self._apply_filter()
        self._update_button()

    def set_summary(self, text: str) -> None:
        self.summary = text
        self.summary_changed.emit(text)

    def show_status(self, text: str) -> None:
        self.status.setText(text)

    # --- selection -----------------------------------------------------------------

    def selected_rows(self) -> list:
        """Checked rows in list order, then checked rows no longer listed (earlier searches)."""
        listed = [r for r in self.data if self.row_key(r) in self.checked]
        keys = {self.row_key(r) for r in listed}
        return listed + [r for k, r in self.checked.items() if k not in keys]

    def selected(self) -> set[tuple[str, str]]:
        return set(self.checked)

    def set_checked(self, key: tuple[str, str], checked: bool) -> None:
        row = next((r for r in self.data if self.row_key(r) == key), None)
        if row is not None and self.checkable(row):
            for r in self.linked(row):
                if checked:
                    self.checked[self.row_key(r)] = r
                else:
                    self.checked.pop(self.row_key(r), None)
        self._sync_checks()
        self._update_button()

    def clear_selection(self) -> None:
        self.checked = {}
        self._sync_checks()
        self._update_button()

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if column == 0 and key is not None:
            self.set_checked(tuple(key), item.checkState(0) == Qt.CheckState.Checked)

    def _sync_checks(self) -> None:
        # itemChanged fires on programmatic changes too: blocked, or every sync would toggle again
        self.tree.blockSignals(True)
        for item, r in self.items:
            item.setCheckState(0, Qt.CheckState.Checked if self.row_key(r) in self.checked else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)

    def action_args(self) -> tuple[str, ...] | None:
        return self.args_for(self.selected_rows())

    def submit(self, rows: list) -> None:
        """Hand these rows to the tab's action (after its confirmation); rows that cannot be checked never go."""
        rows = [r for r in rows if self.checkable(r)]
        args = self.args_for(rows)
        if args is not None and not self.running and self.confirm(rows):
            self.on_action(args)

    def _update_button(self) -> None:
        n = len(self.selected_rows())
        self.primary.setText(f"{self.VERB} selected ({n})")
        self.primary.setEnabled(n > 0 and not self.running)
        self.after_selection()

    # --- filter ------------------------------------------------------------------

    def set_filter(self, name: str, key: str) -> None:
        self.picked[name] = key
        self.filter_buttons[name][key].setChecked(True)
        self._apply_filter()

    def visible(self, row) -> bool:
        text = self.search.text().strip().lower() if self.search is not None else ""
        return (all(key == ALL or self.filter_values[name](row) == key for name, key in self.picked.items())
                and self.matches_text(row, text))

    def _apply_filter(self) -> None:
        for item, r in self.items:
            item.setHidden(not self.visible(r))
        self.after_filter()

    def visible_rows(self) -> list:
        return [r for item, r in self.items if not item.isHidden()]

    # --- running -----------------------------------------------------------------

    def set_running(self, running: bool, text: str = "") -> None:
        """Busy indicator while something runs for this tab; the list and the buttons wait meanwhile."""
        self.running = running
        self.busy.setVisible(running or self.extra_busy())
        self.show_status(text)
        for widget in self.locked:
            widget.setEnabled(not running)
        self._update_button()
