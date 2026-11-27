# Architecture

Static pipeline: file discovery -> bounded text reads -> rule evaluation ->
structured findings -> JSON export. Future model providers must receive only
user-approved source excerpts and must not execute tools implicitly.

## The rule table

`analyzer.RULES` is the single definition of what the tool detects. Each entry
is a `Rule` and carries its own metadata:

| Field | Meaning |
| --- | --- |
| `rule_id` | Stable identifier (`RG001` …), quoted in every report and test plan. |
| `severity` | How bad it is if true: `high`, `medium`, `low`, `info`. |
| `confidence` | How sure the textual matcher is: `high`, `medium`, `low`. |
| `pattern` | The compiled regular expression. Matching is per line. |
| `cwe` | The CWE the weakness maps to. Only identifiers MITRE accepts for vulnerability mapping are used; an identifier it marks *discouraged* names a catch-all class instead of the defect. |
| `owasp_llm` | The OWASP Top 10 for LLM Applications 2026 category, when the weakness is specific to LLM-backed code. |
| `references` | The reference links that back the mapping. |
| `scope` | The file kinds the rule applies to. Empty means every source file. |
| `requires` | Patterns that must appear in the file for the rule to fire. |
| `forbids` | Patterns that must not appear in the file for the rule to fire. |

Severity and confidence are kept apart on purpose: a pattern that matches a
dangerous spelling with certainty (`trust_remote_code=True`) is high on both,
while a pattern that matches a shape that is often harmless (`from_pretrained("org/model")`
without a pinned revision) is medium severity and low confidence. A reviewer
needs to know both.

A rule that can match a whole file needs to be anchored tightly. The rules
that describe an absence — a missing timeout, a missing filter — use a
negative lookahead so that a call that *does* carry the guard stays quiet.

## The scope of a rule

The rule table grew past the point where every rule could sensibly be applied
to every file. A hook script, a skill manifest and a Python module are
different things, and a signal that means a weakness in one of them means
nothing in the others. A rule therefore carries a `scope`:

| Token | The rule applies to |
| --- | --- |
| *(empty)* | Every source file. |
| `source` | Every source file. |
| `manifest` | The agent manifests. |
| `.py`, `.sh`, `.json`, … | Every file with that extension. |
| `*mcp*.json`, `claude_desktop_config.json` | Every file whose name matches the pattern. |

A rule with several tokens applies to a file when *one* of them matches. A
rule with no scope applies to every source file and to no manifest.

### The manifest scope

An agent reads `SKILL.md`, `AGENTS.md`, `CLAUDE.md`, `.cursorrules` and
`*.mdc` as instructions, not as source code. Those files get their own scan
scope — `MANIFEST_NAMES`, `MANIFEST_SUFFIXES` and `MANIFEST_PATTERNS` — and
are read by the manifest-scoped rules only. Pulling every Markdown file into
the source-code scan instead would fill the report with prose matches, and
would hide the handful of lines that matter.

### Rules that need more than one line

Most rules decide from a single line. Two cannot:

- A rule that describes an *absence* — a call without a timeout, a retrieval
  without a filter — uses a negative lookahead, so a call that carries the
  guard stays quiet. The guard is recognized wherever it appears in the call,
  including inside a nested dictionary.
- A rule that describes an *unbounded loop* cannot. `RG020` anchors on the
  `while True:` header and states two file-level conditions in `requires` and
  `forbids`: the file has to hold the model call, and it must not hold a
  `break` or a `return`. That is a file-level approximation of the loop, so
  the rule ships with low confidence and says so.

Both mechanisms are additive. The rule table is still a list of regular
expressions, and the report shape does not change.

## Silencing a line

A reviewed line can opt out of the scanner with `# rasgulla: ignore`. The
marker is matched per line, it is never inferred, and it silences that line
only. A reviewer who has looked at a finding can record that, and a reviewer
who has not leaves it in the report.

The marker is also how this repository scans itself: its own test fixtures
contain the dangerous spellings the rules look for, because a test has to
contain them. Without the marker the scanner would report 97 findings on its
own source tree, all of them true positives about the text and none of them
useful.

## The report

`cli.main` writes `schema_version: 2`:

- `summary` — total, and counts by severity, by confidence and by OWASP category.
- `rules` — the whole rule catalogue, so a consumer renders the taxonomy without hardcoding it.
- `findings` — one entry per match, with the evidence line, the recommendation and the rule metadata.
- `notice` — the standing statement of what the tool does not do.

The test plan is derived from the findings through `testplan.RULE_TEMPLATES`,
one template per rule. A rule without a template would be silently dropped
from the plan, so a test asserts that the two tables agree.

Planned interfaces: Analyzer, FindingStore, ModelProvider, TestPlanGenerator.
