"""Talk test: click the orb, the microphone opens, the answer comes back.

The smoke test proves the app starts and the face is right.  This one proves
the thing the user actually does: click the orb, speak, and hear the answer.

It drives the real app in ``--probe-talk`` mode and checks three links:

- a level event that arrives from the brain moves the orb,
- clicking the orb really opens the microphone (the brain starts sending
  levels on its own),
- a final transcript starts a real answer and the orb starts speaking.

It needs a microphone, so it is kept out of the fast smoke gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"
TALK_MARKER = "RASGULLA_TALK "

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


def kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def find_talk(output: str) -> dict:
    for line in output.splitlines():
        if line.startswith(TALK_MARKER):
            try:
                return json.loads(line[len(TALK_MARKER) :])
            except json.JSONDecodeError:
                return {}
    return {}


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1

    print("Talk test")
    print("=" * 16)
    print("  (opens the real microphone for a few seconds)")

    proc = subprocess.Popen(
        [str(ELECTRON), ".", "--probe-talk"],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = proc.communicate(timeout=180)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            output, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            output = ""
        returncode = -1

    talk = find_talk(output)
    if not talk:
        print(output[-4000:])
        check("the app printed a talk result", False, "no talk line in the output")
        print("=" * 16)
        print(f"passed: {PASSED}   failed: {len(FAILED)}")
        return 1

    print("talk payload:")
    print(json.dumps(talk, indent=2))
    print("-" * 16)

    check(
        "a level event moves the orb",
        talk.get("orbLevel") == 0.77,
        str(talk),
    )
    check(
        "clicking the orb opens the microphone",
        int(talk.get("micLevels", 0)) > 0,
        str(talk),
    )
    check(
        "the brain reported the microphone closing",
        int(talk.get("micClosedEvents", 0)) > 0,
        str(talk),
    )
    check(
        "a final transcript starts a real answer",
        int(talk.get("planEvents", 0)) > 0,
        str(talk),
    )
    check(
        "the orb starts speaking",
        talk.get("phase") == "speaking",
        str(talk),
    )
    check(
        "the spoken line is captioned on the orb",
        len(str(talk.get("captionText", "")).strip()) > 0,
        str(talk),
    )
    check("the app exited cleanly", returncode == 0, f"exit={returncode}")
    if returncode != 0:
        print(output[-4000:])

    check(
        "no content security policy violation",
        "Refused to" not in output and "Content Security Policy" not in output,
        output[:400],
    )

    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
