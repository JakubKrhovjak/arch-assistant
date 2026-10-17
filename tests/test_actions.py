import logging
import stat

from arch_assistant import actions as A
from arch_assistant.config import load_config
from arch_assistant.model import Action, Finding

LOG = logging.getLogger("test")


def _action(d, name, body):
    p = d / f"{name}.sh"
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_run_action_ok(tmp_path):
    _action(tmp_path, "hello", 'echo "done $1"\necho detail >&2\n')
    r = A.run_action(tmp_path, Action("hello", "Hello", ("x",)), LOG)
    assert r.ok and r.output == "done x" and r.detail == "detail" and r.action_id == "hello"


def test_run_action_failure_keeps_stderr(tmp_path):
    _action(tmp_path, "bad", 'echo "Update failed: boom"\necho full log >&2\nexit 1\n')
    r = A.run_action(tmp_path, Action("bad", "Bad"), LOG)
    assert not r.ok and r.output == "Update failed: boom" and "full log" in r.detail


def test_run_action_output_limited_to_three_lines(tmp_path):
    _action(tmp_path, "many", 'printf "a\\nb\\nc\\nd\\n"\n')
    r = A.run_action(tmp_path, Action("many", "Many"), LOG)
    assert r.output == "a\nb\nc" and "d" in r.detail


def test_run_action_timeout(tmp_path, monkeypatch):
    monkeypatch.setitem(A.TIMEOUTS, "slow", 0.2)
    _action(tmp_path, "slow", "sleep 5\n")
    r = A.run_action(tmp_path, Action("slow", "Slow"), LOG)
    assert not r.ok and r.output.startswith("Action slow exceeded")


def test_run_action_missing_script(tmp_path):
    r = A.run_action(tmp_path, Action("nope", "Nope"), LOG)
    assert not r.ok and "not found" in r.output


def _updates(*names):
    return Finding("updates", "high", "T", "S", tuple({"name": n} for n in names),
                   (Action("update", "Update system"),))


def _news(packages):
    return Finding("arch-news", "high", "N", "S", ({"name": "n", "title": "n", "packages": packages},))


def test_needs_confirmation_qemu_with_running_vm():
    f = _updates("qemu-desktop", "vim")
    reason = A.needs_confirmation(f.actions[0], f, [f, _news([])], running_vms=lambda: ["win11-tia"])
    assert reason and "win11-tia" in reason and "qemu-desktop" in reason


def test_needs_confirmation_libvirt_with_running_vm():
    f = _updates("libvirt")
    assert "vm" in A.needs_confirmation(f.actions[0], f, [f, _news([])], running_vms=lambda: ["vm"])


def test_needs_confirmation_arch_news_match():
    f = _updates("linux", "vim")
    reason = A.needs_confirmation(f.actions[0], f, [f, _news(["linux"])], running_vms=lambda: [])
    assert reason and "linux" in reason


def test_needs_confirmation_none_without_vm_or_news():
    f = _updates("qemu-desktop", "linux")
    assert A.needs_confirmation(f.actions[0], f, [f, _news(["mkinitcpio"])], running_vms=lambda: []) is None


def test_needs_confirmation_other_action_is_none():
    f = Finding("orphans", "low", "T", "S", ({"name": "a"},), (Action("remove-orphans", "R", ("a",)),))
    assert A.needs_confirmation(f.actions[0], f, [f], running_vms=lambda: ["vm"]) is None


def test_effective_mode_defaults_to_ask(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    assert A.effective_mode("update", cfg) == "ask"


def test_effective_mode_reads_config(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    cfg.actions["update"] = "auto"
    assert A.effective_mode("update", cfg) == "auto"


def test_effective_mode_never_auto(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    cfg.actions["remove-package"] = "auto"
    cfg.actions["pacdiff"] = "auto"
    assert A.effective_mode("remove-package", cfg) == "ask"
    assert A.effective_mode("pacdiff", cfg) == "ask"
    cfg.actions["remove-package"] = "never"
    assert A.effective_mode("remove-package", cfg) == "never"


def test_running_vms_failure_is_unknown(shim):
    shim.cmd("virsh", "", exit_code=1, stderr="error: failed to connect")
    assert A.running_vms() is None


def test_running_vms_parses_names(shim):
    shim.cmd("virsh", "win11-tia\n\n")
    assert A.running_vms() == ["win11-tia"]
