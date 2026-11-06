"""Heuristic static risk analysis for Rasgulla AI.

Findings are hypotheses for human review. The analyzer never executes the code
it inspects, never accesses a network, and never collects credentials.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "EXTENSIONS",
    "IGNORED_DIRS",
    "RULES",
    "Finding",
    "analyze_file",
    "analyze_path",
]

EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".rs", ".go", ".cs", ".java",
    ".kt", ".swift", ".rb", ".php", ".sh", ".bash", ".ps1", ".yaml", ".yml", ".json", ".toml",
    ".ini", ".conf", ".cfg", ".env", ".properties", ".xml", ".tf", ".gradle", ".dockerfile",
}
# Dependency, build and cache directories hold third-party or generated code, not project source.
IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
    "site-packages", "target", ".idea", ".vscode", "vendor", "coverage",
}


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    path: str
    line: int
    evidence: str
    recommendation: str

    def to_dict(self):
        return asdict(self)


RULES = [
    ("RG001", "high", re.compile(r"\beval\s*\("),
     "Dynamic evaluation detected", "Avoid evaluating untrusted input; use a parser or explicit dispatch."),
    ("RG002", "high", re.compile(r"\b(exec|system)\s*\("),
     "Process execution primitive detected", "Validate inputs and prefer argument arrays with shell disabled."),
    # TLS verification is disabled in several ecosystems. Only the Python spelling
    # was detected before, which missed the most common real-world cases.
    ("RG003", "medium", re.compile(
        r"verify\s*=\s*False|CERT_NONE|verify\s*:\s*false|rejectUnauthorized\s*:\s*false"
        r"|InsecureSkipVerify\s*:\s*true|verify_ssl\s*:\s*false|check_hostname\s*=\s*False"
        r"|_create_unverified_context|SSL_VERIFY_NONE|CURLOPT_SSL_VERIFYPEER\s*,\s*0"
        r"|CURLOPT_SSL_VERIFYHOST\s*,\s*0"),
     "TLS certificate verification may be disabled", "Keep certificate and hostname verification enabled."),
    ("RG004", "medium", re.compile(
        r"(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\n]{4,}['\"]"
        r"|(?m:^\s*(?:password|passwd|secret|api[_-]?key|token)\s*:\s*(?![<\[({])[^\s'\"#]{4,}\s*$)"),
     "Possible hard-coded secret", "Move secrets to a managed secret store and rotate exposed credentials."),
    ("RG005", "high", re.compile(r"shell\s*[:=]\s*True|shell\s*:\s*true", re.IGNORECASE),
     "Shell interpreter enabled for a child process",
     "Pass an argument array and leave shell disabled so input cannot reach a command interpreter."),
    ("RG006", "high", re.compile(
        r"\b(?:pickle|cPickle|marshal)\.loads?\s*\(|\byaml\.(?:load|unsafe_load)\s*\("
        r"|\bnew\s+Function\s*\(|\bunserialize\s*\("),
     "Unsafe deserialization or dynamic code construction",
     "Use a safe loader for untrusted data and never rebuild code from input."),
]

# A secret value that is obviously a placeholder is not worth reporting.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*)['\"]?([^\s'\"#,}]{4,})['\"]?"
)
_PLACEHOLDER_PREFIXES = (
    "your", "example", "changeme", "change-me", "change_me", "placeholder", "redacted",
    "todo", "dummy", "fake", "sample", "insert", "replace", "xxx",
)
_PLACEHOLDER_VALUES = {"none", "null", "nil", "true", "false", "test", "password", "secret"}


def _secret_value(line: str) -> str | None:
    match = _SECRET_ASSIGNMENT.search(line)
    return match.group(2) if match else None


def _is_placeholder_secret(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.strip().strip("\"'").lower()
    if not lowered or lowered in _PLACEHOLDER_VALUES:
        return True
    if set(lowered) <= {"x", "*", ".", "-", "_"}:
        return True
    return lowered.startswith(_PLACEHOLDER_PREFIXES)


def _redact_secrets(text: str) -> str:
    """Replace a detected secret value so the report cannot leak it."""
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}***", text)


def _is_ignored(path: Path, root: Path) -> bool:
    # Only directory components below the scan root are ignored, so a project that
    # happens to live under a directory with an ignored name is still scanned.
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        parts = path.parts[:-1]
    return any(part in IGNORED_DIRS for part in parts)


def _display_path(path: Path, base: Path | None) -> str:
    if base:
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(path)


def should_scan(path: Path, root: Path) -> bool:
    """Decide whether a path is in scope.

    Symbolic links are never followed: a link can point outside the project, or
    back at a parent directory.
    """
    if path.is_symlink():
        return False
    if _is_ignored(path, root):
        return False
    return path.suffix.lower() in EXTENSIONS


def analyze_file(path: Path, base: Path | None = None):
    results = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return results
    display = _display_path(path, base)
    for number, line in enumerate(lines, 1):
        for rule_id, severity, pattern, message, recommendation in RULES:
            if not pattern.search(line):
                continue
            evidence = line.strip()[:240]
            if rule_id == "RG004":
                # A placeholder value is not a finding, and a real secret must
                # never be copied into the report.
                if _is_placeholder_secret(_secret_value(line)):
                    continue
                evidence = _redact_secrets(evidence)
            results.append(Finding(rule_id, severity, message, display, number,
                                   evidence, recommendation))
    return results


def analyze_path(root: Path):
    root = root.resolve()
    findings = []
    if root.is_file():
        # A file named explicitly is analyzed even when its type is outside the
        # traversal scope: the operator asked for that file.
        files = [root]
    else:
        files = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                name for name in dirnames
                if name not in IGNORED_DIRS and not (Path(dirpath) / name).is_symlink()
            )
            for name in sorted(filenames):
                path = Path(dirpath) / name
                if should_scan(path, root):
                    files.append(path)
    for file in files:
        findings.extend(analyze_file(file, root if root.is_dir() else file.parent))
    return [f.to_dict() for f in findings]
