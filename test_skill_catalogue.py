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
