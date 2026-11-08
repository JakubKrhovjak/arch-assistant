"""The Browse tab: one search over the Arch repos, the AUR and Flathub, install what is picked."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItem

from ..catalog import MIN_TERM, Result, SearchResult, normalize
from ..packages import SOURCE_LABELS
from .confirm import confirm_install
from .packages_tab import PackagesTab
from .widgets import dim_label

SEARCH_DEBOUNCE_MS = 500
AUR_NOTE = ("AUR packages are made by users, not by Arch, and are built from a PKGBUILD on this machine: "
            "check the package page and its votes before installing.")


class BrowseTab(PackagesTab):
    """Find and install new software: the search runs in a worker (debounced, at least MIN_TERM characters, Enter
    searches at once). Installed results are marked and cannot be picked; picks from several searches add up and
    are installed after a confirmation that lists them."""

    VERB = "Install"
    COLUMNS = ("Name", "Description", "Version", "Source", "")

    def __init__(self, on_install: Callable[[tuple[str, ...]], None], on_search: Callable[[str], None],
                 on_close: Callable[[], None], confirm: Callable[[list[Result]], bool] | None = None, parent=None):
        super().__init__(on_install, on_close, parent)
        self.on_search = on_search
        self.confirm = confirm or (lambda rows: confirm_install(rows, self.window()))
        self.term = ""            # what the shown results are for
        self.searching = ""       # the term a search is running for ("" = none)
        self.summary = "Search the Arch repos, the AUR and Flathub"
        search = self.add_search(f"Search apps and packages (at least {MIN_TERM} characters, Enter to search now)",
                                 filters=False)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(SEARCH_DEBOUNCE_MS)
        self.debounce.timeout.connect(self.start_search)
        search.textChanged.connect(lambda _t: self.debounce.start())
        search.returnPressed.connect(self.start_search)
        self.add_filter("source", SOURCE_LABELS, lambda r: r.source)
        self.add_tree(self.COLUMNS)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 260)
        self.aur_note = dim_label(AUR_NOTE)
        self.aur_note.hide()
        self.layout_.addWidget(self.aur_note)
        self.add_bottom_row()
        self.refresh_view()

    # --- hooks ---------------------------------------------------------------------

    def checkable(self, row: Result) -> bool:
        return not row.installed

    def extra_busy(self) -> bool:
        return bool(self.searching)

    def make_item(self, r: Result) -> QTreeWidgetItem:
        item = QTreeWidgetItem([r.name, r.description, r.version, SOURCE_LABELS.get(r.source, r.source),
                                "Installed" if r.installed else ""])
        for col in (1, 2, 3, 4):
            item.setForeground(col, self.palette().placeholderText())
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
        return item

    def after_filter(self) -> None:
        self.after_selection()

    def after_selection(self) -> None:
        """The AUR note shows while an AUR package is picked or listed."""
        self.aur_note.setVisible(any(r.source == "aur" for r in self.checked.values())
                                 or any(r.source == "aur" for r in self.visible_rows()))

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
        self.term = found.term
        self.show_rows(found.results, keep_missing=True)
        self.set_summary(f"{len(self.data)} results for “{found.term}”" if self.data else f"Nothing found for “{found.term}”")
        self.show_status("Not searched: " + "; ".join(found.errors) if found.errors else "")
