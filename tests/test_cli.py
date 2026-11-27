import json

from rasgulla import cli


def test_cli_reports_a_missing_path_without_a_traceback(tmp_path, capsys):
    code = cli.main([str(tmp_path / "absent")])
    captured = capsys.readouterr()
    assert code == cli.EXIT_USAGE
    assert "rasgulla:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_prints_a_json_report(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    code = cli.main([str(tmp_path)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    report = json.loads(captured.out)
    assert report["tool"] == "Rasgulla AI"
    assert report["mode"] == "static-analysis"
    assert report["schema_version"] == 2
    assert len(report["findings"]) == 1
    assert "notice" in report


def test_cli_writes_the_report_to_the_output_path(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    output = tmp_path / "report.json"
    code = cli.main([str(tmp_path), "--output", str(output)])
    capsys.readouterr()
    assert code == cli.EXIT_OK
    written = json.loads(output.read_text(encoding="utf-8"))
    assert len(written["findings"]) == 1


def test_cli_reports_an_unwritable_output_path_without_a_traceback(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    output = tmp_path / "missing-directory" / "report.json"
    code = cli.main([str(tmp_path), "--output", str(output)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_INVALID_INPUT
    assert "rasgulla:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_fail_on_findings_returns_a_failing_status(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    code = cli.main([str(tmp_path), "--fail-on-findings"])
    capsys.readouterr()
    assert code == cli.EXIT_INVALID_INPUT


def test_cli_fail_on_findings_succeeds_on_a_clean_tree(tmp_path, capsys):
    (tmp_path / "app.py").write_text("value = 1\n")
    code = cli.main([str(tmp_path), "--fail-on-findings"])
    capsys.readouterr()
    assert code == cli.EXIT_OK


def test_cli_adds_a_test_plan_on_request(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    code = cli.main([str(tmp_path), "--test-plan"])
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert report["adapter"] == "static"
    assert len(report["test_plan"]) == 1
    assert report["test_plan"][0]["rule_id"] == "RG001"


def test_cli_reports_no_test_plan_by_default(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    cli.main([str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert "test_plan" not in report


def test_cli_rejects_an_unknown_adapter(tmp_path, capsys):
    code = cli.main([str(tmp_path), "--test-plan", "--adapter", "does-not-exist"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_USAGE
    assert "rasgulla:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_caps_the_test_plan(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(a)\neval(b)\neval(c)\n")  # rasgulla: ignore
    code = cli.main([str(tmp_path), "--test-plan", "--max-steps", "2"])
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert len(report["test_plan"]) == 2


def test_cli_rejects_a_negative_step_cap(tmp_path, capsys):
    code = cli.main([str(tmp_path), "--test-plan", "--max-steps", "-1"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_USAGE
    assert "rasgulla:" in captured.err


def test_cli_accepts_a_single_file(tmp_path, capsys):
    target = tmp_path / "app.py"
    target.write_text("eval(user_input)\n")  # rasgulla: ignore
    code = cli.main([str(target)])
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert len(report["findings"]) == 1


def test_cli_report_carries_a_summary(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    cli.main([str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["total"] == 1
    assert report["summary"]["by_severity"]["high"] == 1
    assert report["summary"]["by_severity"]["low"] == 0
    assert report["summary"]["by_confidence"]["high"] == 1


def test_cli_report_carries_the_rule_catalogue(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    cli.main([str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    rules = {rule["rule_id"]: rule for rule in report["rules"]}
    assert "RG001" in rules
    assert rules["RG001"]["cwe"] == "CWE-95"
    assert rules["RG007"]["owasp_llm"] == "LLM01"


def test_the_rule_catalogue_carries_what_a_reviewer_needs(tmp_path, capsys):
    """The catalogue is what a consumer renders the taxonomy from, so it has to
    carry the recommendation and the pattern, not only the identifiers."""
    (tmp_path / "app.py").write_text("eval(user_input)\n")  # rasgulla: ignore
    cli.main([str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    rules = {rule["rule_id"]: rule for rule in report["rules"]}
    assert rules["RG001"]["recommendation"]
    assert rules["RG001"]["pattern"]
    assert "eval" in rules["RG001"]["pattern"]


def test_cli_report_carries_the_metadata_of_each_finding(tmp_path, capsys):
    (tmp_path / "app.py").write_text("exec(completion)\n")  # rasgulla: ignore
    cli.main([str(tmp_path)])
    report = json.loads(capsys.readouterr().out)
    findings = {finding["rule_id"]: finding for finding in report["findings"]}
    assert "RG015" in findings
    assert findings["RG015"]["confidence"] in {"high", "medium", "low"}
    assert findings["RG015"]["cwe"].startswith("CWE-")
    assert findings["RG015"]["owasp_llm"] == "LLM10"


def test_cli_test_plan_entries_carry_the_rule_metadata(tmp_path, capsys):
    (tmp_path / "app.py").write_text("exec(completion)\n")  # rasgulla: ignore
    cli.main([str(tmp_path), "--test-plan"])
    report = json.loads(capsys.readouterr().out)
    entries = {entry["rule_id"]: entry for entry in report["test_plan"]}
    assert "RG015" in entries
    assert entries["RG015"]["cwe"].startswith("CWE-")
    assert entries["RG015"]["owasp_llm"] == "LLM10"
