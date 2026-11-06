import importlib


def _testplan():
    """Return the test-plan module at runtime.

    Resolving the module inside the test keeps a missing module a test
    failure instead of a collection error.
    """
    return importlib.import_module("rasgulla.testplan")


def _finding(rule_id="RG001", severity="high", path="app.py", line=3):
    return {"rule_id": rule_id, "severity": severity, "path": path, "line": line}


def test_a_plan_entry_is_built_for_every_finding():
    plan = _testplan().build_test_plan([_finding()])
    assert len(plan) == 1
    assert plan[0]["rule_id"] == "RG001"
    assert plan[0]["location"] == "app.py:3"
    assert plan[0]["steps"]
    assert plan[0]["verification"]


def test_the_plan_is_ordered_by_severity():
    findings = [
        _finding("RG004", "medium", "b.py", 1),
        _finding("RG001", "high", "a.py", 1),
        _finding("RG003", "medium", "c.py", 1),
    ]
    plan = _testplan().build_test_plan(findings)
    assert [entry["severity"] for entry in plan] == ["high", "medium", "medium"]


def test_the_plan_is_deterministic():
    findings = [_finding("RG001", "high", "b.py", 2), _finding("RG001", "high", "a.py", 1)]
    module = _testplan()
    assert module.build_test_plan(findings) == module.build_test_plan(findings)
    assert module.build_test_plan(findings)[0]["location"] == "a.py:1"


def test_the_plan_is_capped():
    findings = [_finding("RG001", "high", f"file{index}.py", index) for index in range(10)]
    assert len(_testplan().build_test_plan(findings, max_steps=3)) == 3


def test_an_unknown_rule_is_skipped():
    plan = _testplan().build_test_plan([_finding("RG999")])
    assert plan == []


def test_a_malformed_finding_does_not_break_the_plan():
    plan = _testplan().build_test_plan(["not-a-dict", None, _finding()])
    assert len(plan) == 1


def test_an_empty_finding_list_produces_an_empty_plan():
    assert _testplan().build_test_plan([]) == []


def test_every_rule_has_a_template():
    module = _testplan()
    for rule_id in ("RG001", "RG002", "RG003", "RG004", "RG005", "RG006"):
        template = module.RULE_TEMPLATES[rule_id]
        assert template["objective"]
        assert template["steps"]
        assert template["verification"]
