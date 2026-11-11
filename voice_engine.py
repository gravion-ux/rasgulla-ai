"""Speaking voice for Rasgulla.

Rasgulla talks with a natural female voice.  When the machine is online the
speech is synthesised by a neural voice engine using Indian female voices
(English-India and Hindi, plus a few regional ones).  When the network is not
available she falls back to the built-in Windows voice so the app never goes
silent.

Nothing here prints provider names: the UI only ever sees the friendly preset
labels in VOICE_PRESETS.
"""

from __future__ import annotations

import asyncio
import io
import queue
import re
import tempfile
import threading
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

# -- optional neural backend -------------------------------------------------
# Imported lazily so the app still starts if the extra package is missing.
try:  # pragma: no cover - depends on an optional dependency
    import aiohttp
    import certifi
    from edge_tts.communicate import (
        connect_id,
        date_to_string,
        get_headers_and_data,
        ssml_headers_plus_data,
    )
    from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
    from edge_tts.drm import DRM

    NEURAL_AVAILABLE = True
except Exception:  # pragma: no cover - offline / not installed
    NEURAL_AVAILABLE = False

try:  # pragma: no cover - optional decoder for the audio stream
    import miniaudio

    DECODER_AVAILABLE = True
except Exception:  # pragma: no cover
    DECODER_AVAILABLE = False

# The natural voice needs both the speech engine and a local decoder.
NATURAL_VOICE_READY = NEURAL_AVAILABLE and DECODER_AVAILABLE

SAMPLE_RATE = 24000
MAX_SPOKEN_CHARS = 900


@dataclass(frozen=True)
class Voice:
    """One speaking preset."""

    preset: str
    label: str
    voice_id: str
    lang: str
    rate: str = "+6%"
    pitch: str = "+4Hz"
    volume: str = "+0%"


VOICES: tuple[Voice, ...] = (
    Voice("auto", "Automatic (Indian)", "", ""),
    Voice("en", "Rasgulla - Indian English", "en-IN-NeerjaNeural", "en-IN"),
    Voice(
        "en_lively",
        "Rasgulla - Indian English (lively)",
        "en-IN-NeerjaExpressiveNeural",
        "en-IN",
        rate="+2%",
        pitch="+8Hz",
    ),
    Voice("hi", "Rasgulla - Hindi", "hi-IN-SwaraNeural", "hi-IN", rate="+4%"),
    Voice("ta", "Rasgulla - Tamil", "ta-IN-PallaviNeural", "ta-IN", rate="+4%"),
    Voice("te", "Rasgulla - Telugu", "te-IN-ShrutiNeural", "te-IN", rate="+4%"),
    Voice("ur", "Rasgulla - Urdu", "ur-IN-GulNeural", "ur-IN", rate="+4%"),
    Voice("mr", "Rasgulla - Marathi", "mr-IN-AarohiNeural", "mr-IN", rate="+4%"),
    Voice("system", "Device voice (offline)", "", ""),
)

VOICE_PRESETS: dict[str, str] = {voice.preset: voice.label for voice in VOICES}
DEFAULT_PRESET = "auto"

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]{0,4}[-*+\u2022][ \t]+", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^[ \t]{0,4}\d+[.)][ \t]+", re.MULTILINE)
_RULE_RE = re.compile(r"^[ \t]*[-=_]{3,}[ \t]*$", re.MULTILINE)
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|json|txt|md|csv|log|bat|cmd|ps1)\b")


