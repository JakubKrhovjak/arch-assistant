import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")
SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'
AMD = "06:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Phoenix [1002:15bf]\n"
INTEL_WIFI = "02:00.0 Network controller [0280]: Intel Corporation Wi-Fi 6E AX211 [8086:51f0]\n"


def _pacman(shim, installed, required_by):
    """pacman shim: `-Qi <pkg>` prints a Required By line from the dict, anything else lists `installed`."""
    lines = "".join(f'  if [[ "$2" == "{p}" ]]; then echo "Required By     : {r}"; exit 0; fi\n' for p, r in required_by.items())
    shim.cmd("pacman", script=(
        'if [[ "$1" == "-Qi" ]]; then\n' + lines + '  echo "Required By     : None"; exit 0\nfi\n'
        + "".join(f"echo {p}\n" for p in installed)))


def test_package_required_by_outside_package_is_hinted_not_offered(shim, check_runner):
    shim.cmd("lspci", AMD + INTEL_WIFI)
    _pacman(shim, ["vulkan-intel", "linux-firmware-nvidia", "linux-firmware"], {"linux-firmware-nvidia": "linux-firmware"})
    f = check_runner.run("hardware-drivers")
    assert [i["name"] for i in f.items] == ["vulkan-intel"]
    assert list(f.actions[0].args) == ["vulkan-intel"]
    assert "linux-firmware-nvidia is pulled in by the linux-firmware meta package" in f.summary


def test_only_blocked_package_gives_no_finding(shim, check_runner):
    shim.cmd("lspci", AMD + INTEL_WIFI)
    _pacman(shim, ["linux-firmware-nvidia", "linux-firmware"], {"linux-firmware-nvidia": "linux-firmware"})
    assert check_runner.run("hardware-drivers").severity == "none"


def test_requirer_inside_flagged_set_is_still_offered(shim, check_runner):
    shim.cmd("lspci", AMD + INTEL_WIFI)
    _pacman(shim, ["nvidia-utils", "nvidia-settings"], {"nvidia-utils": "nvidia-settings"})
    f = check_runner.run("hardware-drivers")
    assert sorted(i["name"] for i in f.items) == ["nvidia-settings", "nvidia-utils"]


def test_action_refuses_package_required_by_others(shim):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("lspci", AMD + INTEL_WIFI)
    _pacman(shim, ["vulkan-intel", "linux-firmware-nvidia", "linux-firmware"], {"linux-firmware-nvidia": "linux-firmware"})
    r = run_action(ACTIONS, Action("remove-driver-packages", "x", ("linux-firmware-nvidia",)), LOG)
    assert not r.ok and r.output.startswith("Refused")
    assert not any(c.startswith("sudo") for c in shim.calls())
