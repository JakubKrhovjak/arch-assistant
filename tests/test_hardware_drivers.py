import logging
from pathlib import Path

from arch_assistant.actions import run_action
from arch_assistant.model import Action

ACTIONS = Path(__file__).resolve().parent.parent / "actions"
LOG = logging.getLogger("test")
SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'

INTEL = "00:02.0 VGA compatible controller [0300]: Intel Corporation Iris Xe [8086:9a49]\n"
AMD = "06:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Phoenix [1002:15bf]\n"
NVIDIA = "01:00.0 3D controller [0302]: NVIDIA Corporation AD107 [10de:28a0]\n"
INTEL_WIFI = "02:00.0 Network controller [0280]: Intel Corporation Wi-Fi 6E AX211 [8086:51f0]\n"
BCM_WIFI = "03:00.0 Network controller [0280]: Broadcom Inc. BCM4360 [14e4:43a0]\n"
ETH = "04:00.0 Ethernet controller [0200]: Realtek Semiconductor RTL8111 [10ec:8168]\n"


def _run(shim, check_runner, lspci, packages):
    shim.cmd("lspci", lspci)
    shim.cmd("pacman", "\n".join(packages) + "\n")
    return check_runner.run("hardware-drivers")


def test_amd_machine_flags_intel_nvidia_ati_and_broadcom_packages(shim, check_runner):
    flagged = ["vulkan-intel", "lib32-vulkan-intel", "intel-media-driver", "xf86-video-intel", "libva-intel-driver",
               "vulkan-nouveau", "nvidia-utils", "linux-firmware-nvidia", "xf86-video-ati", "b43-fwcutter",
               "broadcom-wl-dkms"]
    kept = ["mesa", "vulkan-radeon", "linux-firmware-intel", "linux-firmware", "linux-firmware-amdgpu"]
    f = _run(shim, check_runner, AMD + INTEL_WIFI + ETH, flagged + kept)
    names = [i["name"] for i in f.items]
    assert sorted(names) == sorted(flagged)
    assert "linux-firmware-intel" not in names
    assert f.actions[0].id == "remove-driver-packages" and sorted(f.actions[0].args) == sorted(flagged)


def test_present_vendors_are_not_flagged(shim, check_runner):
    f = _run(shim, check_runner, INTEL + NVIDIA + BCM_WIFI,
             ["vulkan-intel", "xf86-video-intel", "nvidia-utils", "vulkan-nouveau", "broadcom-wl-dkms", "xf86-video-ati"])
    assert f.severity == "none"


def test_present_nvidia_does_not_save_absent_intel(shim, check_runner):
    f = _run(shim, check_runner, NVIDIA + ETH, ["vulkan-intel", "nvidia-utils"])
    assert [i["name"] for i in f.items] == ["vulkan-intel"]


def test_intel_only_never_flags_intel_firmware(shim, check_runner):
    f = _run(shim, check_runner, INTEL + INTEL_WIFI, ["vulkan-intel", "linux-firmware-intel", "mesa"])
    assert f.severity == "none"
    f = _run(shim, check_runner, AMD + INTEL_WIFI, ["linux-firmware-intel"])
    assert f.severity == "none"


def test_unreadable_lspci_flags_nothing(shim, check_runner):
    shim.cmd("lspci", "", exit_code=1)
    shim.cmd("pacman", "vulkan-intel\nnvidia-utils\nb43-fwcutter\n")
    assert check_runner.run("hardware-drivers").severity == "none"


def test_action_runs_pacman_for_flagged_and_refuses_others(shim):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("lspci", AMD + INTEL_WIFI)
    shim.cmd("pacman", "vulkan-intel\nlinux-firmware-intel\nmesa\n")
    r = run_action(ACTIONS, Action("remove-driver-packages", "x", ("vulkan-intel",)), LOG)
    assert r.ok and r.output == "Removed 1 driver package: vulkan-intel"
    assert "sudo /usr/bin/pacman -Rns --noconfirm vulkan-intel" in shim.calls()
    for other in ("linux-firmware-intel", "mesa"):
        r = run_action(ACTIONS, Action("remove-driver-packages", "x", (other,)), LOG)
        assert not r.ok and r.output.startswith("Refused")
    assert sum(c.startswith("sudo") for c in shim.calls()) == 1
