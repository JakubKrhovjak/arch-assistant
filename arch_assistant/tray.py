"""System tray application: icon, menu, periodic cycles, notification handling. Qt only lives here."""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QSystemTrayIcon, QVBoxLayout)

from .config import REPO_ROOT, Config, set_action_mode
from .cycle import Cycle, CycleResult, Pending
from .model import ActionResult, Finding, Summary
from .notify import BUTTONS_ASK, URGENCY_CRITICAL, URGENCY_NORMAL, Notifier, result_popup
from .state import State
from .summarizer import template_summary

ASSETS = REPO_ROOT / "assets"
ICONS = {"idle": "tray.svg", "attention": "tray-attention.svg", "busy": "tray-busy.svg"}
NOTIFY_ICON = str(ASSETS / "tray.svg")
EXAMPLE_CONFIG = ASSETS / "config.example.toml"
BUTTON_ACTIONS: list[tuple[str, str]] = [("actions", "Show actions")]


AUTO_LABEL = "Next time run automatically, just notify"


class PendingDialog(QDialog):
    """One row per open action: label (+ confirmation reason), 'next time automatically' box, Run, No."""

    def __init__(self, pending: list[Pending], on_run: Callable[[Pending, bool], None],
                 on_decline: Callable[[Pending], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Arch Assistant – pending actions")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.labels: list[QLabel] = []
        self.auto_boxes: list[QCheckBox] = []
        self.run_buttons: list[QPushButton] = []
        self.decline_buttons: list[QPushButton] = []
        layout = QVBoxLayout(self)
        if not pending:
            layout.addWidget(QLabel("No pending actions."))
        for p in pending:
            row = QHBoxLayout()
            text = p.label + (f"\n⚠ {p.reason}" if p.reason else "")
            label = QLabel(text)
            label.setWordWrap(True)
            auto = QCheckBox(AUTO_LABEL)
            auto.setToolTip("Sets this action to `auto`: it runs without asking and the result pops up in the tray.")
            run = QPushButton("Run")
            no = QPushButton("No")
            run.clicked.connect(lambda _c=False, p=p, auto=auto: (on_run(p, auto.isChecked()), self.close()))
            no.clicked.connect(lambda _c=False, p=p: (on_decline(p), self.close()))
            texts = QVBoxLayout()
            texts.addWidget(label)
            texts.addWidget(auto)
            row.addLayout(texts, 1)
            row.addWidget(run)
            row.addWidget(no)
            layout.addLayout(row)
            self.labels.append(label)
            self.auto_boxes.append(auto)
            self.run_buttons.append(run)
            self.decline_buttons.append(no)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.resize(600, 120 + 80 * len(pending))


class TrayApp(QObject):
    cycle_finished = Signal(object)                   # CycleResult | None
    action_finished = Signal(object, object, object)  # Pending, ActionResult, list[Finding]

    def __init__(self, cfg: Config, state: State, log: logging.Logger, api_key: str | None,
                 notifier: Notifier | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg, self.log = cfg, log
        self.cycle = Cycle(cfg, state, log, api_key)
        self.notifier = notifier or Notifier(icon=NOTIFY_ICON)
        self.notifier.icon = NOTIFY_ICON
        self.pending: list[Pending] = []
        self.busy = False
        self.dialog: PendingDialog | None = None

        self.tray = QSystemTrayIcon(self._icon("idle"), self)
        self.tray.setToolTip("Arch Assistant")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
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
                act = QAction(p.label + (f"  (confirm: {p.reason})" if p.reason else ""), self.menu)
                if p.reason:
                    act.setToolTip(p.reason)
                act.triggered.connect(lambda _c=False, p=p: self.run_pending(p))
                self.menu.addAction(act)
            self.menu.addSeparator()
            self.menu.addAction("Show actions…", self.show_actions)
        else:
            self.menu.addAction("No pending actions").setEnabled(False)
        self.menu.addSeparator()
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
        self.dialog = PendingDialog(self.pending, on_run=self.run_pending, on_decline=self.decline)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

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
            return
        for res in result.auto_run:
            self._popup_result(res, self._label_for(res, result.findings), [])
        self.pending = list(result.pending)   # replaced wholesale: stale entries disappear, surviving ones stay
        self._rebuild_menu()
        if result.summary:
            self._notify_summary(result.summary, result.new_pending, result.new)

    def _notify_summary(self, summary: Summary, new_pending: list[Pending], new: list[Finding]) -> None:
        urgency = URGENCY_CRITICAL if any(f.severity == "critical" for f in new) else URGENCY_NORMAL
        self.log.info("notify: %s (%d new pending)", summary.headline, len(new_pending))
        if len(new_pending) == 1:
            p = new_pending[0]
            body = summary.body + (f"\n\n{p.reason}" if p.reason else "")
            self.notifier.send(Summary(summary.headline, body, summary.risks), BUTTONS_ASK,
                               lambda key, p=p: self._on_button(key, p),
                               on_closed=lambda: self.log.info("notification closed, %s stays pending", p.action.id),
                               urgency=urgency)
        elif new_pending:
            self.notifier.send(summary, BUTTON_ACTIONS, lambda key: self.show_actions(), urgency=urgency)
        else:
            self.notifier.send(summary, [], lambda _k: None, urgency=urgency)

    def _on_button(self, key: str, p: Pending) -> None:
        if key == "no":
            self.decline(p)
        elif key in ("yes", "yes_auto"):
            self.run_pending(p, auto=(key == "yes_auto"))

    # --- actions -----------------------------------------------------------

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
            self.notifier.send(Summary("Arch Assistant is busy", f"{p.action.label} stays in the menu, try again later.", []),
                               [], lambda _k: None, timeout_ms=5000)
            return
        self.busy = True
        self._refresh_icon()

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
        if res.ok:
            self._remove_pending(p)
        self._rebuild_menu()
        self._popup_result(res, p.action.label, extra)

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


def run_tray(cfg: Config, state: State, log: logging.Logger, api_key: str | None) -> int:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("arch-assistant")
    app.setDesktopFileName("arch-assistant")
    tray = TrayApp(cfg, state, log, api_key)  # noqa: F841 - keeps the tray alive
    return app.exec()
