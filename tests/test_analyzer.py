import importlib
from pathlib import Path

import rasgulla
from rasgulla.analyzer import analyze_file, analyze_path


def _analyzer():
    """Return the analyzer module at runtime.

    The module is resolved inside the test so a missing attribute is reported
    as a test failure instead of an import error during collection.
    """
    return importlib.import_module("rasgulla.analyzer")


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


# --- TLS verification is disabled in more than one ecosystem ------------------


def test_node_tls_verification_is_detected(tmp_path: Path):
    (tmp_path / "client.js").write_text(
        "const agent = new https.Agent({ rejectUnauthorized: false });\n"
    )
    assert "RG003" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_go_tls_verification_is_detected(tmp_path: Path):
    (tmp_path / "client.go").write_text(
        "client := &http.Client{Transport: &http.Transport{\n"
        "    TLSClientConfig: &tls.Config{InsecureSkipVerify: true},\n"
        "}}\n"
    )
    assert "RG003" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_python_unverified_ssl_context_is_detected(tmp_path: Path):
    (tmp_path / "client.py").write_text(
        "context = ssl._create_unverified_context()\n"
    )
    assert "RG003" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_curl_option_that_disables_verification_is_detected(tmp_path: Path):
    (tmp_path / "fetch.c").write_text(
        "curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);\n"
    )
    assert "RG003" in {f["rule_id"] for f in analyze_path(tmp_path)}


# --- shell execution and unsafe deserialization ------------------------------


def test_shell_enabled_child_process_is_detected(tmp_path: Path):
    (tmp_path / "runner.py").write_text(
        "subprocess.run(command, shell=True)\n"
    )
    assert "RG005" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_unsafe_deserialization_is_detected(tmp_path: Path):
    (tmp_path / "loader.py").write_text(
        "state = pickle.loads(payload)\n"
        "other = yaml.load(document)\n"
    )
    rules = {f["rule_id"] for f in analyze_path(tmp_path)}
    assert "RG006" in rules


def test_dynamic_function_construction_is_detected(tmp_path: Path):
    (tmp_path / "app.js").write_text("const fn = new Function('return 1');\n")
    assert "RG006" in {f["rule_id"] for f in analyze_path(tmp_path)}


# --- hard-coded secrets ------------------------------------------------------


def test_hardcoded_secret_is_detected(tmp_path: Path):
    (tmp_path / "settings.py").write_text('password = "s3cr3t-value-here"\n')
    assert "RG004" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_yaml_secret_is_detected(tmp_path: Path):
    (tmp_path / "settings.yaml").write_text("api_key: 9f8e7d6c5b4a3210\n")
    assert "RG004" in {f["rule_id"] for f in analyze_path(tmp_path)}


def test_secret_value_is_never_copied_into_the_report(tmp_path: Path):
    secret = "s3cr3t-value-here"
    (tmp_path / "settings.py").write_text(f'password = "{secret}"\n')
    findings = analyze_path(tmp_path)
    evidence = " ".join(f["evidence"] for f in findings)
    assert secret not in evidence
    assert "***" in evidence


def test_placeholder_secret_is_not_reported(tmp_path: Path):
    (tmp_path / "settings.py").write_text(
        'password = "your_password_here"\n'
        'api_key = "changeme"\n'
        'token = "example-token"\n'
    )
    assert "RG004" not in {f["rule_id"] for f in analyze_path(tmp_path)}


# --- scope of the scan -------------------------------------------------------


def test_more_source_types_are_scanned(tmp_path: Path):
    (tmp_path / "Service.cs").write_text('password = "s3cr3t-value-here"\n')
    (tmp_path / "deploy.sh").write_text('api_key = "s3cr3t-value-here"\n')
    (tmp_path / "config.yaml").write_text("password: 9f8e7d6c5b4a3210\n")
    findings = analyze_path(tmp_path)
    assert {f["path"] for f in findings} == {
        "Service.cs",
        "config.yaml",
        "deploy.sh",
    }


def test_an_explicitly_named_file_is_always_analyzed(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("eval(x)\n")
    assert len(analyze_path(target)) == 1


def test_findings_are_ordered_deterministically(tmp_path: Path):
    for name in ("zeta.py", "Alpha.py", "beta.py"):
        (tmp_path / name).write_text("eval(x)\n")
    paths = [f["path"] for f in analyze_path(tmp_path)]
    assert paths == sorted(paths)


# --- symbolic links ----------------------------------------------------------


class _FakeSymlink:
    """Minimal path-like object that reports itself as a symbolic link."""

    def __init__(self, parts):
        self._parts = tuple(parts)

    @property
    def parts(self):
        return self._parts

    def relative_to(self, other):
        return self

    def is_symlink(self):
        return True


def test_should_scan_rejects_a_symbolic_link():
    assert _analyzer().should_scan(_FakeSymlink(("project", "link.py")), "project") is False


def test_should_scan_accepts_an_ordinary_source_file(tmp_path: Path):
    path = tmp_path / "app.py"
    path.write_text("value = 1\n")
    assert _analyzer().should_scan(path, tmp_path) is True


def test_symlinked_directory_is_not_followed(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("eval(user_input)\n")
    project = tmp_path / "project"
    project.mkdir()
    try:
        (project / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        import pytest
        pytest.skip("symbolic links require an extra privilege on this platform")
    assert analyze_path(project) == []


# --- public API --------------------------------------------------------------


def test_package_exposes_the_public_api():
    assert rasgulla.analyze_path is analyze_path
    assert rasgulla.analyze_file is analyze_file
    assert rasgulla.__version__
