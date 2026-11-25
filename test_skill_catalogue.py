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
    _query_terms,
    _score,
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
        # The local scorer ranks every skill that scored above zero, while
        # upstream returns only the top eight, so the two are compared on
        # the same slice.
        check(
            f"the cached scorer agrees with upstream on '{query[:32]}'",
            local[: len(upstream)] == upstream,
            f"local={local[:2]} upstream={upstream[:2]}",
        )


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

    print(f"\npassed: {PASSED}   failed: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
