"""The Packages window: the Updates, Installed and Browse tabs under one header."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QTabWidget

from ..cycle import Pending
from .browse_tab import BrowseTab
from .installed_tab import InstalledTab
from .packages_tab import PackagesTab
from .updates_tab import UpdatesTab
from .widgets import StyledDialog


class PackagesWindow(StyledDialog):
    """One window for packages: Updates (what can be updated), Installed (what is there and what can be
    uninstalled) and Browse (find and install new software). The header subtitle follows the current tab's
    summary; the Installed tab asks for its list the first time it is opened."""

    TABS = ("updates", "installed", "browse")

    def __init__(self, pending: Pending | None, *, on_update: Callable[[tuple[str, ...]], None],
                 on_refresh: Callable[[], None], on_load_installed: Callable[[], None] = lambda: None,
                 on_uninstall: Callable[[tuple[str, ...]], None] = lambda args: None,
                 on_search: Callable[[str], None] = lambda term: None,
                 on_install: Callable[[tuple[str, ...]], None] = lambda args: None, loaded: bool = True,
                 confirm_install: Callable[[list], bool] | None = None, parent=None):
        super().__init__("Arch Assistant – packages", parent)
        self.on_load_installed = on_load_installed
        self.subtitle = self.add_header("Packages", "")
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.updates_tab = UpdatesTab(on_update, on_refresh, self.close)
        self.installed_tab = InstalledTab(on_uninstall, on_load_installed, self.close)
        self.browse_tab = BrowseTab(on_install, on_search, self.close, confirm_install)
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
