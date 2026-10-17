import os

from arch_assistant.single_instance import InstanceLock


def test_first_instance_acquires_lock(tmp_path):
    lock = InstanceLock(tmp_path / "tray.lock")
    assert lock.acquire() is None
    assert (tmp_path / "tray.lock").read_text().strip() == str(os.getpid())
    lock.release()


def test_second_instance_gets_running_pid(tmp_path):
    first = InstanceLock(tmp_path / "tray.lock")
    assert first.acquire() is None
    second = InstanceLock(tmp_path / "tray.lock")
    assert second.acquire() == os.getpid()
    first.release()
    third = InstanceLock(tmp_path / "tray.lock")
    assert third.acquire() is None
    third.release()


def test_stale_lock_file_is_taken_over(tmp_path):
    (tmp_path / "tray.lock").write_text("999999\n")   # no process holds it
    lock = InstanceLock(tmp_path / "tray.lock")
    assert lock.acquire() is None
    lock.release()
