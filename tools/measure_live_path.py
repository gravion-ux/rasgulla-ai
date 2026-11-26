"""Measure the real Rasgulla request path.

This runs the same call the brain makes: the real system prompt, the real
tier brief, the real chain from models.json, against the real free endpoints.
Every attempt is timed, so a slow answer can be told apart from a slow
fallback, and every failure is counted.

Usage:  measure_live_path.py [count] [tier]
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
from core_router import ModelRouter, RouterError, TaskClassifier, UsageTracker  # noqa: E402

CONFIG = ROOT / "models.json"
REQUESTS = [
    "hey, what is the capital of France?",
    "write a python function that reverses a string",
    "in two lines, why does the sky look blue?",
    "what are three good uses of a Raspberry Pi?",
]

ATTEMPTS: list[dict] = []
_original_request = ModelRouter._request


def _timed_request(self, model, spec, messages, image, timeout_override=None, provider_override=None):
    started = time.monotonic()
    try:
        result = _original_request(
            self, model, spec, messages, image, timeout_override, provider_override
        )
    except Exception as error:  # noqa: BLE001 - this is a measurement harness
        ATTEMPTS.append(
            {
                "model": model,
                "provider": provider_override,
                "seconds": round(time.monotonic() - started, 2),
                "outcome": "FAIL",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        raise
    ATTEMPTS.append(
        {
            "model": model,
            "provider": provider_override,
            "seconds": round(time.monotonic() - started, 2),
            "outcome": "ok",
            "chars": len(result[0]),
            "preview": result[0][:120].replace("\n", " "),
        }
    )
    return result


ModelRouter._request = _timed_request


def main() -> int:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    forced_tier = sys.argv[2] if len(sys.argv) > 2 else "auto"

    usage = UsageTracker(Path.home() / "rasgulla-measure-usage.json")
    router = ModelRouter(CONFIG, "", usage)
    router.key = core_router.read_api_key(router.key_env)
    classifier = TaskClassifier()

    prompt = build_system_prompt("main")
    print(f"system prompt: {len(prompt)} chars")
    for tier, brief in TIER_BRIEF.items():
        print(f"tier brief {tier}: {len(brief)} chars")
    print("-" * 72)

    totals: list[float] = []
    failed_calls = 0
    total_attempts = 0
    failed_attempts = 0

    for index in range(count):
        request = REQUESTS[index % len(REQUESTS)]
        tier = forced_tier if forced_tier != "auto" else classifier.classify(request)
        messages = [
            {"role": "system", "content": build_system_prompt(tier)},
            {"role": "user", "content": request},
        ]
        ATTEMPTS.clear()
        started = time.monotonic()
        print(f"[{index + 1}/{count}] tier={tier} request={request!r}")
        try:
            result = router.call(tier, messages, extra_instructions=TIER_BRIEF.get(tier, ""))
            elapsed = time.monotonic() - started
            totals.append(elapsed)
            print(f"      answered in {elapsed:.2f}s by {result['label']} ({result['model']})")
            if result.get("fallbacks"):
                print(f"      fallbacks: {result['fallbacks']}")
            preview = str(result.get("text", ""))[:160].replace("\n", " ")
            print(f"      text: {preview}")
        except RouterError as error:
            elapsed = time.monotonic() - started
            totals.append(elapsed)
            failed_calls += 1
            print(f"      NO ANSWER in {elapsed:.2f}s: {error}")
        for attempt in ATTEMPTS:
            total_attempts += 1
            if attempt["outcome"] == "FAIL":
                failed_attempts += 1
            print(
                f"      attempt {attempt['model']} ({attempt['provider']}): "
                f"{attempt['seconds']}s {attempt['outcome']} {attempt.get('error', '')}"
            )
        print("-" * 72)

    print("SUMMARY")
    print(f"  calls              : {count}")
    print(f"  calls with no answer: {failed_calls}")
    print(f"  attempts           : {total_attempts}")
    print(f"  failed attempts    : {failed_attempts}")
    if totals:
        print(f"  latency min/median/max: {min(totals):.2f} / {statistics.median(totals):.2f} / {max(totals):.2f}")
        print(f"  latency mean       : {statistics.mean(totals):.2f}")
    print("  router status rows :")
    for row in router.status_rows():
        print(
            f"    {row['label']:<10} requests={row['requests']:<3} errors={row['errors']:<3} "
            f"avg={row['avg_seconds']:<6} cooldown={row['cooldown']} disabled={row['disabled']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
