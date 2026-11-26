"""A/B the live path with and without the injected playbook.

Same request, same router, same model: the only difference is whether the
matched playbook is in the system prompt. That is exactly the question the
design asks - does injection push the median past the 3.5 s budget?

The pairs are reported one after the other so the two sides share the same
endpoint conditions. When the free tier starts rate-limiting, both sides get
slower together, which is why the paired times matter more than the medians:
the difference between them is the injection, and everything else is the
endpoint.

Usage:  ab_skill_latency.py [rounds]
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core_router  # noqa: E402
from core_app import TIER_BRIEF, build_system_prompt  # noqa: E402
from core_router import ModelRouter, TaskClassifier, UsageTracker  # noqa: E402

import skill_catalogue as sc  # noqa: E402

REQUEST = "write a python function that reverses a string"


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3

    usage = UsageTracker(Path.home() / "rasgulla-ab-usage.json")
    router = ModelRouter(ROOT / "models.json", "", usage)
    router.key = core_router.read_api_key(router.key_env)
    classifier = TaskClassifier()

    tier = classifier.classify(REQUEST)
    match = sc.match_skill(REQUEST)
    if match is None:
        print("no skill matched this request - nothing to compare")
        return 1

    plain = build_system_prompt(tier)
    injected = build_system_prompt(tier, skill=match)
    print(f"request   : {REQUEST!r}")
    print(f"tier      : {tier}")
    print(f"skill     : {match.name} (score {match.score}, matched {','.join(match.matched)})")
    print(f"prompt    : {len(plain)} chars plain -> {len(injected)} chars with the playbook")
    print(f"             (+{len(injected) - len(plain)} chars, ~{(len(injected) - len(plain)) // 4} tokens)")
    print("-" * 72)

    def call(prompt: str) -> tuple[float, str]:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": REQUEST},
        ]
        started = time.monotonic()
        result = router.call(tier, messages, extra_instructions=TIER_BRIEF.get(tier, ""))
        return time.monotonic() - started, result.get("model", "?")

    plain_times: list[float] = []
    skill_times: list[float] = []
    for index in range(rounds):
        for label, prompt, bucket in (
            ("plain    ", plain, plain_times),
            ("playbook ", injected, skill_times),
        ):
            elapsed, model = call(prompt)
            bucket.append(elapsed)
            print(f"  [{index + 1}] {label} {elapsed:6.2f}s  {model}")

    print("-" * 72)
    print(f"  plain    median: {statistics.median(plain_times):.2f}s  "
          f"min {min(plain_times):.2f}  max {max(plain_times):.2f}")
    print(f"  playbook median: {statistics.median(skill_times):.2f}s  "
          f"min {min(skill_times):.2f}  max {max(skill_times):.2f}")
    print(f"  cost of the playbook: {statistics.median(skill_times) - statistics.median(plain_times):+.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
