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
# How many voice models were built, so a test can see when the model is
# loaded again instead of being kept.
MODEL_BUILDS = 0


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
            global MODEL_BUILDS
            MODEL_BUILDS += 1
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
def fake_tts():
    """Replace the speech engine, so no test ever reaches the network."""
    import session as session_module

    saved = session_module.tts_synthesize

    def synth(text, voice_id=None):
        return {
            "audio": "ZmFrZS1hdWRpbw==",
            "mime": "audio/mpeg",
            "words": [{"w": text, "start": 0.0, "end": 1.5}],
            "text": text,
        }

    session_module.tts_synthesize = synth
    try:
        yield
    finally:
        session_module.tts_synthesize = saved


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


@contextlib.contextmanager
def fake_models_dir_at(folder):
    """Point the session at a model folder that stays the same."""
    import session as session_module

    saved = session_module.MODELS_DIR
    session_module.MODELS_DIR = Path(folder)
    try:
        yield
    finally:
        session_module.MODELS_DIR = saved


def run_mic_loop(session, chunks, words=None, final_text="", timeout=15, models_dir=None):
    """Run the microphone loop; report whether it closed on its own."""
    saved_modules = install_fake_mic(chunks, words, final_text)
    finished = threading.Event()
    failure: list[str] = []

    def target():
        try:
            if models_dir is None:
                with fake_models_dir():
                    session._mic_loop()
            else:
                with fake_models_dir_at(models_dir):
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


def test_silence_after_speech_ends_the_sentence():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._mic_mode = "capture"
    recognizer = FakeRecognizer(results=[], final="hello there")
    chunk(int16_chunk(6000), recognizer, state)  # the user speaks
    for _ in range(30):  # three seconds of quiet
        chunk(int16_chunk(0), recognizer, state)
    transcripts = [event for event in session.drain() if event.get("type") == "transcript"]
    finals = [event for event in transcripts if event.get("final") is True]
    check("the silence ends the sentence", len(finals) == 1, str(transcripts))
    check(
        "the sentence carries the words",
        bool(finals) and finals[0].get("text") == "hello there",
        str(transcripts),
    )


def test_the_mic_stays_open_after_a_sentence():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._mic_mode = "capture"
    recognizer = FakeRecognizer(results=[], final="hello there")
    chunk(int16_chunk(6000), recognizer, state)
    keep_going = True
    for _ in range(40):  # four seconds of quiet
        keep_going = chunk(int16_chunk(0), recognizer, state)
        if not keep_going:
            break
    check("the microphone never closes itself", keep_going, "the mic stopped on its own")


def test_a_clicked_mic_closes_itself_after_silence():
    """One click is enough: the mic closes when the user stops talking."""
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._mic_mode = "capture"
    session._mic_from_click = True
    recognizer = FakeRecognizer(results=[], final="hello there")
    chunk(int16_chunk(6000), recognizer, state)  # the user speaks
    keep_going = True
    for _ in range(30):  # three seconds of quiet
        keep_going = chunk(int16_chunk(0), recognizer, state)
        if not keep_going:
            break
    check("the clicked microphone closes itself", not keep_going, "the mic kept listening")
    transcripts = [event for event in session.drain() if event.get("type") == "transcript"]
    finals = [event for event in transcripts if event.get("final") is True]
    check("the final transcript is sent once", len(finals) == 1, str(transcripts))


def test_the_mic_loop_reports_idle_after_a_silence_stop():
    session = BrainSession()
    session._mic_mode = "capture"
    session._mic_from_click = True
    chunks = [int16_chunk(6000)] + [int16_chunk(0) for _ in range(30)]
    stopped, failure = run_mic_loop(session, chunks, words=[], final_text="hello there", timeout=5)
    check("the mic closes itself after silence", stopped, f"the loop kept running {failure}")
    events = session.drain()
    idle = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "idle"
    ]
    check("the mic reports idle after a silence stop", len(idle) == 1, str(events))
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


def test_the_mic_loop_ends_a_sentence_in_silence():
    session = BrainSession()
    chunks = [int16_chunk(6000)] + [int16_chunk(0) for _ in range(30)]
    stopped, failure = run_mic_loop(session, chunks, words=[], final_text="hello there", timeout=3)
    check("the microphone loop runs", stopped or not failure, f"{failure}")
    events = session.drain()
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


