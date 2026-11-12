"""Tests for app/brain/tts.py - word-timed speech synthesis.

The neural path is faked so the suite never touches the network.  Two things
matter: the mp3 path must actually run (and be awaited), and the fallback must
hand back a complete WAV file rather than bare PCM frames.
"""

from __future__ import annotations

import base64
import io
import sys
import wave
from pathlib import Path

APP_DIR = Path(r"C:\Users\priya\Documents\Rasgulla")
BRAIN = APP_DIR / "app" / "brain"
for _p in (str(APP_DIR), str(BRAIN)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tts

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


def wav_bytes(seconds: float = 0.25, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


COMMUNICATE_CALLS: list[dict] = []


class FakeCommunicate:
    """Stands in for edge_tts.Communicate: yields boundaries then audio."""

    def __init__(self, text, voice_id, boundary=None, **kwargs):
        self.text = text
        self.voice_id = voice_id
        self.kwargs = kwargs
        COMMUNICATE_CALLS.append(
            {"text": text, "voice_id": voice_id, "boundary": boundary, **kwargs}
        )

    async def stream(self):
        yield {"type": "WordBoundary", "text": "hello", "offset": 0, "duration": 5000000}
        yield {"type": "WordBoundary", "text": "world", "offset": 5000000, "duration": 6000000}
        yield {"type": "audio", "data": b"\x00\x01\x02\x03"}


class BrokenCommunicate:
    """Stands in for a neural backend that cannot be reached."""

    def __init__(self, text, voice_id, boundary=None, **kwargs):
        pass

    async def stream(self):
        raise OSError("network is unreachable")
        yield  # pragma: no cover - never reached, keeps this an async generator


def test_mp3_path_returns_audio_and_word_timings():
    original_communicate = tts.edge_tts.Communicate
    original_wav = tts.synthesize_wav
    tts.edge_tts.Communicate = FakeCommunicate
    tts.synthesize_wav = lambda text, voice: wav_bytes()
    try:
        result = tts.synthesize("hello world", "en-IN-NeerjaNeural")
    finally:
        tts.edge_tts.Communicate = original_communicate
        tts.synthesize_wav = original_wav

    check("mp3 mime is reported", result.get("mime") == "audio/mpeg", str(result.get("mime")))
    decoded = base64.b64decode(result.get("audio", ""))
    check("mp3 bytes are the streamed audio", decoded == b"\x00\x01\x02\x03", repr(decoded))
    words = result.get("words", [])
    check("two word timings", len(words) == 2, str(words))
    if len(words) == 2:
        check("first word text", words[0].get("w") == "hello", str(words[0]))
        check("first word start", abs(words[0].get("start", -1) - 0.0) < 1e-6, str(words[0]))
        check("first word end", abs(words[0].get("end", -1) - 0.5) < 1e-6, str(words[0]))
        check("second word start", abs(words[1].get("start", -1) - 0.5) < 1e-6, str(words[1]))
        check("second word end", abs(words[1].get("end", -1) - 1.1) < 1e-6, str(words[1]))


def test_fallback_returns_a_complete_wav_file():
    original_communicate = tts.edge_tts.Communicate
    original_wav = tts.synthesize_wav
    seen: list[object] = []

    def fake_wav(text, voice):
        seen.append(voice)
        return wav_bytes()

    tts.edge_tts.Communicate = BrokenCommunicate
    tts.synthesize_wav = fake_wav
    try:
        result = tts.synthesize("hello world", "en-IN-NeerjaNeural")
    finally:
        tts.edge_tts.Communicate = original_communicate
        tts.synthesize_wav = original_wav

    check("fallback mime is wav", result.get("mime") == "audio/wav", str(result.get("mime")))
    decoded = base64.b64decode(result.get("audio", ""))
    check("fallback audio starts with a RIFF header", decoded[:4] == b"RIFF", repr(decoded[:12]))
    check("fallback audio carries a WAVE tag", decoded[8:12] == b"WAVE", repr(decoded[:12]))
    check("fallback receives a voice", len(seen) == 1 and seen[0] is not None, str(seen))
    if seen and seen[0] is not None:
        check(
            "fallback voice matches the requested id",
            getattr(seen[0], "voice_id", "") == "en-IN-NeerjaNeural",
            str(getattr(seen[0], "voice_id", "")),
        )
    words = result.get("words", [])
    check("fallback still reports one caption window", len(words) == 1, str(words))


def test_empty_text_is_silent():
    result = tts.synthesize("", "en-IN-NeerjaNeural")
    check("empty text has no audio", result.get("audio") == "", str(result))
    check("empty text has no words", result.get("words") == [], str(result))


def test_neural_path_sends_the_preset_prosody():
    original_communicate = tts.edge_tts.Communicate
    original_wav = tts.synthesize_wav
    COMMUNICATE_CALLS.clear()
    tts.edge_tts.Communicate = FakeCommunicate
    tts.synthesize_wav = lambda text, voice: wav_bytes()
    try:
        tts.synthesize("hello world", "en-IN-NeerjaExpressiveNeural")
    finally:
        tts.edge_tts.Communicate = original_communicate
        tts.synthesize_wav = original_wav

    check("one speech request was made", len(COMMUNICATE_CALLS) == 1, str(COMMUNICATE_CALLS))
    call = COMMUNICATE_CALLS[0] if COMMUNICATE_CALLS else {}
    check("the requested voice is used", call.get("voice_id") == "en-IN-NeerjaExpressiveNeural", str(call))
    check("the lively rate is passed on", call.get("rate") == "+2%", str(call))
    check("the lively pitch is passed on", call.get("pitch") == "+8Hz", str(call))
    check("the volume is passed on", call.get("volume") == "+0%", str(call))
    check("word boundaries are still asked for", call.get("boundary") == "WordBoundary", str(call))


def test_prosody_follows_the_preset_that_owns_the_voice():
    expected = {
        "en-IN-NeerjaNeural": ("+6%", "+4Hz"),
        "en-IN-NeerjaExpressiveNeural": ("+2%", "+8Hz"),
        "hi-IN-SwaraNeural": ("+4%", "+4Hz"),
    }
    original_communicate = tts.edge_tts.Communicate
    original_wav = tts.synthesize_wav
    tts.edge_tts.Communicate = FakeCommunicate
    tts.synthesize_wav = lambda text, voice: wav_bytes()
    try:
        for voice_id, (rate, pitch) in expected.items():
            COMMUNICATE_CALLS.clear()
            tts.synthesize("hello world", voice_id)
            call = COMMUNICATE_CALLS[0] if COMMUNICATE_CALLS else {}
            check(
                f"{voice_id} keeps its own rate",
                call.get("rate") == rate,
                str(call),
            )
            check(
                f"{voice_id} keeps its own pitch",
                call.get("pitch") == pitch,
                str(call),
            )
    finally:
        tts.edge_tts.Communicate = original_communicate
        tts.synthesize_wav = original_wav


def test_fallback_uses_the_same_prosody_as_the_neural_path():
    original_communicate = tts.edge_tts.Communicate
    original_wav = tts.synthesize_wav
    seen: list[object] = []

    def fake_wav(text, voice):
        seen.append(voice)
        return wav_bytes()

    tts.edge_tts.Communicate = BrokenCommunicate
    tts.synthesize_wav = fake_wav
    try:
        tts.synthesize("hello world", "en-IN-NeerjaExpressiveNeural")
    finally:
        tts.edge_tts.Communicate = original_communicate
        tts.synthesize_wav = original_wav

    check("the fallback got a voice", len(seen) == 1 and seen[0] is not None, str(seen))
    if seen and seen[0] is not None:
        check(
            "the fallback uses the same rate",
            getattr(seen[0], "rate", "") == "+2%",
            str(getattr(seen[0], "rate", "")),
        )
        check(
            "the fallback uses the same pitch",
            getattr(seen[0], "pitch", "") == "+8Hz",
            str(getattr(seen[0], "pitch", "")),
        )
        check(
            "the fallback uses the same voice",
            getattr(seen[0], "voice_id", "") == "en-IN-NeerjaExpressiveNeural",
            str(getattr(seen[0], "voice_id", "")),
        )


def test_unknown_voice_id_resolves_without_crashing():
    try:
        voice = tts._voice_for_id("no-such-voice")
    except Exception as error:  # noqa: BLE001 - the point of the test
        check("an unknown voice id does not crash", False, f"{type(error).__name__}: {error}")
        return
    check("an unknown voice id still resolves", voice is not None, str(voice))
    check(
        "an unknown voice id falls back to indian english",
        getattr(voice, "voice_id", "") == "en-IN-NeerjaNeural",
        str(getattr(voice, "voice_id", "")),
    )


def main():
    print("Brain TTS tests")
    print("=" * 16)
    test_mp3_path_returns_audio_and_word_timings()
    test_fallback_returns_a_complete_wav_file()
    test_empty_text_is_silent()
    test_neural_path_sends_the_preset_prosody()
    test_prosody_follows_the_preset_that_owns_the_voice()
    test_fallback_uses_the_same_prosody_as_the_neural_path()
    test_unknown_voice_id_resolves_without_crashing()
    print("=" * 16)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
