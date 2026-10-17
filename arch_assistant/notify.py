"""Desktop notifications with buttons via D-Bus org.freedesktop.Notifications (Plasma popups).

Uses jeepney for the wire protocol because PySide6's QtDBus cannot type Python values as
uint32 / string arrays, which the notification daemon rejects. Signals (ActionInvoked,
NotificationClosed) are received on a background thread and forwarded to the Qt main thread.
"""
from __future__ import annotations

import threading
from typing import Callable, Protocol

from jeepney import DBusAddress, MatchRule, message_bus, new_method_call
from jeepney.io.blocking import open_dbus_connection
from PySide6.QtCore import QObject, Signal, Slot

from .model import ActionResult, Summary

NOTIFICATIONS = DBusAddress("/org/freedesktop/Notifications", bus_name="org.freedesktop.Notifications",
                            interface="org.freedesktop.Notifications")

BUTTONS_ASK: list[tuple[str, str]] = [("yes", "Yes"), ("yes_auto", "Yes, automatically next time"), ("no", "No")]
BUTTON_MENU: list[tuple[str, str]] = [("menu", "Open menu")]
URGENCY_LOW, URGENCY_NORMAL, URGENCY_CRITICAL = 0, 1, 2
RESULT_TIMEOUT_MS = 15000


def build_body(summary: Summary) -> str:
    return summary.body + (f"\n\nRisks: {'; '.join(summary.risks)}" if summary.risks else "")


class Transport(Protocol):
    def notify(self, app_name: str, replaces_id: int, icon: str, headline: str, body: str,
               actions: list[str], hints: dict, timeout_ms: int) -> int: ...

    def listen(self, on_action: Callable[[int, str], None], on_closed: Callable[[int, int], None]) -> None: ...


class JeepneyTransport:
    """One connection for sending (main thread), one for receiving broadcast signals (daemon thread)."""

    def __init__(self):
        self._send = open_dbus_connection(bus="SESSION")
        self._lock = threading.Lock()

    def notify(self, app_name, replaces_id, icon, headline, body, actions, hints, timeout_ms) -> int:
        typed_hints = {k: _variant(v) for k, v in hints.items()}
        msg = new_method_call(NOTIFICATIONS, "Notify", "susssasa{sv}i",
                              (app_name, replaces_id, icon, headline, body, actions, typed_hints, timeout_ms))
        with self._lock:
            reply = self._send.send_and_get_reply(msg, timeout=5)
        return int(reply.body[0])

    def listen(self, on_action, on_closed) -> None:
        def loop():
            conn = open_dbus_connection(bus="SESSION")
            for member in ("ActionInvoked", "NotificationClosed"):
                rule = MatchRule(type="signal", interface=NOTIFICATIONS.interface, member=member)
                conn.send_and_get_reply(message_bus.AddMatch(rule))
            while True:
                msg = conn.receive()
                member = msg.header.fields.get(3)  # HeaderFields.member
                if member == "ActionInvoked":
                    on_action(int(msg.body[0]), str(msg.body[1]))
                elif member == "NotificationClosed":
                    on_closed(int(msg.body[0]), int(msg.body[1]))

        threading.Thread(target=loop, name="aa-notify-signals", daemon=True).start()


def _variant(v):
    if isinstance(v, bool):
        return ("b", v)
    if isinstance(v, int):
        return ("y", v) if 0 <= v <= 255 else ("i", v)
    return ("s", str(v))


class Notifier(QObject):
    """Sends notifications and routes ActionInvoked / NotificationClosed to per-notification callbacks."""

    action_invoked = Signal(int, str)
    closed = Signal(int, int)

    def __init__(self, app_name: str = "Arch Assistant", icon: str = "arch-assistant",
                 transport: Transport | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.app_name, self.icon = app_name, icon
        self._pending: dict[int, tuple[Callable[[str], None], Callable[[], None] | None]] = {}
        self._transport = transport or JeepneyTransport()
        # signals come from the listener thread; queued connection delivers them on the Qt thread
        self.action_invoked.connect(self._on_action_invoked)
        self.closed.connect(self._on_closed)
        self._transport.listen(self.action_invoked.emit, self.closed.emit)

    def send(self, summary: Summary, buttons: list[tuple[str, str]], on_action: Callable[[str], None],
             on_closed: Callable[[], None] | None = None, urgency: int = URGENCY_NORMAL,
             timeout_ms: int = -1) -> int:
        actions = [part for key, label in buttons for part in (key, label)]
        hints = {"urgency": urgency, "resident": bool(buttons), "desktop-entry": "arch-assistant"}
        nid = self._transport.notify(self.app_name, 0, self.icon, summary.headline, build_body(summary),
                                     actions, hints, timeout_ms)
        if buttons and nid:
            self._pending[nid] = (on_action, on_closed)
        return nid

    @Slot(int, str)
    def _on_action_invoked(self, nid: int, key: str) -> None:
        entry = self._pending.pop(int(nid), None)
        if entry:
            entry[0](key)

    @Slot(int, int)
    def _on_closed(self, nid: int, reason: int) -> None:
        entry = self._pending.pop(int(nid), None)
        if entry and entry[1]:
            entry[1]()


def result_popup(notifier: Notifier, result: ActionResult, label: str) -> int:
    headline = f"{label}: {'done' if result.ok else 'failed'}"
    body = result.output or ("" if result.ok else "see the log for details")
    return notifier.send(Summary(headline, body, []), [], lambda _k: None,
                         urgency=URGENCY_NORMAL if result.ok else URGENCY_CRITICAL, timeout_ms=RESULT_TIMEOUT_MS)