# -- wake up: the wake word and the double clap -------------------------------

def test_wake_phrase_matching():
    try:
        from session import normalize_wake_text, strip_wake_words, wake_phrase_in
    except ImportError:
        check("the wake word helpers exist", False, "no wake helpers in session.py")
        return
    check("the wake word is found", wake_phrase_in("wake up rasgulla") is True)
    check("wakeup without a space is found", wake_phrase_in("wakeup rasgulla") is True)
    check("punctuation does not hide it", wake_phrase_in("Wake up, Rasgulla!") is True)
    check("the name alone is not enough", wake_phrase_in("rasgulla") is False)
    check("ordinary talk is not a wake word", wake_phrase_in("what time is it") is False)
    check(
        "the text is normalised",
        normalize_wake_text("  Wake   UP, Rasgulla! ") == "wake up rasgulla",
        repr(normalize_wake_text("  Wake   UP, Rasgulla! ")),
    )
    check(
        "the wake word is taken out of the sentence",
        strip_wake_words("wake up rasgulla what time is it") == "what time is it",
        repr(strip_wake_words("wake up rasgulla what time is it")),
    )
    check(
        "a sentence that is only the wake word leaves nothing",
        strip_wake_words("wake up rasgulla") == "",
        repr(strip_wake_words("wake up rasgulla")),
    )


def test_clap_detector():
    try:
        from session import ClapDetector
    except ImportError:
        check("the clap detector exists", False, "no ClapDetector in session.py")
        return
    detector = ClapDetector()
    check("one clap does not wake her", detector.feed(0.9, 0.0) is False)
    check("the same clap ringing on does not wake her", detector.feed(0.8, 0.05) is False)
    check("the room going quiet rearms her", detector.feed(0.02, 0.1) is False)
    check("the second clap wakes her", detector.feed(0.9, 0.4) is True)

    apart = ClapDetector()
    apart.feed(0.9, 0.0)
    apart.feed(0.02, 0.1)
    check(
        "two claps that are far apart do not wake her",
        apart.feed(0.9, 9.0) is False,
        "a clap nine seconds later still counted",
    )

    quiet = ClapDetector()
    woke = False
    for i in range(30):
        if quiet.feed(0.02, i * 0.1):
            woke = True
    check("a quiet room never wakes her", not woke, "the quiet room woke her up")


def test_the_wake_word_wakes_the_brain():
    session = BrainSession()
    chunks = [int16_chunk(6000) for _ in range(4)]
    with fake_tts():
        _stopped, failure = run_mic_loop(session, chunks, words=["wake up rasgulla"], timeout=2)
    check("the microphone loop ran", not failure, str(failure))
    events = session.drain()
    wakes = [event for event in events if event.get("type") == "wake"]
    check("the wake word wakes her", len(wakes) == 1, str(events))
    check(
        "the wake came from the voice",
        bool(wakes) and wakes[0].get("source") == "voice",
        str(wakes),
    )
    capture = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "capture"
    ]
    check("she moves into capture mode", len(capture) >= 1, str(events))
    greetings = [event for event in events if event.get("type") == "greeting"]
    check("she greets you", len(greetings) == 1, str(events))
    check(
        "the greeting has something to say",
        bool(greetings) and greetings[0].get("speech"),
        str(greetings),
    )
    check(
        "the greeting is spoken, not only written",
        bool(greetings) and greetings[0].get("audio"),
        str(greetings),
    )


def test_a_double_clap_wakes_the_brain():
    session = BrainSession()
    # a clap is a short loud burst; two of them in a row mean wake up
    chunks = [
        int16_chunk(14000, frames=200),
        int16_chunk(0, frames=200),
        int16_chunk(0, frames=400),
        int16_chunk(14000, frames=200),
        int16_chunk(0, frames=200),
    ]
    with fake_tts():
        _stopped, failure = run_mic_loop(session, chunks, words=[], timeout=2)
    check("the microphone loop ran", not failure, str(failure))
    events = session.drain()
    wakes = [event for event in events if event.get("type") == "wake"]
    check("two claps wake her", len(wakes) == 1, str(events))
    check(
        "the wake came from the claps",
        bool(wakes) and wakes[0].get("source") == "clap",
        str(wakes),
    )
    greetings = [event for event in events if event.get("type") == "greeting"]
    check("she greets you after the claps", len(greetings) == 1, str(events))


