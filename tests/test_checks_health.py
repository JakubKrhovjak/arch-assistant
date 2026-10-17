import json

SYSTEMCTL_WITH_FAILURES = r'''
case "$*" in
  "--failed --plain --no-legend") printf 'systemd-tpm2-setup-early.service masked failed failed systemd-tpm2-setup-early.service\n' ;;
  "--user --failed --plain --no-legend") printf 'plasma-krunner.service loaded failed failed KRunner\n' ;;
  "list-units --type=service --state=running --plain --no-legend") printf 'sshd.service loaded active running OpenSSH\n' ;;
  "--user list-units --type=service --state=running --plain --no-legend") printf 'kde-baloo.service loaded active running Baloo\napp-ghostty.service loaded active running Ghostty\n' ;;
  "show -p Id,NRestarts sshd.service") printf 'Id=sshd.service\nNRestarts=0\n\n' ;;
  "--user show -p Id,NRestarts kde-baloo.service app-ghostty.service") printf 'Id=kde-baloo.service\nNRestarts=12\n\nId=app-ghostty.service\nNRestarts=1\n\n' ;;
  *) exit 0 ;;
esac
'''

SYSTEMCTL_CLEAN = r'''
case "$*" in
  *"list-units"*) printf 'a.service loaded active running A\n' ;;
  *"show -p Id,NRestarts"*) printf 'Id=a.service\nNRestarts=0\n\n' ;;
  *) exit 0 ;;
esac
'''


def test_failed_units_reports_failed_and_restarting(shim, check_runner):
    shim.cmd("systemctl", script=SYSTEMCTL_WITH_FAILURES)
    f = check_runner.run("failed-units")
    assert f.severity == "high"
    by_name = {i["name"]: i for i in f.items}
    assert by_name["systemd-tpm2-setup-early.service"]["scope"] == "system"
    assert by_name["systemd-tpm2-setup-early.service"]["state"] == "failed"
    assert by_name["plasma-krunner.service"]["scope"] == "user"
    assert by_name["kde-baloo.service"]["state"] == "restarting" and by_name["kde-baloo.service"]["restarts"] == 12
    assert "app-ghostty.service" not in by_name
    # a masked unit cannot be restarted: reported with its load state, no action offered
    assert by_name["systemd-tpm2-setup-early.service"]["load"] == "masked"
    assert "masked" in f.summary
    assert [(a.id, a.args) for a in f.actions] == [
        ("restart-unit", ("user", "plasma-krunner.service")),
        ("restart-unit", ("user", "kde-baloo.service")),
    ]
    assert f.title == "3 systemd units failed or restarting"


def test_failed_units_none(shim, check_runner):
    shim.cmd("systemctl", script=SYSTEMCTL_CLEAN)
    assert check_runner.run("failed-units").severity == "none"


DF_FULL = "Mounted on Use%  Avail\n/           64% 168120\n/home       64% 168120\n/boot/efi   92%     25\n/mnt/data   95%  30000\n"
DF_OK = "Mounted on Use%  Avail\n/           64% 168120\n/home       64% 168120\n/boot/efi   48%    156\n/mnt/data   34% 633504\n"


def test_disk_space_reports_esp_and_data(shim, check_runner):
    shim.cmd("df", DF_FULL)
    f = check_runner.run("disk-space", {"AA_DISK_PCT": "10", "AA_ESP_MB": "30"})
    assert f.severity == "high"
    names = [i["name"] for i in f.items]
    assert names == ["/boot/efi", "/mnt/data"]
    assert f.items[0]["free_mb"] == 25 and f.items[1]["free_pct"] == 5
    assert f.actions == ()


def test_disk_space_none(shim, check_runner):
    shim.cmd("df", DF_OK)
    assert check_runner.run("disk-space").severity == "none"


COREDUMPS = json.dumps([
    {"time": 1, "pid": 1, "sig": 6, "corefile": "present", "exe": "/home/redo/.local/share/JetBrains/Toolbox/apps/goland/jbr/lib/cef_server", "size": 119213756},
    {"time": 2, "pid": 2, "sig": 6, "corefile": "present", "exe": "/home/redo/.local/share/JetBrains/Toolbox/apps/goland/jbr/lib/cef_server", "size": 119219743},
    {"time": 3, "pid": 3, "sig": 11, "corefile": "present", "exe": "/usr/lib/baloo_file_extractor", "size": 1000},
])


def test_coredumps_groups_by_exe(shim, check_runner):
    shim.cmd("coredumpctl", COREDUMPS)
    shim.cmd("du", "2550\t/var/lib/systemd/coredump\n")
    (check_runner.state_dir / "state.json").write_text('{"last_cycle_at": "2026-10-16T10:00:00"}')
    f = check_runner.run("coredumps")
    assert f.severity == "medium"
    assert f.items[0] == {"name": "cef_server", "count": 2, "size_mb": 2550}
    assert f.items[1]["name"] == "baloo_file_extractor" and f.items[1]["count"] == 1
    assert f.title == "3 new core dumps, 2550 MB on disk"
    assert f.actions[0].id == "clean-coredumps"
    assert any("--since 2026-10-16T10:00:00" in c for c in shim.calls())


def test_coredumps_none(shim, check_runner):
    shim.cmd("coredumpctl", "No coredumps found.\n", exit_code=1)
    shim.cmd("du", "0\t/var/lib/systemd/coredump\n")
    f = check_runner.run("coredumps")
    assert f.severity == "none"
    assert any("--since -7days" in c for c in shim.calls())


def _journal(n_tdx, n_other):
    lines = [json.dumps({"MESSAGE": "virt/tdx: TDX not supported by the host platform", "PRIORITY": "3"})] * n_tdx
    lines += [json.dumps({"MESSAGE": f"other error {i}", "PRIORITY": "3"}) for i in range(n_other)]
    return "\n".join(lines) + "\n"


def test_journal_errors_above_threshold(shim, check_runner):
    shim.cmd("journalctl", _journal(40, 20))
    f = check_runner.run("journal-errors", {"AA_JOURNAL_ERR": "50"})
    assert f.severity == "low"
    assert f.items[0]["name"].startswith("virt/tdx") and f.items[0]["count"] == 40
    assert len(f.items) == 10
    assert f.title == "60 errors in the journal since boot"


def test_journal_errors_below_threshold(shim, check_runner):
    shim.cmd("journalctl", _journal(10, 5))
    assert check_runner.run("journal-errors", {"AA_JOURNAL_ERR": "50"}).severity == "none"
