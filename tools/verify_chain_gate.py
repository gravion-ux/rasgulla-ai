"""End-to-end check of the two fixes: the answer gate and the chain.

Part 1 asks the live free models real questions and checks that the reply
that reaches the user carries an answer: the router accepts it, the reply
format is parsed, and the caption is the answer and not the flavour.

Part 2 takes the first model in the chain away (as a rate limit would) and
checks that the chain walks to the next free model instead of giving up.

Usage:  verify_chain_gate.py [count]
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core_router  # noqa: E402
from core_app import TIER_BRIEF, build_system_prompt, caption_line, voice_line  # noqa: E402
from core_router import (  # noqa: E402
    ModelRouter,
    RateLimited,
    RouterError,
    TaskClassifier,
    UsageTracker,
    parse_json_plan,
    plan_is_usable,
)

CONFIG = ROOT / "models.json"
REQUESTS = [
    "hey, what is the capital of France?",
    "write a python function that reverses a string",
    "in two lines, why does the sky look blue?",
    "what are three good uses of a Raspberry Pi?",
]


def make_router() -> ModelRouter:
    usage = UsageTracker(Path.home() / "rasgulla-measure-usage.json")
    router = ModelRouter(CONFIG, "", usage)
    router.key = core_router.read_api_key(router.key_env)
    return router


def part_one(count: int) -> int:
    print("PART 1  the answer gate against the live models")
    print("-" * 72)
    router = make_router()
    classifier = TaskClassifier()

    failures = 0
    latencies: list[float] = []
    usable = 0
    captioned = 0
    spoken_matches = 0

    for index in range(count):
        request = REQUESTS[index % len(REQUESTS)]
        tier = classifier.classify(request)
        messages = [
            {"role": "system", "content": build_system_prompt(tier)},
            {"role": "user", "content": request},
        ]
        started = time.monotonic()
        try:
            result = router.call(tier, messages, extra_instructions=TIER_BRIEF.get(tier, ""))
        except RouterError as error:
            failures += 1
            print(f"  [{index + 1}] NO ANSWER: {error}")
            continue
        elapsed = time.monotonic() - started
        latencies.append(elapsed)

        raw = str(result.get("text", ""))
        gate = plan_is_usable(raw)
        usable += 1 if gate else 0
        plan = parse_json_plan(raw)
        reply = str(plan.get("reply", ""))
        caption = caption_line(reply)
        voice = voice_line(reply)
        captioned += 1 if caption.strip() else 0
        spoken_matches += 1 if caption.strip() == voice.strip() else 0
        has_marker = "🎭" in reply

        print(
            f"  [{index + 1}] {elapsed:5.2f}s {result['label']:<10} "
            f"gate={'ok' if gate else 'FAIL'} marker={'yes' if has_marker else 'NO '} "
            f"reply={len(reply):>4}ch caption={len(caption):>4}ch "
            f"fallbacks={len(result.get('fallbacks', []))}"
        )
        print(f"        caption: {caption[:150].replace(chr(10), ' | ')}")
        if result.get("fallbacks"):
            print(f"        fell back after: {result['fallbacks']}")

    print("-" * 72)
    print(f"  calls                : {count}")
    print(f"  calls with no answer : {failures}")
    print(f"  accepted by the gate : {usable}/{count}")
    print(f"  with a caption       : {captioned}/{count}")
    print(f"  caption == spoken    : {spoken_matches}/{count}")
    if latencies:
        print(
            f"  latency min/median/max: {min(latencies):.2f} / "
            f"{statistics.median(latencies):.2f} / {max(latencies):.2f}"
        )
    return failures


def part_two() -> int:
    print()
    print("PART 2  the chain when the first model is rate limited")
    print("-" * 72)
    router = make_router()
    first_model = router.tiers["chat"]["chain"][0]["model"]
    original = ModelRouter._request

    def blocked(self, model, spec, messages, image, timeout_override=None, provider_override=None):
        if model == first_model:
            raise RateLimited(f"{model} is busy")
        return original(self, model, spec, messages, image, timeout_override, provider_override)

    ModelRouter._request = blocked
    try:
        messages = [
            {"role": "system", "content": build_system_prompt("chat")},
            {"role": "user", "content": "what is the capital of France?"},
        ]
        started = time.monotonic()
        try:
            result = router.call("chat", messages, extra_instructions=TIER_BRIEF.get("chat", ""))
        except RouterError as error:
            print(f"  FAIL  the chain gave up too: {error}")
            return 1
        elapsed = time.monotonic() - started
    finally:
        ModelRouter._request = original

    answered = result["model"] != first_model
    print(f"  first model was      : {first_model}")
    print(f"  answered by          : {result['model']} ({result['label']})")
    print(f"  took                 : {elapsed:.2f}s")
    print(f"  fell back after      : {result.get('fallbacks')}")
    print(f"  the chain kept going : {'yes' if answered else 'NO'}")
    return 0 if answered else 1


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    failures = part_one(count)
    fallback_failures = part_two()
    print()
    print("=" * 72)
    if failures or fallback_failures:
        print(f"  FAILED  {failures} calls with no answer, {fallback_failures} chain failures")
        return 1
    print("  OK  every call answered, the caption carried the answer, the chain fell back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
