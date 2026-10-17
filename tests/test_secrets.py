from arch_assistant.secrets import read_api_key


def test_export_with_double_quotes(tmp_path):
    p = tmp_path / "s.zsh"
    p.write_text('# secrets\nexport JIRA_TOKEN="abc"\nexport ANTHROPIC_API_KEY="sk-ant-x"\n')
    assert read_api_key(p) == "sk-ant-x"


def test_plain_assignment_without_quotes(tmp_path):
    p = tmp_path / "s.zsh"
    p.write_text("ANTHROPIC_API_KEY=sk-ant-y\n")
    assert read_api_key(p) == "sk-ant-y"


def test_single_quotes(tmp_path):
    p = tmp_path / "s.zsh"
    p.write_text("export ANTHROPIC_API_KEY='sk-ant-z'   # comment\n")
    assert read_api_key(p) == "sk-ant-z"


def test_file_without_key(tmp_path):
    p = tmp_path / "s.zsh"
    p.write_text("export OTHER=1\n")
    assert read_api_key(p) is None


def test_missing_file(tmp_path):
    assert read_api_key(tmp_path / "nope.zsh") is None
