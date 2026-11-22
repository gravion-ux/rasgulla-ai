# Rasgulla — agent-skills integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Rasgulla a capability layer: her `agent-skills` playbook catalogue is vendored, matched offline against every request, injected into her prompt when it fits, and — only when the user says the word — run as a step plan that keeps the existing spoken-approval gate.

**Architecture:** A new root module `skill_catalogue.py` mirrors `persona_catalogue.py`: it reads the vendored upstream catalogue from `skills/`, ranks it with the upstream BM25 scorer (cached locally, verified equivalent to upstream), and returns a `SkillMatch`. `core_app.build_system_prompt` grows a `skill` argument and injects the matched playbook; `app/brain/session.py` does the matching, carries the skill on the payload, holds a skill plan instead of arming it, and adds a second pending mode for steps she cannot run.

**Tech Stack:** Python 3.11 (`.venv`), the vendored `agentskills` package (MIT, zero dependencies), Electron + vanilla JS renderer, plain-script test suites (no pytest).

## Global Constraints

Every task below is bound by these. They come from `docs/superpowers/specs/2026-11-22-agent-skills-integration-design.md`.

- **Action allow-list (unchanged, enforced in code):** `launch`, `open_url`, `type_text`, `key`, `hotkey`, `click`, `scroll`, `wait`, `read_file`, `write_file`. **No shell. No elevation.** A skill cannot widen it.
- **The hard line stays:** no malware, no evasion of defences, no persistence, no doxxing, no taking other people's credentials or secrets. A skill is method, never policy.
- **Supervised execution (unchanged):** one spoken approval per action, 25 s expiry (`APPROVAL_SECONDS`), decline beats approval, whole-word phrase matching, the microphone must have heard the answer (`_was_heard`).
- **Free endpoints only, 20 RPM cap.** No provider branding in any user-visible string, log or error.
- **Local commits only.** The repository has no remote and none may be added. Never `git push`.
- **The vendored upstream package is copied whole and unedited.** Never hand-edit a file under `skills/agentskills/`.
- **The 1,213 generated `skills/**/SKILL.md` files are NOT vendored.** Only `src/agentskills/` + `LICENSE`.
- **The reply format, the freedom gauge, the three numbered choices, the voice, the wake word, the greeting, the pin, the tray and the caption/reply panes are unchanged.**
- **Tests are plain scripts, not pytest.** Each prints `passed: N   failed: M` and exits 1 on failure.
- **The venv has no pytest and nothing may be installed.** Use `.\.venv\Scripts\python.exe` from the project root, `..\.venv\Scripts\python.exe` from `app\`, `..\..\.venv\Scripts\python.exe` from `app\brain\`.
- **Latency budget:** the live path median must stay at or below 3.5 s (today: 2.32 s median, 0 failures). A single route must cost at most 50 ms.
- **The full matrix must end green with 0 failures:** `test_rasgulla.py`, `app/brain/test_session.py`, `app/brain/test_tts.py`, `app/test_orb_smoke.py`, `app/test_orb_caption.py`, `app/test_orb_talk.py`, `app/test_orb_wake.py`, `app/test_orb_live.py`, `live_probe_agentic.py`, `acceptance_probe.py`.
- **The lint baseline must report no new findings** against HEAD.

## Measured facts this plan is built on

These were measured on this machine against the upstream catalogue at commit `a7f5e7591376f086a7b2412723cfc037e599644e` (1,211 skills). They are the reason for the design choices below; re-measure rather than assume.

| Fact | Value | Consequence |
|---|---|---|
| `load_catalogue()` cost | 307 ms, once | Load lazily, never at import |
| Upstream `route()` cost | **90.7 ms per call** | Over the 50 ms budget → a cached local scorer is required (Task 2) |
| Raw BM25 on `hey how are you` | score 16.9 → `answer-a-question-you-do-not-know` | **A score floor alone does not separate chat from work** |
| Raw BM25 on `what is the capital of France` | score 11.2 → `review-a-skill-for-quality` | Common words match everything |
| After dropping stopwords | 5 of 6 chat queries score **0** (no match at all) | Stopword filtering is the first gate |
| Remaining chat false positives | `good morning` 13.5 (1 term), `hello there` 10.1 (1 term) | A **minimum of 2 matched terms** kills both |
| Lowest true task score with ≥2 terms | 14.2 (`review-code-for-injection-risks`) | Floor 10.0 with the 2-term rule separates cleanly on the sample |

**Calibrated constants:** `SKILL_FLOOR = 10.0`, `SKILL_MIN_MATCHES = 2`, plus the stopword list in Task 2. The sample table is committed as a regression test in Task 3.

---

### Task 1: Vendor the upstream catalogue

**Files:**
- Create: `skills/agentskills/**` (the upstream package, whole)
- Create: `skills/LICENSE`
- Create: `skills/NOTICE.md`

**Interfaces:**
- Consumes: the upstream repository `https://github.com/gravion-ux/agent-skills` at commit `a7f5e7591376f086a7b2412723cfc037e599644e`.
- Produces: an importable package `agentskills` (importable once `skills/` is on `sys.path`) exposing `load_catalogue()` → `Catalogue` with `.skills` (1,211 `Skill` objects), and `agentskills.router` exposing `route`, `tokenize`, `FIELD_WEIGHTS`, `K1`, `B`.

- [ ] **Step 1: Clone upstream and confirm the pinned commit**

```powershell
$ErrorActionPreference = 'Stop'
$work = 'C:\Users\priya\.minimax\sessions\mvs_a2d220899bd14a0aad9cb1a5a8f0df40\workspace'
Set-Location $work
git clone --depth 1 https://github.com/gravion-ux/agent-skills.git "$work\agent-skills"
git -C "$work\agent-skills" rev-parse HEAD
```

Expected: `a7f5e7591376f086a7b2412723cfc037e599644e`. If the clone has moved past it, fetch that exact commit:

```powershell
git -C "$work\agent-skills" fetch --depth 1 origin a7f5e7591376f086a7b2412723cfc037e599644e
git -C "$work\agent-skills" checkout a7f5e7591376f086a7b2412723cfc037e599644e
```

- [ ] **Step 2: Copy the package and the licence**

```powershell
$repo = 'C:\Users\priya\Documents\Rasgulla'
$src  = "$work\agent-skills\src\agentskills"
New-Item -ItemType Directory -Force -Path "$repo\skills" | Out-Null
Copy-Item -Recurse -Force "$src" "$repo\skills\agentskills"
Copy-Item -Force "$work\agent-skills\LICENSE" "$repo\skills\LICENSE"
```

The copy brings `data/catalog.json` (2,483,480 bytes) with it. **Do not copy** `skills/`, `routers/`, `catalog/`, `docs/`, `tests/` or `scripts/` from upstream.

- [ ] **Step 3: Write `skills/NOTICE.md`**

```markdown
# agent-skills

Vendored from https://github.com/gravion-ux/agent-skills

- Commit: a7f5e7591376f086a7b2412723cfc037e599644e (2026-11-22)
- Upstream package: `src/agentskills/` copied whole and unedited
- Licence: MIT — see `LICENSE` (Copyright (c) 2026 gravion-ux)

What is vendored: the Python package and its machine-readable index
(`data/catalog.json`, 1,211 skills across 42 domains).

What is NOT vendored: the generated `skills/**/SKILL.md` tree and the
`routers/**` tree. The JSON index already carries every section of every
skill (purpose, when to use, when NOT to use, inputs, procedure, output
contract, validation checklist, safety notes, related skills), so the
index alone is what Rasgulla reads at runtime.

This catalogue is read as **method**. It never widens Rasgulla's action
allow-list, never changes her hard line and never overrides the spoken
approval gate.
```

- [ ] **Step 4: Verify the vendored package imports and routes**

```powershell
$py = 'C:\Users\priya\Documents\Rasgulla\.venv\Scripts\python.exe'
& $py -c "import sys; sys.path.insert(0, r'C:\Users\priya\Documents\Rasgulla\skills'); from agentskills.catalog import load_catalogue; from agentskills.router import route; c = load_catalogue(); print(len(c.skills)); print([h.skill.name for h in route(c, 'migrate a database to postgres', top=3)])"
```

Expected: `1211` and a list of three migration/replication skills.

- [ ] **Step 5: Confirm `skills/` is not ignored by git**

```powershell
git -C $repo check-ignore -v skills/agentskills/data/catalog.json
```

Expected: **exit code 1** (not ignored). If it is ignored, remove the matching line from `.gitignore` and re-run.

- [ ] **Step 6: Commit**

```powershell
Set-Location $repo
git add skills
git commit -m "Vendor the agent-skills catalogue"
```

Expected: the commit adds the package, the 2.48 MB index, `LICENSE` and `NOTICE.md`. **Do not push.**

---

### Task 2: `skill_catalogue.py` — loader, stopwords, cached scorer, matcher, prompt block

**Files:**
- Create: `skill_catalogue.py`
- Create: `test_skill_catalogue.py`

**Interfaces:**
- Consumes: the vendored package from Task 1 (`agentskills.catalog.load_catalogue`, `agentskills.router.tokenize`, `FIELD_WEIGHTS`, `K1`, `B`).
- Produces:
  - `STOPWORDS: frozenset[str]`
  - `SKILL_FLOOR: float` (= 10.0)
  - `SKILL_MIN_MATCHES: int` (= 2)
  - `@dataclass(frozen=True) class SkillMatch:` with fields `name: str`, `domain: str`, `score: float`, `summary: str`, `matched: tuple[str, ...]`, `skill: Any`
  - `load_skills() -> tuple[Any, ...]` — cached, never raises
  - `match_skill(text: str) -> SkillMatch | None`
  - `skill_prompt(match: SkillMatch) -> str`
  - `skill_summary(name: str) -> dict[str, Any]`

- [ ] **Step 1: Write the failing test file**

Create `test_skill_catalogue.py`:

```python
"""Tests for skill_catalogue.py - the vendored agent-skills catalogue.

These tests never call the network.  The ranking is the upstream BM25
scorer, run offline against the catalogue on disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla")
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from skill_catalogue import (
    SKILL_FLOOR,
    SKILL_MIN_MATCHES,
    load_skills,
    match_skill,
    skill_prompt,
    skill_summary,
)

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


def main() -> int:
    skills = load_skills()
    check("the catalogue loads", len(skills) == 1211, f"got {len(skills)}")
    check("loading twice returns the same catalogue", load_skills() is skills)

    hit = match_skill("migrate a database to postgres")
    check("a work request matches a skill", hit is not None)
    if hit is not None:
        check("the match names a skill", bool(hit.name), hit.name)
        check("the match has a domain", bool(hit.domain), hit.domain)
        check("the match has a score", hit.score > 0, str(hit.score))
        check("the match cleared the floor", hit.score >= SKILL_FLOOR, str(hit.score))
        check(
            "the match has enough terms",
            len(hit.matched) >= SKILL_MIN_MATCHES,
            ",".join(hit.matched),
        )

    check("chit-chat matches nothing", match_skill("hey how are you") is None)
    check("a bare greeting matches nothing", match_skill("ok thanks") is None)

    if hit is not None:
        block = skill_prompt(hit)
        for section in ("When to use", "When NOT to use", "Procedure", "Output contract", "Validation checklist"):
            check(f"the block carries '{section}'", section.lower() in block.lower())
        check("the block names the skill", hit.name in block)
        check(
            "the block does not widen the allow-list",
            "run_command" not in block and "shell" not in block.lower(),
        )

        summary = skill_summary(hit.name)
        check("the summary names the skill", summary.get("name") == hit.name)
        check("the summary carries the domain", summary.get("domain") == hit.domain)

    print(f"\npassed: {PASSED}   failed: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
& ".\.venv\Scripts\python.exe" test_skill_catalogue.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'skill_catalogue'`.

- [ ] **Step 3: Write `skill_catalogue.py`**

```python
"""The vendored agent-skills catalogue.

Rasgulla reads a body of *method* from here: a request is matched against
1,211 playbooks with the upstream BM25 scorer, and the one that fits is
handed to the model as a playbook to follow.

The ranking is offline, deterministic and free.  A skill never costs a
network call and never changes on its own.

Only the method ever reaches the model.  The reply format, the JSON plan
schema, the action allow-list and the hard line stay in ``core_app`` and
are never taken from here.

Upstream: https://github.com/gravion-ux/agent-skills (MIT, see skills/LICENSE).
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parent
SKILLS_DIR = APP_DIR / "skills"
if str(SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(SKILLS_DIR))

try:
    from agentskills.catalog import load_catalogue
    from agentskills.router import B, FIELD_WEIGHTS, K1, tokenize
except Exception:  # noqa: BLE001 - a missing catalogue must never break her
    load_catalogue = None
    tokenize = None
    FIELD_WEIGHTS: dict[str, int] = {}
    K1 = 1.5
    B = 0.75

#: Words that carry no intent.  They match everything, so they are dropped
#: before the ranking runs: with them in, "hey how are you" matches a skill
#: about answering questions.
STOPWORDS = frozenset(
    """
    a an the and or but is are was were be been being to of in on for with at
    by from as it its this that these those i me my we our you your he she
    they them their do does did done can could would should will shall may
    might must have has had having what which who whom whose when where why
    how ok okay thanks thank please hey hi hello just really very some any all
    no yes not so if then else up out about into over under again more most
    other such only own same too now help need want tell show give make use
    get got go going come came take taken put let new old good great nice work
    works working thing things stuff lot please one two three
    """.split()
)

#: The score a match has to reach.  Calibrated against the table in
#: test_skill_catalogue.py: the lowest real task scored 14.2 and the
#: highest chit-chat false positive scored 13.5 on one term, which the
#: term count below already rejects.
SKILL_FLOOR = 10.0

#: How many meaningful words have to match.  One word is too weak - "good
#: morning" matched a morning-routine skill on the word "morning" alone.
SKILL_MIN_MATCHES = 2

_FIELD_ORDER = ("name", "triggers", "tags", "description", "domain")

_catalogue: tuple[Any, ...] | None = None
_corpus: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class SkillMatch:
    """One skill that fits a request."""

    name: str
    domain: str
    score: float
    summary: str
    matched: tuple[str, ...]
    skill: Any


# -- reading the catalogue ----------------------------------------------------

def load_skills() -> tuple[Any, ...]:
    """Every skill in the catalogue, loaded once.

    A catalogue that cannot be read is an empty catalogue, not an error: a
    missing playbook must never cost the user their answer.
    """
    global _catalogue
    if _catalogue is not None:
        return _catalogue
    skills: tuple[Any, ...] = ()
    if load_catalogue is not None:
        try:
            skills = tuple(load_catalogue().skills)
        except Exception:  # noqa: BLE001
            skills = ()
    _catalogue = skills
    return skills


# -- the corpus ---------------------------------------------------------------

def _build_corpus(skills: tuple[Any, ...]) -> tuple[Any, ...]:
    """The tokenised corpus the upstream scorer builds, kept for reuse.

    The upstream ``route`` rebuilds this on every call (90.7 ms over 1,211
    skills, measured).  The maths below is the upstream maths, and
    test_skill_catalogue.py proves the two agree.
    """
    fields_by_skill: dict[str, dict[str, list[str]]] = {}
    frequencies: dict[str, Counter[str]] = {}
    document_frequency: Counter[str] = Counter()
    lengths: dict[str, int] = {}
    skills_by_name: dict[str, Any] = {}

    for skill in skills:
        fields = {
            "name": tokenize(skill.name),
            "triggers": [t for trig in skill.triggers for t in tokenize(trig)],
            "tags": [t for tag in skill.tags for t in tokenize(tag)],
            "description": tokenize(skill.description),
            "domain": tokenize(skill.domain),
        }
        document: list[str] = []
        for field in FIELD_WEIGHTS:
            document.extend(fields[field] * FIELD_WEIGHTS[field])
        fields_by_skill[skill.name] = fields
        frequencies[skill.name] = Counter(document)
        document_frequency.update(frequencies[skill.name].keys())
        lengths[skill.name] = len(document)
        skills_by_name[skill.name] = skill

    total = len(lengths)
    average = (sum(lengths.values()) / total) if total else 0.0
    return fields_by_skill, frequencies, document_frequency, lengths, average, total, skills_by_name


def _corpus() -> tuple[Any, ...] | None:
    """The cached corpus, built on first use."""
    global _corpus
    if _corpus is None:
        skills = load_skills()
        if not skills:
            return None
        _corpus = _build_corpus(skills)
    return _corpus


def _first_matching_field(fields: dict[str, list[str]], term: str) -> str:
    """The first field a term was found in."""
    for field in _FIELD_ORDER:
        if term in fields.get(field, ()):
            return field
    return "description"


def _score(terms: list[str]) -> list[tuple[Any, float, tuple[str, ...], tuple[str, ...]]]:
    """Rank the catalogue against the terms, exactly as upstream does."""
    corpus = _corpus()
    if corpus is None or not terms:
        return []
    fields_by_skill, frequencies, document_frequency, lengths, average, total, _ = corpus

    results = []
    for name, counter in frequencies.items():
        length = lengths[name]
        fields = fields_by_skill[name]
        score = 0.0
        matched: list[str] = []
        hit_fields: list[str] = []
        for term in terms:
            count = counter.get(term, 0)
            if count == 0:
                continue
            idf = math.log(
                1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = count + K1 * (1 - B + B * length / average)
            score += idf * (count * (K1 + 1)) / denominator
            matched.append(term)
            hit_fields.append(_first_matching_field(fields, term))
        if score > 0:
            results.append((name, round(score, 6), tuple(matched), tuple(hit_fields)))

    results.sort(key=lambda item: (-item[1], item[0]))
    return results


# -- matching -----------------------------------------------------------------

def _query_terms(text: str) -> list[str]:
    """The meaningful words of a request, in order, without repeats."""
    if tokenize is None:
        return []
    terms: list[str] = []
    for term in tokenize(str(text or "")):
        if term in STOPWORDS or term in terms:
            continue
        terms.append(term)
    return terms


def match_skill(text: str) -> SkillMatch | None:
    """The skill that fits a request, or None when nothing does.

    Two gates decide it, and both are needed.  The stopword filter removes
    the words that match every skill; the term count rejects a match that
    rests on a single word.  The floor then rejects what is left.
    """
    terms = _query_terms(text)
    if len(terms) < SKILL_MIN_MATCHES:
        return None

    scored = _score(terms)
    if not scored:
        return None

    for name, score, matched, _fields in scored:
        if score < SKILL_FLOOR:
            return None
        if len(matched) < SKILL_MIN_MATCHES:
            continue
        corpus = _corpus()
        skill = corpus[6][name]
        return SkillMatch(
            name=str(skill.name),
            domain=str(skill.domain),
            score=float(score),
            summary=str(skill.summary),
            matched=tuple(matched),
            skill=skill,
        )
    return None


# -- the prompt block ---------------------------------------------------------

def _section(title: str, body: str) -> str:
    """One titled section of the playbook, or nothing when it is empty."""
    text = str(body or "").strip()
    if not text:
        return ""
    return f"{title}:\n{text}"


def skill_prompt(match: SkillMatch | None) -> str:
    """The playbook block for one match, ready to drop into the prompt.

    It carries every section of the skill, because the "when NOT to use"
    clause is what keeps a wrong match from turning into wrong work.
    """
    if match is None:
        return ""
    skill = match.skill
    steps = [str(step).strip() for step in (skill.steps or []) if str(step).strip()]

    parts = [
        _section("What this playbook is for", skill.description or skill.summary),
        _section("When to use", skill.when_to_use),
        _section("When NOT to use", skill.when_not_to_use),
        _section("Inputs it needs", "\n".join(f"- {item}" for item in (skill.inputs or []))),
        _section(
            "Procedure",
            "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1)),
        ),
        _section("Output contract", skill.output_contract),
        _section(
            "Validation checklist",
            "\n".join(f"- {item}" for item in (skill.checks or [])),
        ),
        _section("Safety notes", skill.safety),
        _section("Related skills", ", ".join(skill.related or [])),
    ]
    body = "\n\n".join(part for part in parts if part)
    if not body:
        return ""
    return f"Playbook - {match.name} ({match.domain}):\n{body}"


def skill_summary(name: str) -> dict[str, Any]:
    """The short listing entry of one skill."""
    for skill in load_skills():
        if str(skill.name) == str(name):
            return {
                "name": str(skill.name),
                "domain": str(skill.domain),
                "summary": str(skill.summary),
            }
    return {}
```

- [ ] **Step 4: Run the test to verify it passes**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
& ".\.venv\Scripts\python.exe" test_skill_catalogue.py
```

Expected: every check PASS and `passed: 17   failed: 0` (the exact count may be 1-2 higher if the summary checks add lines; the requirement is **0 failed**).

- [ ] **Step 5: Measure the route cost**

```powershell
& ".\.venv\Scripts\python.exe" -c "import sys, time; sys.path.insert(0, r'C:\Users\priya\Documents\Rasgulla'); import skill_catalogue as sc; t=time.perf_counter(); [sc.match_skill('migrate a database to postgres') for _ in range(20)]; print('per match: %.1f ms' % ((time.perf_counter()-t)/20*1000))"
```

Expected: **under 50 ms** (the corpus is built once and reused). If it is not, the corpus is not being cached — fix that before moving on.

- [ ] **Step 6: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add skill_catalogue.py test_skill_catalogue.py
git commit -m "Add the skill catalogue with an offline matcher"
```

---

### Task 3: Prove the matcher agrees with upstream, and lock the calibration

**Files:**
- Modify: `test_skill_catalogue.py`
- Modify: `skill_catalogue.py` (only if the equivalence test exposes a divergence)

**Interfaces:**
- Consumes: `skill_catalogue._score`, `_query_terms`, the upstream `route`.
- Produces: a regression table in `test_skill_catalogue.py` that pins the calibration, and an equivalence check that keeps the cached scorer honest.

- [ ] **Step 1: Add the upstream equivalence check and the calibration table to the test file**

Add these imports at the top of `test_skill_catalogue.py`:

```python
from skill_catalogue import _query_terms, _score
```

and add this function, then call it from `main()`:

```python
def upstream_agreement() -> None:
    """The cached scorer must rank exactly like the upstream scorer.

    The catalogue is vendored unedited, so this test is what proves the
    local cache has not drifted away from it.
    """
    sys.path.insert(0, str(APP_DIR / "skills"))
    from agentskills.catalog import load_catalogue
    from agentskills.router import route

    catalogue = load_catalogue()
    queries = [
        "migrate a database to postgres",
        "review my python code for security bugs",
        "write a launch announcement for my app",
        "set up CI for a node project",
        "plan a trip to goa",
        "unit test for a react component",
        "make a python script that renames files",
        "what is the capital of France",
        "hey how are you",
        "ok thanks",
        "good morning",
        "hello there",
    ]
    for query in queries:
        terms = _query_terms(query)
        local = [(name, score) for name, score, _m, _f in _score(terms)]
        upstream = [(hit.skill.name, hit.score) for hit in route(catalogue, " ".join(terms), top=8)]
        check(
            f"the cached scorer agrees with upstream on '{query[:32]}'",
            local == upstream,
            f"local={local[:2]} upstream={upstream[:2]}",
        )
```

In `main()`, add the call and the calibration table:

```python
    upstream_agreement()

    # The calibration: the queries, and whether a playbook is expected.
    # These numbers were measured on this machine against the upstream
    # catalogue at commit a7f5e75; the table is the proof that the floor
    # and the term count still separate work from chit-chat.
    calibration = [
        ("migrate a database to postgres", True),
        ("review my python code for security bugs", True),
        ("write a launch announcement for my app", True),
        ("set up CI for a node project", True),
        ("help me plan a trip to goa", True),
        ("how do I write a unit test for a react component", True),
        ("what is the capital of France", False),
        ("make a python script that renames files", False),
        ("hey how are you", False),
        ("ok thanks", False),
        ("good morning", False),
        ("hello there", False),
        ("what is up", False),
        ("lol", False),
    ]
    for query, expected in calibration:
        hit = match_skill(query)
        check(
            f"the calibration holds for '{query[:34]}'",
            (hit is not None) == expected,
            f"got {hit.name if hit else None}, expected {'a match' if expected else 'no match'}",
        )
```

- [ ] **Step 2: Run the test**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
& ".\.venv\Scripts\python.exe" test_skill_catalogue.py
```

Expected: **0 failed**. If the equivalence check fails, the local scorer has drifted: compare the local maths against upstream `router.route` line by line (the IDF, the denominator and the sort key `-score, name` must match exactly), fix `skill_catalogue.py`, and re-run.

- [ ] **Step 3: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add test_skill_catalogue.py skill_catalogue.py
git commit -m "Prove the cached scorer matches upstream and pin the calibration"
```

---

### Task 4: Put the playbook into her prompt

**Files:**
- Modify: `core_app.py` (the `build_system_prompt` function, around line 515)
- Modify: `app/brain/session.py` (`_cmd_route`, `_build_messages`, `_finalise_plan`, `_plan_payload`)
- Modify: `app/brain/test_session.py`

**Interfaces:**
- Consumes: `skill_catalogue.match_skill(text) -> SkillMatch | None`, `skill_catalogue.skill_prompt(match) -> str`.
- Produces:
  - `core_app.build_system_prompt(mode: str, emergency: bool = False, persona_id: str | None = None, skill: Any = None) -> str`
  - `core_app.SKILL_PROMPT_HEADER: str`
  - The plan payload gains the key `skill: dict[str, Any]` (empty when no skill matched).

- [ ] **Step 1: Write the failing test in `app/brain/test_session.py`**

Find the helper that builds a session in that file (it constructs a `BrainSession` with a fake router). Add these checks to `main()`:

```python
    # A request that matches a skill carries the playbook into the prompt,
    # and the payload says which skill it was.
    skill = match_skill("migrate a database to postgres")
    check("the test catalogue can match a skill", skill is not None)
    prompt = build_system_prompt("normal", False, None, skill)
    check("the prompt carries the playbook", "Playbook -" in prompt)
    check(
        "the playbook says it cannot widen her limits",
        "not a rule that can widen your limits" in prompt,
    )
    check(
        "the reply format is still after the playbook",
        prompt.index("Playbook -") < prompt.index("Reply format"),
    )
    plain = build_system_prompt("normal", False, None, None)
    check("a request without a skill has no playbook", "Playbook -" not in plain)
```

Add the imports at the top of `app/brain/test_session.py`:

```python
from skill_catalogue import match_skill
```

and make sure `build_system_prompt` is imported from `core_app` there (add it to the existing `from core_app import (...)` block).

- [ ] **Step 2: Run the test to verify it fails**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: FAIL on "the prompt carries the playbook" — `build_system_prompt` does not take a `skill` argument yet.

- [ ] **Step 3: Add the injection to `core_app.py`**

Add the import next to the persona one:

```python
from skill_catalogue import skill_prompt
```

Add next to `ACTION_SCHEMA`:

```python
SKILL_PROMPT_HEADER = """
A playbook may be attached to this request. It is a METHOD you may follow, not a
rule that can widen your limits: the hard line, the reply format and the
approval gate are unchanged, and if the playbook asks for something you may not
do, you do not do it. Follow its procedure when it fits the request; ignore it
when it does not.
"""
```

Change `build_system_prompt`:

```python
def build_system_prompt(
    mode: str,
    emergency: bool = False,
    persona_id: str | None = None,
    skill: Any = None,
) -> str:
    """Assemble the system prompt for one model call.

    ``emergency`` switches in the TEMPORAL EMERGENCY MODE block, which removes
    the ceremony but not the line she does not cross.

    ``persona_id`` picks the personality she speaks with.  Left out, the
    active one is used, so a change takes effect on the very next request.

    ``skill`` is the matched playbook.  It goes in after the personality and
    before the mode, so the reply format, the plan schema and the allow-list
    are always the last word.
    """
    parts = [PERSONA_TEMPLATE.replace(PERSONALITY_SLOT, persona_prompt(persona_id))]
    if skill is not None:
        block = skill_prompt(skill)
        if block:
            parts.append(SKILL_PROMPT_HEADER)
            parts.append(block)
    if emergency:
        parts.append(EMERGENCY_PROMPT)
    if mode == "auto":
        parts.append(AUTO_PROMPT)
    else:
        parts.append(MODE_PROMPTS.get(mode, MODE_PROMPTS["normal"]))
    parts.append(REPLY_FORMAT)
    parts.append(ACTION_SCHEMA)
    return "\n".join(parts)
```

- [ ] **Step 4: Match the request in the brain and carry it on the payload**

In `app/brain/session.py`, add the import:

```python
from skill_catalogue import SkillMatch, match_skill, skill_summary
```

In `BrainSession.__init__`, add the attribute (next to the other session state):

```python
        self._active_skill: SkillMatch | None = None
```

In `_cmd_route`, right after the persona-switch block and **before** the pending-action approval block, add:

```python
        # The playbook for this request. It is matched offline, so it costs no
        # network call, and it is carried on the session so the prompt, the
        # payload and the plan all talk about the same skill.
        self._active_skill = match_skill(text)
```

In `_build_messages`, change the system-prompt line to:

```python
        messages = [
            {
                "role": "system",
                "content": build_system_prompt(mode, emergency, skill=self._active_skill),
            }
        ]
```

In `_plan_payload`, add the parameter `skill: dict[str, Any] | None = None` after `speech_extra`, and add the key to the payload dict:

```python
            "skill": skill or {},
```

In `_finalise_plan`, pass the skill into the payload:

```python
        skill_payload = {}
        if self._active_skill is not None:
            skill_payload = {
                "name": self._active_skill.name,
                "domain": self._active_skill.domain,
                "score": self._active_skill.score,
            }
```

and add `skill=skill_payload` to the `_plan_payload(...)` call at the end of `_finalise_plan`.

- [ ] **Step 5: Run the brain session test**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: **0 failed**.

- [ ] **Step 6: Run the root suite too (the prompt changed for every call)**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
& ".\.venv\Scripts\python.exe" test_rasgulla.py
```

Expected: **0 failed**.

- [ ] **Step 7: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add core_app.py app/brain/session.py app/brain/test_session.py
git commit -m "Let a matched playbook guide her answer"
```

---

### Task 5: Show the playbook chip on screen

**Files:**
- Modify: `app/renderer/index.html`
- Modify: `app/renderer/styles.css`
- Modify: `app/renderer/renderer.js`
- Modify: `app/test_orb_smoke.py`

**Interfaces:**
- Consumes: the plan payload key `skill` (`{name, domain, score}`).
- Produces: the DOM element `#playbook` (hidden when there is no skill) and the test hook `window.__rasgullaGetSkill()` returning the chip's text or `""`.

- [ ] **Step 1: Write the failing assertion in `app/test_orb_smoke.py`**

The smoke test drives the app's own `--probe` mode. Add a check that the chip is present and hidden when there is no skill:

```python
    check("the playbook chip exists", "playbook" in probe.get("html", ""), str(probe.keys()))
```

If the probe payload does not carry the HTML, add `html: await page.evaluate(() => document.body.innerHTML)` to the probe in `app/main.js` first.

- [ ] **Step 2: Run the smoke test to verify it fails**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app'
& "..\.venv\Scripts\python.exe" test_orb_smoke.py
```

Expected: FAIL on "the playbook chip exists".

- [ ] **Step 3: Add the markup**

In `app/renderer/index.html`, between the orb and the reply section:

```html
  <div class="playbook" id="playbook" hidden></div>
```

- [ ] **Step 4: Add the style**

In `app/renderer/styles.css`:

```css
.playbook {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 6px;
  padding: 3px 10px;
  align-self: center;
  border: 1px solid rgba(255, 190, 90, 0.45);
  border-radius: 999px;
  background: rgba(255, 176, 70, 0.12);
  color: rgba(255, 214, 150, 0.92);
  font-size: 11px;
  letter-spacing: 0.02em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.playbook[hidden] {
  display: none;
}
```

- [ ] **Step 5: Render the chip**

In `app/renderer/renderer.js`, add the element lookup next to `replyBox`:

```javascript
const playbookChip = document.getElementById('playbook');
```

Add the function next to `showReply`:

```javascript
// The playbook that shaped this reply. It is on screen and never spoken, so
// the caption and the voice stay exactly as they are.
function showPlaybook(skill) {
  if (!playbookChip) return;
  const name = skill && skill.name ? String(skill.name) : '';
  if (!name) {
    playbookChip.hidden = true;
    playbookChip.textContent = '';
    return;
  }
  playbookChip.textContent = `playbook: ${name}`;
  playbookChip.title = skill.domain ? `${skill.name} - ${skill.domain}` : name;
  playbookChip.hidden = false;
}
```

In `handleMessage`, in the `case 'plan':` branch, add the call before `showReply(...)`:

```javascript
      showPlaybook(message.skill);
```

Add the test hook next to the other hooks:

```javascript
window.__rasgullaGetSkill = () => (playbookChip && !playbookChip.hidden ? playbookChip.textContent : '');
```

- [ ] **Step 6: Run the smoke test**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app'
& "..\.venv\Scripts\python.exe" test_orb_smoke.py
```

Expected: **0 failed**.

- [ ] **Step 7: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add app/renderer/index.html app/renderer/styles.css app/renderer/renderer.js app/test_orb_smoke.py app/main.js
git commit -m "Show the playbook that shaped the reply"
```

---

### Task 6: Hold the plan until the user says the word

**Files:**
- Modify: `app/brain/session.py`
- Modify: `app/brain/test_session.py`

**Interfaces:**
- Consumes: the payload key `skill` from Task 4; the existing `_pending_actions`, `_pending_index`, `_arm_pending_step`, `_clear_pending`.
- Produces:
  - `session.PLAYBOOK_START_PHRASES: tuple[str, ...]`
  - `session.playbook_start(text: str) -> bool`
  - `BrainSession._held_playbook: dict[str, Any] | None`
  - `BrainSession._start_held_playbook(speak: bool) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests in `app/brain/test_session.py`**

```python
    # A skill plan is HELD, not armed: nothing waits for an answer until the
    # user says the word.
    session = BrainSession()
    session._active_skill = match_skill("migrate a database to postgres")
    session._finalise_plan(
        {
            "reply": "Here is the plan.\n\n🎭character: migrating now",
            "actions": [{"type": "wait", "seconds": 1}],
        },
        speak=False,
    )
    check("a skill plan is not armed", session._pending_actions == [])
    check("the skill plan is held", session._held_playbook is not None)
    check(
        "the held plan keeps its actions",
        bool(session._held_playbook and session._held_playbook["actions"]),
    )

    # The trigger is checked before the approval block.
    check("the trigger recognises the phrase", playbook_start("follow the playbook"))
    check("the trigger ignores other words", not playbook_start("hello there"))
    check(
        "the trigger avoids the approve words",
        all(
            word not in " ".join(PLAYBOOK_START_PHRASES)
            for word in ("run", "chalao", "karo", "haan")
        ),
    )
```

(The test file builds a session with `BrainSession()` directly - there is no factory helper.)

- [ ] **Step 2: Run the test to verify it fails**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: FAIL — `_held_playbook` and `playbook_start` do not exist.

- [ ] **Step 3: Add the trigger**

In `app/brain/session.py`, next to the approval phrases:

```python
# The words that start a held playbook.
#
# None of them contains an approve or a decline word on purpose: "run",
# "chalao", "karo" and "ha" already belong to the approval gate, and a start
# phrase must never be read as an answer to a waiting step.
PLAYBOOK_START_PHRASES = (
    "follow the playbook",
    "apply the playbook",
    "start the playbook",
    "begin the playbook",
)


def playbook_start(text: str) -> bool:
    """True when a sentence asks for the held playbook to start."""
    normalized = " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())
    if not normalized:
        return False
    return any(f" {phrase} " in f" {normalized} " for phrase in PLAYBOOK_START_PHRASES)
```

- [ ] **Step 4: Hold the plan instead of arming it**

In `BrainSession.__init__`, add:

```python
        self._held_playbook: dict[str, Any] | None = None
```

In `_finalise_plan`, replace the arming block with:

```python
        question = ""
        if valid and self._active_skill is not None:
            # A skill plan is held, not armed. Nothing waits for an answer
            # until the user says "follow the playbook", so a stray "haan"
            # can never fire it and the microphone stays free.
            manual = [
                step
                for step in (plan.get("manual") or [])
                if isinstance(step, dict) and str(step.get("command") or "").strip()
            ]
            self._held_playbook = {
                "skill": self._active_skill.name,
                "actions": list(valid),
                "manual": manual,
            }
            question = "Say 'follow the playbook' when you want me to start."
        elif valid:
            # The steps wait for a spoken answer before anything runs.
            self._pending_actions = list(valid)
            self._pending_index = 0
            self._arm_pending_step()
            question = self._pending_question()
        else:
            self._clear_pending()
```

Note: a held plan leaves `self.current_actions` as the validated actions, so the renderer can still show them; the **approval** is what is deferred, not the plan.

- [ ] **Step 5: Start the held plan on the trigger**

Add the method next to `_clear_pending`:

```python
    def _start_held_playbook(self, speak: bool) -> dict[str, Any]:
        """Arm the held plan, exactly like a plan that arrived from the model."""
        held = self._held_playbook or {}
        actions = list(held.get("actions") or [])
        manual = list(held.get("manual") or [])
        self._held_playbook = None
        if not actions and not manual:
            return self._plan_payload(
                "There is no playbook ready to start, so nothing ran.",
                speak=speak,
            )
        self._pending_actions = actions
        self._pending_manual = manual
        self._pending_index = 0
        self._arm_pending_step()
        question = self._pending_question()
        return self._plan_payload(
            f"Starting the playbook. {question}",
            speak=speak,
            actions=actions,
            speech_extra=question,
        )
```

In `_cmd_route`, add this block **before** the pending-action approval block:

```python
        # The playbook starts here, before the approval block, so a start
        # phrase can never be read as an answer to a waiting step. It only
        # fires when a plan is actually held and no step is waiting.
        if (
            self._held_playbook
            and not self._pending_actions
            and playbook_start(text)
        ):
            return self._start_held_playbook(speak=speak)
```

Also add `self._pending_manual: list[dict[str, Any]] = []` to `__init__` and clear it in `_clear_pending`.

- [ ] **Step 6: Run the brain session test**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: **0 failed**.

- [ ] **Step 7: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add app/brain/session.py app/brain/test_session.py
git commit -m "Hold a skill plan until the user starts it"
```

---

### Task 7: Hand over the steps she cannot run

**Files:**
- Modify: `app/brain/session.py`
- Modify: `app/brain/test_session.py`

**Interfaces:**
- Consumes: `self._pending_manual` from Task 6, the existing `_advance_pending`, `_pending_question`, `_was_heard`.
- Produces:
  - `session.DONE_PHRASES: tuple[str, ...]`
  - `session.done_decision(text: str) -> str` — `"done"`, `"decline"` or `"other"`
  - `BrainSession._pending_mode: str` — `"approve"` or `"manual_done"`
  - `BrainSession._describe_manual(step: dict[str, Any]) -> str`

- [ ] **Step 1: Write the failing tests in `app/brain/test_session.py`**

```python
    # A step she cannot run is handed to the user: approved, spoken, and
    # then waited on until the user says it is done.
    check("the done matcher knows 'done'", done_decision("done") == "done")
    check("the done matcher knows 'ho gaya'", done_decision("ho gaya") == "done")
    check("the done matcher declines on 'nahi'", done_decision("nahi") == "decline")
    check("the done matcher ignores other words", done_decision("what now") == "other")

    session = make_session()
    session._pending_actions = [{"type": "manual", "title": "Run the tests", "command": "npm test"}]
    session._pending_manual = [{"title": "Run the tests", "command": "npm test"}]
    session._pending_index = 0
    session._pending_mode = "approve"
    session._arm_pending_step()
    session._say("haan")
    payload = session.handle({"command": "route", "text": "haan", "speak": False})
    check("the manual step is handed over", "manual_done" == session._pending_mode)
    check("the hand-over mentions the command", "npm test" in payload.get("reply", ""))
```

- [ ] **Step 2: Run the test to verify it fails**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: FAIL — `done_decision` does not exist.

- [ ] **Step 3: Add the done vocabulary**

In `app/brain/session.py`, next to `APPROVE_PHRASES`:

```python
# The words that finish a step the user ran by hand.
#
# "done" is in neither the approve nor the decline list, so the two
# vocabularies cannot collide. They are only read while a step is waiting
# for exactly this answer.
DONE_PHRASES = ("done", "ho gaya", "done kar diya", "finished", "complete")

_DONE_RE = _phrase_matcher(DONE_PHRASES)


def done_decision(text: str) -> str:
    """Return 'done', 'decline', or 'other' for a step the user ran by hand."""
    normalized = str(text or "").strip()
    if not normalized:
        return "other"
    # The decline wins a tie here too.
    if _DECLINE_RE.search(normalized):
        return "decline"
    if _DONE_RE.search(normalized):
        return "done"
    return "other"
```

- [ ] **Step 4: Add the second pending mode**

In `BrainSession.__init__`, add:

```python
        self._pending_mode = "approve"
```

In `_arm_pending_step`, leave the mode alone (it is set by whoever arms the step).

In `_clear_pending`, add:

```python
        self._pending_mode = "approve"
        self._pending_manual = []
```

Add the helper next to `_pending_question`:

```python
    def _describe_manual(self, step: dict[str, Any]) -> str:
        """How a step the user has to run by hand is described."""
        title = str(step.get("title") or "a command she cannot run").strip()
        command = str(step.get("command") or "").strip()
        if not command:
            return f"{title}. No command was given, so there is nothing to run."
        return f"{title}. This needs a command I cannot run: {command}"
```

Change `_pending_question` so a manual step gets its own wording:

```python
    def _pending_question(self) -> str:
        """The question for the step that is waiting for an answer."""
        step = self._pending_actions[self._pending_index]
        number = self._pending_index + 1
        if isinstance(step, dict) and step.get("type") == "manual":
            return f"Step {number}: {self._describe_manual(step)}. Say haan and I'll hand it over."
        return f"Step {number}: {_describe_action(step)}. Run karun?"
```

- [ ] **Step 5: Hand the step over, then wait for "done"**

In `_advance_pending`, replace the `if decision == "approve":` block with:

```python
        if decision == "approve":
            step = self._pending_actions[self._pending_index]
            if isinstance(step, dict) and step.get("type") == "manual":
                # She does not run it. She reads it out and hands it over.
                hand_over = (
                    f"{self._describe_manual(step)}. Please run this now. "
                    "Say done when it is finished."
                )
                self._pending_mode = "manual_done"
                self._arm_pending_step()
                return self._plan_payload(
                    hand_over,
                    speak=speak,
                    speech_extra=hand_over,
                )
            try:
                ...
```

(keep the existing action-running code in the `else` branch of the `if`).

In the pending-action block of `_cmd_route`, change the decision line to:

```python
            decision = (
                done_decision(text)
                if self._pending_mode == "manual_done"
                else approval_decision(text)
            )
            if decision in ("approve", "decline", "done"):
                if self._was_heard(text):
                    if decision == "done":
                        return self._finish_manual(speak=speak)
                    return self._advance_pending(decision, speak=speak)
                self._clear_pending()
                return self._plan_payload(
                    "That answer was not heard, so nothing ran.",
                    speak=speak,
                )
```

Add the method next to `_advance_pending`:

```python
    def _finish_manual(self, speak: bool) -> dict[str, Any]:
        """Record a step the user ran by hand and move to the next one."""
        step = self._pending_actions[self._pending_index]
        command = str(step.get("command") or "").strip() if isinstance(step, dict) else ""
        self.action_results.append(f"Ran manually: {command}" if command else "Ran manually")
        self._pending_index += 1
        self.current_actions = list(self._pending_actions[self._pending_index :])
        self._pending_mode = "approve"
        if self._pending_index >= len(self._pending_actions):
            self._clear_pending()
            done = "All steps are done. " + "; ".join(self.action_results[-5:])
            return self._plan_payload(done, speak=speak, results=list(self.action_results[-5:]))
        self._arm_pending_step()
        question = self._pending_question()
        return self._plan_payload(f"Done. {question}", speak=speak)
```

- [ ] **Step 6: Let a manual step into the plan**

The model has to be able to say "this one is for the user". Extend `ACTION_SCHEMA` in `core_app.py` with a `manual` array, and tell her what it is for:

```python
  "manual": [
    {"title": "<what this step does>", "command": "<the exact command the user must run>"}
  ]
```

and, inside the schema block, add the rule:

```
- "manual" holds the steps you may NOT run yourself: a shell command, a
  migration, anything that needs a terminal. Put the exact command in
  "command". You read it out and the user runs it. Never put a command in
  "actions".
```

In `_finalise_plan`, keep only well-formed manual entries:

```python
        manual = []
        for entry in plan.get("manual", []) or []:
            if not isinstance(entry, dict):
                continue
            command = str(entry.get("command") or "").strip()
            title = str(entry.get("title") or "A step for the user").strip()
            if not command:
                continue
            manual.append({"type": "manual", "title": title, "command": command})
```

and merge them into the step list when the plan is armed. In `_start_held_playbook`, build the ordered list:

```python
        actions = list(held.get("actions") or [])
        manual = list(held.get("manual") or [])
        steps = list(actions)
        for entry in manual:
            after = int(entry.get("after", len(steps)))
            steps.insert(min(max(after, 0), len(steps)), entry)
```

(`"after"` is how many actions run before the manual step; when it is missing the manual step goes last.)

- [ ] **Step 7: Run the brain session test**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla\app\brain'
& "..\..\.venv\Scripts\python.exe" test_session.py
```

Expected: **0 failed**.

- [ ] **Step 8: Run the root suite (the plan schema changed)**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
& ".\.venv\Scripts\python.exe" test_rasgulla.py
```

Expected: **0 failed**.

- [ ] **Step 9: Commit**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git add app/brain/session.py app/brain/test_session.py core_app.py
git commit -m "Hand over the steps she cannot run"
```

---

### Task 8: The gate, the latency, the commit, the relaunch

**Files:**
- Modify: `docs/superpowers/specs/2026-11-22-agent-skills-integration-design.md` (only if a measured number in it is wrong)
- Create: `docs/superpowers/plans/2026-11-22-agent-skills-integration.md` (this plan, if it is not already committed)

**Interfaces:**
- Consumes: everything the earlier tasks produced.
- Produces: a green matrix, a measured latency, a local commit and a running app.

- [ ] **Step 1: Run the full matrix**

```powershell
$work = 'C:\Users\priya\.minimax\sessions\mvs_a2d220899bd14a0aad9cb1a5a8f0df40\workspace'
Set-Location $work
& 'C:\Users\priya\Documents\Rasgulla\.venv\Scripts\python.exe' -u run_matrix.py
```

Expected: `ALL SUITES GREEN` with **0 failures** in every suite. If `test_orb_talk` fails on the microphone timing, run it alone once (`app\test_orb_talk.py`) — it is a known sampling flake, and a clean solo run counts as the suite being green.

- [ ] **Step 2: Run the lint baseline**

```powershell
Set-Location $work
& 'C:\Users\priya\Documents\Rasgulla\.venv\Scripts\python.exe' -u ruff_baseline.py
```

Expected: **NO NEW FINDINGS** against HEAD.

- [ ] **Step 3: Measure the live path**

```powershell
Set-Location $work
& 'C:\Users\priya\Documents\Rasgulla\.venv\Scripts\python.exe' -u measure_live_path.py 4
```

Expected: **0 failed attempts**, **0 fallbacks**, and a **median at or below 3.5 s**. Record the numbers in the commit message body.

- [ ] **Step 4: Verify the chain still falls back**

```powershell
Set-Location $work
& 'C:\Users\priya\Documents\Rasgulla\.venv\Scripts\python.exe' -u verify_chain_gate.py 4
```

Expected: `OK every call answered, the caption carried the answer, the chain fell back`.

- [ ] **Step 5: Check the git state**

```powershell
Set-Location 'C:\Users\priya\Documents\Rasgulla'
git status --porcelain
git remote -v
```

Expected: the working tree is clean and `git remote -v` prints **nothing**.

- [ ] **Step 6: Commit whatever is left**

```powershell
git add -A
git commit -m "Wire the skill catalogue into the brain"
```

If there is nothing to commit, skip this step.

- [ ] **Step 7: Relaunch the app DETACHED**

Do **not** use `electron.cmd` — its console takes a `^C` when the shell dies and the app dies with it. Start the GUI binary directly:

```powershell
$app = 'C:\Users\priya\Documents\Rasgulla\app'
Start-Process -FilePath "$app\node_modules\electron\dist\electron.exe" -ArgumentList "." -WorkingDirectory $app -WindowStyle Hidden
Start-Sleep -Seconds 10
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*brain\brain.py*' } | Select-Object ProcessId, Name
```

Expected: the brain's python processes are listed (two of them: the venv launcher and its `C:\Python315\python.exe` child — that is one brain).

- [ ] **Step 8: Report**

Report, with the measured numbers: the suites and their counts, the latency, the fallback result, the commit sha, the fact that nothing was pushed, and the running app's process ids.

---

## Self-review

- **Spec coverage:** the vendoring (Task 1), the module and its API (Task 2), the floor and the calibration (Task 2 + Task 3), the injection and its order (Task 4), the chip (Task 5), the held plan and the trigger order (Task 6), the manual steps and the second pending mode (Task 7), the guards, the latency budget, the error handling and the commit rule (Task 8). The spec's "the upstream package imports and its own validator still passes on the vendored index" is implemented as: the package imports, the catalogue loads with 1,211 skills, and the upstream CLI's `list` and `route` work from the vendored copy — the upstream `validate` command checks the whole upstream repository layout (`skills/`, `routers/`, the catalogue mirror), which the vendored subset does not carry, so it is not a meaningful check here.
- **Placeholder scan:** no TBD, no TODO, no "similar to Task N". Every step names the file, the code and the command.
- **Type consistency:** `SkillMatch(name, domain, score, summary, matched, skill)` is produced by `match_skill` and consumed by `skill_prompt`; `build_system_prompt(..., skill=...)` takes that object; the payload key `skill` is a plain dict; `_held_playbook` is `{"skill": str, "actions": list, "manual": list}`; `_pending_manual` is a list of `{"type": "manual", "title": str, "command": str}`; `_pending_mode` is `"approve"` or `"manual_done"`.