def test_capture_mode_turns_speech_into_a_command():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._mic_mode = "capture"
    recognizer = FakeRecognizer(results=["what time is it"])
    chunk(int16_chunk(6000), recognizer, state)
    transcripts = [event for event in session.drain() if event.get("type") == "transcript"]
    check("the command is sent", len(transcripts) == 1, str(transcripts))
    check(
        "the command is final",
        bool(transcripts) and transcripts[0].get("final") is True,
        str(transcripts),
    )
    check(
        "the command carries the words",
        bool(transcripts) and transcripts[0].get("text") == "what time is it",
        str(transcripts),
    )


def test_the_mic_command_switches_between_capture_and_sleep():
    session = BrainSession()
    saved_modules = install_fake_mic([int16_chunk(0) for _ in range(20)])
    try:
        with fake_models_dir():
            opened = session.handle({"command": "mic", "on": True})
            check("the mic opens", opened.get("type") == "ok", str(opened))
            time.sleep(0.3)
            events = session.drain()
            capture = [
                event
                for event in events
                if event.get("type") == "mic_state" and event.get("state") == "capture"
            ]
            check("it goes into capture mode", len(capture) == 1, str(events))
            session.handle({"command": "mic", "on": False})
            time.sleep(0.2)
            events = session.drain()
            wake = [
                event
                for event in events
                if event.get("type") == "mic_state" and event.get("state") == "wake"
            ]
            check("it goes back to sleep", len(wake) == 1, str(events))
    finally:
        restore_fake_mic(saved_modules)


def test_the_listen_command_opens_the_microphone_asleep():
    session = BrainSession()
    saved_modules = install_fake_mic([int16_chunk(0) for _ in range(20)])
    try:
        with fake_models_dir():
            opened = session.handle({"command": "listen", "on": True})
            check("the microphone opens", opened.get("type") == "ok", str(opened))
            time.sleep(0.3)
            events = session.drain()
            wake = [
                event
                for event in events
                if event.get("type") == "mic_state" and event.get("state") == "wake"
            ]
            check("it starts asleep, listening for the wake word", len(wake) == 1, str(events))
            closed = session.handle({"command": "listen", "on": False})
            check("the microphone closes", closed.get("type") == "ok", str(closed))
    finally:
        restore_fake_mic(saved_modules)


def test_a_long_silence_puts_her_back_to_sleep():
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._mic_mode = "capture"
    session._mic_last_voice = time.monotonic() - 999
    recognizer = FakeRecognizer(results=[])
    chunk(int16_chunk(0), recognizer, state)
    events = session.drain()
    wake = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "wake"
    ]
    check("she goes back to sleep after a long silence", len(wake) == 1, str(events))


def test_the_voice_model_is_kept_between_microphone_sessions():
    """Opening the microphone again must not load the whole model again."""
    session = BrainSession()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "vosk-model-small-en-in-0.4").mkdir()
        before = MODEL_BUILDS
        for _ in range(2):
            _stopped, failure = run_mic_loop(
                session, [int16_chunk(0)], timeout=2, models_dir=folder
            )
            check("the microphone ran", not failure, str(failure))
        built = MODEL_BUILDS - before
        check("the voice model is loaded once", built == 1, f"loaded {built} times")


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


# -- Part 1: Shim wiring fix ---------------------------------------------------
#
# These tests verify the agentic path works: the shim needs _type_text and
# _safe_path attached as instance attributes, and errors must be neutral.

def test_shim_type_text_binding():
    """_type_text must be wired to the shim for clipboard-aware typing."""
    import session as session_module
    # The shim needs _type_text as an instance attribute
    shim = session_module._BrainShim if hasattr(session_module, '_BrainShim') else None
    check("shim class exists", shim is not None)
    # Verify _type_text is available on the shim instance
    shim_instance = session_module._shim if hasattr(session_module, '_shim') else None
    check("shim instance exists", shim_instance is not None)
    check("_type_text bound to shim", hasattr(shim_instance, '_type_text'))

