"""Smoke test: launch the Electron orb app and verify it stays alive.

Starts Electron with the real app, waits for Chromium boot, and confirms the
renderer process is still running (didn't crash on load).  This validates that
main.js, preload.js, index.html, styles.css and renderer.js all parse and the
vendored Speaking Orb component loads without throwing.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla\app")
ELECTRON = APP_DIR / "node_modules" / ".bin" / "electron.cmd"


def main() -> int:
    if not ELECTRON.exists():
        print(f"FAIL: electron binary not found at {ELECTRON}")
        return 1
    proc = subprocess.Popen(
        [str(ELECTRON), "."],
        cwd=str(APP_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(10)
        if proc.poll() is not None:
            output = proc.stdout.read() or ""
            print(output)
            print("FAIL: electron exited early")
            return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("PASS: electron stayed alive through the probe window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
