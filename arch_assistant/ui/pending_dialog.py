"""The pending-actions window (one card per open action) and the read-only list of what an action affects."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from ..cycle import Pending
from .widgets import StyledDialog, make_busy, match_heights

AUTO_LABEL = "Next time run automatically, just notify"
FAILED_NOTE = "✗ last run failed, see the notification"


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
        busy = make_busy()
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

    def finish_row(self, p: Pending, ok: bool, note: str = FAILED_NOTE) -> None:
        """Success removes the card; otherwise its buttons come back with `note` under the label ("" = none,
        e.g. after a cancelled confirmation)."""
        row = self.rows.get(p.key)
        if not row:
            return
        if ok:
            self._remove_row(p)
            return
        row["busy"].hide()
        for key in ("run", "no", "auto"):
            row[key].setEnabled(True)
        row["label"].setText(row["label"].text().split("\n✗")[0] + (f"\n{note}" if note else ""))

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
