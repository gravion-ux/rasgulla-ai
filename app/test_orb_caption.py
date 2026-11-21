"""Caption test: the reply text has to be visible on the orb.

The orb host clips everything it paints, so a caption can sit in the DOM and
still never reach the screen.  The old probes only read ``cap.textContent``,
which is exactly why a clipped caption passed every test.

There are two things on screen now and both of them have to hold up:

- the live caption under the face, which follows the words as she speaks, and
- the reply itself, the whole of it, in a place that can be read and scrolled.

A reply of any length has to survive: a normal answer fits, a long dump
scrolls instead of being cut off, and in both cases the text that was sent is
the text that ends up on the screen.

This drives the real renderer with a real reply and then measures where the
boxes actually are and how many of their pixels were painted.  Text that is in
the DOM but off screen fails both.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"
PROBE_MARKER = "RASGULLA_CAPTION "

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
        [str(ELECTRON), ".", "--probe-caption"],
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


def check_case(name: str, case: dict, *, must_fit: bool) -> None:
    """Check one measured reply.

    ``must_fit`` is true for a normal answer, which has to be readable without
    scrolling, and false for a long dump, which has to scroll instead of
    losing the text.
    """
    cap = case.get("cap", {})
    orb = case.get("orb", {})
    host = case.get("host", {})
    reply = case.get("reply") or {}
    reply_text = str(case.get("replyText", ""))
    driven_text = str(case.get("drivenText", ""))

    # -- the live caption under the face ---------------------------------
    check(
        f"{name}: the live caption has text",
        len(str(case.get("capText", "")).strip()) > 0,
        str(case.get("capText")),
    )
    check(
        f"{name}: the live caption sits inside the host that clips it",
        case.get("insideHost") is True,
        f"overflows by {case.get('overflowBy')}px",
    )
    check(
        f"{name}: the live caption is painted on the screen",
        int(case.get("brightPixels", 0)) > 0,
        f"bright pixels: {case.get('brightPixels')}",
    )
    check(
        f"{name}: the live caption is wide enough to read",
        float(cap.get("w", 0)) >= 240,
        f"caption width: {cap.get('w')}",
    )
    check(
        f"{name}: the live caption is tall enough to hold a line",
        float(cap.get("h", 0)) >= 40,
        f"caption height: {cap.get('h')}",
    )
    check(
        f"{name}: the live caption does not cover the face",
        float(cap.get("top", 0)) >= float(orb.get("bottom", 0)) - 30,
        f"caption top {cap.get('top')} vs orb bottom {orb.get('bottom')}",
    )
    check(
        f"{name}: the live caption is still under the face",
        float(cap.get("top", 0)) <= float(orb.get("bottom", 0)) + 5,
        f"caption top {cap.get('top')} vs orb bottom {orb.get('bottom')}",
    )

    # -- the reply itself -------------------------------------------------
    check(
        f"{name}: the reply text is on the screen",
        len(reply_text.strip()) > 0,
        f"reply area: {reply}",
    )
    check(
        f"{name}: the whole reply is kept, nothing cut",
        bool(driven_text) and reply_text == driven_text,
        f"sent {len(driven_text)} chars, showing {len(reply_text)} chars",
    )
    check(
        f"{name}: the reply text is painted on the screen",
        int(case.get("replyBrightPixels", 0)) > 0,
        f"bright pixels: {case.get('replyBrightPixels')}",
    )
    check(
        f"{name}: the reply area is inside the window",
        case.get("replyInsideWindow") is True,
        f"reply area: {reply}",
    )
    check(
        f"{name}: the reply area is under the face",
        float(reply.get("top", 0)) >= float(host.get("bottom", 0)) - 1,
        f"reply top {reply.get('top')} vs host bottom {host.get('bottom')}",
    )
    if must_fit:
        check(
            f"{name}: the reply fits on the screen without scrolling",
            int(case.get("replyScrollHeight", 0))
            <= int(case.get("replyClientHeight", 0)) + 2,
            f"content {case.get('replyScrollHeight')}px in {case.get('replyClientHeight')}px",
        )
    else:
        check(
            f"{name}: the reply scrolls instead of being cut off",
            int(case.get("replyScrollHeight", 0))
            > int(case.get("replyClientHeight", 0)),
            f"content {case.get('replyScrollHeight')}px in {case.get('replyClientHeight')}px",
        )
        check(
            f"{name}: the reply area can scroll",
            str(case.get("replyOverflowY", "")) in ("auto", "scroll"),
            str(case.get("replyOverflowY")),
        )


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1

    print("Caption test")
    print("=" * 16)
    returncode, output = run_probe()
    probe = find_probe(output)
    if not probe:
        print(output[-4000:])
        check("the app printed a caption result", False, "no caption line in the output")
        print("=" * 16)
        print(f"passed: {PASSED}   failed: {len(FAILED)}")
        return 1

    print("probe payload:")
    print(json.dumps(probe, indent=2))
    print("-" * 16)

    answer = probe.get("answer", {})
    dump = probe.get("dump", {})
    if answer.get("error"):
        print(f"FAIL: the answer case failed: {answer.get('error')}")
        check("the app could show a reply", False, str(answer.get("error")))
    else:
        check_case("answer", answer, must_fit=True)
    if dump.get("error"):
        print(f"FAIL: the dump case failed: {dump.get('error')}")
        check("the app could show a long reply", False, str(dump.get("error")))
    else:
        check_case("dump", dump, must_fit=False)

    check(
        "the host still clips its own painting",
        "paint" in str(answer.get("hostContain", "")),
        str(answer.get("hostContain")),
    )
    check("the app exited cleanly", returncode == 0, f"exit={returncode}")
    if returncode != 0:
        print(output[-4000:])

    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
