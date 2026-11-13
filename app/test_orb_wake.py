"""Wake test: the wake word and the greeting, through the real app.

The smoke test proves the app starts and the face is right.  The talk test
proves that clicking the orb opens the microphone.  This one proves the new
part: she sleeps with the microphone open, the wake word wakes her up, and
she greets you out loud.

It drives the real app in ``--probe-wake`` mode and checks five links:

- the renderer opens the always on microphone by itself,
- the brain really listens (levels arrive) and says she is asleep,
- a wake event puts her awake,
- a greeting event carries something to say,
- the orb speaks the greeting and captions it.

It needs the microphone, so it is kept out of the fast smoke gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"
WAKE_MARKER = "RASGULLA_WAKE "

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


def find_wake(output: str) -> dict:
    for line in output.splitlines():
        if line.startswith(WAKE_MARKER):
            try:
                return json.loads(line[len(WAKE_MARKER) :])
            except json.JSONDecodeError:
                return {}
    return {}


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1

    print("Wake test")
    print("=" * 16)
    print("  (opens the real microphone for a few seconds)")

    proc = subprocess.Popen(
        [str(ELECTRON), ".", "--probe-wake"],
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

    wake = find_wake(output)
    if not wake:
        print(output[-4000:])
        check("the app printed a wake result", False, "no wake line in the output")
        print("=" * 16)
        print(f"passed: {PASSED}   failed: {len(FAILED)}")
        return 1

    print("wake payload:")
    print(json.dumps(wake, indent=2))
    print("-" * 16)

    check(
        "she opens the microphone by herself",
        int(wake.get("micLevels", 0)) > 0,
        str(wake),
    )
    check(
        "the microphone starts without a long wait",
        0 <= int(wake.get("firstLevelAfterMs", -1)) < 10000,
        str(wake),
    )
    check(
        "the microphone keeps up with the room",
        int(wake.get("levelRate", 0)) >= 10,
        str(wake),
    )
    check(
        "she falls asleep waiting for the wake word",
        int(wake.get("micStateWake", 0)) >= 1,
        str(wake),
    )
    check(
        "the wake event arrives",
        int(wake.get("wakeEvents", 0)) >= 1,
        str(wake),
    )
    check(
        "the greeting event arrives",
        int(wake.get("greetingEvents", 0)) >= 1,
        str(wake),
    )
    check(
        "the greeting carries a voice",
        bool(wake.get("greetingAudio")),
        str(wake),
    )
    check(
        "the orb starts speaking",
        wake.get("phase") == "speaking",
        str(wake),
    )
    check(
        "the greeting is captioned on the orb",
        len(str(wake.get("captionText", "")).strip()) > 0,
        str(wake),
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
