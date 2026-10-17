"""System tray application: icon, menu, periodic cycles, notification handling. Qt only lives here."""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .config import REPO_ROOT, Config, set_action_mode
from .cycle import Cycle, CycleResult, Pending
from .model import ActionResult, Finding, Summary
from .notify import BUTTON_MENU, BUTTONS_ASK, URGENCY_CRITICAL, URGENCY_NORMAL, Notifier, result_popup
from .state import State
from .summarizer import template_summary

ASSETS = REPO_ROOT / "assets"
ICONS = {"idle": "tray.svg", "attention": "tray-attention.svg", "busy": "tray-busy.svg"}
EXAMPLE_CONFIG = ASSETS / "config.example.toml"


class TrayApp(QObject):
    cycle_finished = Signal(object)          # CycleResult | None
    action_finished = Signal(object, object, object)  # Pending, ActionResult, list[Finding]

    def __init__(self, cfg: Config, state: State, log: logging.Logger, api_key: str | None,
                 notifier: Notifier | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg, self.log = cfg, log
        self.cycle = Cycle(cfg, state, log, api_key)
        self.notifier = notifier or Notifier()
        self.pending: list[Pending] = []
        self.busy = False

        self.tray = QSystemTrayIcon(self._icon("idle"), self)
        self.tray.setToolTip("Arch Assistant")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self._rebuild_menu()
        self.tray.show()

        self.cycle_finished.connect(self._on_cycle_finished)
        self.action_finished.connect(self._on_action_finished)
        self.timer = QTimer(self)
        self.timer.setInterval(int(cfg.interval_hours * 3600 * 1000))
        self.timer.timeout.connect(self.check_now)
        self.timer.start()
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
                act = QAction(p.label, self.menu)
                if p.reason:
                    act.setToolTip(p.reason)
                    act.setText(f"{p.label}  (confirm: {p.reason})")
                act.triggered.connect(lambda _c=False, p=p: self.run_pending(p))
                self.menu.addAction(act)
            self.menu.addSeparator()
        else:
            self.menu.addAction("No pending actions").setEnabled(False)
            self.menu.addSeparator()
        self.menu.addAction("Check now", self.check_now)
        self.menu.addAction("Open log", self.open_log)
        self.menu.addAction("Settings", self.open_settings)
        self.menu.addSeparator()
        self.menu.addAction("Quit", QApplication.instance().quit)
        self._refresh_icon()

    # --- cycle -------------------------------------------------------------

    def check_now(self) -> None:
        if self.busy:
            self.log.info("check requested while busy; ignored")
            return
        self.busy = True
        self._refresh_icon()
        threading.Thread(target=lambda: self.cycle_finished.emit(self.cycle.run()),
                         name="aa-cycle", daemon=True).start()

    def _on_cycle_finished(self, result: CycleResult | None) -> None:
        self.busy = False
        if result is None:
            self._rebuild_menu()
            return
        for res in result.auto_run:
            self._popup_result(res, self._label_for(res, result.findings), [])
        known = {(p.finding.id, p.action.id, p.action.args) for p in self.pending}
        self.pending += [p for p in result.pending if (p.finding.id, p.action.id, p.action.args) not in known]
        self._rebuild_menu()
        if result.summary:
            self._notify_summary(result.summary, result.pending, result.new)

    def _notify_summary(self, summary: Summary, new_pending: list[Pending], new: list[Finding]) -> None:
        urgency = URGENCY_CRITICAL if any(f.severity == "critical" for f in new) else URGENCY_NORMAL
        self.log.info("notify: %s (%d pending)", summary.headline, len(new_pending))
        if len(new_pending) == 1:
            p = new_pending[0]
            body = summary.body + (f"\n\n{p.reason}" if p.reason else "")
            self.notifier.send(Summary(summary.headline, body, summary.risks), BUTTONS_ASK,
                               lambda key, p=p: self._on_button(key, p),
                               on_closed=lambda: self.log.info("notification closed, %s stays pending", p.action.id),
                               urgency=urgency)
        elif new_pending:
            self.notifier.send(summary, BUTTON_MENU, lambda key: self.menu.popup(QCursor.pos()), urgency=urgency)
        else:
            self.notifier.send(summary, [], lambda _k: None, urgency=urgency)

    def _on_button(self, key: str, p: Pending) -> None:
        if key == "no":
            self.cycle.decline(p)
            self._remove_pending(p)
            self.log.info("declined %s for %s", p.action.id, p.finding.id)
        elif key in ("yes", "yes_auto"):
            if key == "yes_auto":
                set_action_mode(self.cfg.config_dir, p.action.id, "auto")
                self.cfg.actions[p.action.id] = "auto"
                self.log.info("action %s set to auto", p.action.id)
            self.run_pending(p)

    # --- actions -----------------------------------------------------------

    def run_pending(self, p: Pending) -> None:
        if self.busy:
            self.log.info("action %s requested while busy; stays pending", p.action.id)
            return
        self.busy = True
        self._refresh_icon()

        def work():
            res, extra = self.cycle.run_pending(p)
            self.action_finished.emit(p, res, extra)

        threading.Thread(target=work, name="aa-action", daemon=True).start()

    def _on_action_finished(self, p: Pending, res: ActionResult, extra: list[Finding]) -> None:
        self.busy = False
        if res.ok:
            self._remove_pending(p)
        self._rebuild_menu()
        self._popup_result(res, p.action.label, extra)

    def _popup_result(self, res: ActionResult, label: str, extra: list[Finding]) -> None:
        notes = [f"{f.title}" + (f" – {f.summary}" if f.summary else "") for f in extra if not f.is_empty]
        if notes:
            res = ActionResult(res.action_id, res.ok, res.output + "\n" + "\n".join(notes), res.detail, res.duration_s)
        result_popup(self.notifier, res, label)
        if any(f.severity == "critical" for f in extra):
            crit = [f for f in extra if f.severity == "critical"]
            self.notifier.send(template_summary(crit), [], lambda _k: None, urgency=URGENCY_CRITICAL)

    def _remove_pending(self, p: Pending) -> None:
        self.pending = [q for q in self.pending if q is not p]
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


def run_tray(cfg: Config, state: State, log: logging.Logger, api_key: str | None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("arch-assistant")
    app.setDesktopFileName("arch-assistant")
    tray = TrayApp(cfg, state, log, api_key)  # noqa: F841 - keeps the tray alive
    return app.exec()