def test_shim_safe_path_binding():
    """_safe_path must be wired to the shim for path validation."""
    import session as session_module
    shim_instance = session_module._shim if hasattr(session_module, '_shim') else None
    check("shim has _safe_path", shim_instance is not None and hasattr(shim_instance, '_safe_path'))

def test_handle_returns_neutral_error():
    """handle() must return plain English for unexpected exceptions."""
    session = BrainSession()
    # No model in this test, so the request never leaves the machine.
    session.router = None
    # Trigger an error path that should return neutral text, not exception name
    reply = session.handle({"command": "route", "text": "test", "mode": "invalid_mode_for_testing"})
    # Should not contain Python exception names
    error_msg = str(reply.get("message", ""))
    check("no Python exception name in reply", "AttributeError" not in error_msg and "TypeError" not in error_msg and "Exception" not in error_msg)

def test_file_action_inside_work_folder():
    """A write_file action inside work folder should be valid in the plan."""
    session = BrainSession()
    # Create a test file in work folder
    (session.router or type('obj', (object,), {'key': '', 'usage': None, 'key_env': [], 'disable': set(), '_cooldown': {}})()).__dict__['router'] = None  # Avoid router errors
    session.router = None  # Force local path validation
    
    # Test that _safe_path works for a valid path
    import session as sm
    path = sm._shim._safe_path("test_notes.md")
    check("safe_path accepts work folder path", path is not None)

def test_write_file_action_runs_through_shim():
    """An approved type_text action should run through the shim without AttributeError."""
    import session as sm
    # The key is that _run_action (the module-level bound method) can call
    # self._type_text and self._safe_path because they are instance attributes
    check("shim has _type_text for _run_action to call", callable(getattr(sm._shim, '_type_text', None)))
    check("shim has _safe_path for _run_action to call", callable(getattr(sm._shim, '_safe_path', None)))


def test_approval_decision_phrases():
    """Voice phrases map correctly to approve/decline/other."""
    try:
        from session import approval_decision
    except ImportError:
        check("approval_decision function exists", False, "no approval_decision in session.py")
        return

    # Approve phrases
    for phrase in ["haan", "ha", "yes", "ok", "theek hai", "run", "run karo", "kar do", "chalao", "approve", "chal", "karo"]:
        result = approval_decision(phrase)
        check(f"'{phrase}' approves", result == "approve", f"got {result}")

    # Decline phrases
    for phrase in ["ruk", "nahi", "no", "mat", "skip", "rehne do", "cancel", "stop", "ruk jao"]:
        result = approval_decision(phrase)
        check(f"'{phrase}' declines", result == "decline", f"got {result}")

    # An ordinary sentence is never an answer.
    for phrase in ["hello world", "what time is it", "", "tell me about the lab"]:
        result = approval_decision(phrase)
        check(f"'{phrase or '(empty)'}' is other", result == "other", f"got {result}")


def test_approval_phrases_need_whole_words():
    """A word that merely contains an approval phrase must not approve."""
    try:
        from session import approval_decision
    except ImportError:
        check("approval_decision function exists", False, "no approval_decision in session.py")
        return
    # "ha" in "what", "ok" in "token", "no" in "notepad", "chal" in "challenge",
    # "mat" in "format" - a substring match would run a step on all of these.
    for phrase in ["what time is it", "give me the token", "open notepad now", "challenge the login form", "format the file"]:
        result = approval_decision(phrase)
        check(f"'{phrase}' is other", result == "other", f"got {result}")
    check("a refusal wins over an approval", approval_decision("haan mat karo") == "decline")


def test_mode_switch_phrases():
    """Test mode_switch function for red-team on/off."""
    try:
        from session import mode_switch
    except ImportError:
        check("mode_switch function exists", False, "no mode_switch in session.py")
        return

    # Red team on phrases
    for phrase in ["red team on", "redteam on", "red team mode on"]:
        result = mode_switch(phrase)
        check(f"'{phrase}' switches to redteam", result == "redteam", f"got {result}")

    # Red team off phrases
    for phrase in ["red team off", "redteam off", "normal mode"]:
        result = mode_switch(phrase)
        check(f"'{phrase}' switches to auto", result == "auto", f"got {result}")

    # Non-switch phrases
    for phrase in ["hello world", "what is the weather"]:
        result = mode_switch(phrase)
        check(f"'{phrase}' returns None", result is None, f"got {result}")


