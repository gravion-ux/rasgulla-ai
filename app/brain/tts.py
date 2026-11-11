"""Word-timed TTS: synthesises one line and returns mp3 bytes plus word offsets.

Uses edge_tts.Communicate with boundary="WordBoundary" so captions land on
the real voice, exactly as verified in the feasibility probes.  Falls back to
voice_engine.synthesize_wav when the neural path is unavailable.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import edge_tts
from voice_engine import synthesize_wav, voice_for


def synthesize(text: str, voice_id: str | None = None) -> dict[str, Any]:
    """Synthesise one spoken line; return mp3 bytes and per-word timings."""
    if not text:
        return {"audio": "", "mime": "audio/mpeg", "words": [], "text": ""}
    voice_id = voice_id or "en-IN-NeerjaNeural"
    try:
        audio, words = _stream(text, voice_id)
    except Exception:  # noqa: BLE001 - fall back to the local engine
        voice = voice_for(voice_id) if voice_id else None
        audio = synthesize_wav(text, voice)
        buffer = io.BytesIO(audio)
        import wave

        with wave.open(buffer, "rb") as handle:
            frames = handle.readframes(handle.getnframes())
        words = [
            {
                "w": text,
                "start": 0.0,
                "end": round(len(frames) / (24000 * 2), 3),
            }
        ]
        return {
            "audio": base64.b64encode(frames).decode("ascii"),
            "mime": "audio/wav",
            "words": words,
            "text": text,
        }
    return {
        "audio": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/mpeg",
        "words": words,
        "text": text,
    }


async def _stream(text: str, voice_id: str) -> tuple[bytes, list[dict[str, Any]]]:
    communicate = edge_tts.Communicate(text, voice_id, boundary="WordBoundary")
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
