# Rasgulla AI

> A local-first AI-assisted security research workbench for code understanding, safe test generation, and reproducible debugging.

**Status:** MVP scaffold / research prototype. Not production-certified.

## Features
- Source-code inventory and heuristic risk hints.
- Explainable findings with file/line evidence.
- Safe local test-plan generation.
- Provider-neutral model adapter interface (no API key required for static mode).
- Redacted evidence: a detected secret value is never copied into the report.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
pytest
```

## Command-line usage

```bash
rasgulla ./src                                # analyze a directory and print the report
rasgulla ./src --output report.json           # also write the report to a file
rasgulla ./src --test-plan                    # add a local, offline test plan
rasgulla ./src --test-plan --adapter static   # choose the adapter (static or null)
rasgulla ./src --test-plan --max-steps 10     # cap the number of plan entries
rasgulla ./src --fail-on-findings             # exit 1 when at least one finding is reported
```

| Exit code | Meaning |
| --- | --- |
| `0` | The scan ran and the report was produced. |
| `1` | The report could not be written, or `--fail-on-findings` was given and findings exist. |
| `2` | The command line itself was wrong: a path that does not exist, an unknown adapter, or a negative `--max-steps`. |

Problems are reported as one-line messages. The command never prints a Python
traceback, so it can be used directly in a CI pipeline.

## Rules

| Rule | Severity | Detects |
| --- | --- | --- |
| `RG001` | high | Dynamic evaluation (`eval(`). |
| `RG002` | high | Process execution primitives (`exec(`, `system(`). |
| `RG003` | medium | TLS certificate or hostname verification disabled, in Python, Node.js, Go, curl and common configuration spellings. |
| `RG004` | medium | Possible hard-coded secret, in assignment and YAML form. The value is redacted in the report. |
| `RG005` | high | A child process started with the shell interpreter enabled. |
| `RG006` | high | Unsafe deserialization (`pickle.loads`, `yaml.load`) or dynamic code construction (`new Function(`). |

A placeholder value such as `your_password_here`, `changeme` or `example-token`
is not reported as a secret.

## Python API

```python
from pathlib import Path

from rasgulla import analyze_path, build_test_plan, get_adapter

findings = analyze_path(Path("./src"))
for finding in findings:
    print(finding["rule_id"], finding["path"], finding["line"], finding["message"])

plan = get_adapter("static").generate_test_plan(findings)
assert plan == build_test_plan(findings)
```

The `static` adapter is deterministic and completely offline: it needs no model,
no network and no API key. Another provider can be plugged in by implementing
the `ModelAdapter` protocol and registering it in `ADAPTERS`.

## Safety and scope
The MVP performs static inspection only. It does not execute generated code, exploit targets, access networks, collect credentials, or bypass model safeguards. Treat findings as hypotheses requiring human validation.

## Roadmap
- [x] Stable public API and versioned schemas
- [x] More unit, integration and adversarial tests
- [ ] Reproducible benchmarks and sample datasets
- [ ] Signed releases and dependency review
- [x] Documentation and contribution guide

## License
MIT. See [LICENSE](LICENSE). The license file carries the complete MIT text, including the warranty and liability clauses.
