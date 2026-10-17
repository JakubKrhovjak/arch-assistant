import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_install_script_syntax():
    assert subprocess.run(["bash", "-n", str(REPO / "install.sh")]).returncode == 0


def test_install_script_shellcheck():
    r = subprocess.run(["shellcheck", str(REPO / "install.sh")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_install_dry_run_lists_steps():
    r = subprocess.run([str(REPO / "install.sh"), "--dry-run"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    steps = [l for l in r.stdout.splitlines() if l[:1].isdigit()]
    assert len(steps) == 7
    assert any("octopi" in s for s in steps)


def test_install_dry_run_no_octopi_skips_step():
    r = subprocess.run([str(REPO / "install.sh"), "--dry-run", "--no-octopi"], capture_output=True, text=True)
    assert r.returncode == 0
    assert not any("octopi" in l and l[:1].isdigit() for l in r.stdout.splitlines())


def test_example_config_matches_defaults():
    import tomllib
    from arch_assistant.config import DEFAULTS
    cfg = tomllib.loads((REPO / "assets" / "config.example.toml").read_text())
    assert cfg["model"] == DEFAULTS["model"]
    assert set(cfg["thresholds"]) == set(DEFAULTS["thresholds"])
    assert cfg["critical_packages"] == DEFAULTS["critical_packages"]


def test_autostart_desktop_entry():
    text = (REPO / "assets" / "arch-assistant.desktop").read_text()
    assert "Exec=" in text and "X-KDE-autostart-after=panel" in text and "Icon=" in text
