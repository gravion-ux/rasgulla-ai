from pathlib import Path

from rasgulla.analyzer import analyze_file, analyze_path


def test_detects_eval_and_tls(tmp_path: Path):
    (tmp_path / "sample.py").write_text("eval(user_input)\nverify=False\n")
    findings = analyze_path(tmp_path)
    assert {f["rule_id"] for f in findings} >= {"RG001", "RG003"}

def test_ignores_non_source_files(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("eval(x)")
    assert analyze_path(tmp_path) == []


def test_build_directory_is_not_scanned(tmp_path: Path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "copy.py").write_text("eval(x)\n")
    (tmp_path / "app.py").write_text("eval(y)\n")
    assert {f["path"] for f in analyze_path(tmp_path)} == {"app.py"}


def test_project_under_an_ignored_directory_name_is_still_scanned(tmp_path: Path):
    project = tmp_path / "node_modules" / "myproject"
    project.mkdir(parents=True)
    (project / "sample.py").write_text("eval(x)\n")
    assert len(analyze_path(project)) == 1


def test_analyze_file_outside_base_is_handled(tmp_path: Path):
    outside = tmp_path / "outside.py"
    outside.write_text("eval(x)\n")
    base = tmp_path / "base"
    base.mkdir()
    assert len(analyze_file(outside, base)) == 1
