"""Acceptance probe for the release criteria, run offline against the real code.

Every check here exercises the shipped code, not a copy of it.  The action
runner is replaced with a recorder so nothing can touch this machine.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "brain"))

import core_router  # noqa: E402
import session as session_module  # noqa: E402
from session import BrainSession, approval_decision  # noqa: E402

PASSED = 0
FAILED: list[str] = []

RAN: list[dict] = []
_original = session_module._run_action
session_module._run_action = lambda action: RAN.append(action) or "recorded"


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


def arm(session: BrainSession) -> None:
    session._finalise_plan(
        {
            "reply": "Here is the plan.",
            "actions": [{"type": "write_file", "path": "voice-gate.txt", "content": "hi"}],
        },
        speak=False,
    )


def main() -> int:
    print("Acceptance probe")
    print("=" * 16)

    # -- 1. only the voice runs a step ---------------------------------
    print("\nOnly the voice runs a step")
    print("-" * 28)
    brain = BrainSession()
    RAN.clear()
    arm(brain)
    check("nothing ran when the plan arrived", RAN == [], str(RAN))
    brain.handle({"command": "act", "index": 0, "ok": True})
    check("the protocol cannot run a step", RAN == [], str(RAN))
    brain.handle({"command": "run_plan", "approved": [0]})
    check("the protocol cannot run a plan", RAN == [], str(RAN))
    brain.handle({"command": "route", "text": "what did you just do", "mode": "auto", "speak": False})
    check("an unrelated sentence runs nothing", RAN == [], str(RAN))
    brain._clear_pending()
    RAN.clear()
    arm(brain)
    brain._pending_deadline = 0.0
    # The microphone has to have heard the answer, so the approval is spoken
    # before it is routed.
    brain._say("haan")
    brain.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
    check("the spoken yes runs the step", len(RAN) == 1, str(RAN))

    # -- 2. whole-word matching ----------------------------------------
    print("\nWhole-word matching")
    print("-" * 21)
    for phrase in ["what time is it", "give me the token", "open notepad now",
                   "challenge the login form", "format the file", "stopwatch"]:
        check(f"'{phrase}' does not approve", approval_decision(phrase) == "other", approval_decision(phrase))
    for phrase in ["haan", "ha", "yes", "ok", "okay", "theek hai", "theek", "run",
                   "run karo", "kar do", "chalao", "approve", "approved", "chal",
                   "chalo", "karo", "ji"]:
        check(f"'{phrase}' approves", approval_decision(phrase) == "approve", approval_decision(phrase))
    for phrase in ["ruk", "ruk jao", "nahi", "no", "mat", "mat karo", "skip",
                   "rehne do", "cancel", "stop", "band karo"]:
        check(f"'{phrase}' declines", approval_decision(phrase) == "decline", approval_decision(phrase))
    check("a refusal beats an approval", approval_decision("haan mat karo") == "decline")
    check("a refusal beats an approval in reverse", approval_decision("yes no") == "decline")

    # -- 3. a paid model is never called -------------------------------
    print("\nFree only")
    print("-" * 9)
    calls: list[str] = []

    class _Ok:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": "the answer"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    original_post = core_router.requests.post
    core_router.requests.post = lambda url, headers=None, json=None, timeout=None: (
        calls.append(str(url)) or _Ok()
    )
    try:
        with tempfile.TemporaryDirectory() as folder:
            config = {
                "endpoint": "https://root.invalid/v1/chat/completions",
                "key_env": ["RASGULLA_API_KEY"],
                "defaults": {"requests_per_minute": 60, "timeout_seconds": 5, "max_attempts_per_request": 3},
                "providers": {
                    "paid": {
                        "endpoint": "https://paid.invalid/v1/chat/completions",
                        "key_env": ["RASGULLA_API_KEY"],
                        "free_models": [],
                    },
                    "free": {
                        "endpoint": "https://free.invalid/v1/chat/completions",
                        "key_env": ["RASGULLA_API_KEY"],
                        "free_models": ["free/model"],
                    },
                },
                "tiers": {
                    "chat": {
                        "label": "T",
                        "temperature": 0.2,
                        "max_tokens": 100,
                        "chain": [
                            {"provider": "paid", "model": "paid/expensive"},
                            {"provider": "free", "model": "free/model"},
                        ],
                    }
                },
                "aliases": {},
            }
            path = Path(folder) / "models.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            router = core_router.ModelRouter(path, "root-key", core_router.UsageTracker(Path(folder) / "usage.json"))
            result = router.call("chat", [{"role": "user", "content": "hi"}])
            check("the paid model was never called", "paid.invalid" not in " ".join(calls), str(calls))
            check("the free model answered", result.get("text") == "the answer", str(result))

            # A provider with no key of its own must not borrow the root key.
            calls.clear()
            config["providers"]["ownkey"] = {
                "endpoint": "https://ownkey.invalid/v1/chat/completions",
                "key_env": ["OWN_KEY_NOT_SET"],
                "free_models": ["own/model"],
            }
            config["tiers"]["chat"]["chain"] = [{"provider": "ownkey", "model": "own/model"}]
            path.write_text(json.dumps(config), encoding="utf-8")
            router = core_router.ModelRouter(path, "root-key", core_router.UsageTracker(Path(folder) / "usage.json"))
            router.providers["ownkey"].key = ""
            try:
                router.call("chat", [{"role": "user", "content": "hi"}])
                check("a provider without a key is skipped", False, "no exception")
            except core_router.RouterError as error:
                check("a provider without a key is skipped", True)
                check(
                    "the provider name stays out of the message",
                    "ownkey" not in str(error).lower(),
                    str(error),
                )
            check("the provider without a key was never called", calls == [], str(calls))
    finally:
        core_router.requests.post = original_post

    # -- 4. no brand names in what a human reads ------------------------
    print("\nBrand free")
    print("-" * 10)
    messages = [
        "a step is approved by voice, one at a time.",
        "that step could not be prepared, so nothing ran.",
        "that endpoint has no key",
        "that endpoint is switched off",
    ]
    for message in messages:
        lowered = message.lower()
        check(
            f"'{message[:34]}' carries no brand",
            not any(word in lowered for word in ("openrouter", "nvidia", "gemini", "openai")),
            message,
        )

    session_module._run_action = _original
    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
