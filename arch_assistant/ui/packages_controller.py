"""The flows behind the Packages window: the tab actions, the uninstall guard (removal set in a worker, then a
refusal or a confirmation) and the read-only workers of the tabs (installed list, last snapshot, search).

The tray app alone owns the busy and pending state and the Updates tab; this controller only asks it through
PackagesHost. Every worker here only computes and emits; the slots run on the GUI thread."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import NamedTuple, Protocol

from PySide6.QtCore import QObject, Signal

from ..catalog import SearchResult, search
from ..cycle import Pending
from ..inventory import Inventory, collect
from ..model import Action, Finding
from ..protection import RemovalPlan, removal_plan
from ..snapshots import snapshot_info
from .confirm import confirm_removal, show_refusal
from .packages_tab import BUSY_TEXT, PackagesTab
from .packages_window import PackagesWindow


class PackagesHost(Protocol):
    """What the controller needs from the tray app, the one owner of the busy and pending state."""
    packages_window: PackagesWindow | None

    def window(self) -> PackagesWindow:
        """The Packages window, created on first use."""

    def blocked(self) -> bool:
        """A check or an action runs, or an uninstall waits for its confirmation: nothing else may start."""

    def hold(self, waiting: bool) -> None:
        """An uninstall is being planned and confirmed (True) or done with that (False)."""

    def run_pending(self, p: Pending, auto: bool = False, confirmed: bool = False) -> None:
        """Run an action through the usual path (terminal progress, guards, result popup)."""

    def finish_row(self, p: Pending, note: str) -> None:
        """Give the pending window's row of an action that did not run its buttons back, with `note`."""


class Removal(NamedTuple):
    """An uninstall waiting for its removal set, carried through the worker to the slot."""
    pending: Pending
    tab: PackagesTab | None          # the tab it came from; None from a notification or the pending window
    on_confirmed: Callable[[], None]


