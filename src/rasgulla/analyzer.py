import re
from dataclasses import asdict, dataclass
from pathlib import Path

EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".rs", ".go"}

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

def analyze_file(path: Path, base: Path | None = None):
    results = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return results
    display = str(path.relative_to(base)) if base else str(path)
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
        and not any(part in {".git", ".venv", "node_modules"} for part in x.parts)
    )
    for file in files:
        if file.suffix.lower() in EXTENSIONS:
            findings.extend(analyze_file(file, root if root.is_dir() else file.parent))
    return [f.to_dict() for f in findings]
