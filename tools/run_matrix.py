"""Run the whole gate in one go and report one line per suite.

Usage:  run_matrix.py [name ...]
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
WORK = ROOT / "work"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

SUITES = [
    ("test_rasgulla", ROOT, [PY, "test_rasgulla.py"]),
    ("brain/test_session", ROOT / "app" / "brain", [PY, "test_session.py"]),
    ("brain/test_tts", ROOT / "app" / "brain", [PY, "test_tts.py"]),
    ("app/test_orb_smoke", ROOT / "app", [PY, "test_orb_smoke.py"]),
    ("app/test_orb_caption", ROOT / "app", [PY, "test_orb_caption.py"]),
    ("app/test_orb_talk", ROOT / "app", [PY, "test_orb_talk.py"]),
    ("app/test_orb_wake", ROOT / "app", [PY, "test_orb_wake.py"]),
    ("app/test_orb_live", ROOT / "app", [PY, "test_orb_live.py"]),
    ("live_probe_agentic", TOOLS, [PY, "live_probe_agentic.py"]),
    ("acceptance_probe", TOOLS, [PY, "acceptance_probe.py"]),
]


def summary_line(output: str) -> str:
    interesting = [
        line.strip()
        for line in output.splitlines()
        if "passed:" in line or line.startswith("  FAIL") or "FAILED" in line
    ]
    if not interesting:
        interesting = [line.strip() for line in output.splitlines() if line.strip()][-3:]
    return " | ".join(interesting[-6:])


def main() -> int:
    wanted = set(sys.argv[1:])
    failed = []
    WORK.mkdir(parents=True, exist_ok=True)
    for name, cwd, command in SUITES:
        if wanted and name not in wanted:
            continue
        print(f"=== {name} ...", flush=True)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            output = proc.stdout + proc.stderr
            elapsed = time.monotonic() - started
            print(f"--- {name} ({elapsed:.1f}s, exit={proc.returncode})")
            print(summary_line(output))
            (WORK / f"matrix_{name.replace('/', '_')}.txt").write_text(output, encoding="utf-8")
            if proc.returncode != 0:
                failed.append(name)
        except subprocess.TimeoutExpired:
            print(f"--- {name} TIMED OUT")
            failed.append(name)
    print("=" * 72)
    if failed:
        print(f"FAILED SUITES: {failed}")
        return 1
    print("ALL SUITES GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
