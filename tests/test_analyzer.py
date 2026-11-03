from pathlib import Path

from rasgulla.analyzer import analyze_path


def test_detects_eval_and_tls(tmp_path: Path):
    (tmp_path / "sample.py").write_text("eval(user_input)\nverify=False\n")
    findings = analyze_path(tmp_path)
    assert {f["rule_id"] for f in findings} >= {"RG001", "RG003"}

def test_ignores_non_source_files(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("eval(x)")
    assert analyze_path(tmp_path) == []
