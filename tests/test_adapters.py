import importlib


def _adapters():
    """Return the adapters module at runtime.

    Resolving the module inside the test keeps a missing module a test
    failure instead of a collection error.
    """
    return importlib.import_module("rasgulla.adapters")


def _finding():
    return {"rule_id": "RG001", "severity": "high", "path": "app.py", "line": 3}


def test_the_static_adapter_builds_the_offline_plan():
    adapters = _adapters()
    adapter = adapters.get_adapter("static")
    assert adapter.name == "static"
    plan = adapter.generate_test_plan([_finding()])
    assert len(plan) == 1
    assert plan[0]["rule_id"] == "RG001"


def test_the_null_adapter_produces_no_plan():
    adapters = _adapters()
    adapter = adapters.get_adapter("null")
    assert adapter.name == "null"
    assert adapter.generate_test_plan([_finding()]) == []


def test_the_default_adapter_is_the_static_one():
    assert _adapters().get_adapter().name == "static"


def test_the_adapter_name_is_case_insensitive():
    assert _adapters().get_adapter("STATIC").name == "static"


def test_an_unknown_adapter_is_rejected():
    try:
        _adapters().get_adapter("does-not-exist")
    except ValueError as exc:
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("an unknown adapter must be rejected")


def test_the_available_adapters_are_listed_in_a_stable_order():
    adapters = _adapters()
    assert adapters.available_adapters() == sorted(adapters.available_adapters())
    assert "static" in adapters.available_adapters()
    assert "null" in adapters.available_adapters()


def test_every_adapter_satisfies_the_protocol():
    adapters = _adapters()
    for name in adapters.available_adapters():
        assert isinstance(adapters.get_adapter(name), adapters.ModelAdapter)
