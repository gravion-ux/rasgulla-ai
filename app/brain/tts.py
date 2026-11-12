"""Word-timed TTS: synthesises one line and returns mp3 bytes plus word offsets.

Uses edge_tts.Communicate with boundary="WordBoundary" so captions land on
the real voice, exactly as verified in the feasibility probes.  Falls back to
voice_engine.synthesize_wav when the neural path is unavailable.
"""

from __future__ import annotations

import asyncio
import base64
import io
import wave
from typing import Any

import edge_tts

from voice_engine import VOICES, synthesize_wav, voice_for


def synthesize(text: str, voice_id: str | None = None) -> dict[str, Any]:
    """Synthesise one spoken line; return mp3 bytes and per-word timings."""
    if not text:
        return {"audio": "", "mime": "audio/mpeg", "words": [], "text": ""}
    voice_id = voice_id or "en-IN-NeerjaExpressiveNeural"
    try:
        audio, words = asyncio.run(_stream(text, voice_id))
    except Exception:  # noqa: BLE001 - fall back to the local engine
        return _fallback(text, voice_id)
    if not audio:
        return _fallback(text, voice_id)
    return {
        "audio": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/mpeg",
        "words": words,
        "text": text,
    }


def _fallback(text: str, voice_id: str) -> dict[str, Any]:
    """Produce a complete WAV file when the neural path is unavailable."""
    voice = _voice_for_id(voice_id)
    if voice is None:
        return {"audio": "", "mime": "audio/wav", "words": [], "text": text}
    try:
        audio = synthesize_wav(text, voice)
    except Exception:  # noqa: BLE001 - no voice at all is still not fatal
        return {"audio": "", "mime": "audio/wav", "words": [], "text": text}
    words = [
        {
            "w": text,
            "start": 0.0,
            "end": round(_wav_seconds(audio), 3),
        }
    ]
    return {
        "audio": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/wav",
        "words": words,
        "text": text,
    }


def _voice_for_id(voice_id: str):
    """The preset that owns this voice id, so its prosody can be reused.

    An id nobody owns still resolves to Indian English: an unknown voice must
    never take the whole speech path down.
    """
    try:
        for voice in VOICES:
            if voice.voice_id and voice.voice_id == voice_id:
                return voice
        fallback = voice_for(voice_id)
    except Exception:  # noqa: BLE001 - an unknown id must not crash the speech
        fallback = None
    return fallback or voice_for("en")


def _wav_seconds(audio: bytes) -> float:
    """Length of a WAV payload in seconds."""
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            rate = handle.getframerate()
            if not rate:
                return 0.0
            return handle.getnframes() / float(rate)
    except Exception:  # noqa: BLE001 - an unreadable header means no timing
        return 0.0


async def _stream(text: str, voice_id: str) -> tuple[bytes, list[dict[str, Any]]]:
    voice = _voice_for_id(voice_id)
    # The prosody belongs to the preset that owns the voice, so the neural path
    # and the local fallback sound the same.
    communicate = edge_tts.Communicate(
        text,
        voice_id,
        rate=voice.rate,
        pitch=voice.pitch,
        volume=voice.volume,
        boundary="WordBoundary",
    )
    audio = bytearray()
    words: list[dict[str, Any]] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "WordBoundary":
            words.append(
                {
                    "w": chunk["text"],
                    "start": round(chunk["offset"] / 1e7, 6),
                    "end": round((chunk["offset"] + chunk["duration"]) / 1e7, 6),
                }
            )
        elif chunk["type"] == "audio":
            audio.extend(chunk["data"])
    return bytes(audio), words
