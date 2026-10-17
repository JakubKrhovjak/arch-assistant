import pytest

from arch_assistant import notify as N
from arch_assistant.model import ActionResult, Summary


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.next_id = 41

    def notify(self, *args):
        self.next_id += 1
        self.calls.append(args)
        return self.next_id

    def listen(self, on_action, on_closed):
        self.on_action, self.on_closed = on_action, on_closed


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def notifier(transport):
    return N.Notifier(transport=transport)


def test_build_body_with_risks():
    assert N.build_body(Summary("H", "B", ["r1", "r2"])) == "B\n\nRisks: r1; r2"
    assert N.build_body(Summary("H", "B", [])) == "B"


def test_notify_flattens_actions_and_dispatches(notifier, transport):
    got = []
    nid = notifier.send(Summary("Head", "Body", []), N.BUTTONS_ASK, got.append)
    app_name, replaces, icon, headline, body, actions, hints, timeout = transport.calls[0]
    assert (app_name, replaces, headline, body) == ("Arch Assistant", 0, "Head", "Body")
    assert actions == ["yes", "Yes", "yes_auto", "Yes, automatically next time", "no", "No"]
    assert hints["urgency"] == 1 and hints["resident"] is True and timeout == -1
    notifier._on_action_invoked(nid, "yes_auto")
    assert got == ["yes_auto"]
    notifier._on_action_invoked(nid + 100, "yes")
    assert got == ["yes_auto"]


def test_closed_without_action_calls_on_closed(notifier):
    got, closed = [], []
    nid = notifier.send(Summary("H", "B", []), N.BUTTONS_ASK, got.append, on_closed=lambda: closed.append(1))
    notifier._on_closed(nid, 2)
    assert got == [] and closed == [1]
    notifier._on_closed(nid, 2)
    assert closed == [1]


def test_action_then_close_does_not_call_on_closed(notifier):
    got, closed = [], []
    nid = notifier.send(Summary("H", "B", []), N.BUTTONS_ASK, got.append, on_closed=lambda: closed.append(1))
    notifier._on_action_invoked(nid, "no")
    notifier._on_closed(nid, 2)
    assert got == ["no"] and closed == []


def test_critical_urgency(notifier, transport):
    notifier.send(Summary("H", "B", []), N.BUTTON_MENU, lambda k: None, urgency=2)
    assert transport.calls[0][6]["urgency"] == 2


def test_result_popup_success_and_failure(notifier, transport):
    N.result_popup(notifier, ActionResult("update", True, "Updated 3 packages", "", 1.0), "Update system")
    N.result_popup(notifier, ActionResult("update", False, "Update failed: x", "", 1.0), "Update system")
    ok_args, fail_args = transport.calls
    assert ok_args[3] == "Update system: done" and ok_args[4] == "Updated 3 packages"
    assert ok_args[5] == [] and ok_args[6]["urgency"] == 1 and ok_args[6]["resident"] is False and ok_args[7] == 15000
    assert fail_args[3] == "Update system: failed" and fail_args[6]["urgency"] == 2


def test_variant_typing():
    assert N._variant(True) == ("b", True)
    assert N._variant(2) == ("y", 2)
    assert N._variant(5000) == ("i", 5000)
    assert N._variant("x") == ("s", "x")
