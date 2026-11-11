"""Tests for app/brain/session.py - the headless brain shim.

These tests never call the network.  They check the protocol round trip,
local intent handling, and the reused action guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla")
BRAIN = APP_DIR / "app" / "brain"
for _p in (str(APP_DIR), str(BRAIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from session import BrainSession  # noqa: E402

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
    from core_app import RasgullaApp as _App
    from types import MethodType

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
    from core_app import RasgullaApp as _App
    from types import MethodType

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


def main():
    print("Brain shim tests")
    print("=" * 16)
    test_hello()
    test_local_intent_time()
    test_validate_actions_through_shim()
    test_rejects_unsafe_action()
    test_voice_line_split()
    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
