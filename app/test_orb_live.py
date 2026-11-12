"""Live acceptance test: drive the real app and watch the orb speak.

This is the end-to-end check from the design spec: a question is handed to the
real window, the real brain answers through the real router, the voice engine
speaks the character line, and the orb has to put captions on screen while the
audio plays.

It needs a working assistant key (the brain finds one in the environment or in
the Windows user registry) and it uses the free endpoint, so it runs inside
the normal rate limit.  It is kept apart from the fast smoke test because it
costs one model call.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"
LIVE_MARKER = "RASGULLA_LIVE "

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


def find_live(output: str) -> dict:
    for line in output.splitlines():
        if line.startswith(LIVE_MARKER):
            try:
                return json.loads(line[len(LIVE_MARKER) :])
            except json.JSONDecodeError:
                return {}
    return {}


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1

    print("Live acceptance test")
    print("=" * 16)
    print("  (one real model call, this can take a minute)")

    proc = subprocess.Popen(
        [str(ELECTRON), ".", "--probe-live"],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = proc.communicate(timeout=300)
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            output, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            output = ""
        returncode = -1

    live = find_live(output)
    if not live:
        print(output[-4000:])
        check("the app printed a live result", False, "no live line in the output")
        print("=" * 16)
        print(f"passed: {PASSED}   failed: {len(FAILED)}")
        return 1

    print("live payload:")
    print(json.dumps(live, indent=2))
    print("-" * 16)

    state = live.get("state", {})
    check("the audio element holds a blob", state.get("audioIsBlob") is True, str(state))
    check("the audio actually played", live.get("audioEverPlayed") is True, str(live))
    caption = str(live.get("captionText", ""))
    check("the orb showed a caption", len(caption.strip()) > 0, repr(caption))
    check("the caption holds real words", len(caption.split()) >= 1, repr(caption))
    check(
        "the screen shows the name and three phases only",
        state.get("brandText") == "Rasgulla"
        and state.get("phaseCount") == 3
        and state.get("hasInput") is False
        and state.get("hasPanel") is False,
        str(state),
    )
    check("the app exited cleanly", returncode == 0, f"exit={returncode}")

    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
