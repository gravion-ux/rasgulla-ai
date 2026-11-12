"""stdio entrypoint for the Rasgulla brain child process.

One JSON command per line on stdin, one JSON result per line on stdout.
Unsolicited events (microphone transcripts, levels, the microphone closing)
are pumped to stdout as their own JSON lines by a background thread, so the
shell sees them as they happen.  Logging goes to stderr so stdout stays pure
protocol.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Any, TextIO

from session import BrainSession

POLL_SECONDS = 0.05


def _writer(stream: TextIO):
    """Return a function that writes one JSON line, safely from any thread.

    The command loop and the pump thread share stdout, so the writes are
    serialised: two lines can never interleave.
    """
    lock = threading.Lock()

    def write(payload: dict[str, Any]) -> None:
        line = json.dumps(payload)
        with lock:
            stream.write(line + "\n")
            stream.flush()

    return write


def _pump(session: BrainSession, write, stop_event: threading.Event) -> None:
    """Forward every queued event to stdout until the stop event is set."""
    while not stop_event.is_set():
        for event in session.drain():
            write(event)
        stop_event.wait(POLL_SECONDS)


def main() -> int:
    session = BrainSession()
    write = _writer(sys.stdout)
    stop_event = threading.Event()
    pump = threading.Thread(
        target=_pump,
        args=(session, write, stop_event),
        daemon=True,
    )
    pump.start()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
            except json.JSONDecodeError:
                write({"type": "error", "message": "not JSON"})
                continue
            result = session.handle(command)
            # The id has to come back, otherwise the shell can never match this
            # reply to the request that asked for it.
            if "id" in command:
                result["id"] = command["id"]
            write(result)
            if command.get("command") == "shutdown":
                break
    finally:
        stop_event.set()
        # The pump is a daemon, so joining it briefly is enough to let it
        # finish the line it was writing without holding up the exit.
        pump.join(timeout=1.0)
        # Nothing may be lost: whatever is still queued goes out now.
        for event in session.drain():
            write(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
