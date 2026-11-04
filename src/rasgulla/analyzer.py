import re
from dataclasses import asdict, dataclass
from pathlib import Path

EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".rs", ".go"}
# Dependency, build and cache directories hold third-party or generated code, not project source.
IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist",
                ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
                "site-packages"}

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
    ("RG003", "medium", re.compile(r"(verify\s*=\s*False|CERT_NONE)"),
     "TLS certificate verification may be disabled", "Keep certificate and hostname verification enabled."),
    ("RG004", "medium", re.compile(r"(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{6,}"),
     "Possible hard-coded secret", "Move secrets to a managed secret store and rotate exposed credentials."),
]

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
            return str(path.relative_to(base))
        except ValueError:
            pass
    return str(path)


def analyze_file(path: Path, base: Path | None = None):
    results = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return results
    display = _display_path(path, base)
    for number, line in enumerate(lines, 1):
        for rule_id, severity, pattern, message, recommendation in RULES:
            if pattern.search(line):
                results.append(Finding(rule_id, severity, message, display, number,
                                       line.strip()[:240], recommendation))
    return results

def analyze_path(root: Path):
    root = root.resolve()
    findings = []
    files = [root] if root.is_file() else sorted(
        x for x in root.rglob("*") if x.is_file() and x.suffix.lower() in EXTENSIONS
        and not _is_ignored(x, root)
    )
    for file in files:
        if file.suffix.lower() in EXTENSIONS:
            findings.extend(analyze_file(file, root if root.is_dir() else file.parent))
    return [f.to_dict() for f in findings]
