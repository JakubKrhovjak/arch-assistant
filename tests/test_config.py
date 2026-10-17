from arch_assistant.config import check_env, load_config, set_action_mode


def test_defaults_without_files(tmp_path):
    cfg = load_config(tmp_path, tmp_path)
    assert cfg.model == "claude-haiku-5-5"
    assert cfg.actions == {}
    assert cfg.thresholds["esp_mb"] == 30
    assert cfg.interval_hours == 6
    assert "linux" in cfg.critical_packages
    assert cfg.checks_dir.name == "checks" and cfg.actions_dir.name == "actions"


def test_config_toml_overrides_model(tmp_path):
    (tmp_path / "config.toml").write_text(
        'model = "claude-sonnet-5-5"\n[thresholds]\nesp_mb = 50\n')
    cfg = load_config(tmp_path, tmp_path)
    assert cfg.model == "claude-sonnet-5-5"
    assert cfg.thresholds["esp_mb"] == 50
    assert cfg.thresholds["disk_pct"] == 10


def test_set_action_mode_roundtrip(tmp_path):
    set_action_mode(tmp_path, "update", "auto")
    set_action_mode(tmp_path, "orphans", "never")
    assert load_config(tmp_path, tmp_path).actions == {"update": "auto", "orphans": "never"}
    set_action_mode(tmp_path, "update", "ask")
    assert load_config(tmp_path, tmp_path).actions["update"] == "ask"


def test_check_env_contains_thresholds(tmp_path):
    env = check_env(load_config(tmp_path, tmp_path))
    assert env["AA_ESP_MB"] == "30"
    assert "linux" in env["AA_CRITICAL_PACKAGES"].split()
    assert env["AA_STATE_DIR"] == str(tmp_path)
