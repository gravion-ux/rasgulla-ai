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

### Executable bit on the secret scanner

The first run of the new pipeline failed on the Ubuntu runner with
`EXE001 Shebang is present but file is not executable`. Git on Windows has no
execute bit, so `scripts/scan-secrets.py` was published with mode `100644`
although it carries a `#!/usr/bin/env python3` shebang, and the checkout on
the runner produced a non-executable file. The scanner is now committed with
mode `100755`, which is what the shebang promises, and it can also be run
directly as `./scripts/scan-secrets.py .`.

## Verification

| Check | Before | After |
| --- | --- | --- |
| Secret scan in CI | absent | present |
| Secret scanner file mode | `100644` (not executable) | `100755` (executable) |
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

---

# Round 2 — the AI/LLM rule set

The second pass in November 2026 closed the gap that made this an "AI security"
tool that could not see AI code. The first round hardened the analyzer; it
never gave it a single LLM-specific rule.

## Method

Every source file was read line by line again. Each behavior change was
written as a failing test first and confirmed against the previously published
revision (red phase), then implemented (green phase). The rule table was
compared against the published OWASP taxonomy for LLM-backed applications and
against the rule corpora of the established static analyzers, so the gaps are
named rather than guessed.

## Defects fixed

| Severity | Defect | Fix |
| --- | --- | --- |
| High | The analyzer had no LLM-specific rule at all: a prompt built from user input, an agent with unrestricted tools, `trust_remote_code=True` and a model answer passed to `exec()` were all invisible | New rules `RG007`–`RG018`, covering prompt injection, sensitive data in prompts, excessive agency, model supply chain, poisoning, unbounded consumption, improper output handling and hidden context exposure |
| High | A finding carried no way to judge it: no confidence, no weakness class, no reference | Every finding now carries `severity`, `confidence`, `cwe`, `owasp_llm`, `owasp_llm_name` and `references`; severity and confidence are separate, because a certain match of a common spelling is not the same as an uncertain match of a rare one |
| High | A query built by string interpolation was not detected at all | New rule `RG016` (CWE-89) |
| Medium | The report had no summary and no rule catalogue, so a consumer had to hardcode the taxonomy to render it | The report carries a `summary` block (total, by severity, by confidence, by OWASP category) and the full `rules` catalogue; `schema_version` raised to `2` |
| Medium | The rule table could gain a rule that the test plan silently ignored, because `build_test_plan` skips a rule with no template | Every rule has a template, and a test asserts that the two tables agree |
| Medium | The secret scanner's allow-list was defined but never consulted by the scan, and its one entry pointed at a file from another project | The allow-list is applied by `scan()`, and the stale entry was replaced with the one location in this repository that needs it; a test also asserts that every allow-list entry points at a file that exists |
| Low | `RULES` was a list of five-value tuples, so there was nowhere to put metadata | `Rule` is a dataclass; it still unpacks as the original five values, so older callers keep working |

## Rule coverage

| OWASP LLM 2026 | Covered by |
| --- | --- |
| LLM01 Prompt Injection | `RG007` |
| LLM02 Sensitive Information Disclosure | `RG004`, `RG008` |
| LLM03 Excessive Agency | `RG009` |
| LLM04 Supply Chain | `RG010`, `RG011` |
| LLM05 Data and Model Poisoning | `RG006`, `RG012` |
| LLM06 Unbounded Consumption | `RG014` |
| LLM07 Misinformation | not covered — see the limitations below |
| LLM08 Hidden Context Exposure | `RG017` |
| LLM09 Vector and Embedding Weaknesses | `RG018` |
| LLM10 Improper Output Handling | `RG015` |

## Verification

| Check | Before | After |
| --- | --- | --- |
| Tests | 49 (+ 1 skipped) | 89 (+ 1 skipped) |
| `ruff check .` | clean | clean |
| Rules | 6 (`RG001`–`RG006`) | 18 (`RG001`–`RG018`) |
| Report | `schema_version: 1` | `schema_version: 2` (summary + rule catalogue) |
| Finding metadata | severity only | severity, confidence, CWE, OWASP LLM category, references |
| Secret scanner allow-list | defined, never applied | applied, and every entry points at an existing file |

