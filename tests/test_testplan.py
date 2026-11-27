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
    """Every rule the analyzer can report has a test-plan template, so no
    finding is ever silently dropped from the plan."""
    module = _testplan()
    analyzer = importlib.import_module("rasgulla.analyzer")
    for rule in analyzer.RULES:
        template = module.RULE_TEMPLATES[rule.rule_id]
        assert template["objective"]
        assert template["steps"]
        assert template["verification"]


def test_a_plan_entry_carries_the_rule_metadata():
    finding = _finding("RG007", "high", "agent.py", 9)
    finding.update({"confidence": "medium", "cwe": "CWE-1427", "owasp_llm": "LLM01"})
    plan = _testplan().build_test_plan([finding])
    assert plan[0]["confidence"] == "medium"
    assert plan[0]["cwe"] == "CWE-1427"
    assert plan[0]["owasp_llm"] == "LLM01"
