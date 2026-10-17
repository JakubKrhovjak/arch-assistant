import json
import os
import stat
import time

SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'


def test_resource_hogs_reports_cpu_hog_and_updates_baseline(shim, check_runner):
    shim.cmd("ps", "  1 baloo_file_ext 95.0 200000 900\n  2 idle_proc 0.5 1000 900\n")
    f = check_runner.run("resource-hogs", {"AA_HOG_CPU_PCT": "50", "AA_HOG_MINUTES": "5", "AA_HOG_RSS_GB": "4"})
    assert f.severity == "medium"
    assert f.items[0]["name"] == "baloo_file_ext" and f.items[0]["pid"] == 1 and f.items[0]["cpu_pct"] == 95.0
    assert f.actions == ()
    baseline = json.loads((check_runner.state_dir / "hogs.json").read_text())
    assert "baloo_file_ext" in baseline and "idle_proc" in baseline


def test_resource_hogs_short_lived_cpu_is_ignored(shim, check_runner):
    shim.cmd("ps", "  1 cc1plus 99.0 200000 30\n")
    assert check_runner.run("resource-hogs", {"AA_HOG_CPU_PCT": "50", "AA_HOG_MINUTES": "5"}).severity == "none"


def test_resource_hogs_rss_hog(shim, check_runner):
    shim.cmd("ps", "  7 leaky 1.0 5242880 100\n")
    f = check_runner.run("resource-hogs", {"AA_HOG_RSS_GB": "4"})
    assert f.severity == "medium" and f.items[0]["rss_mb"] == 5120


def test_resource_hogs_known_heavy_app_within_baseline(shim, check_runner):
    (check_runner.state_dir / "hogs.json").write_text(json.dumps({"java": {"cpu": 40.0, "rss": 3000}}))
    shim.cmd("ps", "  3 java 60.0 3000000 9000\n")
    assert check_runner.run("resource-hogs", {"AA_HOG_CPU_PCT": "50", "AA_HOG_MINUTES": "5"}).severity == "none"
    shim.cmd("ps", "  3 java 95.0 3000000 9000\n")
    f = check_runner.run("resource-hogs", {"AA_HOG_CPU_PCT": "50", "AA_HOG_MINUTES": "5"})
    assert f.severity == "medium" and f.items[0]["baseline_cpu"] > 0


def _desktop(d, name, exec_path, app_name):
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.desktop"
    p.write_text(f"[Desktop Entry]\nType=Application\nName={app_name}\nExec={exec_path} %U\n")
    return p


def _bin(d, name, atime_days_ago):
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    t = time.time() - atime_days_ago * 86400
    os.utime(p, (t, t))
    return p


def test_unused_apps_by_atime_and_recently_used(shim, check_runner, tmp_path):
    apps = tmp_path / "share" / "applications"
    old_bin = _bin(tmp_path / "bin2", "oldapp", 200)
    fresh_bin = _bin(tmp_path / "bin2", "freshapp", 1)
    recent_bin = _bin(tmp_path / "bin2", "recentapp", 300)
    _desktop(apps, "oldapp", str(old_bin), "Old App")
    _desktop(apps, "freshapp", str(fresh_bin), "Fresh App")
    _desktop(apps, "recentapp", str(recent_bin), "Recent App")
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".local" / "share" / "recently-used.xbel").write_text(
        '<?xml version="1.0" encoding="UTF-8"?><xbel version="1.0" xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">'
        '<bookmark href="file:///x" added="2026-10-10T10:00:00Z" modified="2026-10-16T10:00:00Z" visited="2026-10-16T10:00:00Z">'
        '<info><metadata owner="http://freedesktop.org"><bookmark:applications>'
        '<bookmark:application name="Recent App" exec="&apos;recentapp %U&apos;" modified="2026-10-16T10:00:00Z" count="1"/>'
        '</bookmark:applications></metadata></info></bookmark></xbel>')
    shim.cmd("pacman", script=r'''
case "$1" in
  -Qqe) printf 'oldapp\nfreshapp\nrecentapp\nplasma-desktop\n' ;;
  -Ql) echo "$2 ''' + str(apps) + r'''/$2.desktop" ;;
esac
''')
    f = check_runner.run("unused-apps", {"AA_UNUSED_DAYS": "90", "AA_HOME": str(home)})
    assert f.severity == "low"
    assert [i["name"] for i in f.items] == ["oldapp"]
    assert f.items[0]["app"] == "Old App" and f.items[0]["last_used"] <= time.strftime("%Y-%m-%d", time.localtime(time.time() - 199 * 86400))
    assert [(a.id, a.args) for a in f.actions] == [("remove-package", ("oldapp",))]
    assert not any("plasma-desktop" in c and "-Ql" in c for c in shim.calls())


def test_unused_apps_none(shim, check_runner, tmp_path):
    apps = tmp_path / "share" / "applications"
    b = _bin(tmp_path / "bin2", "fresh", 1)
    _desktop(apps, "fresh", str(b), "Fresh")
    shim.cmd("pacman", script=r'''
case "$1" in
  -Qqe) echo fresh ;;
  -Ql) echo "$2 ''' + str(apps) + r'''/$2.desktop" ;;
esac
''')
    assert check_runner.run("unused-apps", {"AA_UNUSED_DAYS": "90", "AA_HOME": str(tmp_path)}).severity == "none"


DOCKER_DF = ('{"Active":"13","Reclaimable":"2.5GB (40%)","Size":"6.5GB","TotalCount":"16","Type":"Images"}\n'
             '{"Active":"9","Reclaimable":"764.6kB (0%)","Size":"603.9MB","TotalCount":"18","Type":"Containers"}\n'
             '{"Active":"13","Reclaimable":"70.84MB (1%)","Size":"3.78GB","TotalCount":"14","Type":"Local Volumes"}\n')


def test_docker_vm_reports_images_and_orphan_overlay(shim, check_runner, tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    for n in ("a.qcow2", "b.qcow2", "c.qcow2"):
        (vm / n).write_bytes(b"x" * 2048)
    shim.cmd("sudo", script=SUDO)
    shim.cmd("docker", script=f'if [[ "$1" == "info" ]]; then exit 0; fi\nprintf %s \'{DOCKER_DF}\'\n')
    shim.cmd("virsh", script=f'''
case "$*" in
  *"list --all --name") echo vm1 ;;
  *"domblklist vm1") printf ' Target   Source\\n----\\n sda      {vm}/a.qcow2\\n sdb      /mnt/iso/x.iso\\n' ;;
esac
''')
    shim.cmd("qemu-img", f"image: {vm}/a.qcow2\nbacking file: {vm}/b.qcow2\nimage: {vm}/b.qcow2\n")
    f = check_runner.run("docker-vm", {"AA_VM_DIR": str(vm)})
    assert f.severity == "low"
    kinds = {(i["kind"], i["name"]) for i in f.items}
    assert ("image", "docker images") in kinds
    assert ("vm-overlay", str(vm / "c.qcow2")) in kinds
    assert ("volume", "docker volumes") not in kinds
    assert f.actions == ()
    assert any(c.startswith(f"sudo /usr/bin/qemu-img info --backing-chain {vm}/a.qcow2") for c in shim.calls())


def test_docker_vm_none(shim, check_runner, tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    shim.cmd("docker", script='if [[ "$1" == "info" ]]; then exit 1; fi\n')
    shim.cmd("virsh", "")
    shim.cmd("sudo", script=SUDO)
    assert check_runner.run("docker-vm", {"AA_VM_DIR": str(vm)}).severity == "none"