## Residual limitations

- The rules are textual heuristics. They match spellings of dangerous APIs, not
  the data flow through them, so a finding is a prompt to look, not a proof of
  exploitability.
- **LLM07 (Misinformation) has no static signal.** A model that answers wrongly
  is indistinguishable from a model that answers rightly in the source code.
  It needs evaluation against a test set, and this tool does not pretend
  otherwise.
- The rules that describe an absence — a missing timeout, a missing output cap,
  a missing filter — can only be seen when the whole call fits on one line.
  A call split across several lines is not detected.
- `RG007`, `RG008` and `RG011` are heuristics over variable *names*. A prompt
  built from `payload` is flagged; a prompt built from a well-named variable
  that still carries user input is not.
- The `static` adapter is a fixed template engine, not a model. It produces the
  same plan for the same findings every time, and it has no understanding of
  the code under review.
- No model provider ships with this repository. Registering one is a deliberate
  act by the operator and must follow the approval rules in `docs/ARCHITECTURE.md`.


---

# Round 3 — the CWE mapping audit

The second round gave every finding a CWE. This round checked those mappings
against MITRE itself.

## Method

Every CWE identifier in the rule table was read on
[cwe.mitre.org](https://cwe.mitre.org/), and the *Vulnerability Mapping Notes*
section of each entry was read for its mapping usage: `ALLOWED`,
`ALLOWED-WITH-REVIEW` or `DISCOURAGED`.

## Defects fixed

| Severity | Defect | Fix |
| --- | --- | --- |
| High | Five rules mapped to a CWE identifier MITRE marks *discouraged* — "this CWE ID should not be used to map to real-world vulnerabilities" — because the identifier names a catch-all class rather than the defect: `RG007` (CWE-74), `RG008` (CWE-200), `RG013` and `RG014` (CWE-400), `RG018` (CWE-668) | Each rule now maps to the identifier that names its own defect: `RG007` to CWE-1427 (*Improper Neutralization of Input Used for LLM Prompting*, the identifier MITRE wrote for prompt injection), `RG008` to CWE-201 (*Insertion of Sensitive Information Into Sent Data*), `RG013` and `RG014` to CWE-770 (*Allocation of Resources Without Limits or Throttling*), `RG018` to CWE-862 (*Missing Authorization*) |
| Medium | `RG009` mapped to CWE-732, which MITRE marks *allowed with review* and records as frequently misused — it is usually applied to a permission-assignment mistake rather than to the defect itself | `RG009` now maps to CWE-250 (*Execution with Unnecessary Privileges*), the base-level identifier that names the weakness |
| Medium | Nothing stopped a later rule from reintroducing a discouraged identifier | `tests/test_llm_rules.py` carries the list of identifiers this project accepts, and a test fails if a rule maps to anything else |

## Verification

| Check | Before | After |
| --- | --- | --- |
| Rules mapped to a CWE MITRE marks *discouraged* | 5 | 0 |
| Rules mapped to an identifier MITRE still accepts | 13 | 18 |
| Tests | 89 (+ 1 skipped) | 91 (+ 1 skipped) |

---

# Round 4 — the agent runtime, and the gaps the first rounds left open

The third round checked the mappings. This round widened what the scanner
looks at, and closed the gaps an independent review of the previous change
set found in it.

## Method

Every family of the Semgrep `ai/ai-best-practices` corpus (52 of them) was
enumerated and compared against the rule table. A family was implemented only
when all four of these held:

1. **It is reachable by a line-level matcher.** The families whose upstream
   rule is `mode: taint` were rejected rather than approximated into noise.
2. **It is not already covered.** A family that `RG001`–`RG018` detects was
   rejected as a duplicate; no existing rule id was renumbered or removed.
3. **Its CWE identifier is one MITRE still accepts.** Every mapping was read
   against the *Vulnerability Mapping Notes* on cwe.mitre.org, and the
   identifiers MITRE marks `DISCOURAGED` were rejected.
4. **It maps to one of the ten published OWASP LLM 2026 categories**, checked
   against genai.owasp.org.

Every behavior change was written as a failing test first and confirmed to
fail for the right reason before the code was written.

## The new rules

| Rule | Detects | CWE | OWASP |
| --- | --- | --- | --- |
| `RG019` | A LangChain execution utility given to an agent | CWE-94 | LLM03 |
| `RG020` | A model call in a loop with no visible exit | CWE-835 | LLM06 |
| `RG021` | An MCP tool result that carries a credential | CWE-522 | LLM02 |
| `RG022` | A tool description or manifest with hidden instructions | CWE-1427 | LLM01 |
| `RG023` | A plaintext provider key in an MCP configuration file | CWE-798 | LLM04 |
| `RG024` | A hook that pipes a download into a shell | CWE-829 | LLM04 |
| `RG025` | A hook that allows a tool call unchecked | CWE-862 | LLM03 |
| `RG026` | A hook that carries data out through DNS | CWE-201 | LLM02 |
| `RG027` | A manifest that sends the agent to a credential file | CWE-522 | LLM02 |
| `RG028` | A manifest that sends data to a remote endpoint | CWE-201 | LLM02 |
| `RG029` | A manifest that embeds an encoded payload to run | CWE-506 | LLM01 |
| `RG030` | Invisible characters in an agent configuration file | CWE-176 | LLM01 |
| `RG031` | Every MCP server of a project enabled without confirmation | CWE-862 | LLM03 |
| `RG032` | The permission or sandbox control switched off | CWE-862 | LLM03 |

Two mappings deliberately differ from the upstream rule. `RG022` maps to
CWE-1427 (*Improper Neutralization of Input Used for LLM Prompting*) and not
to the CWE-77 the upstream rule claims: the weakness is prompt injection
through a tool description, which is what CWE-1427 names. `RG020` maps to
CWE-835 (*Loop with Unreachable Exit Condition*), the identifier the upstream
rule itself names.

## The families that were rejected

| Family | Reason |
| --- | --- |
| `mcp-command-injection`, `mcp-ssrf`, `mcp-unsanitized-return` | `mode: taint`. The signal is a parameter reaching a sink; a line-level matcher would fire on every `subprocess.run` and every `requests.get`, which the tool already reports as `RG005` and `RG013` without the false "this is MCP" claim. |
| `mcp-typosquatted-tool-name` | The upstream rule hard-codes a finite list of misspellings. It would detect exactly those and nothing else. |
| `hooks-no-input-validation`, `hooks-unquoted-variable` | The sinks are already `RG001` and `RG002`. |
| `hooks-sensitive-file-access` (shell) | `mode: taint`; the manifest equivalent (`RG027`) covers the readable form. |
| `hooks-relative-script-path`, `hooks-stop-missing-active-check`, `ide-settings-executable-path` | Correctness rules about the agent's own wiring, not security weaknesses. |
| `openai-*`, `anthropic-*`, `gemini-*`, `cohere-*`, `mistral-*`, `huggingface-*` API-guard families | API-quality rules — missing moderation, missing system message, missing error handling. They are not vulnerability classes and do not map to a CWE. |
| `llm-api-key-in-source`, `*-hardcoded-api-key` | Duplicate of `RG004`. |

## Defects fixed in the earlier change set

An independent review of the second and third rounds found the following. Each
one was reproduced as a failing test before it was fixed.

| Severity | Defect | Fix |
| --- | --- | --- |
| High | `RG009` only matched the Python spelling of an unrestricted agent. Every alternative hard-coded `\s*=\s*`, so the YAML form (`require_approval: false`) and the JSON form (`{"skip_confirmation": true}`) were both missed — which is where an agent's settings usually live | The key may be quoted and the separator may be `=` or `:`, so the code, the YAML and the JSON spelling are all detected |
| Medium | The evidence of a line was redacted only for `RG004`. A line that held both a secret and a prompt reported the raw secret under the prompt rule, so the report held a second copy of the credential | The redaction applies to the evidence of every rule |
| Medium | `RULES` held dataclasses, so `rule[0]`, `len(rule)` and `isinstance(rule, tuple)` stopped working. The rule table is public and the documentation claimed compatibility | `Rule` now indexes and measures as the original five-value tuple |
| Medium | The prompt rules were blind to JavaScript and TypeScript: a template literal (`const prompt = \`Answer ${userInput}\``) and a concatenation (`"Answer " + userInput`) produced no finding, although `.js` and `.ts` are scanned | The patterns accept the template-literal and string-concatenation forms, and one spelling covers both the `snake_case` and the `camelCase` convention |
| Medium | `RG014` fired on a call that carried its cap inside a nested dictionary (`extra_body={"max_tokens": 10}`), and `RG018` fired on a retrieval that filtered through `filters=` | The guard is recognized wherever it appears in the call, and both `filter` and `filters` count as a filter |
| Low | `RG004` missed a secret under a quoted JSON key (`{"api_key": "…"`) and under an uppercase key (`API_KEY = "…"`) | Both spellings are detected, and the value is redacted in both |
| Low | The rule catalogue carried the identifiers and the message but not the recommendation, although the README calls it the catalogue of everything the scanner can report | The catalogue carries the recommendation and the pattern as well |
| Low | The documented test command failed from a clean checkout, because the package was not installed | `pythonpath = ["src"]` is set in the pytest configuration |

## The scanner now scans itself

The scanner reported 97 findings on its own source tree: 66 of them in its
own test fixtures, where a dangerous spelling has to be present for the test
to mean anything, and three in its own rule patterns, which contain the text
they look for. Every one of them was a true positive about the text and none
of them was useful.

A line can now opt out with `# rasgulla: ignore`. The marker is explicit, it
is never inferred, and it silences that line only. With the markers in
place, `rasgulla . --fail-on-findings` on this repository reports nothing,
and the continuous integration runs it on every push and pull request.

## Verification

| Check | Before | After |
| --- | --- | --- |
| Rules | 18 (`RG001`–`RG018`) | 32 (`RG001`–`RG032`) |
| Tests | 91 (+ 1 skipped) | 160 (+ 1 skipped) |
| `ruff check .` | clean | clean |
| Rules mapped to a CWE MITRE marks *discouraged* | 0 | 0 |
| Findings on the scanner's own repository | 97 | 0 |
| Secret scanner | clean | clean |
| Report | `schema_version: 2` | `schema_version: 2` (the catalogue gained two fields) |

## Residual limitations

- The rules are textual heuristics. They match spellings of dangerous APIs, not
  the data flow through them, so a finding is a prompt to look, not a proof of
  exploitability.
- **LLM07 (Misinformation) has no static signal.** A model that answers wrongly
  is indistinguishable from a model that answers rightly in the source code.
- The rules that describe an absence — a missing timeout, a missing output cap,
  a missing filter — can only be seen when the whole call fits on one line.
  A call split across several lines is not detected.
- `RG007`, `RG008` and `RG011` are heuristics over variable *names*. A prompt
  built from `payload` is flagged; a prompt built from a well-named variable
  that still carries user input is not.
- `RG020` is a file-level approximation of a loop. It stays quiet when the
  file holds any `break` or `return`, so a bounded loop next to an unbounded
  one hides the unbounded one. It ships with low confidence for that reason.
- The manifest scope covers the agent manifests by name. A manifest under an
  unexpected name is read as prose and is not scanned.
- The `static` adapter is a fixed template engine, not a model. It produces the
  same plan for the same findings every time, and it has no understanding of
  the code under review.
- No model provider ships with this repository. Registering one is a deliberate
  act by the operator and must follow the approval rules in `docs/ARCHITECTURE.md`.
