# Remediation notes — 2026-11

This document records the audit and remediation applied to Rasgulla AI in November 2026.
It is additive: the original README, SECURITY.md, CONTRIBUTING.md and architecture notes
remain in the repository and were updated only where they were inaccurate or
inconsistent.

## Method

Every source file was read line by line and each suspected defect was confirmed with
an executable regression test. The new tests were first run against the previously
published revision to confirm the defect was real (red phase: 42 failing tests), and
only then were the fixes applied (green phase: 49 passing tests, 1 skipped).

The one skipped test is a symbolic-link integration test that needs a privilege this
Windows host does not grant. The same rule is covered locally by a unit test with a
link stub, and the integration test runs in GitHub Actions on Linux.

## Defects fixed

| Severity | Defect | Fix |
| --- | --- | --- |
| High | The corpus walk used `rglob` and followed symlinked directories, so a scan could read files outside the project or loop forever | The scan walks with `os.walk(followlinks=False)` and never follows a link |
| Medium | TLS verification was only detected in the Python spelling (`verify=False`, `CERT_NONE`), so the most common real-world cases (Node.js, Go, curl, configuration files) were missed | `RG003` now covers `rejectUnauthorized: false`, `InsecureSkipVerify: true`, `verify_ssl: false`, `check_hostname: false`, `_create_unverified_context`, `SSL_VERIFY_NONE` and `CURLOPT_SSL_VERIFYPEER/HOST, 0` |
| Medium | A detected secret was copied verbatim into the report, so the report itself became a second copy of the credential | `RG004` evidence is redacted; the value is replaced with `***` |
| Medium | A placeholder such as `your_password_here` or `changeme` was reported as a hard-coded secret | Placeholder values are filtered out before a finding is raised |
| Medium | The command line raised a raw Python traceback for an unwritable report file, and it always exited `0`, so a CI pipeline could not fail on findings | `cli.main` reports one-line messages and uses exit codes `0` / `1` / `2`, with `--fail-on-findings` and `--output` |
| Medium | The README promised "safe local test-plan generation" and a "provider-neutral model adapter interface", but neither existed | New `rasgulla.testplan` and `rasgulla.adapters` modules, exposed on the command line with `--test-plan` and `--adapter` |
| Low | A child process started with the shell interpreter enabled was not detected | New rule `RG005` |
| Low | Unsafe deserialization (`pickle.loads`, `yaml.load`) and dynamic code construction (`new Function(`) were not detected | New rule `RG006` |
| Low | Only ten file extensions were scanned, so most source and configuration files were invisible | The scan scope now covers the common source, script and configuration types |
| Low | A file named explicitly on the command line was silently skipped when its extension was outside the scan scope | An explicitly named file is always analyzed |
| Low | The package exposed no public API and the report carried no format marker | `rasgulla.__all__`, `rasgulla.__version__`, and `schema_version` in the report |

## Documentation and spelling corrections

- `LICENSE` shipped a truncated MIT text: the warranty and liability clauses were
  missing. The complete, canonical MIT text is now in place.
- Spelling was inconsistent across the same file set (`behaviour` in `CONTRIBUTING.md`
  against `authorization` in `SECURITY.md` and `normalized` in `README.md`). The
  prose now uses one convention throughout: American English.
- The roadmap listed "Documentation and contribution guide" as unfinished while
  `CONTRIBUTING.md`, `SECURITY.md` and this file exist. The checklist now reflects
  reality, and "Stable public API and versioned report schema" is marked complete.
- The README documented no command-line usage, no exit codes and no rule catalogue,
  although the project ships a console script and six rules. All three are now
  documented, including the redaction guarantee for `RG004`.
- A dictionary spelling scan (`codespell`) now reports no misspelling in this
  repository.

## Continuous integration hardening

The workflow previously ran the test suite only. It now also runs a credential scan
on every push and pull request.

| Job | What it does |
| --- | --- |
| `secret-scan` | Runs `scripts/scan-secrets.py`, which fails the build when the repository contains a high-confidence credential pattern (cloud keys, tokens, private key blocks, JWTs) or a file that must never be committed (`.env`, `*.pem`, `*.key`, `*.p12`, `id_rsa`, credential files) |

The scanner is the one already used by the Gravion Toolkit split repositories, so
every toolkit project now shares one definition of what counts as a secret. A
"soft" match, such as a dummy `api_key` inside a test fixture, is reported for
review but does not fail the build.

## Verification

| Check | Before | After |
| --- | --- | --- |
| Secret scan in CI | absent | present |
| Tests | 5 | 49 (+ 1 skipped) |
| `ruff check .` | clean | clean |
| Rules | 4 (`RG001`–`RG004`) | 6 (`RG001`–`RG006`) |
| Modules | `analyzer` | `analyzer`, `testplan`, `adapters` |

## Residual limitations

- The rules are textual heuristics. They match spellings of dangerous APIs, not
  the data flow through them, so a finding is a prompt to look, not a proof of
  exploitability.
- `RG004` matches assignment and YAML shapes only. A secret built from parts, or
  stored in a format this scan does not cover, is not reported.
- The `static` adapter is a fixed template engine, not a model. It produces the
  same plan for the same findings every time, and it has no understanding of the
  code under review.
- No model provider ships with this repository. Registering one is a deliberate
  act by the operator and must follow the approval rules in `docs/ARCHITECTURE.md`.
