"""System tray application: icon, menu, periodic cycles, notification handling, actions and the Updates tab; the one
owner of the busy and pending state. The Installed/Browse flows and the uninstall guard live in
ui/packages_controller.py, the windows and dialogs in the rest of the ui package; Qt is imported only there and here."""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .actions import effective_mode, needs_confirmation
from .checks import run_all
from .config import Config, check_env, set_action_mode
from .cycle import Cycle, CycleResult, Pending, merge_findings
from .model import Action, ActionResult, Finding, Summary
from .notify import BUTTONS_ASK, URGENCY_CRITICAL, URGENCY_NORMAL, Notifier, result_popup
from .single_instance import InstanceLock
from .state import State
from .summarizer import template_summary
from .ui.packages_controller import PackagesController
from .ui.packages_tab import BUSY_TEXT
from .ui.packages_window import PackagesWindow
from .ui.pending_dialog import PendingDialog
from .ui.widgets import ASSETS, NOTIFY_ICON

ICONS = {"idle": "tray.svg", "attention": "tray-attention.svg", "busy": "tray-busy.svg"}
EXAMPLE_CONFIG = ASSETS / "config.example.toml"
BUTTON_ACTIONS: list[tuple[str, str]] = [("actions", "Show actions")]
# Updates are never run straight from a notification: the button opens the Updates tab
BUTTONS_UPDATES: list[tuple[str, str]] = [("updates", "Show updates"), ("no", "Later")]


class TrayApp(QObject):
    cycle_finished = Signal(object)                   # CycleResult | None
    action_finished = Signal(object, object, object)  # Pending, ActionResult, list[Finding]
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
        self.busy = False                      # a check cycle, an updates refresh or an action runs
        self.removal_waiting = False           # an uninstall is being planned and confirmed (PackagesController)
        self.dialog: PendingDialog | None = None
        self.packages_window: PackagesWindow | None = None
        self.packages = PackagesController(self, log, self)

        self.tray = QSystemTrayIcon(self._icon("idle"), self)
        self.tray.setToolTip("Arch Assistant")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._rebuild_menu()
        self.tray.show()

        self.cycle_finished.connect(self._on_cycle_finished)
        self.action_finished.connect(self._on_action_finished)
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

    def window(self) -> PackagesWindow:
        """The Packages window, created on first use; its Installed and Browse flows live in PackagesController."""
        if self.packages_window is None:
            self.packages_window = PackagesWindow(
                self.updates, on_update=self._update_selected, on_refresh=self.refresh_updates,
                on_load_installed=self.packages.load_installed, on_uninstall=self.packages.uninstall_selected,
                on_search=self.packages.search_packages, on_install=self.packages.install_selected,
                loaded=self.updates_loaded)
        return self.packages_window

    # the narrow interface PackagesController asks (PackagesHost); busy and pending state stay here

    def blocked(self) -> bool:
        return self.busy or self.removal_waiting

    def hold(self, waiting: bool) -> None:
        self.removal_waiting = waiting

    def finish_row(self, p: Pending, note: str) -> None:
        if self.dialog is not None:
            self.dialog.finish_row(p, ok=False, note=note)

    def show_packages(self, tab: str = "updates") -> None:
        """Open the Packages window on a tab; the Updates tab also re-reads the last Timeshift snapshot."""
        w = self.window()
        if self.busy:
            w.updates_tab.set_running(True, "Checking…")
        elif not self.updates_loaded:
            self.refresh_updates()
        w.show_tab(tab)
        if tab == "updates":
            self.packages.load_snapshot()
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
                self.packages_window.updates_tab.show_status(BUSY_TEXT)
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
        self.packages.run_from_tab(p, self.window().updates_tab, "Updating…")

    # --- cycle -------------------------------------------------------------

    def check_now(self) -> None:
        if self.blocked():
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

    def run_pending(self, p: Pending, auto: bool = False, confirmed: bool = False) -> None:
        """Run a pending action. An uninstall that was not confirmed yet (from the pending window or a
        notification, e.g. unused-apps) first gets the same removal set, guard and confirmation as the Installed tab."""
        if auto:
            set_action_mode(self.cfg.config_dir, p.action.id, "auto")
            self.cfg.actions[p.action.id] = "auto"
            self.log.info("action %s set to auto", p.action.id)
        if self.blocked():
            self.log.info("action %s requested while busy; stays pending", p.action.id)
            if self.dialog is not None:
                self.dialog.finish_row(p, ok=False)
            self.notifier.send(Summary("Arch Assistant is busy", f"{p.action.label} stays in the menu, try again later.", []),
                               [], lambda _k: None)
            return
        if p.action.id == "uninstall" and not confirmed:
            self.packages.plan_removal(p, None, lambda: self.run_pending(p, confirmed=True))
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
                self.packages.load_installed()
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
            with contextlib.suppress(OSError):   # it may have exited meanwhile
                os.kill(other, signal.SIGUSR1)
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
