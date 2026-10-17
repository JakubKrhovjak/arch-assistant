"""System tray application: icon, menu, periodic cycles, notification handling, dialogs. Qt only lives here."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMenu, QProgressBar,
                               QPushButton, QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .config import REPO_ROOT, Config, set_action_mode
from .cycle import Cycle, CycleResult, Pending
from .diagnose import Diagnosis, collect_context, diagnose
from .model import ActionResult, Finding, Summary
from .notify import BUTTONS_ASK, URGENCY_CRITICAL, URGENCY_NORMAL, Notifier, result_popup
from .single_instance import InstanceLock
from .state import State
from .summarizer import template_summary

ASSETS = REPO_ROOT / "assets"
ICONS = {"idle": "tray.svg", "attention": "tray-attention.svg", "busy": "tray-busy.svg"}
NOTIFY_ICON = str(ASSETS / "tray.svg")
EXAMPLE_CONFIG = ASSETS / "config.example.toml"
BUTTON_ACTIONS: list[tuple[str, str]] = [("actions", "Show actions")]
AUTO_LABEL = "Next time run automatically, just notify"


class PendingDialog(QDialog):
    """One row per open action: label (+ reason), 'next time automatically' box, Diagnose, Run, No.

    The window stays open while an action runs: the row shows a busy bar, disappears on success
    and gets its buttons back on failure (the result itself is reported by the usual notification).
    """

    def __init__(self, pending: list[Pending], on_run: Callable[[Pending, bool], None],
                 on_decline: Callable[[Pending], None], on_diagnose: Callable[[Pending], None] | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Arch Assistant – pending actions")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.on_run, self.on_decline = on_run, on_decline
        self.on_diagnose = on_diagnose or (lambda p: None)
        self.rows: dict[tuple, dict] = {}
        self.labels: list[QLabel] = []
        self.auto_boxes: list[QCheckBox] = []
        self.run_buttons: list[QPushButton] = []
        self.decline_buttons: list[QPushButton] = []
        self.diagnose_buttons: list[QPushButton] = []
        self.layout_ = QVBoxLayout(self)
        self.empty_label = QLabel("No pending actions.")
        self.layout_.addWidget(self.empty_label)
        self.empty_label.setVisible(not pending)
        for p in pending:
            self._add_row(p)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        self.layout_.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.resize(680, 140 + 90 * len(pending))

    def _add_row(self, p: Pending) -> None:
        container = QWidget()
        row = QHBoxLayout(container)
        label = QLabel(p.label + (f"\n⚠ {p.reason}" if p.reason else ""))
        label.setWordWrap(True)
        auto = QCheckBox(AUTO_LABEL)
        auto.setToolTip("Sets this action to `auto`: it runs without asking and the result pops up in the tray.")
        busy = QProgressBar()
        busy.setRange(0, 0)
        busy.setFixedWidth(90)
        busy.setTextVisible(False)
        busy.hide()
        diag = QPushButton("Diagnose")
        diag.setToolTip("Ask Claude what this means and how to fix it (nothing is executed).")
        run = QPushButton("Run")
        no = QPushButton("No")
        diag.clicked.connect(lambda _c=False, p=p: self.on_diagnose(p))
        run.clicked.connect(lambda _c=False, p=p, auto=auto: self._run_clicked(p, auto.isChecked()))
        no.clicked.connect(lambda _c=False, p=p: self._decline_clicked(p))
        texts = QVBoxLayout()
        texts.addWidget(label)
        texts.addWidget(auto)
        row.addLayout(texts, 1)
        row.addWidget(busy)
        row.addWidget(diag)
        row.addWidget(run)
        row.addWidget(no)
        self.layout_.addWidget(container)
        self.rows[p.key] = {"widget": container, "label": label, "auto": auto, "busy": busy,
                            "diag": diag, "run": run, "no": no}
        self.labels.append(label)
        self.auto_boxes.append(auto)
        self.run_buttons.append(run)
        self.decline_buttons.append(no)
        self.diagnose_buttons.append(diag)

    def _run_clicked(self, p: Pending, auto: bool) -> None:
        self.set_running(p)
        self.on_run(p, auto)

    def _decline_clicked(self, p: Pending) -> None:
        self.on_decline(p)
        self._remove_row(p)

    def set_running(self, p: Pending) -> None:
        row = self.rows.get(p.key)
        if row:
            row["busy"].show()
            for key in ("run", "no", "diag", "auto"):
                row[key].setEnabled(False)

    def finish_row(self, p: Pending, ok: bool) -> None:
        row = self.rows.get(p.key)
        if not row:
            return
        if ok:
            self._remove_row(p)
            return
        row["busy"].hide()
        for key in ("run", "no", "diag", "auto"):
            row[key].setEnabled(True)
        row["label"].setText(row["label"].text().split("\n✗")[0] + "\n✗ last run failed, see the notification")

    def _remove_row(self, p: Pending) -> None:
        row = self.rows.pop(p.key, None)
        if row:
            row["widget"].hide()
            row["widget"].deleteLater()
        self.empty_label.setVisible(not self.rows)


class DiagnosisWindow(QDialog):
    """Explanation + suggested commands (read only) with a button that copies the commands."""

    def __init__(self, p: Pending, d: Diagnosis, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Diagnosis – {p.finding.title}")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        cmds = "\n".join(d.commands) if d.commands else "(no commands suggested)"
        self.text.setPlainText(f"{d.explanation}\n\nConfidence: {d.confidence}\n\nSuggested commands (not executed):\n{cmds}")
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        copy = QPushButton("Copy commands")
        copy.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(d.commands)))
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addStretch(1)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.resize(720, 420)


class TrayApp(QObject):
    cycle_finished = Signal(object)                   # CycleResult | None
    action_finished = Signal(object, object, object)  # Pending, ActionResult, list[Finding]
    diagnosis_ready = Signal(object, object)          # Pending, Diagnosis | None

    def __init__(self, cfg: Config, state: State, log: logging.Logger, api_key: str | None,
                 notifier: Notifier | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.cfg, self.log, self.api_key = cfg, log, api_key
        self.cycle = Cycle(cfg, state, log, api_key)
        self.notifier = notifier or Notifier(icon=NOTIFY_ICON)
        self.notifier.icon = NOTIFY_ICON
        self.pending: list[Pending] = []
        self.busy = False
        self.dialog: PendingDialog | None = None
        self.diagnosis_window: DiagnosisWindow | None = None
        self._diagnosing: set[tuple] = set()

        self.tray = QSystemTrayIcon(self._icon("idle"), self)
        self.tray.setToolTip("Arch Assistant")
        self.menu = QMenu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_tray_activated)
        self._rebuild_menu()
        self.tray.show()

        self.cycle_finished.connect(self._on_cycle_finished)
        self.action_finished.connect(self._on_action_finished)
        self.diagnosis_ready.connect(self._on_diagnosis_ready)
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
        self.dialog = PendingDialog(self.pending, on_run=self.run_pending, on_decline=self.decline,
                                    on_diagnose=self.diagnose_pending)
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
            if self.dialog is not None:
                self.dialog.finish_row(p, ok=False)
            self.notifier.send(Summary("Arch Assistant is busy", f"{p.action.label} stays in the menu, try again later.", []),
                               [], lambda _k: None)
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
        if res.ok:
            self._remove_pending(p)
        self._rebuild_menu()
        if self.dialog is not None:
            self.dialog.finish_row(p, res.ok)
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

    # --- diagnosis ---------------------------------------------------------

    def diagnose_pending(self, p: Pending) -> None:
        """Collect context and ask Claude in a worker thread; the result opens a window. Nothing is executed."""
        if p.key in self._diagnosing:
            return
        self._diagnosing.add(p.key)
        self.log.info("diagnose %s requested", p.finding.id)
        if self.dialog is not None and p.key in self.dialog.rows:
            self.dialog.rows[p.key]["diag"].setEnabled(False)

        def work():
            try:
                context = collect_context(p.finding, self.cfg, self.log)
                result = diagnose(p.finding, self.cfg, self.api_key, self.log, context=context)
            except Exception:
                self.log.exception("diagnose %s crashed", p.finding.id)
                result = None
            self.diagnosis_ready.emit(p, result)

        threading.Thread(target=work, name="aa-diagnose", daemon=True).start()

    def _on_diagnosis_ready(self, p: Pending, result: Diagnosis | None) -> None:
        self._diagnosing.discard(p.key)
        if self.dialog is not None and p.key in self.dialog.rows:
            self.dialog.rows[p.key]["diag"].setEnabled(True)
        if result is None:
            self.notifier.send(Summary("Diagnosis unavailable", f"Claude could not be reached for {p.finding.title}; see the log.", []),
                               [], lambda _k: None, urgency=URGENCY_CRITICAL)
            return
        self.log.info("diagnose %s: %s", p.finding.id, result.explanation[:200])
        self.diagnosis_window = DiagnosisWindow(p, result)
        self.diagnosis_window.show()
        self.diagnosis_window.raise_()
        self.diagnosis_window.activateWindow()

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
    lock = InstanceLock(cfg.state_dir / "tray.lock")
    other = lock.acquire()
    if other is not None:
        # launched from the menu while the tray already runs: ask that instance to show its actions
        log.info("tray already running (pid %s); asking it to show actions", other)
        if other > 0:
            try:
                os.kill(other, signal.SIGUSR1)
            except OSError:
                pass
        return 0
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("arch-assistant")
    app.setDesktopFileName("arch-assistant")   # KWin looks up arch-assistant.desktop for the window icon
    app.setWindowIcon(QIcon(NOTIFY_ICON))
    tray = TrayApp(cfg, state, log, api_key)
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
