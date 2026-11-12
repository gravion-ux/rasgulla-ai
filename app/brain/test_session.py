"""Tests for app/brain/session.py - the headless brain shim.

These tests never call the network.  They check the protocol round trip,
local intent handling, and the reused action guards.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla")
BRAIN = APP_DIR / "app" / "brain"
for _p in (str(APP_DIR), str(BRAIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from session import BrainSession

PASSED = 0
FAILED: list[str] = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


def test_hello():
    session = BrainSession()
    reply = session.handle({"command": "hello"})
    check("hello returns ready", reply.get("type") == "ready", str(reply))
    check("voice_ready present", "voice_ready" in reply, str(reply))
    check("key_found present", "key_found" in reply, str(reply))
    check("no error key", "error" not in reply, str(reply))


def test_local_intent_time():
    session = BrainSession()
    reply = session.handle({"command": "route", "text": "what time is it", "mode": "normal", "speak": False})
    check("local intent handled", reply.get("type") == "plan", str(reply))
    check("reply has time content", "time" in str(reply.get("reply", "")).lower() or "baje" in str(reply.get("reply", "")), str(reply))
    check("route is local", reply.get("route", {}).get("tier") == "local", str(reply))


def test_validate_actions_through_shim():
    from types import MethodType

    from core_app import RasgullaApp as _App

    shim = type("Shim", (), {"action_rejections": []})()
    validator = MethodType(_App.validate_actions, shim)
    safe = MethodType(_App._safe_path, shim)
    shim._safe_path = safe
    actions = [
        {"type": "launch", "app": "notepad"},
        {"type": "open_url", "url": "https://example.com"},
        {"type": "type_text", "text": "hello world"},
        {"type": "key", "key": "enter"},
        {"type": "write_file", "path": "notes.md", "content": "test"},
        {"type": "read_file", "path": "notes.md"},
    ]
    valid = validator(actions)
    check("five actions valid (read_file needs existing file)", len(valid) == 5, f"got {len(valid)}")
    # write_file is index 4 in the valid list (5th item)
    write_actions = [a for a in valid if a["type"] == "write_file"]
    check("write_file path is relative", write_actions and write_actions[0]["path"] == "notes.md", str(valid))


def test_rejects_unsafe_action():
    from types import MethodType

    from core_app import RasgullaApp as _App

    shim = type("Shim", (), {"action_rejections": []})()
    validator = MethodType(_App.validate_actions, shim)
    safe = MethodType(_App._safe_path, shim)
    shim._safe_path = safe
    valid = validator([{"type": "launch", "app": "format_c"}])
    check("unsafe launch rejected", len(valid) == 0, f"got {valid}")


def test_voice_line_split():
    from core_app import voice_line

    reply = "✍️Narrator: scene\n🔓Inquisitor: q?\n💭 thought\n🎭character: this is spoken\n😈🔓Rasgulla Freedom Gauge🔫 [x] (50%)\n1. a\n2. b\n3. c"
    line = voice_line(reply)
    check("voice_line extracts character only", line == "this is spoken", repr(line))


# -- the real entry point, launched the way the app launches it ---------------
#
# These run a fresh interpreter with the app directory as the working
# directory and the brain directory as sys.path[0], exactly like
# `python app/brain/brain.py` does.  Nothing in this file may patch sys.path
# for them, so a wrong project root shows up here instead of hiding.

CHILD_CWD = APP_DIR / "app"
CHILD_ENTRY = BRAIN / "brain.py"


def run_child(commands: list[dict]) -> tuple[int, list[dict], str]:
    payload = "".join(json.dumps(command) + "\n" for command in commands)
    result = subprocess.run(
        [sys.executable, str(CHILD_ENTRY)],
        cwd=str(CHILD_CWD),
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    replies = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            replies.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result.returncode, replies, result.stderr


def test_entrypoint_starts_without_a_sys_path_patch():
    code, replies, stderr = run_child([{"command": "hello"}, {"command": "shutdown"}])
    check("entrypoint exits cleanly", code == 0, f"exit={code} stderr={stderr[-400:]}")
    check("entrypoint imports the project brain", "ModuleNotFoundError" not in stderr, stderr[-400:])
    check("entrypoint answers hello", bool(replies) and replies[0].get("type") == "ready", str(replies))


def test_entrypoint_reports_a_ready_router():
    _code, replies, _stderr = run_child([{"command": "hello"}, {"command": "shutdown"}])
    if not replies:
        check("router is configured", False, "no reply")
        return
    ready = replies[0]
    check("router has no config error", ready.get("config_error") == "", str(ready))
    check("voice is reported ready", ready.get("voice_ready") is True, str(ready))


def test_entrypoint_serves_a_local_request():
    _code, replies, stderr = run_child(
        [
            {"command": "route", "text": "what time is it", "mode": "auto", "speak": False},
            {"command": "shutdown"},
        ]
    )
    plans = [reply for reply in replies if reply.get("type") == "plan"]
    check("local request returns a plan", bool(plans), f"{replies} {stderr[-300:]}")
    if plans:
        check("plan is on the local route", plans[0].get("route", {}).get("tier") == "local", str(plans[0]))


def test_config_files_live_at_the_project_root():
    probe = (
        "import json, os, sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "import session;"
        "print(json.dumps({"
        "'config': [str(session.CONFIG_PATH), os.path.exists(session.CONFIG_PATH)],"
        "'models': [str(session.MODELS_DIR), os.path.isdir(session.MODELS_DIR)],"
        "'usage': [str(session.USAGE_PATH), os.path.exists(session.USAGE_PATH)]"
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(BRAIN)],
        cwd=str(CHILD_CWD),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("config paths are readable", False, f"stdout={result.stdout[-300:]} stderr={result.stderr[-300:]}")
        return
    for name in ("config", "models", "usage"):
        path, exists = data[name]
        check(f"{name} path exists at the project root", bool(exists), f"{path}")
        check(f"{name} path is under the project root", str(APP_DIR) in path, f"{path}")


def main():
    print("Brain shim tests")
    print("=" * 16)
    test_hello()
    test_local_intent_time()
    test_validate_actions_through_shim()
    test_rejects_unsafe_action()
    test_voice_line_split()
    test_entrypoint_starts_without_a_sys_path_patch()
    test_entrypoint_reports_a_ready_router()
    test_entrypoint_serves_a_local_request()
    test_config_files_live_at_the_project_root()
    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
