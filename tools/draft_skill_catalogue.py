"""The vendored agent-skills catalogue.

A verbatim copy of the module the implementation plan describes, used here to
measure the plan's calibration table against the real upstream catalogue.

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
SKILLS_DIR = APP_DIR.parent / "skills"
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
_CORPUS: tuple[Any, ...] | None = None


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
    global _CORPUS
    if _CORPUS is None:
        skills = load_skills()
        if not skills:
            return None
        _CORPUS = _build_corpus(skills)
    return _CORPUS


def _first_matching_field(fields: dict[str, list[str]], term: str) -> str:
    """The first field a term was found in."""
    for field in _FIELD_ORDER:
        if term in fields.get(field, ()):
            return field
    return "description"


def _score(terms: list[str]) -> list[tuple[Any, float, tuple[str, ...], tuple[str, ...]]]:
    """Rank the catalogue against the terms, exactly as upstream does."""
    corp = _CORPUS
    if corp is None:
        corp = _build_corpus(load_skills())
        globals().update(_CORPUS=corp)
    if corp is None or not terms:
        return []
    fields_by_skill, frequencies, document_frequency, lengths, average, total, _ = corp

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
        corp = _CORPUS
        if corp is None:
            corp = _build_corpus(load_skills())
            globals().update(_CORPUS=corp)
        skill = corp[6][name]
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
