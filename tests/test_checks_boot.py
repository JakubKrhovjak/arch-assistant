import json
import time

SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'
SBCTL_OK = "Verifying file database and EFI images in /boot/efi...\n✓ /boot/efi/EFI/Linux/arch-linux.efi is signed\n✓ /boot/efi/EFI/Linux/arch-linux-lts.efi is signed\n"
SBCTL_BAD = "Verifying file database and EFI images in /boot/efi...\n✗ /boot/efi/EFI/Linux/arch-linux.efi is not signed\n✓ /boot/efi/EFI/Linux/arch-linux-lts.efi is signed\n"


def _esp(tmp_path, with_uki=True):
    esp = tmp_path / "esp"
    (esp / "EFI" / "Linux").mkdir(parents=True)
    if with_uki:
        (esp / "EFI" / "Linux" / "arch-linux.efi").write_bytes(b"x")
    return esp


def test_secure_boot_unsigned_is_critical(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", SBCTL_BAD)
    esp = _esp(tmp_path)
    f = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert f.severity == "critical"
    assert f.title == "UKI is not signed: /boot/efi/EFI/Linux/arch-linux.efi"
    assert f.items[0]["name"] == "/boot/efi/EFI/Linux/arch-linux.efi"
    assert "secureboot-tpm2.sh" in f.summary


def test_secure_boot_missing_uki_is_critical(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", SBCTL_OK)
    esp = _esp(tmp_path, with_uki=False)
    f = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert f.severity == "critical" and "No UKI" in f.title


def test_secure_boot_ok_after_update_is_reported_once(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", SBCTL_OK)
    esp = _esp(tmp_path)
    (check_runner.state_dir / "state.json").write_text(json.dumps({"last_update_run": "2026-10-17T10:00:00"}))
    f = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert f.severity == "low" and f.title == "Secure Boot OK after update"
    g = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert g.severity == "none"


def test_secure_boot_quiet_without_update(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", SBCTL_OK)
    f = check_runner.run("secure-boot", {"AA_ESP": str(_esp(tmp_path))})
    assert f.severity == "none"


PACMAN_Q = r'''
case "$*" in
  "-Q linux") echo "linux 7.2.11.arch1-1" ;;
  "-Q linux-lts") echo "linux-lts 6.18.57-1" ;;
  "-Qi "*) printf 'Name            : %s\nInstall Date    : %s\n' "$2" "$INSTALL_DATE" ;;
esac
'''


def test_reboot_needed_kernel_mismatch(shim, check_runner, monkeypatch):
    shim.cmd("uname", "7.2.10-arch1-1\n")
    shim.cmd("uptime", "2026-10-17 02:56:41\n")
    monkeypatch.setenv("INSTALL_DATE", "Sun Oct 11 16:28:44 2026")
    shim.cmd("pacman", script=PACMAN_Q)
    f = check_runner.run("reboot-needed")
    assert f.severity == "medium"
    assert f.items[0] == {"name": "linux", "running": "7.2.10-arch1-1", "installed": "7.2.11.arch1-1"}
    assert f.title == "Reboot needed"


def test_reboot_needed_lts_kernel_matches(shim, check_runner, monkeypatch):
    shim.cmd("uname", "6.18.57-1-lts\n")
    shim.cmd("uptime", "2026-10-17 02:56:41\n")
    monkeypatch.setenv("INSTALL_DATE", "Sun Oct 11 16:28:44 2026")
    shim.cmd("pacman", script=PACMAN_Q)
    assert check_runner.run("reboot-needed").severity == "none"


def test_reboot_needed_systemd_updated_after_boot(shim, check_runner, monkeypatch):
    shim.cmd("uname", "7.2.11-arch1-1\n")
    shim.cmd("uptime", "2026-10-17 02:56:41\n")
    monkeypatch.setenv("INSTALL_DATE", "Fri Oct 17 17:00:00 2026")
    shim.cmd("pacman", script=PACMAN_Q)
    f = check_runner.run("reboot-needed")
    assert f.severity == "medium"
    assert {i["name"] for i in f.items} == {"systemd", "mesa", "qt6-base"}


def test_pacnew_lists_files(shim, check_runner):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("pacdiff", "/etc/pacman.conf.pacnew\n/etc/makepkg.conf.d/fortran.conf.pacnew\n")
    f = check_runner.run("pacnew")
    assert f.severity == "medium" and len(f.items) == 2
    assert f.items[0]["name"] == "/etc/pacman.conf.pacnew"
    assert f.actions[0].id == "pacdiff" and f.actions[0].args == ()


def test_pacnew_none(shim, check_runner):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("pacdiff", "")
    assert check_runner.run("pacnew").severity == "none"


def test_mirrors_stale(shim, check_runner, tmp_path):
    ml = tmp_path / "mirrorlist"
    ml.write_text("Server = x\n")
    old = time.time() - 10 * 86400
    import os
    os.utime(ml, (old, old))
    shim.cmd("systemctl", "inactive\n", exit_code=3)
    f = check_runner.run("mirrors", {"AA_MIRRORLIST": str(ml), "AA_MIRROR_DAYS": "7"})
    assert f.severity == "low" and f.items[0]["age_days"] == 10 and f.items[0]["timer"] == "inactive"
    assert f.actions[0].id == "refresh-mirrors"


def test_mirrors_fresh(shim, check_runner, tmp_path):
    ml = tmp_path / "mirrorlist"
    ml.write_text("Server = x\n")
    shim.cmd("systemctl", "active\n")
    assert check_runner.run("mirrors", {"AA_MIRRORLIST": str(ml)}).severity == "none"


AUDIT = "libxml2 CVE-2025-6170, CVE-2025-49796 High risk\npam CVE-2025-6020 High risk\n"


def test_vuln_packages_with_fix_available(shim, check_runner):
    shim.cmd("arch-audit", script='if [[ "$1" == "-u" ]]; then echo pam; else printf %s "$AUDIT"; fi\n')
    import os
    os.environ["AUDIT"] = AUDIT
    try:
        f = check_runner.run("vuln-packages")
    finally:
        del os.environ["AUDIT"]
    assert f.severity == "high"
    by = {i["name"]: i for i in f.items}
    assert by["pam"]["fix_available"] is True and by["libxml2"]["fix_available"] is False
    assert by["libxml2"]["cves"] == ["CVE-2025-6170", "CVE-2025-49796"]
    assert f.actions == ()


def test_vuln_packages_without_fix_is_medium(shim, check_runner):
    shim.cmd("arch-audit", script='if [[ "$1" == "-u" ]]; then exit 0; else printf %s "$AUDIT"; fi\n')
    import os
    os.environ["AUDIT"] = AUDIT
    try:
        f = check_runner.run("vuln-packages")
    finally:
        del os.environ["AUDIT"]
    assert f.severity == "medium"


def test_vuln_packages_none(shim, check_runner):
    shim.cmd("arch-audit", "")
    assert check_runner.run("vuln-packages").severity == "none"


TIMESHIFT = "Device : /dev/dm-0\nMode   : BTRFS\nStatus : OK\n16 snapshots, 176.3 GB free\n\nNum     Name                 Tags  Description\n----\n0    >  2026-04-16_15-29-43  O\n1    >  {latest}  O     {{timeshift-autosnap}}\n"


def test_timeshift_recent_snapshot(shim, check_runner):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("timeshift", TIMESHIFT.format(latest=time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(time.time() - 3600))))
    assert check_runner.run("timeshift").severity == "none"


def test_timeshift_old_snapshot(shim, check_runner):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("timeshift", TIMESHIFT.format(latest="2026-10-10_04-06-21"))
    f = check_runner.run("timeshift")
    assert f.severity == "medium" and f.items[0]["name"] == "2026-10-10_04-06-21" and f.items[0]["age_days"] >= 2


def test_timeshift_no_snapshots(shim, check_runner):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("timeshift", "Status : OK\n0 snapshots\n")
    f = check_runner.run("timeshift")
    assert f.severity == "medium" and f.title == "No Timeshift snapshots"


def test_clocksource_hpet(check_runner, tmp_path):
    p = tmp_path / "cs"
    p.write_text("hpet\n")
    f = check_runner.run("clocksource", {"AA_CLOCKSOURCE_FILE": str(p)})
    assert f.severity == "medium" and "hpet" in f.title and "tsc=reliable" in f.summary


def test_clocksource_tsc(check_runner, tmp_path):
    p = tmp_path / "cs"
    p.write_text("tsc\n")
    assert check_runner.run("clocksource", {"AA_CLOCKSOURCE_FILE": str(p)}).severity == "none"
