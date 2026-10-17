import stat
import time

from arch_assistant.checks import discover_checks, run_all, run_check


def _script(d, name, body):
    p = d / name
    p.write_text("#!/bin/bash\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def test_run_check_parses_output(tmp_path):
    p = _script(tmp_path, "ok.sh", 'echo \'{"id":"ok","severity":"low","title":"T","summary":"S"}\'\n')
    f = run_check(p, {})
    assert f.id == "ok" and f.error is None


def test_run_check_invalid_json_is_failed_finding(tmp_path):
    p = _script(tmp_path, "bad.sh", "echo nope\n")
    f = run_check(p, {})
    assert f.title == "Check bad failed"
    assert "nope" in f.error


def test_run_check_nonzero_exit_uses_stderr(tmp_path):
    p = _script(tmp_path, "err.sh", "echo err >&2; exit 2\n")
    f = run_check(p, {})
    assert f.summary == "err" and f.severity == "low"


def test_run_check_timeout(tmp_path):
    p = _script(tmp_path, "slow.sh", "sleep 5\n")
    f = run_check(p, {}, timeout=0.2)
    assert "timeout" in f.error.lower()


def test_run_check_python_script(tmp_path):
    p = tmp_path / "py.py"
    p.write_text('import os, json; print(json.dumps({"id": "py", "severity": "none", "title": "", "env": os.environ["AA_X"]}))\n')
    f = run_check(p, {"AA_X": "1"})
    assert f.id == "py" and f.is_empty


def test_run_all_keeps_order_and_runs_parallel(tmp_path):
    for n in "abc":
        _script(tmp_path, f"{n}.sh", f'sleep 0.3; echo \'{{"id":"{n}","severity":"none","title":""}}\'\n')
    t0 = time.monotonic()
    out = run_all(tmp_path, {})
    assert time.monotonic() - t0 < 0.7
    assert [f.id for f in out] == ["a", "b", "c"]


def test_discover_only(tmp_path):
    for n in "ab":
        _script(tmp_path, f"{n}.sh", "")
    (tmp_path / "_helper.py").write_text("")
    assert [p.name for p in discover_checks(tmp_path, only=["b"])] == ["b.sh"]
    assert [p.name for p in discover_checks(tmp_path)] == ["a.sh", "b.sh"]
