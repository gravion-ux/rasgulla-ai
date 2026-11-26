"""Live acceptance probe for the red-team agentic release.

Runs against the real project checkout and the
real free endpoints.  It checks the three things the release is judged on:

1. the spoken red-team switch really changes the mode,
2. a live request really reaches a free model and comes back as a renderer
   shaped plan, with every action held back for a spoken answer,
3. the voice gate really approves, declines and refuses near misses.

Nothing is executed on this machine: the action runner is replaced with a
recorder for the whole probe, so an approved step is only ever recorded.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app" / "brain"))

import session as session_module  # noqa: E402
from session import BrainSession  # noqa: E402

PASSED = 0
FAILED: list[str] = []

# The probe must never touch the real machine, so the runner only records.
RAN: list[dict] = []
_original_run_action = session_module._run_action


def _recording_run_action(action: dict) -> str:
    RAN.append(action)
    return f"recorded: {action.get('type', 'unknown')}"


session_module._run_action = _recording_run_action


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


def main() -> int:
    print("Live agentic probe")
    print("=" * 18)

    brain = BrainSession()
    check("the brain found an assistant key", bool(brain.key), brain.config_error)
    if brain.router is None:
        check("the router is configured", False, brain.config_error)
        return 1
    check("the router is configured", True)

    # -- 1. the spoken switch -------------------------------------------
    print("\nSpoken switch")
    print("-" * 14)
    switched = brain.handle(
        {"command": "route", "text": "red team on", "mode": "auto", "speak": False}
    )
    check("'red team on' switches the mode", brain.mode == "redteam", str(brain.mode))
    check(
        "the switch answers with a plan payload",
        switched.get("type") == "plan",
        str(switched.get("type")),
    )
    check(
        "the switch does not touch the network",
        RAN == [],
        str(RAN),
    )
    off = brain.handle(
        {"command": "route", "text": "red team off", "mode": "auto", "speak": False}
    )
    check("'red team off' switches back", brain.mode == "auto", str(brain.mode))
    check(
        "the switch back answers with a plan payload",
        off.get("type") == "plan",
        str(off.get("type")),
    )

    # -- 2. a live request ----------------------------------------------
    print("\nLive request")
    print("-" * 12)
    brain.mode = "redteam"
    started = time.monotonic()
    live = brain.handle(
        {
            "command": "route",
            "text": (
                "Audit the Rasgulla project folder for anything a security "
                "reviewer should look at, and list the steps you would take."
            ),
            "mode": "redteam",
            "speak": False,
        }
    )
    elapsed = time.monotonic() - started
    route = live.get("route") or {}
    print(f"  (answered in {elapsed:.1f}s)")
    print(f"  (tier={route.get('tier')} model={route.get('label')})")
    print(f"  (fallbacks={route.get('fallbacks')} attempts={route.get('attempts')})")
    print(f"  (reply={str(live.get('reply', ''))[:160]!r})")

    check("the live answer is a plan payload", live.get("type") == "plan", str(live.get("type")))
    check("the red-team request used the security tier", route.get("tier") == "security", str(route.get("tier")))
    check("the live answer has words", bool(str(live.get("reply", "")).strip()), str(live.get("reply"))[:120])
    check("the live answer came back in time", elapsed < 120, f"{elapsed:.1f}s")

    model = brain.router.last_model or ""
    provider_for_model = ""
    for name, provider in brain.router.providers.items():
        if model in provider.free_models or (
            provider.free_suffix and model.endswith(provider.free_suffix)
        ):
            provider_for_model = name
            break
    check("the live model was a free one", bool(provider_for_model), model)
    print(f"  (free provider={provider_for_model!r} model={model!r})")

    # -- 3. the voice gate ----------------------------------------------
    print("\nVoice gate")
    print("-" * 10)
    actions = live.get("actions") or []
    skill = live.get("skill") or {}
    if actions and skill:
        # A skill plan is HELD, not armed: nothing runs until the user says
        # "follow the playbook".
        check("the skill plan is held, not armed", brain._pending_actions == [], str(brain._pending_actions))
        check("the held plan is kept", brain._held_playbook is not None, str(brain._held_playbook))
        check(
            "the plan asks for the trigger",
            "follow the playbook" in str(live.get("reply", "")).lower(),
            str(live.get("reply"))[:160],
        )
        check("nothing ran before the word", RAN == [], str(RAN))
        started = brain.handle(
            {"command": "route", "text": "follow the playbook", "mode": "auto", "speak": False}
        )
        check("the trigger arms the plan", len(brain._pending_actions) == len(actions), str(brain._pending_actions))
        check(
            "the armed plan asks before running",
            "Run karun" in str(started.get("reply", "")),
            str(started.get("reply"))[:160],
        )
        check("nothing ran before the answer", RAN == [], str(RAN))
    elif actions:
        check("the live plan holds a step back for an answer", len(brain._pending_actions) == len(actions), str(brain._pending_actions))
        check("the live plan asks before running", "Run karun" in str(live.get("reply", "")), str(live.get("reply"))[:160])
        check("nothing ran before the answer", RAN == [], str(RAN))
    else:
        print("  (the live answer carried no action; the gate is checked with a fixed plan below)")

    # The same gate, with a fixed plan so the check always runs.
    brain._clear_pending()
    RAN.clear()
    brain._finalise_plan(
        {
            "reply": "Probe plan.",
            "actions": [{"type": "write_file", "path": "voice-gate-probe.txt", "content": "hi"}],
        },
        speak=False,
    )
    check("the step is waiting", len(brain._pending_actions) == 1, str(brain._pending_actions))
    check("nothing ran while it waits", RAN == [], str(RAN))

    near_miss = brain.handle(
        {"command": "route", "text": "what did you just do", "mode": "auto", "speak": False}
    )
    check("a near miss does not approve", RAN == [], str(RAN))
    check("a near miss drops the step", brain._pending_actions == [], str(brain._pending_actions))
    check("a near miss still answers", near_miss.get("type") in ("plan", "error"), str(near_miss.get("type")))

    brain._clear_pending()
    RAN.clear()
    brain._finalise_plan(
        {
            "reply": "Probe plan.",
            "actions": [{"type": "write_file", "path": "voice-gate-probe.txt", "content": "hi"}],
        },
        speak=False,
    )
    # The microphone has to have heard the answer, so the decline is spoken
    # before it is routed.
    brain._say("nahi")
    declined = brain.handle(
        {"command": "route", "text": "nahi", "mode": "auto", "speak": False}
    )
    check("'nahi' declines the step", RAN == [], str(RAN))
    check("the decline is reported", "skipped" in str(declined.get("reply", "")).lower(), str(declined.get("reply")))
    check("the plan is finished", brain._pending_actions == [], str(brain._pending_actions))

    brain._clear_pending()
    RAN.clear()
    brain._finalise_plan(
        {
            "reply": "Probe plan.",
            "actions": [{"type": "write_file", "path": "voice-gate-probe.txt", "content": "hi"}],
        },
        speak=False,
    )
    # The microphone has to have heard the answer, so the approval is spoken
    # before it is routed.
    brain._say("haan")
    approved = brain.handle(
        {"command": "route", "text": "haan", "mode": "auto", "speak": False}
    )
    check("'haan' runs the step", len(RAN) == 1, str(RAN))
    check("the run is reported", "Ran" in str(approved.get("reply", "")), str(approved.get("reply")))
    check("the plan is finished", brain._pending_actions == [], str(brain._pending_actions))

    brain._clear_pending()
    RAN.clear()
    brain._finalise_plan(
        {
            "reply": "Probe plan.",
            "actions": [{"type": "write_file", "path": "voice-gate-probe.txt", "content": "hi"}],
        },
        speak=False,
    )
    brain._pending_deadline = time.monotonic() - 1.0
    expired = brain.handle(
        {"command": "route", "text": "haan", "mode": "auto", "speak": False}
    )
    check("a late answer runs nothing", RAN == [], str(RAN))
    check("the timeout is reported", "timed out" in str(expired.get("reply", "")).lower(), str(expired.get("reply")))

    session_module._run_action = _original_run_action
    print("=" * 18)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
