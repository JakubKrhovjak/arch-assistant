"""The Installed tab: explicit Arch packages, AUR packages and Flatpak apps grouped into Apps, Utilities, System."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QTreeWidgetItem

from ..inventory import CATEGORY_LABELS, Installed, Inventory
from ..packages import SOURCE_LABELS
from ..protection import PROTECTED_REASON
from .packages_tab import PackagesTab
from .widgets import dim_label

GENERIC_ICONS = {"apps": "application-x-executable", "utilities": "package-x-generic",
                 "system": "package-x-generic"}


def row_icon(row: Installed) -> QIcon:
    """The .desktop / Flatpak icon from the icon theme (or its absolute path), else a generic package icon."""
    icon = QIcon(row.icon) if row.icon.startswith("/") else QIcon.fromTheme(row.icon) if row.icon else QIcon()
    return icon if not icon.isNull() else QIcon.fromTheme(GENERIC_ICONS.get(row.category, "package-x-generic"))


class InstalledTab(PackagesTab):
    """What is installed and what can go. System rows are protected (no checkbox); the rest can be checked and
    handed to `on_uninstall`, which computes the real removal set, guards it and confirms it (TrayApp)."""

    VERB = "Uninstall"
    COLUMNS = ("Name", "Package", "Version", "Source")

    def __init__(self, on_uninstall: Callable[[tuple[str, ...]], None], on_refresh: Callable[[], None],
                 on_close: Callable[[], None], parent=None):
        super().__init__(on_uninstall, on_close, parent)
        self.loaded = False
        self.summary = "Not loaded yet"
        self.headers: dict[str, QTreeWidgetItem] = {}
        self.add_search("Search name or package").setMinimumWidth(240)
        self.add_filter("category", CATEGORY_LABELS, lambda r: r.category)
        self.add_filter("source", SOURCE_LABELS, lambda r: r.source)
        self.refresh_button = self.add_refresh("Read the installed packages and Flatpaks again.", on_refresh)
        self.add_tree(self.COLUMNS)
        self.tree.setUniformRowHeights(False)   # section headers are taller than the rows
        self.tree.setItemsExpandable(False)
        self.tree.setIndentation(0)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.note = dim_label("Click a row to select it, right-click to uninstall a single package. "
                              "Only explicitly installed packages are listed, not their dependencies; the "
                              "confirmation shows every dependency that would go with them. "
                              "System packages are protected and cannot be uninstalled here.")
        self.layout_.addWidget(self.note)
        self.add_bottom_row(self.refresh_button)
        self.refresh_view()

    # --- hooks ---------------------------------------------------------------------

    def checkable(self, row: Installed) -> bool:
        return not row.protected

    def matches_text(self, row: Installed, text: str) -> bool:
        return text in row.name.lower() or text in row.id.lower()

    def build_items(self) -> list[tuple[QTreeWidgetItem, Installed]]:
        """One bold section header per category, the rows below it."""
        self.headers, items = {}, []
        dim = self.palette().placeholderText()
        for category, label in CATEGORY_LABELS.items():
            rows = [r for r in self.data if r.category == category]
            if not rows:
                continue
            header = QTreeWidgetItem([label])
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = header.font(0)
            font.setBold(True)
            header.setFont(0, font)
            header.setForeground(0, dim)
            self.tree.addTopLevelItem(header)
            header.setFirstColumnSpanned(True)
            self.headers[category] = header
            for r in rows:
                item = self.make_item(r)
                header.addChild(item)
                items.append((item, r))
        self.tree.expandAll()
        return items

    def make_item(self, r: Installed) -> QTreeWidgetItem:
        item = QTreeWidgetItem([r.name, r.id, r.version, SOURCE_LABELS.get(r.source, r.source)])
        item.setIcon(0, row_icon(r))
        for col in (1, 2, 3):
            item.setForeground(col, self.palette().placeholderText())
        if r.protected:
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            for col in range(len(self.COLUMNS)):
                item.setToolTip(col, PROTECTED_REASON)
        else:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            if r.source == "flatpak":
                item.setToolTip(0, f"Flatpak app ({r.installation} installation)")
        return item

    def after_filter(self) -> None:
        shown = {c: sum(1 for item, r in self.items if r.category == c and not item.isHidden()) for c in self.headers}
        for category, header in self.headers.items():
            header.setHidden(not shown[category])
            header.setText(0, f"{CATEGORY_LABELS[category]}  ·  {shown[category]}")

    # --- content -------------------------------------------------------------

    def set_inventory(self, inv: Inventory | None, error: str | None = None) -> None:
        """Show what is installed; an `error` keeps the old list."""
        if error is not None or inv is None:
            self.set_summary(f"Reading installed packages failed: {error or 'unknown error'}")
            return
        self.loaded = True
        self.show_rows(inv.rows)
        counts = {c: sum(1 for r in self.data if r.category == c) for c in CATEGORY_LABELS}
        self.set_summary(f"{len(self.data)} installed: {counts['apps']} apps, {counts['utilities']} utilities, "
                         f"{counts['system']} system")
        problems = ", ".join(inv.errors)
        self.show_status(f"Could not read: {problems}" if problems else "")

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
        act.triggered.connect(lambda _c=False, r=r: self.submit([r]))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
