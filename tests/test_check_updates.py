def test_updates_with_critical_and_aur(shim, check_runner):
    shim.cmd("checkupdates", "linux 7.2.10-arch1-1 -> 7.2.11-arch1-1\nvim 9.1-1 -> 9.2-1\n")
    shim.cmd("yay", "aur/mbpoll-git 1.5-1 -> 1.6-1\n")
    f = check_runner.run("updates", {"AA_CRITICAL_PACKAGES": "linux plasma-*"})
    assert f.severity == "high"
    assert [i["name"] for i in f.items] == ["linux", "vim", "mbpoll-git"]
    assert f.items[0]["critical"] is True and f.items[1]["critical"] is False
    assert (f.items[0]["old"], f.items[0]["new"]) == ("7.2.10-arch1-1", "7.2.11-arch1-1")
    assert f.items[2]["source"] == "aur" and f.items[0]["source"] == "repo"
    assert f.summary.startswith("Critical: linux 7.2.10-arch1-1 -> 7.2.11-arch1-1")
    assert f.summary.endswith("; 2 other packages")
    assert f.actions[0].id == "update"
    assert any(c.startswith("yay -Qua") for c in shim.calls())


def test_updates_nothing_pending(shim, check_runner):
    shim.cmd("checkupdates", "", exit_code=2)
    shim.cmd("yay", "")
    f = check_runner.run("updates")
    assert f.severity == "none"


def test_updates_only_regular_packages_is_low(shim, check_runner):
    shim.cmd("checkupdates", "vim 9.1-1 -> 9.2-1\ngit 2.50-1 -> 2.51-1\n")
    shim.cmd("yay", "")
    f = check_runner.run("updates", {"AA_CRITICAL_PACKAGES": "linux"})
    assert f.severity == "low" and f.summary == "2 packages"


def test_updates_checkupdates_error_is_failed_check(shim, check_runner):
    shim.cmd("checkupdates", "", exit_code=1, stderr="ERROR: Cannot fetch updates")
    shim.cmd("yay", "")
    f = check_runner.run("updates")
    assert f.title == "Check updates failed" and "Cannot fetch updates" in f.error
