import logging
from pathlib import Path

from arch_assistant.actions import effective_mode, run_action
from arch_assistant.config import load_config
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")


def test_restart_user_unit(shim):
    shim.cmd("systemctl", "")
    r = run_action(ACTIONS, Action("restart-unit", "Restart unit", ("user", "kde-baloo.service")), LOG)
    assert r.ok and r.output == "Unit kde-baloo.service restarted"
    assert shim.calls() == ["systemctl --user restart kde-baloo.service"]


def test_restart_system_unit_uses_sudo(shim):
    shim.cmd("sudo", script='cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n')
    shim.cmd("systemctl", "")
    r = run_action(ACTIONS, Action("restart-unit", "Restart unit", ("system", "x.service")), LOG)
    assert r.ok and r.output == "Unit x.service restarted"
    assert shim.calls()[0] == "sudo /usr/bin/systemctl restart x.service"


def test_restart_unit_failure(shim):
    shim.cmd("systemctl", "", exit_code=1, stderr="Job for x.service failed.")
    r = run_action(ACTIONS, Action("restart-unit", "Restart unit", ("user", "x.service")), LOG)
    assert not r.ok and r.output == "Restart of x.service failed: Job for x.service failed."


def test_restart_unit_rejects_bad_args():
    r = run_action(ACTIONS, Action("restart-unit", "Restart unit", ("nope", "x.service")), LOG)
    assert not r.ok and "scope" in r.output


def test_clean_coredumps(shim):
    shim.cmd("sudo", script='cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n')
    shim.cmd("find", script='if [[ " $* " == *" -delete "* ]]; then exit 0; fi\nprintf "a\\nb\\nc\\n"\n')
    shim.cmd("du", script='if [[ -f "$AA_DU_STATE" ]]; then echo "0\t/var/lib/systemd/coredump"; else touch "$AA_DU_STATE"; echo "2550\t/var/lib/systemd/coredump"; fi\n')
    import os
    os.environ["AA_DU_STATE"] = str(shim.bin_dir / "du.state")
    try:
        r = run_action(ACTIONS, Action("clean-coredumps", "Remove core dumps"), LOG)
    finally:
        del os.environ["AA_DU_STATE"]
    assert r.ok and r.output == "Removed 3 files, freed 2550 MB"
    assert any(c.startswith("sudo /usr/bin/find /var/lib/systemd/coredump -type f -delete") for c in shim.calls())


def test_effective_mode_system_restart_never_auto(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    cfg.actions["restart-unit"] = "auto"
    assert effective_mode("restart-unit", cfg, ("system", "x.service")) == "ask"
    assert effective_mode("restart-unit", cfg, ("user", "x.service")) == "auto"
