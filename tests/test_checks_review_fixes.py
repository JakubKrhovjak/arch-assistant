SUDO = 'cmd=$(basename "$1"); shift; exec "$cmd" "$@"\n'


def test_secure_boot_sbctl_failure_is_critical(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", "", exit_code=1, stderr="sbctl: permission denied")
    esp = tmp_path / "esp"
    (esp / "EFI" / "Linux").mkdir(parents=True)
    (esp / "EFI" / "Linux" / "arch-linux.efi").write_bytes(b"x")
    f = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert f.severity == "critical" and "sbctl verify failed" in f.title


def test_secure_boot_no_signed_lines_is_critical(shim, check_runner, tmp_path):
    shim.cmd("sudo", script=SUDO)
    shim.cmd("sbctl", "Verifying file database and EFI images in /boot/efi...\n")
    esp = tmp_path / "esp"
    (esp / "EFI" / "Linux").mkdir(parents=True)
    (esp / "EFI" / "Linux" / "arch-linux.efi").write_bytes(b"x")
    f = check_runner.run("secure-boot", {"AA_ESP": str(esp)})
    assert f.severity == "critical"


def test_docker_vm_qemu_img_failure_fails_the_check(shim, check_runner, tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "a.qcow2").write_bytes(b"x")
    (vm / "b.qcow2").write_bytes(b"x")
    shim.cmd("sudo", script=SUDO)
    shim.cmd("docker", script='exit 1\n')
    shim.cmd("virsh", script=f'''
case "$*" in
  *"list --all --name") echo vm1 ;;
  *"domblklist vm1") printf ' Target   Source\\n----\\n sda      {vm}/a.qcow2\\n' ;;
esac
''')
    shim.cmd("qemu-img", "", exit_code=1, stderr="qemu-img: Failed to get shared write lock")
    f = check_runner.run("docker-vm", {"AA_VM_DIR": str(vm)})
    assert f.title == "Check docker-vm failed" and "lock" in f.error


def test_docker_vm_uses_force_share_and_counts_the_domain_disk(shim, check_runner, tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "a.qcow2").write_bytes(b"x")
    shim.cmd("sudo", script=SUDO)
    shim.cmd("docker", script='exit 1\n')
    shim.cmd("virsh", script=f'''
case "$*" in
  *"list --all --name") echo vm1 ;;
  *"domblklist vm1") printf ' Target   Source\\n----\\n sda      {vm}/a.qcow2\\n' ;;
esac
''')
    shim.cmd("qemu-img", f"image: {vm}/a.qcow2\n")
    f = check_runner.run("docker-vm", {"AA_VM_DIR": str(vm)})
    assert f.severity == "none"
    assert any(c.startswith("sudo /usr/bin/qemu-img info -U --backing-chain") for c in shim.calls())


def test_docker_vm_virsh_failure_fails_the_check(shim, check_runner, tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "a.qcow2").write_bytes(b"x")
    shim.cmd("sudo", script=SUDO)
    shim.cmd("docker", script='exit 1\n')
    shim.cmd("virsh", "", exit_code=1, stderr="error: failed to connect to the hypervisor")
    f = check_runner.run("docker-vm", {"AA_VM_DIR": str(vm)})
    assert f.title == "Check docker-vm failed"