class PackagesController(QObject):
    inventory_loaded = Signal(object, object)         # Inventory | None, error str | None
    snapshot_loaded = Signal(object)                  # list[str] | None
    search_finished = Signal(object, object)          # SearchResult | None, error str | None
    removal_planned = Signal(object, object, object)  # Removal, RemovalPlan | None, error str | None

    def __init__(self, host: PackagesHost, log: logging.Logger, parent: QObject | None = None):
        super().__init__(parent)
        self.host, self.log = host, log
        self.inventory_loading = False         # the Installed list is being read (independent of the host's busy)
        self.snapshot_loading = False
        self.confirm_removal = confirm_removal   # the dialogs, replaceable in tests
        self.show_refusal = show_refusal
        self.inventory_loaded.connect(self._on_inventory_loaded)
        self.snapshot_loaded.connect(self._on_snapshot_loaded)
        self.search_finished.connect(self._on_search_finished)
        self.removal_planned.connect(self._on_removal_planned)

    # --- tab actions -------------------------------------------------------

    def uninstall_selected(self, args: tuple[str, ...]) -> None:
        """The Installed tab's "Uninstall selected": removal set, guard and confirmation, then actions/uninstall.sh."""
        p = Pending(Finding("installed", "none", "Installed packages", ""), Action("uninstall", "Uninstall packages", args))
        self.run_from_tab(p, self.host.window().installed_tab, "Uninstalling…")

    def install_selected(self, args: tuple[str, ...]) -> None:
        """The Browse tab's "Install selected" (already confirmed): actions/install.sh in the terminal."""
        p = Pending(Finding("browse", "none", "New packages", ""), Action("install", "Install packages", args))
        self.run_from_tab(p, self.host.window().browse_tab, "Installing…")

    def run_from_tab(self, p: Pending, tab: PackagesTab, text: str) -> None:
        if self.host.blocked():
            tab.show_status(BUSY_TEXT)
            return
        self.log.info("%s selected from the Packages window: args=%s", p.action.id, list(p.action.args))

        def start() -> None:
            tab.set_running(True, text)
            self.host.run_pending(p, confirmed=True)

        if p.action.id == "uninstall":
            self.plan_removal(p, tab, start)
        else:
            start()

    # --- uninstall guard -----------------------------------------------------

    def plan_removal(self, p: Pending, tab: PackagesTab | None, on_confirmed: Callable[[], None]) -> None:
        """Compute what pacman would really remove in a worker; _on_removal_planned refuses or confirms it."""
        self.host.hold(True)
        if tab is not None:
            tab.set_running(True, "Checking what would be removed…")
        removal = Removal(p, tab, on_confirmed)

        def work():
            try:
                self.removal_planned.emit(removal, removal_plan(p.action.args), None)
            except Exception as e:   # a crashed check must not leave the uninstall waiting forever
                self.log.exception("computing the removal set crashed")
                self.removal_planned.emit(removal, None, str(e))

        threading.Thread(target=work, name="aa-removal", daemon=True).start()

    def _on_removal_planned(self, removal: Removal, plan: RemovalPlan | None, error: str | None) -> None:
        if removal.tab is not None:
            removal.tab.set_running(False)
        try:
            # the host stays held while a dialog is open, so no check cycle or second action starts meanwhile
            confirmed = self._decide_removal(removal, plan, error)
        finally:
            self.host.hold(False)
        if confirmed:
            removal.on_confirmed()

    def _decide_removal(self, removal: Removal, plan: RemovalPlan | None, error: str | None) -> bool:
        """Refuse a blocked removal with its reason, otherwise ask with the full removal set."""
        p = removal.pending
        window = self.host.packages_window
        parent = window if window is not None and window.isVisible() else None
        problem = error or (plan.problem if plan is not None else "unknown error")
        if problem:
            self._refuse_removal(removal, plan, problem, parent=parent)
            return False
        known = {r.id: r for r in window.installed_tab.data} if window is not None else {}
        if self.confirm_removal(plan, known, parent):
            return True
        self.log.info("uninstall %s cancelled", list(p.action.args))
        self.host.finish_row(p, note="")
        return False

    def _refuse_removal(self, removal: Removal, plan: RemovalPlan | None, problem: str, *, parent) -> None:
        p = removal.pending
        self.log.warning("uninstall %s refused: %s", list(p.action.args), problem)
        if removal.tab is not None:
            removal.tab.show_status(f"Not uninstalled: {problem}")
        self.host.finish_row(p, note=f"✗ not uninstalled: {problem}")
        self.show_refusal(problem, plan, parent)

    # --- installed list, snapshot info and search (read only, independent of the host's busy) ---------

    def load_installed(self) -> None:
        """Read the installed packages in a worker thread for the Installed tab."""
        if self.inventory_loading:
            return
        self.inventory_loading = True
        self.host.window().installed_tab.set_running(True, "Reading installed packages…")

        def work():
            try:
                self.inventory_loaded.emit(collect(), None)
            except Exception as e:   # a crashed read must not leave the tab busy forever
                self.log.exception("reading installed packages crashed")
                self.inventory_loaded.emit(None, str(e))

        threading.Thread(target=work, name="aa-inventory", daemon=True).start()

    def _on_inventory_loaded(self, inv: Inventory | None, error: str | None) -> None:
        self.inventory_loading = False
        window = self.host.packages_window
        if window is None:
            return
        tab = window.installed_tab
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
            except Exception:   # a crashed read must not keep the snapshot line loading forever
                self.log.exception("reading the snapshot info crashed")
                lines = None
            self.snapshot_loaded.emit(lines)

        threading.Thread(target=work, name="aa-snapshot", daemon=True).start()

    def _on_snapshot_loaded(self, lines: list[str] | None) -> None:
        self.snapshot_loading = False
        if self.host.packages_window is not None:
            self.host.packages_window.updates_tab.set_snapshot(lines)

    def search_packages(self, term: str) -> None:
        """Search the repos, the AUR and Flathub in a worker thread for the Browse tab."""
        def work():
            try:
                self.search_finished.emit(search(term), None)
            except Exception as e:   # the Browse tab waits for an answer: a crash must still send one
                self.log.exception("package search crashed")
                self.search_finished.emit(None, str(e))

        threading.Thread(target=work, name="aa-search", daemon=True).start()

    def _on_search_finished(self, found: SearchResult | None, error: str | None) -> None:
        if self.host.packages_window is not None:
            self.host.packages_window.browse_tab.set_results(found, error)
