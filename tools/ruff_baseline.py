"""Compare the lint findings against the HEAD baseline.

The project already carries a lot of ruff findings, so what matters is that
the changes in the working tree did not add any new ones.

Usage:  ruff_baseline.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
BASE = WORK / "headbase"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

FILES = [
    "core_router.py",
    "core_app.py",
    "persona_catalogue.py",
    "app/brain/session.py",
    "app/brain/test_session.py",
    "test_rasgulla.py",
]


def extract_head() -> None:
    for name in FILES:
        target = BASE / name
        target.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{name}"],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            target.write_bytes(proc.stdout)
        else:
            # A new file: there is no baseline for it.
            target.write_bytes(b"")


def findings(root: Path, names: list[str]) -> Counter:
    counter: Counter = Counter()
    for name in names:
        path = root / name
        if not path.exists() or path.stat().st_size == 0:
            continue
        proc = subprocess.run(
            [PY, "-m", "ruff", "check", "--output-format=json", str(path)],
            capture_output=True,
            text=True,
            cwd=str(root),
            check=False,
        )
        try:
            rows = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            continue
        for row in rows:
            code = row.get("code") or "?"
            counter[f"{name}:{code}"] += 1
    return counter


def main() -> int:
    extract_head()
    before = findings(BASE, FILES)
    after = findings(ROOT, FILES)

    print("HEAD baseline findings")
    for key, count in sorted(before.items()):
        print(f"  {key} x{count}")
    print()
    print("Working tree findings")
    for key, count in sorted(after.items()):
        print(f"  {key} x{count}")
    print()

    new = after - before
    fixed = before - after
    if not new:
        print("NO NEW FINDINGS")
    else:
        print("NEW FINDINGS:")
        for key, count in sorted(new.items()):
            print(f"  {key} x{count}")
    if fixed:
        print("GONE:")
        for key, count in sorted(fixed.items()):
            print(f"  {key} x{count}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
