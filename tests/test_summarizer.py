from arch_assistant import summarizer as S
from arch_assistant.model import Action, Finding


def f(id, severity="low", title="T", summary="S", n_items=0):
    return Finding(id, severity, title, summary, tuple({"name": f"i{i}", "size_mb": i} for i in range(n_items)),
                   (Action("x", "X"),))


def test_template_single_finding():
    s = S.template_summary([f("orphans", title="3 orphan packages", summary="a, b, c")])
    assert s.headline == "3 orphan packages"
    assert s.body == "• 3 orphan packages – a, b, c"
    assert s.risks == []


def test_template_many_findings_truncates_to_5():
    findings = [f(f"c{i}", "high" if i == 2 else "low", title=f"T{i}", summary=f"S{i}") for i in range(7)]
    s = S.template_summary(findings)
    assert s.headline == "7 things to deal with: Important"
    lines = s.body.splitlines()
    assert len(lines) == 6 and lines[0] == "• T0 – S0" and lines[-1] == "… and 2 more"