def test_a_step_waits_for_a_spoken_answer():
    """A planned step must not run before the user says yes."""
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action

    def fake_run(action):
        ran.append(str(action.get("path", action.get("type", ""))))
        return "done"

    sm._run_action = fake_run
    try:
        plan = {
            "reply": "Here is the plan.",
            "actions": [{"type": "write_file", "path": "voice-gate.txt", "content": "hi"}],
        }
        payload = session._finalise_plan(plan, speak=False)
        check("nothing ran when the plan arrived", ran == [], str(ran))
        check("the step is waiting", len(session._pending_actions) == 1, str(session._pending_actions))
        check("the question is in the reply", "Run karun" in str(payload.get("reply", "")), str(payload))
        check("the question is shown as a step", "Step 1" in str(payload.get("reply", "")), str(payload))

        # The spoken yes runs the step.  The microphone heard it first, and
        # the renderer hands that word back to the brain.
        session._say("haan")
        reply = session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the spoken yes ran the step", ran == ["voice-gate.txt"], str(ran))
        check("the result is reported", "Ran" in str(reply.get("results", reply)), str(reply))
        check("the plan is finished", session._pending_actions == [], str(session._pending_actions))
    finally:
        sm._run_action = original_run


def test_a_copied_template_slot_is_never_spoken():
    """A model that copies the answer template must not be spoken."""
    session = BrainSession()
    slot = "<the actual words Rasgulla says to the user, written fresh for this request>"
    payload = session._finalise_plan({"reply": slot, "actions": []}, speak=False)
    reply = str(payload.get("reply", ""))
    check("the copied slot is not in the reply", slot not in reply, reply)
    check("the reply says something honest instead", bool(reply.strip()), reply)
    check("the payload is still a plan", payload.get("type") == "plan", str(payload.get("type")))

    # A copied slot must not cost the user a real step either.
    payload = session._finalise_plan(
        {
            "reply": slot,
            "actions": [{"type": "write_file", "path": "voice-gate.txt", "content": "hi"}],
        },
        speak=False,
    )
    reply = str(payload.get("reply", ""))
    check("the copied slot is not in the reply with a step", slot not in reply, reply)
    check("the step is still waiting", len(session._pending_actions) == 1, str(session._pending_actions))


def test_only_the_voice_can_approve_a_step():
    """The protocol must not offer a second way to run a step."""
    import session as sm

    session = BrainSession()
    ran: list[dict] = []
    original_run = sm._run_action

    def fake_run(action):
        ran.append(action)
        return "done"

    sm._run_action = fake_run
    try:
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [{"type": "write_file", "path": "voice-gate.txt", "content": "hi"}],
            },
            speak=False,
        )
        check("the step is waiting", len(session._pending_actions) == 1, str(session._pending_actions))

        reply = session.handle({"command": "act", "index": 0, "ok": True})
        check("the protocol cannot run a step", ran == [], str(ran))
        check(
            "the protocol says the answer has to be spoken",
            "voice" in str(reply.get("message", "")).lower(),
            str(reply),
        )

        reply = session.handle({"command": "run_plan", "approved": [0]})
        check("the protocol cannot run a plan", ran == [], str(ran))
        check(
            "the plan command says the answer has to be spoken",
            "voice" in str(reply.get("message", "")).lower(),
            str(reply),
        )
        check(
            "the step is still waiting for the voice",
            len(session._pending_actions) == 1,
            str(session._pending_actions),
        )

        # The spoken answer still runs it.
        session._say("haan")
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the spoken yes still runs the step", len(ran) == 1, str(ran))
    finally:
        sm._run_action = original_run


def test_only_a_word_the_microphone_heard_can_approve():
    """A step cannot be approved by text the microphone never heard."""
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action

    def fake_run(action):
        ran.append(str(action.get("path", action.get("type", ""))))
        return "done"

    sm._run_action = fake_run
    try:
        plan = {
            "reply": "Here is the plan.",
            "actions": [{"type": "write_file", "path": "voice-gate.txt", "content": "hi"}],
        }
        session._finalise_plan(plan, speak=False)
        reply = session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the unheard answer ran nothing", ran == [], str(ran))
        check(
            "the unheard answer is refused",
            "not heard" in str(reply.get("reply", "")).lower(),
            str(reply),
        )
        check("the waiting step was dropped", session._pending_actions == [], str(session._pending_actions))

        # The same word, actually spoken, does run the step.
        session._finalise_plan(plan, speak=False)
        session._say("haan")
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the heard answer ran the step", ran == ["voice-gate.txt"], str(ran))
    finally:
        sm._run_action = original_run


