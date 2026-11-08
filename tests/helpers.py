"""Helpers shared by the Qt tests (the qapp fixture itself is in conftest.py)."""
from __future__ import annotations

import time


class FakeTransport:
    """A notification transport that records what was sent and never shows anything."""

    def __init__(self):
        self.calls = []

    def notify(self, *args):
        self.calls.append(args)
        return len(self.calls)

    def listen(self, on_action, on_closed):
        pass


def make_tray(tmp_path, log):
    """A TrayApp on empty checks/ and actions/ dirs in tmp_path, its timer stopped; (app, transport)."""
    from arch_assistant.config import load_config
    from arch_assistant.notify import Notifier
    from arch_assistant.state import State
    from arch_assistant.tray import TrayApp
    cfg = load_config(tmp_path / "cfg", tmp_path / "state")
    cfg.checks_dir, cfg.actions_dir = tmp_path / "checks", tmp_path / "actions"
    cfg.checks_dir.mkdir(exist_ok=True)
    cfg.actions_dir.mkdir(exist_ok=True)
    transport = FakeTransport()
    app = TrayApp(cfg, State(tmp_path / "state" / "state.json"), log, notifier=Notifier(transport=transport))
    app.timer.stop()
    return app, transport


def close_tray(app, qapp) -> None:
    """Tear the Qt objects down explicitly, so interpreter exit does not race their destructors."""
    for window in (app.packages_window, app.dialog):
        if window is not None:
            window.close()
    app.tray.hide()
    app.tray.setContextMenu(None)
    app.menu.deleteLater()
    app.tray.deleteLater()
    app.deleteLater()
    qapp.processEvents()


def wait_until(qapp, cond, timeout: float = 5.0) -> bool:
    """Process Qt events until cond() is true (a worker thread emitted its signal); False after the timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False
