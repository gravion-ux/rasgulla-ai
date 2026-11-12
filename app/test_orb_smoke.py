"""Smoke test: launch the real Electron app and prove it actually works.

The old version of this test only checked that the process stayed alive, which
passed even while the app threw during load and never opened a window.  This
version drives the app's own ``--probe`` mode and asserts on what really
happened inside the renderer.

The app was stripped down to one face: the name, the three phases and the orb.
Nothing else may be on screen, and the pet overlay must be gone.

It prints the probe payload so a failure can be read without a debugger.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"
PROBE_MARKER = "RASGULLA_PROBE "

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


def find_probe(output: str) -> dict:
    for line in output.splitlines():
        if line.startswith(PROBE_MARKER):
            try:
                return json.loads(line[len(PROBE_MARKER) :])
            except json.JSONDecodeError:
                return {}
    return {}


def kill_tree(pid: int) -> None:
    """Electron spawns a whole process tree; taskkill is the only reliable stop."""
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


def run_probe() -> tuple[int, str]:
    proc = subprocess.Popen(
        [str(ELECTRON), ".", "--probe"],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        output, _ = proc.communicate(timeout=120)
        return proc.returncode, output or ""
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
        try:
            output, _ = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            output = ""
        return -1, output or ""


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1

    print("Smoke test")
    print("=" * 16)
    returncode, output = run_probe()
    probe = find_probe(output)
    if not probe:
        print(output[-4000:])
        check("the app printed a probe result", False, "no probe line in the output")
        print("=" * 16)
        print(f"passed: {PASSED}   failed: {len(FAILED)}")
        return 1

    print("probe payload:")
    print(json.dumps(probe, indent=2))
    print("-" * 16)

    windows = probe.get("windows", {})
    check("the orb window was created", windows.get("orb") is True, str(windows))
    check("there is no pet window", windows.get("pet") in (None, False), str(windows))
    check("the orb window is the only window", windows.get("count") == 1, str(windows))

    orb = probe.get("orb", {})
    check("the speaking orb element is defined", orb.get("elementDefined") is True, str(orb))
    check("the renderer script ran", orb.get("rendererRan") is True, str(orb))
    check("the orb has a shadow root", orb.get("shadowRoot") is True, str(orb))
    check("the name shown is Rasgulla", orb.get("brandText") == "Rasgulla", str(orb))
    check(
        "there are exactly three phases",
        orb.get("phaseCount") == 3,
        str(orb),
    )
    check(
        "the phases are Listening, Thinking and Speaking",
        orb.get("phaseLabels") == ["Listening", "Thinking", "Speaking"],
        str(orb),
    )
    check("there is no text input", orb.get("hasInput") is False, str(orb))
    check("there is no chat panel", orb.get("hasPanel") is False, str(orb))
    check("there is no headline strip", orb.get("hasStrip") is False, str(orb))
    check("there is no plan card", orb.get("hasPlanCard") is False, str(orb))
    check("there is no confirm card", orb.get("hasConfirmCard") is False, str(orb))
    check("the orb is clickable", orb.get("orbCursor") == "pointer", str(orb))

    brain = probe.get("brain", {})
    check("the brain child is alive", brain.get("alive") is True, str(brain))
    check("the brain answered ready", brain.get("type") == "ready", str(brain))
    check("the brain router is configured", brain.get("configError") == "", str(brain))
    check(
        "the default voice is the expressive one",
        brain.get("voiceId") == "en-IN-NeerjaExpressiveNeural",
        str(brain),
    )
    check("the brain accepts the mic command", brain.get("micAccepted") is True, str(brain))

    assets = probe.get("assets", {})
    check("the vendor component was served", assets.get("orbScript") is True, str(assets))
    check("no pet frame was served", assets.get("petFrame") in (None, False), str(assets))

    layout = probe.get("layout", {})
    # The content area is what the spec describes; Windows adds a one pixel
    # invisible border around the frameless window itself.
    check(
        "the orb window is the compact size",
        layout.get("contentBefore", {}).get("width") == 440
        and layout.get("contentBefore", {}).get("height") == 660,
        str(layout),
    )

    check("the app exited cleanly", returncode == 0, f"exit={returncode}")
    if returncode != 0:
        print(output[-4000:])

    check(
        "no Electron security warning",
        "Electron Security Warning" not in output,
        output[:400],
    )
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