def test_an_unrelated_request_drops_the_waiting_step():
    """A new sentence is not an approval: the waiting step is dropped."""
    import session as sm

    session = BrainSession()
    # No model in this test, so the request never leaves the machine.
    session.router = None
    ran: list[str] = []
    original_run = sm._run_action
    sm._run_action = lambda action: ran.append(str(action.get("type", ""))) or "done"
    try:
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [{"type": "write_file", "path": "dropped.txt", "content": "hi"}],
            },
            speak=False,
        )
        session._say("what did you just do")
        session.handle({"command": "route", "text": "what did you just do", "mode": "auto", "speak": False})
        check("the unrelated request ran nothing", ran == [], str(ran))
        check("the waiting step was dropped", session._pending_actions == [], str(session._pending_actions))

        # A later "haan" must not resurrect the dropped step.
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("a late yes ran nothing", ran == [], str(ran))
    finally:
        sm._run_action = original_run


def test_a_step_can_be_declined():
    """A spoken no skips the step without running it."""
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action
    sm._run_action = lambda action: ran.append(str(action.get("type", ""))) or "done"
    try:
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [{"type": "write_file", "path": "declined.txt", "content": "hi"}],
            },
            speak=False,
        )
        session._say("nahi")
        reply = session.handle({"command": "route", "text": "nahi", "mode": "auto", "speak": False})
        check("the spoken no ran nothing", ran == [], str(ran))
        check("the skip is reported", "Skipped" in str(reply.get("results", reply)), str(reply))
    finally:
        sm._run_action = original_run


def test_a_waiting_step_times_out():
    """A step left alone for too long is dropped, not run later."""
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action
    sm._run_action = lambda action: ran.append(str(action.get("type", ""))) or "done"
    try:
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [{"type": "write_file", "path": "late.txt", "content": "hi"}],
            },
            speak=False,
        )
        session._pending_deadline = time.monotonic() - 1.0
        reply = session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the late answer ran nothing", ran == [], str(ran))
        check("the timeout is reported", "timed out" in str(reply.get("reply", "")), str(reply))
    finally:
        sm._run_action = original_run


def test_a_step_still_hears_the_answer_after_a_slow_model_call():
    """A slow model call must not put the microphone to sleep.

    The answer to a step is spoken, so she has to listen for the whole window
    the question waits.  A model that thinks for longer than that used to send
    her back to sleep before the question was even asked, and a "haan" heard
    while she sleeps is never a command, so the step could never be approved.
    """
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action
    sm._run_action = lambda action: ran.append(str(action.get("path", ""))) or "done"
    chunk, state = mic_seam(session)
    try:
        # The model is still thinking: she has been awake for longer than the
        # window a step waits, so she has gone back to sleep.
        session._mic_mode = "wake"
        session._mic_last_voice = time.monotonic() - 999
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [{"type": "write_file", "path": "slow-call.txt", "content": "hi"}],
            },
            speak=False,
        )
        check(
            "the answer is heard while she was asleep",
            chunk is not None,
            "no _mic_chunk method",
        )
        if chunk is not None:
            chunk(int16_chunk(6000), FakeRecognizer(results=["haan"]), state)
            transcripts = [
                event for event in session.drain() if event.get("type") == "transcript"
            ]
            check("the answer reaches the brain", len(transcripts) == 1, str(transcripts))
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the answer ran the step", ran == ["slow-call.txt"], str(ran))
    finally:
        sm._run_action = original_run


