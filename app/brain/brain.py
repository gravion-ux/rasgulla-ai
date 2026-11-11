"""stdio entrypoint for the Rasgulla brain child process.

One JSON command per line on stdin, one JSON result per line on stdout.
Logging goes to stderr so stdout stays pure protocol.
"""

from __future__ import annotations

import json
import sys

from session import BrainSession  # noqa: E402


def main() -> int:
    session = BrainSession()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            out.write(json.dumps({"type": "error", "message": "not JSON"}) + "\n")
            out.flush()
            continue
        result = session.handle(command)
        out.write(json.dumps(result) + "\n")
        out.flush()
        if command.get("command") == "shutdown":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
