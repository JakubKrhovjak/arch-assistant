"""The Updates tab: pending updates with installed → new version and source, selective update."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QTreeWidgetItem

from ..cycle import Pending
from ..packages import SOURCE_LABELS, SOURCES, package_arg
from .packages_tab import PackagesTab
from .widgets import dim_label

REPO_NOTE = "Arch repo packages are updated together: partial upgrades are not supported on Arch."


def package_key(item: dict) -> tuple[str, str]:
    return (str(item.get("source", "")), str(item.get("name", "")))


def _update_order(item: dict) -> tuple:
    """Critical packages first, then repo, AUR, Flatpak, then by name."""
    source = str(item.get("source", ""))
    rank = SOURCES.index(source) if source in SOURCES else len(SOURCES)
    return (not item.get("critical"), rank, str(item.get("name", "")).lower())


def update_args(items: Iterable[dict], selected: set[tuple[str, str]]) -> tuple[str, ...] | None:
    """Args of actions/update.sh for the selected (source, name) keys, in list order.

    Everything selected = no args (the full update). The tab checks repo packages all or none, and update.sh
    upgrades all of them for any repo argument, since Arch does not support partial upgrades. None = nothing selected.
    """
    keys = [package_key(i) for i in items]
    chosen = [k for k in keys if k in selected]
    if not chosen:
        return None
    if len(chosen) == len(keys):
        return ()
    return tuple(package_arg(source, name) for source, name in chosen)


class UpdatesTab(PackagesTab):
    """bauh-like list of pending updates: name, installed → new version, source; selective update.

    Repo packages are all-or-nothing (toggling one toggles all of them), AUR packages and Flatpaks can be
    picked one by one; everything starts checked. A dimmed line below tells when the last Timeshift snapshot
    was taken.
    """

    VERB = "Update"
    COLUMNS = ("Name", "Installed", "", "New", "Source")

    def __init__(self, on_update: Callable[[tuple[str, ...]], None], on_refresh: Callable[[], None],
                 on_close: Callable[[], None], parent=None):
        super().__init__(on_update, on_close, parent)
        self.pending: Pending | None = None
        self.summary = "Checking for updates…"
        self.add_search("Search packages")
        self.add_filter("source", SOURCE_LABELS, lambda item: str(item.get("source", "")))
        self.refresh_button = self.add_refresh("Check for updates again (repo, AUR and Flatpak).", on_refresh)
        self.add_tree(self.COLUMNS)
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
        self.add_bottom_row(self.refresh_button)
        self.refresh_view()

    # --- hooks ---------------------------------------------------------------------

    def row_key(self, row: dict) -> tuple[str, str]:
        return package_key(row)

    def linked(self, row: dict) -> list[dict]:
        """Any repo package stands for all of them."""
        return [i for i in self.data if i.get("source") == "repo"] if row.get("source") == "repo" else [row]

    def matches_text(self, row: dict, text: str) -> bool:
        return text in str(row.get("name", "")).lower()

    def args_for(self, rows: list[dict]) -> tuple[str, ...] | None:
        return update_args(self.data, {package_key(i) for i in rows})

    def make_item(self, item: dict) -> QTreeWidgetItem:
        critical = bool(item.get("critical"))
        name = str(item.get("name", ""))
        source = str(item.get("source", ""))
        row = QTreeWidgetItem([f"⚠ {name}" if critical else name, str(item.get("old") or "?"), "→",
                               str(item.get("new", "")), SOURCE_LABELS.get(source, source)])
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        dim = self.palette().placeholderText()
        for col in (1, 2, 4):
            row.setForeground(col, dim)
        bold = row.font(3)
        bold.setBold(True)
        row.setFont(3, bold)
        row.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
        if critical:
            row.setToolTip(0, "Critical package: read the update notes before rebooting.")
        return row

    # --- content -------------------------------------------------------------

    def set_pending(self, pending: Pending | None, error: str | None = None) -> None:
        """Show the current updates finding (None = nothing to update); an `error` keeps the old list."""
        if error is not None:
            self.set_summary(f"Update check failed: {error}")
            return
        self.pending = pending
        reason = pending.reason if pending else None
        self.reason_label.setText(f"⚠ {reason}" if reason else "")
        self.show_rows(sorted(pending.finding.items if pending else (), key=_update_order), new_checked=True)
        self.set_summary(f"{len(self.data)} updates available" if self.data else "System is up to date")

    def set_snapshot(self, lines: list[str] | None) -> None:
        """The last Timeshift snapshot and the timeshift-autosnap note; None = unknown, the line is hidden."""
        self.snapshot_label.setText("\n".join(lines or ()))
        self.snapshot_label.setVisible(bool(lines))

    def refresh_view(self) -> None:
        self.repo_note.setVisible(any(i.get("source") == "repo" for i in self.data))
        self.reason_label.setVisible(bool(self.reason_label.text()))
        super().refresh_view()