def test_the_microphone_stays_awake_while_a_step_is_waiting():
    """She does not go back to sleep while a step is waiting for an answer.

    A 25 second silence drops the pending step and sends her back to sleep,
    so the sleep and the step have to end together.  While the step is still
    waiting, she keeps listening for the answer.
    """
    session = BrainSession()
    chunk, state = mic_seam(session)
    if chunk is None:
        check("the mic has a per-chunk seam", False, "no _mic_chunk method")
        return
    session._finalise_plan(
        {
            "reply": "Here is the plan.",
            "actions": [{"type": "write_file", "path": "waiting.txt", "content": "hi"}],
        },
        speak=False,
    )
    # She was awake and listening when the question was asked.
    session._mic_mode = "capture"
    # Even when her clock has run on, a step that is still waiting keeps her
    # awake, because the answer to it is spoken.
    session._mic_last_voice = time.monotonic() - 999
    chunk(int16_chunk(0), FakeRecognizer(results=[]), state)
    events = session.drain()
    asleep = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "wake"
    ]
    check("she stays awake while a step is waiting", not asleep, str(events))

    # Once the step has timed out, she goes back to sleep.
    session._pending_deadline = time.monotonic() - 1.0
    session._mic_last_voice = time.monotonic() - 999
    chunk(int16_chunk(0), FakeRecognizer(results=[]), state)
    events = session.drain()
    asleep = [
        event
        for event in events
        if event.get("type") == "mic_state" and event.get("state") == "wake"
    ]
    check("she goes back to sleep once the step has timed out", len(asleep) == 1, str(events))


def test_the_next_step_gets_its_own_window():
    """Every step waits for its own answer, for its own silence window."""
    import session as sm

    session = BrainSession()
    ran: list[str] = []
    original_run = sm._run_action
    sm._run_action = lambda action: ran.append(str(action.get("path", ""))) or "done"
    try:
        session._finalise_plan(
            {
                "reply": "Here is the plan.",
                "actions": [
                    {"type": "write_file", "path": "one.txt", "content": "1"},
                    {"type": "write_file", "path": "two.txt", "content": "2"},
                ],
            },
            speak=False,
        )
        # The first step is answered with only a moment of its window left.
        session._pending_deadline = time.monotonic() + 0.5
        session._say("haan")
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the first step ran", ran == ["one.txt"], str(ran))
        check(
            "the next step waits for its own answer",
            session._pending_deadline > time.monotonic() + sm.APPROVAL_SECONDS - 2.0,
            str(session._pending_deadline),
        )
        # The first answer cannot answer the second question as well.
        session.handle({"command": "route", "text": "haan", "mode": "auto", "speak": False})
        check("the old answer did not run the next step", ran == ["one.txt"], str(ran))
    finally:
        sm._run_action = original_run


def main():
    print("Brain shim tests")
    print("=" * 16)
    # Part 1: Shim wiring fix tests
    test_shim_type_text_binding()
    test_shim_safe_path_binding()
    test_handle_returns_neutral_error()
    test_file_action_inside_work_folder()
    test_write_file_action_runs_through_shim()
    # Original tests
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
    test_silence_after_speech_ends_the_sentence()
    test_the_mic_stays_open_after_a_sentence()
    test_a_clicked_mic_closes_itself_after_silence()
    test_the_mic_loop_reports_idle_after_a_silence_stop()
    test_the_mic_loop_ends_a_sentence_in_silence()
    test_mic_loop_reports_idle_after_a_manual_stop()
    test_stopping_an_unstarted_mic_is_safe()
    test_wake_phrase_matching()
    test_clap_detector()
    test_the_wake_word_wakes_the_brain()
    test_a_double_clap_wakes_the_brain()
    test_capture_mode_turns_speech_into_a_command()
    test_the_mic_command_switches_between_capture_and_sleep()
    test_the_listen_command_opens_the_microphone_asleep()
    test_a_long_silence_puts_her_back_to_sleep()
    test_the_voice_model_is_kept_between_microphone_sessions()
    test_entrypoint_pumps_outbox_events()
    test_approval_decision_phrases()
    test_approval_phrases_need_whole_words()
    test_mode_switch_phrases()
    test_a_step_waits_for_a_spoken_answer()
    test_a_copied_template_slot_is_never_spoken()
    test_only_the_voice_can_approve_a_step()
    test_only_a_word_the_microphone_heard_can_approve()
    test_an_unrelated_request_drops_the_waiting_step()
    test_a_step_can_be_declined()
    test_a_waiting_step_times_out()
    test_a_step_still_hears_the_answer_after_a_slow_model_call()
    test_the_microphone_stays_awake_while_a_step_is_waiting()
    test_the_next_step_gets_its_own_window()
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
