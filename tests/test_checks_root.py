import pytest

ROOT_CHECKS = ["pacnew", "secure-boot", "timeshift", "docker-vm"]

VIRSH_ONE_DISK = '''
case "$*" in
  *"list --all --name") echo vm1 ;;
  *"domblklist vm1") printf ' Target   Source\\n----\\n sda      {vm}/a.qcow2\\n' ;;
esac
'''


@pytest.mark.parametrize("check_id", ROOT_CHECKS)
def test_root_check_never_prompts_without_a_sudo_rule(shim, check_runner, tmp_path, check_id):
    vm = tmp_path / "vm"
    vm.mkdir()
    (vm / "a.qcow2").write_bytes(b"x")
    shim.sudo_needing_password()
    shim.cmd("docker", exit_code=1)
    shim.cmd("virsh", script=VIRSH_ONE_DISK.format(vm=vm))
    f = check_runner.run(check_id, {"AA_ESP": str(tmp_path / "esp"), "AA_VM_DIR": str(vm)})
    assert f.error and "passwordless sudo" in f.error
    sudo_calls = [c for c in shim.calls() if c.startswith("sudo")]
    assert sudo_calls and all(c.startswith("sudo -n ") for c in sudo_calls)
