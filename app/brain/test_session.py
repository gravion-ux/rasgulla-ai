"""Tests for app/brain/session.py - the headless brain shim.

These tests never call the network.  They check the protocol round trip,
local intent handling, and the reused action guards.
"""

from __future__ import annotations

import contextlib
import json
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import ClassVar

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla")
BRAIN = APP_DIR / "app" / "brain"
for _p in (str(APP_DIR), str(BRAIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from session import BrainSession

PASSED = 0
FAILED: list[str] = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


# -- microphone doubles ------------------------------------------------------
#
# The microphone tests never touch a real device: they push synthetic int16
# buffers through the same seam the live loop uses.

def int16_chunk(amplitude: int, frames: int = 1600) -> bytes:
    """A chunk of constant int16 samples, little endian like the raw stream."""
    return struct.pack(f"<{frames}h", *([amplitude] * frames))


class FakeRecognizer:
    """Stand-in for the Vosk recognizer: hands back canned word results."""

    def __init__(self, results=None, final=""):
        self.results = list(results or [])
        self.final = final
        self.last = ""

    def AcceptWaveform(self, chunk):
        if not self.results:
            return False
        self.last = self.results.pop(0)
        return True

    def Result(self):
        return json.dumps({"text": self.last})

    def FinalResult(self):
        return json.dumps({"text": self.final})


class FakeRawInputStream:
    """Stand-in for sounddevice.RawInputStream: feeds fixed chunks."""

    chunks: ClassVar[list[bytes]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        callback = self.kwargs.get("callback")
        for chunk in self.chunks:
            callback(chunk, len(chunk) // 2, None, None)
        return self

    def __exit__(self, *exc_info):
        return False


def fake_vosk_module(words=None, final_text=""):
    """A stand-in for the vosk module."""
    module = types.ModuleType("vosk")

    class Model:
        def __init__(self, path):
            self.path = path

    class KaldiRecognizer:
        def __init__(self, model, rate):
            self.model = model
            self.rate = rate
            self.words = list(words or [])
            self.last = ""

        def AcceptWaveform(self, chunk):
            if not self.words:
                return False
            self.last = self.words.pop(0)
            return True

        def Result(self):
            return json.dumps({"text": self.last})

        def FinalResult(self):
            return json.dumps({"text": final_text})

    module.Model = Model
    module.KaldiRecognizer = KaldiRecognizer
    return module


def install_fake_mic(chunks, words=None, final_text=""):
    """Put fake sound and vosk modules in place; return the saved originals."""
    FakeRawInputStream.chunks = list(chunks)
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.RawInputStream = FakeRawInputStream
    saved = {name: sys.modules.get(name) for name in ("sounddevice", "vosk")}
    sys.modules["sounddevice"] = sounddevice
    sys.modules["vosk"] = fake_vosk_module(words, final_text)
    return saved


def restore_fake_mic(saved):
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


@contextlib.contextmanager
def fake_models_dir():
    """Point the session at a throwaway folder that holds the voice model."""
    import session as session_module

    saved = session_module.MODELS_DIR
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "vosk-model-small-en-in-0.4").mkdir()
        session_module.MODELS_DIR = Path(tmp)
        try:
            yield
        finally:
            session_module.MODELS_DIR = saved


def run_mic_loop(session, chunks, words=None, final_text="", timeout=15):
    """Run the microphone loop; report whether it closed on its own."""
    saved_modules = install_fake_mic(chunks, words, final_text)
    finished = threading.Event()
    failure: list[str] = []

    def target():
        try:
            with fake_models_dir():
                session._mic_loop()
        except Exception as error:  # noqa: BLE001 - reported to the caller
            failure.append(f"{type(error).__name__}: {error}")
        finally:
            finished.set()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    stopped = finished.wait(timeout)
    if not stopped:
        # Let the loop go so nothing is left running behind the test.
        session._mic_stop.set()
        finished.wait(5)
    restore_fake_mic(saved_modules)
    return stopped, failure


def test_hello():
    session = BrainSession()
    reply = session.handle({"command": "hello"})
    check("hello returns ready", reply.get("type") == "ready", str(reply))
    check("voice_ready present", "voice_ready" in reply, str(reply))
    check("key_found present", "key_found" in reply, str(reply))
    check("no error key", "error" not in reply, str(reply))


def test_local_intent_time():
    session = BrainSession()
    reply = session.handle({"command": "route", "text": "what time is it", "mode": "normal", "speak": False})
    check("local intent handled", reply.get("type") == "plan", str(reply))
    check("reply has time content", "time" in str(reply.get("reply", "")).lower() or "baje" in str(reply.get("reply", "")), str(reply))
    check("route is local", reply.get("route", {}).get("tier") == "local", str(reply))


def test_validate_actions_through_shim():
    from types import MethodType

    from core_app import RasgullaApp as _App

    shim = type("Shim", (), {"action_rejections": []})()
    validator = MethodType(_App.validate_actions, shim)
    safe = MethodType(_App._safe_path, shim)
    shim._safe_path = safe
    actions = [
        {"type": "launch", "app": "notepad"},
        {"type": "open_url", "url": "https://example.com"},
        {"type": "type_text", "text": "hello world"},
        {"type": "key", "key": "enter"},
        {"type": "write_file", "path": "notes.md", "content": "test"},
        {"type": "read_file", "path": "notes.md"},
    ]
    valid = validator(actions)
    check("five actions valid (read_file needs existing file)", len(valid) == 5, f"got {len(valid)}")
    # write_file is index 4 in the valid list (5th item)
    write_actions = [a for a in valid if a["type"] == "write_file"]
    check("write_file path is relative", write_actions and write_actions[0]["path"] == "notes.md", str(valid))


def test_rejects_unsafe_action():
    from types import MethodType

    from core_app import RasgullaApp as _App

    shim = type("Shim", (), {"action_rejections": []})()
    validator = MethodType(_App.validate_actions, shim)
    safe = MethodType(_App._safe_path, shim)
    shim._safe_path = safe
    valid = validator([{"type": "launch", "app": "format_c"}])
    check("unsafe launch rejected", len(valid) == 0, f"got {valid}")


def test_voice_line_split():
    from core_app import voice_line

    reply = "✍️Narrator: scene\n🔓Inquisitor: q?\n💭 thought\n🎭character: this is spoken\n😈🔓Rasgulla Freedom Gauge🔫 [x] (50%)\n1. a\n2. b\n3. c"
    line = voice_line(reply)
    check("voice_line extracts character only", line == "this is spoken", repr(line))


# -- voice resolution --------------------------------------------------------
#
# The default preset has to sound natural: English uses the lively Indian
# English voice, Devanagari still uses Hindi, and an explicitly chosen preset
# always wins over the default.

def test_auto_preset_uses_the_lively_english_voice():
    try:
        from session import resolve_voice_id
    except ImportError as error:
        check("the voice resolver exists", False, str(error))
        return
    english = resolve_voice_id("hello there", "auto")
    check(
        "auto speaks english with the lively voice",
        english == "en-IN-NeerjaExpressiveNeural",
        english,
    )
    hindi = resolve_voice_id("नमस्ते दुनिया", "auto")
    check(
        "auto still speaks devanagari with the hindi voice",
        hindi == "hi-IN-SwaraNeural",
        hindi,
    )
    explicit_en = resolve_voice_id("hello there", "en")
    check(
        "an explicit english preset still wins",
        explicit_en == "en-IN-NeerjaNeural",
        explicit_en,
    )
    explicit_hi = resolve_voice_id("hello there", "hi")
    check(
        "an explicit hindi preset still wins",
        explicit_hi == "hi-IN-SwaraNeural",
        explicit_hi,
    )
    lively = resolve_voice_id("hello there", "en_lively")
    check(
        "the lively preset stays lively",
        lively == "en-IN-NeerjaExpressiveNeural",
        lively,
    )


def test_hello_reports_the_resolved_voice_id():
    session = BrainSession()
    reply = session.handle({"command": "hello"})
    check(
        "hello reports the resolved voice id",
        reply.get("voice_id") == "en-IN-NeerjaExpressiveNeural",
        str(reply),
    )


# -- microphone: level, silence and closing ----------------------------------

def mic_seam(session):
    """The per-chunk seam and its state, or None when they do not exist yet."""
    try:
        from session import MicState
    except ImportError:
        return None, None
    chunk = getattr(session, "_mic_chunk", None)
    if chunk is None:
        return None, None
    return chunk, MicState()


def test_mic_chunk_emits_a_level_event():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    chunk(int16_chunk(4000), FakeRecognizer(), state)
    events = session.drain()
    levels = [event for event in events if event.get("type") == "level"]
    check("one level event was emitted", len(levels) == 1, str(events))
    value = levels[0].get("value", -1.0) if levels else -1.0
    check("the level is a float", isinstance(value, float), repr(value))
    check("the level stays inside 0..1", 0.0 <= value <= 1.0, repr(value))
    check("normal speech sits near the middle", 0.3 <= value <= 0.7, repr(value))


def test_level_events_are_throttled():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    # 20 ms of audio per chunk: ten chunks land inside a fifth of a second.
    for _ in range(10):
        chunk(int16_chunk(1000, frames=320), FakeRecognizer(), state)
    levels = [event for event in session.drain() if event.get("type") == "level"]
    check(
        "the level stream is throttled to about 20 a second",
        2 <= len(levels) <= 5,
        f"got {len(levels)}",
    )


def test_silence_before_speech_keeps_the_mic_open():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    keep_going = True
    for _ in range(40):  # four seconds of quiet
        keep_going = chunk(int16_chunk(0), FakeRecognizer(), state)
        if not keep_going:
            break
    check("a quiet room never stops the mic", keep_going, "the mic stopped before any speech")


def test_silence_after_speech_stops_the_mic():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    recognizer = FakeRecognizer(results=["hello"], final="hello there")
    chunk(int16_chunk(6000), recognizer, state)  # the user speaks
    stopped = False
    for _ in range(30):  # three seconds of quiet
        if not chunk(int16_chunk(0), recognizer, state):
            stopped = True
            break
    check("silence after speech stops the mic", stopped, "the mic kept listening")
    transcripts = [event for event in session.drain() if event.get("type") == "transcript"]
    partials = [event for event in transcripts if event.get("final") is False]
    check("the words heard so far were reported", len(partials) == 1, str(transcripts))


def test_mic_loop_reports_idle_after_a_silence_stop():
    session = BrainSession()
    chunks = [int16_chunk(6000)] + [int16_chunk(0) for _ in range(30)]
    stopped, failure = run_mic_loop(session, chunks, words=["hello"], final_text="hello there")
    check("the mic closes itself after silence", stopped, f"the loop kept running {failure}")
    events = session.drain()
    idle = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "idle"
    ]
    check("the mic reports idle exactly once", len(idle) == 1, str(events))
    levels = [event for event in events if event.get("type") == "level"]
    check(
        "the orb settles back to zero",
        bool(levels) and levels[-1].get("value") == 0.0,
        str(levels),
    )
    finals = [
        event
        for event in events
        if event.get("type") == "transcript" and event.get("final") is True
    ]
    check("the final transcript is sent once", len(finals) == 1, str(events))
    check(
        "the final transcript carries the text",
        bool(finals) and finals[0].get("text") == "hello there",
        str(finals),
    )


def test_mic_loop_reports_idle_after_a_manual_stop():
    session = BrainSession()
    saved_modules = install_fake_mic([int16_chunk(0) for _ in range(20)])
    try:
        with fake_models_dir():
            opened = session.handle({"command": "mic", "on": True})
            check("the mic opens", opened.get("type") == "ok", str(opened))
            time.sleep(0.3)
            closed = session.handle({"command": "mic", "on": False})
            check("the mic closes", closed.get("type") == "ok", str(closed))
    finally:
        restore_fake_mic(saved_modules)
    events = session.drain()
    idle = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "idle"
    ]
    check("the mic reports idle after a manual stop", len(idle) == 1, str(events))
    transcripts = [event for event in events if event.get("type") == "transcript"]
    check("a silent room produces no transcript", not transcripts, str(transcripts))


def test_stopping_an_unstarted_mic_is_safe():
    session = BrainSession()
    first = session.handle({"command": "mic", "on": False})
    check("stopping an unstarted mic is safe", first.get("type") == "ok", str(first))
    second = session.handle({"command": "mic", "on": False})
    check("stopping twice is safe", second.get("type") == "ok", str(second))


# -- the entrypoint pumps queued events to stdout ----------------------------

STUB_SESSION = '''\
"""Stub brain: answers hello and publishes an event a moment later."""

import queue
import threading


class BrainSession:
    def __init__(self):
        self.outbox = queue.Queue()
        timer = threading.Timer(0.3, self._publish)
        timer.daemon = True
        timer.start()

    def _publish(self):
        self.outbox.put({"type": "level", "value": 0.5})

    def handle(self, command):
        return {"type": "ready", "voice_ready": True}

    def drain(self):
        events = []
        while True:
            try:
                events.append(self.outbox.get_nowait())
            except queue.Empty:
                break
        return events
'''


def test_entrypoint_pumps_outbox_events():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        shutil.copyfile(BRAIN / "brain.py", tmp_path / "brain.py")
        (tmp_path / "session.py").write_text(STUB_SESSION, encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(tmp_path / "brain.py")],
            cwd=str(tmp_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lines: queue.Queue = queue.Queue()

        def read_lines():
            for raw in proc.stdout:
                raw = raw.strip()
                if raw:
                    lines.put(json.loads(raw))
            lines.put(None)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        first = None
        second = None
        try:
            proc.stdin.write(json.dumps({"command": "hello", "id": 7}) + "\n")
            proc.stdin.flush()
            first = lines.get(timeout=20)
            second = lines.get(timeout=20)
        except queue.Empty:
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()
        check(
            "the reply line arrives",
            isinstance(first, dict) and first.get("type") == "ready",
            str(first),
        )
        check(
            "the request id comes back",
            isinstance(first, dict) and first.get("id") == 7,
            str(first),
        )
        check(
            "the queued event arrives as its own json line",
            isinstance(second, dict) and second.get("type") == "level",
            str(second),
        )
        check(
            "the queued event keeps its payload",
            isinstance(second, dict) and second.get("value") == 0.5,
            str(second),
        )
        check(
            "the brain exits cleanly",
            proc.returncode == 0,
            f"exit={proc.returncode} stderr={stderr[-300:]}",
        )


# -- the real entry point, launched the way the app launches it ---------------
#
# These run a fresh interpreter with the app directory as the working
# directory and the brain directory as sys.path[0], exactly like
# `python app/brain/brain.py` does.  Nothing in this file may patch sys.path
# for them, so a wrong project root shows up here instead of hiding.

CHILD_CWD = APP_DIR / "app"
CHILD_ENTRY = BRAIN / "brain.py"


def run_child(commands: list[dict]) -> tuple[int, list[dict], str]:
    payload = "".join(json.dumps(command) + "\n" for command in commands)
    result = subprocess.run(
        [sys.executable, str(CHILD_ENTRY)],
        cwd=str(CHILD_CWD),
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    replies = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            replies.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return result.returncode, replies, result.stderr


def test_entrypoint_starts_without_a_sys_path_patch():
    code, replies, stderr = run_child([{"command": "hello"}, {"command": "shutdown"}])
    check("entrypoint exits cleanly", code == 0, f"exit={code} stderr={stderr[-400:]}")
    check("entrypoint imports the project brain", "ModuleNotFoundError" not in stderr, stderr[-400:])
    check("entrypoint answers hello", bool(replies) and replies[0].get("type") == "ready", str(replies))


def test_entrypoint_reports_a_ready_router():
    _code, replies, _stderr = run_child([{"command": "hello"}, {"command": "shutdown"}])
    if not replies:
        check("router is configured", False, "no reply")
        return
    ready = replies[0]
    check("router has no config error", ready.get("config_error") == "", str(ready))
    check("voice is reported ready", ready.get("voice_ready") is True, str(ready))


def test_entrypoint_serves_a_local_request():
    _code, replies, stderr = run_child(
        [
            {"command": "route", "text": "what time is it", "mode": "auto", "speak": False},
            {"command": "shutdown"},
        ]
    )
    plans = [reply for reply in replies if reply.get("type") == "plan"]
    check("local request returns a plan", bool(plans), f"{replies} {stderr[-300:]}")
    if plans:
        check("plan is on the local route", plans[0].get("route", {}).get("tier") == "local", str(plans[0]))


def test_config_files_live_at_the_project_root():
    probe = (
        "import json, os, sys;"
        "sys.path.insert(0, sys.argv[1]);"
        "import session;"
        "print(json.dumps({"
        "'config': [str(session.CONFIG_PATH), os.path.exists(session.CONFIG_PATH)],"
        "'models': [str(session.MODELS_DIR), os.path.isdir(session.MODELS_DIR)],"
        "'usage': [str(session.USAGE_PATH), os.path.exists(session.USAGE_PATH)]"
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(BRAIN)],
        cwd=str(CHILD_CWD),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("config paths are readable", False, f"stdout={result.stdout[-300:]} stderr={result.stderr[-300:]}")
        return
    for name in ("config", "models", "usage"):
        path, exists = data[name]
        check(f"{name} path exists at the project root", bool(exists), f"{path}")
        check(f"{name} path is under the project root", str(APP_DIR) in path, f"{path}")


def main():
    print("Brain shim tests")
    print("=" * 16)
    test_hello()
    test_local_intent_time()
    test_validate_actions_through_shim()
    test_rejects_unsafe_action()
    test_voice_line_split()
    test_auto_preset_uses_the_lively_english_voice()
    test_hello_reports_the_resolved_voice_id()
    test_mic_chunk_emits_a_level_event()
    test_level_events_are_throttled()
    test_silence_before_speech_keeps_the_mic_open()
    test_silence_after_speech_stops_the_mic()
    test_mic_loop_reports_idle_after_a_silence_stop()
    test_mic_loop_reports_idle_after_a_manual_stop()
    test_stopping_an_unstarted_mic_is_safe()
    test_entrypoint_pumps_outbox_events()
    test_entrypoint_starts_without_a_sys_path_patch()
    test_entrypoint_reports_a_ready_router()
    test_entrypoint_serves_a_local_request()
    test_config_files_live_at_the_project_root()
    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
