import json

from rasgulla import cli


def test_cli_reports_a_missing_path_without_a_traceback(tmp_path, capsys):
    code = cli.main([str(tmp_path / "absent")])
    captured = capsys.readouterr()
    assert code == cli.EXIT_USAGE
    assert "rasgulla:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_prints_a_json_report(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
    code = cli.main([str(tmp_path)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    report = json.loads(captured.out)
    assert report["tool"] == "Rasgulla AI"
    assert report["mode"] == "static-analysis"
    assert report["schema_version"] == 1
    assert len(report["findings"]) == 1
    assert "notice" in report


def test_cli_writes_the_report_to_the_output_path(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
    output = tmp_path / "report.json"
    code = cli.main([str(tmp_path), "--output", str(output)])
    capsys.readouterr()
    assert code == cli.EXIT_OK
    written = json.loads(output.read_text(encoding="utf-8"))
    assert len(written["findings"]) == 1


def test_cli_reports_an_unwritable_output_path_without_a_traceback(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
    output = tmp_path / "missing-directory" / "report.json"
    code = cli.main([str(tmp_path), "--output", str(output)])
    captured = capsys.readouterr()
    assert code == cli.EXIT_INVALID_INPUT
    assert "rasgulla:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_fail_on_findings_returns_a_failing_status(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
    code = cli.main([str(tmp_path), "--fail-on-findings"])
    capsys.readouterr()
    assert code == cli.EXIT_INVALID_INPUT


def test_cli_fail_on_findings_succeeds_on_a_clean_tree(tmp_path, capsys):
    (tmp_path / "app.py").write_text("value = 1\n")
    code = cli.main([str(tmp_path), "--fail-on-findings"])
    capsys.readouterr()
    assert code == cli.EXIT_OK


def test_cli_adds_a_test_plan_on_request(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
    code = cli.main([str(tmp_path), "--test-plan"])
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert report["adapter"] == "static"
    assert len(report["test_plan"]) == 1
    assert report["test_plan"][0]["rule_id"] == "RG001"


def test_cli_reports_no_test_plan_by_default(tmp_path, capsys):
    (tmp_path / "app.py").write_text("eval(user_input)\n")
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
    (tmp_path / "app.py").write_text("eval(a)\neval(b)\neval(c)\n")
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
    target.write_text("eval(user_input)\n")
    code = cli.main([str(target)])
    report = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK
    assert len(report["findings"]) == 1
