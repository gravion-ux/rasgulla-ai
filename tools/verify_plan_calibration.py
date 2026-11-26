"""Measure the plan's calibration table against the real upstream catalogue.

Runs the module the implementation plan describes (draft_skill_catalogue.py,
a verbatim copy) against the vendored upstream catalogue
(a7f5e7591376f086a7b2412723cfc037e599644e, see skills/), and prints:

  * what match_skill() actually returns for every query in the plan's
    calibration table, so a wrong row is visible before Task 3 fails;
  * whether the cached scorer agrees with upstream route() - both as the
    plan writes the assertion, and with the top-8 slice it must use;
  * the cost of one match.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
# The upstream catalogue is vendored under skills/ (see skills/NOTICE.md).  Set
# AGENT_SKILLS_SRC to a separate checkout of gravion-ux/agent-skills to compare
# against a different tree instead.
UPSTREAM_SRC = Path(os.environ.get("AGENT_SKILLS_SRC", str(ROOT / "skills")))
sys.path.insert(0, str(UPSTREAM_SRC))
sys.path.insert(0, str(TOOLS))

import draft_skill_catalogue as sc  # noqa: E402

from agentskills.catalog import load_catalogue  # noqa: E402
from agentskills.router import route  # noqa: E402

CALIBRATION = [
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

EQUIVALENCE_QUERIES = [
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


def main() -> int:
    catalogue = load_catalogue()
    skills = sc.load_skills()
    print(f"catalogue skills: {len(skills)} (upstream index: {len(catalogue.skills)})")
    print()

    print("== the plan's calibration table ==")
    wrong = 0
    for query, expected in CALIBRATION:
        hit = sc.match_skill(query)
        got = hit is not None
        flag = "ok " if got == expected else "BAD"
        if got != expected:
            wrong += 1
        detail = (
            f"{hit.name}  score={hit.score}  matched={','.join(hit.matched)}"
            if hit
            else "no match"
        )
        terms = sc._query_terms(query)
        print(f"  {flag} {query!r:52} expected={expected!s:5} terms={terms} -> {detail}")
    print(f"  rows wrong: {wrong}")
    print()

    print("== upstream equivalence ==")
    for query in EQUIVALENCE_QUERIES:
        terms = sc._query_terms(query)
        local = [(name, score) for name, score, _m, _f in sc._score(terms)]
        upstream = [
            (hit.skill.name, hit.score)
            for hit in route(catalogue, " ".join(terms), top=8)
        ]
        whole = local == upstream
        sliced = local[: len(upstream)] == upstream
        print(f"  {query!r:46} local_rows={len(local):5} plan_assertion={whole!s:5} sliced={sliced}")
        if not sliced:
            print(f"      local[:8]  = {local[:8]}")
            print(f"      upstream   = {upstream}")
    print()

    print("== cost ==")
    sc.match_skill("migrate a database to postgres")
    start = time.perf_counter()
    for _ in range(20):
        sc.match_skill("migrate a database to postgres")
    print(f"  per match: {(time.perf_counter() - start) / 20 * 1000:.1f} ms")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