# -- text preparation --------------------------------------------------------
def clean_for_speech(text: str) -> str:
    """Turn a written reply into something worth listening to.

    Markdown, code blocks, links and emoji are removed, line breaks become
    sentence pauses, and the result is capped so a long answer never becomes a
    monologue.
    """
    if not text:
        return ""
    cleaned = _CODE_FENCE_RE.sub(" ", text)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _LINK_RE.sub(r"\1", cleaned)
    cleaned = _HEADING_RE.sub("", cleaned)
    cleaned = _BULLET_RE.sub("", cleaned)
    cleaned = _NUMBERED_RE.sub("", cleaned)
    cleaned = _RULE_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("*", "")
    cleaned = _EMOJI_RE.sub("", cleaned)
    cleaned = _URL_RE.sub("link", cleaned)
    cleaned = _PATH_RE.sub("file", cleaned)
    cleaned = cleaned.replace("`", "").replace("|", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s*\n+\s*", ". ", cleaned)
    cleaned = re.sub(r"\.\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if len(cleaned) > MAX_SPOKEN_CHARS:
        head = cleaned[:MAX_SPOKEN_CHARS]
        cut = head.rfind(". ")
        cleaned = (head[:cut] if cut > MAX_SPOKEN_CHARS // 2 else head).rstrip(" ,;:")
        if not cleaned.endswith((".", "!", "?")):
            cleaned += "."
    return cleaned


def voice_for(preset: str) -> Optional[Voice]:
    """Return the preset definition, or None for the offline device voice."""
    for voice in VOICES:
        if voice.preset == preset:
            return voice if voice.voice_id else None
    return None


def pick_voice(text: str, preset: str = DEFAULT_PRESET) -> Optional[Voice]:
    """Choose the voice for a line of text.

    "auto" follows the script: Devanagari goes to the Hindi voice, everything
    else (including Hinglish written in Latin letters) goes to the Indian
    English voice.
    """
    if preset == "system":
        return None
    if preset != DEFAULT_PRESET:
        chosen = voice_for(preset)
        if chosen is not None:
            return chosen
    if _DEVANAGARI_RE.search(text or ""):
        return voice_for("hi")
    return voice_for("en")


# -- neural synthesis --------------------------------------------------------
def _ssml(text: str, voice: Voice) -> str:
    return (
        "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xml:lang='{voice.lang}'>"
        f"<voice name='{voice.voice_id}'>"
        f"<prosody pitch='{voice.pitch}' rate='{voice.rate}' volume='{voice.volume}'>"
        f"{escape(text)}"
        "</prosody></voice></speak>"
    )


async def _synthesize(text: str, voice: Voice) -> bytes:
    if not NEURAL_AVAILABLE:
        raise RuntimeError("neural voice backend is not installed")
    import ssl

    context = ssl.create_default_context(cafile=certifi.where())
    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=45),
    ) as session:
        async with session.ws_connect(
            f"{WSS_URL}&ConnectionId={connect_id()}"
            f"&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
            compress=15,
            headers=DRM.headers_with_muid(WSS_HEADERS),
            ssl=context,
        ) as websocket:
            await websocket.send_str(
                f"X-Timestamp:{date_to_string()}\r\n"
                "Content-Type:application/json; charset=utf-8\r\n"
                "Path:speech.config\r\n\r\n"
                '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                '"sentenceBoundaryEnabled":"false","wordBoundaryEnabled":"false"'
                "},"
                '"outputFormat":"audio-24khz-48kbitrate-mono-mp3"'
                "}}}}\r\n"
            )
            await websocket.send_str(
                ssml_headers_plus_data(
                    connect_id(),
                    date_to_string(),
                    _ssml(text, voice),
                )
            )
            payload = bytearray()
            async for received in websocket:
                if received.type == aiohttp.WSMsgType.BINARY:
                    data = received.data
                    if len(data) < 2:
                        continue
                    header_length = int.from_bytes(data[:2], "big")
                    if header_length > len(data):
                        continue
                    _, chunk = get_headers_and_data(data, header_length)
                    payload.extend(chunk)
                elif received.type == aiohttp.WSMsgType.TEXT:
                    encoded = received.data.encode("utf-8")
                    parameters, _ = get_headers_and_data(
                        encoded, encoded.find(b"\r\n\r\n")
                    )
                    if parameters.get(b"Path") == b"turn.end":
                        break
    if not payload:
        raise RuntimeError("no audio came back")
    return bytes(payload)


def _mp3_to_wav(mp3: bytes) -> bytes:
    """Decode the compressed stream into a plain PCM WAV."""
    import miniaudio

    decoded = miniaudio.decode(
        mp3,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=SAMPLE_RATE,
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(decoded.nchannels)
        handle.setsampwidth(2)
        handle.setframerate(decoded.sample_rate)
        handle.writeframes(decoded.samples.tobytes())
    return buffer.getvalue()


def synthesize_wav(text: str, voice: Voice) -> bytes:
    """Blocking helper: synthesise one line and return WAV bytes."""
    return _mp3_to_wav(asyncio.run(_synthesize(text, voice)))


# -- playback ----------------------------------------------------------------
def play_wav(data: bytes) -> None:
    """Play WAV bytes through the Windows sound engine."""
    import winsound

    path = Path(tempfile.gettempdir()) / f"rasgulla-voice-{uuid.uuid4().hex}.wav"
    path.write_bytes(data)
    try:
        winsound.PlaySound(str(path), winsound.SND_FILENAME)
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def stop_playback() -> None:
    """Cut off whatever is playing right now."""
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def _local_speak(text: str) -> None:
    """Offline fallback using the voices Windows ships with."""
    try:
        import pyttsx3

        engine = pyttsx3.init()
        voices = engine.getProperty("voices") or []
        chosen = None
        for voice in voices:
            name = f"{voice.name} {voice.id}".lower()
            if any(
                marker in name
                for marker in ("swara", "heera", "kalpana", "neerja", "asha", "female")
            ):
                chosen = voice.id
                break
        if chosen is None:
            for voice in voices:
                if "female" in f"{voice.name} {voice.id}".lower():
                    chosen = voice.id
                    break
        if chosen is not None:
            engine.setProperty("voice", chosen)
        engine.setProperty("rate", 180)
        engine.setProperty("volume", 0.95)
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception:
        pass


# -- speech queue ------------------------------------------------------------
_QUEUE: "queue.Queue[tuple[int, str, str]]" = queue.Queue()
_GENERATION = 0
_WORKER: Optional[threading.Thread] = None
_LOCK = threading.Lock()


def _worker() -> None:
    while True:
        generation, text, preset = _QUEUE.get()
        try:
            with _LOCK:
                if generation != _GENERATION:
                    continue
            spoken = clean_for_speech(text)
            if not spoken:
                continue
            voice = pick_voice(spoken, preset)
            if voice is not None and NATURAL_VOICE_READY:
                try:
                    audio = synthesize_wav(spoken, voice)
                    with _LOCK:
                        if generation != _GENERATION:
                            # A newer line arrived while this one was being
                            # synthesised, so this line is dropped instead of
                            # talking over the newer one.
                            continue
                    play_wav(audio)
                    continue
                except Exception:
                    pass
            with _LOCK:
                if generation != _GENERATION:
                    continue
            _local_speak(spoken)
        except Exception:
            pass
        finally:
            _QUEUE.task_done()


def _ensure_worker() -> None:
    global _WORKER
    with _LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker, daemon=True)
            _WORKER.start()


def speak(text: str, preset: str = DEFAULT_PRESET) -> None:
    """Queue a line for speech.  The newest line always wins."""
    global _GENERATION
    if not text:
        return
    with _LOCK:
        _GENERATION += 1
        generation = _GENERATION
    stop_playback()
    _ensure_worker()
    _QUEUE.put((generation, text, preset))


def stop() -> None:
    """Drop anything queued and silence the current line."""
    global _GENERATION
    with _LOCK:
        _GENERATION += 1
    while True:
        try:
            _QUEUE.get_nowait()
            _QUEUE.task_done()
        except queue.Empty:
            break
    stop_playback()


def preset_from_label(label: str) -> str:
    """Map a friendly label shown in the interface back to its preset id."""
    for preset, text in VOICE_PRESETS.items():
        if text == label:
            return preset
    return DEFAULT_PRESET


def engine_status() -> str:
    """Short, branding-free status for the interface."""
    return "Natural voice ready" if NATURAL_VOICE_READY else "Device voice only"


SAMPLE_LINE = "Hi, main Rasgulla. Aaj mood kaisa hai? Batao, kya kaam karna hai."
