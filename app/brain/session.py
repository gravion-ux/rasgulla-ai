"""Headless shim: bridges the stdio protocol to the real Rasgulla brain.

Reuses core_router, core_app (persona, guards, voice_line) and voice_engine
verbatim.  No Tk window is created; only pure functions and classes are
instantiated, so this module imports cleanly without a display.

The bound guards (_safe_path, validate_actions, describe_action, run_action)
run against a tiny shim instance that carries the instance state they need.
Everything else is module-level in core_app, so binding works unchanged.
"""

from __future__ import annotations

import array
import ctypes
import json
import math
import queue
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import MethodType
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
BRAIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
for _p in (str(PROJECT_ROOT), str(BRAIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pyautogui

from persona_catalogue import (
    PersonaError,
    active_persona_id,
    catalogue_summaries,
    persona_summary,
    resolve_persona,
    set_active_persona,
)
from core_app import (
    WORK_DIR,
    caption_line,
    freedom_gauge,
    local_intent,
    parse_json_plan,
    voice_line,
)
from core_app import RasgullaApp as _RasgullaApp
from core_router import (
    ModelRouter,
    RouterError,
    TaskClassifier,
    UsageTracker,
    is_template_slot,
    read_api_key,
)
from skill_catalogue import SkillMatch, match_skill, skill_summary
from voice_engine import (
    clean_for_speech,
    pick_voice,
    stop,
    voice_for,
)

try:  # tts lives next door in app/brain
    from tts import synthesize as tts_synthesize

    TTS_AVAILABLE = True
except Exception:  # noqa: BLE001 - TTS is optional at import time
    TTS_AVAILABLE = False
    tts_synthesize = None

MODELS_DIR = PROJECT_ROOT / "models"
CONFIG_PATH = PROJECT_ROOT / "models.json"
USAGE_PATH = PROJECT_ROOT / "usage.json"

# The plain Indian English voice.  It is the fallback whenever a preset has no
# neural voice of its own, and the voice `auto` replaces with the lively one.
PLAIN_ENGLISH_VOICE_ID = "en-IN-NeerjaNeural"

# -- microphone tuning --------------------------------------------------------
#
# The microphone runs at 16 kHz.  Levels are the RMS of a chunk normalised to
# 0..1, so ordinary speech sits near the middle of the range instead of
# pinning the orb at either end.
MIC_SAMPLE_RATE = 16000
MIC_BLOCK_FRAMES = 1600
LEVEL_INTERVAL = 0.05  # at most twenty level events a second
SILENCE_SECONDS = 1.35  # how long the room has to stay quiet before we stop
SPEECH_LEVEL = 0.10  # above this the room counts as somebody talking
SILENCE_LEVEL = 0.03  # below this the room counts as quiet
FULL_SCALE_RMS = 8000.0  # the int16 RMS that fills the orb

# -- wake up ------------------------------------------------------------------
#
# Rasgulla sleeps with the microphone open.  In `wake` mode she only listens
# for her name or for two claps; in `capture` mode every word is a command.

WAKE_PHRASES = (
    "wake up rasgulla",
    "hey rasgulla",
    "hello rasgulla",
    "ok rasgulla",
    "wake up",
    "wakeup",
)

# Mode switch phrases for red-team agentic mode
REDTEAM_ON_PHRASES = ("red team on", "redteam on", "red team mode on")
REDTEAM_OFF_PHRASES = ("red team off", "redteam off", "normal mode")

# How long a planned step waits for a spoken answer before it is dropped.
APPROVAL_SECONDS = 25.0

# Words that may be taken off the front of a sentence before it becomes a
# command.  The bare name belongs here even though it never wakes her up: it
# is only removed from something she is already listening to.
STRIP_PHRASES = (
    "wake up rasgulla",
    "hello rasgulla",
    "hey rasgulla",
    "ok rasgulla",
    "rasgulla",
    "wake up",
    "wakeup",
)

GREETING = "Awaake. Bol, kya karna hai?"

SLEEP_SECONDS = 25.0  # how long she stays awake before she goes back to sleep

CLAP_WINDOW = 1.2  # two claps closer together than this mean wake up
CLAP_MINIMUM = 0.25  # below this the room is quiet, not clapping
CLAP_RATIO = 2.5  # a clap has to be this much louder than the room
CLAP_REARM = 0.5  # the quiet has to fall under this part of the minimum

# The voice model is heavy and slow to load, so it is kept between
# microphone sessions.  Opening the microphone again is then instant.
_MODEL_LOCK = threading.Lock()
_VOSK_MODEL: Any = None
_VOSK_MODEL_PATH: Path | None = None


def _load_vosk_model(path: Path) -> Any:
    """The voice model, loaded once and kept for the life of the session."""
    global _VOSK_MODEL, _VOSK_MODEL_PATH
    with _MODEL_LOCK:
        if _VOSK_MODEL is not None and _VOSK_MODEL_PATH == path:
            return _VOSK_MODEL
        from vosk import Model

        model = Model(str(path))
        _VOSK_MODEL = model
        _VOSK_MODEL_PATH = path
        return model


def normalize_wake_text(text: str) -> str:
    """Lower case, drop the punctuation and squeeze the spaces."""
    cleaned = "".join(char if char.isalnum() else " " for char in str(text).lower())
    return " ".join(cleaned.split())


def _phrase_pattern(phrase: str) -> str:
    """A pattern that finds the phrase whatever spacing or punctuation is used."""
    gap = r"[^\w]*"
    return r"\b" + gap.join(re.escape(word) for word in phrase.split()) + r"\b"


_STRIP_PATTERNS = tuple(
    re.compile(_phrase_pattern(phrase), re.IGNORECASE)
    for phrase in sorted(STRIP_PHRASES, key=len, reverse=True)
)


def wake_phrase_in(text: str) -> bool:
    """True when the sentence holds the wake word."""
    normalized = normalize_wake_text(text)
    if not normalized:
        return False
    padded = f" {normalized} "
    return any(f" {phrase} " in padded for phrase in WAKE_PHRASES)


def strip_wake_words(text: str) -> str:
    """Take the wake word off the front of the sentence, if it is there."""
    remainder = str(text).strip()
    changed = True
    while changed:
        changed = False
        for pattern in _STRIP_PATTERNS:
            match = pattern.match(remainder)
            if match is None:
                continue
            rest = remainder[match.end() :]
            if rest and rest[0].isalnum():
                # The phrase only matched half a word, so it is not a phrase.
                continue
            remainder = rest.lstrip(" \t,.;:!?-")
            changed = True
            break
    return remainder.strip()


def mode_switch(text: str) -> str | None:
    """Return 'auto' or 'redteam' when a mode switch phrase is detected, else None."""
    normalized = text.lower()
    if any(phrase in normalized for phrase in REDTEAM_OFF_PHRASES):
        return "auto"
    if any(phrase in normalized for phrase in REDTEAM_ON_PHRASES):
        return "redteam"
    return None


def persona_switch(text: str) -> str | None:
    """Return the personality id a sentence is asking for, else None.

    Only a phrase that asks for a change may change her: a bare name inside an
    ordinary sentence is just a name.
    """
    return resolve_persona(text)


# Voice approval phrases for agentic action execution.
#
# The phrases are matched on word boundaries, never as bare substrings: "ha"
# lives inside "what", "ok" inside "token", "no" inside "notepad" and "chal"
# inside "challenge".  A substring match would let an ordinary sentence
# approve a pending step, which is exactly what the approval gate exists to
# prevent.
APPROVE_PHRASES = ("haan", "ha", "yes", "ok", "okay", "theek hai", "theek", "run", "run karo", "kar do", "chalao", "approve", "approved", "chal", "chalo", "karo", "ji")
DECLINE_PHRASES = ("ruk", "ruk jao", "nahi", "no", "mat", "mat karo", "skip", "rehne do", "cancel", "stop", "band karo")


def _phrase_matcher(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """One pattern that finds any of the phrases on word boundaries."""
    alternatives = "|".join(re.escape(phrase) for phrase in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternatives})\b", re.IGNORECASE)


_APPROVE_RE = _phrase_matcher(APPROVE_PHRASES)
_DECLINE_RE = _phrase_matcher(DECLINE_PHRASES)


def approval_decision(text: str) -> str:
    """Return 'approve', 'decline', or 'other' based on a spoken phrase.

    An approval only counts when the sentence actually carries the phrase on
    its own words, so an unrelated request never runs a pending step.
    """
    normalized = str(text or "").strip()
    if not normalized:
        return "other"
    # The decline wins a tie: "haan mat karo" is a refusal, not an approval.
    if _DECLINE_RE.search(normalized):
        return "decline"
    if _APPROVE_RE.search(normalized):
        return "approve"
    return "other"


class ClapDetector:
    """Counts claps.  Two claps in a row mean wake up.

    A clap is a short loud burst.  The detector keeps track of the noise
    floor of the room, so a clap has to be much louder than the room itself.
    After a clap it waits for the room to go quiet again, so that one clap is
    never counted twice.
    """

    def __init__(
        self,
        minimum: float = CLAP_MINIMUM,
        ratio: float = CLAP_RATIO,
        window: float = CLAP_WINDOW,
        rearm: float = CLAP_REARM,
    ) -> None:
        self.minimum = minimum
        self.ratio = ratio
        self.window = window
        self.rearm = rearm
        self.floor = 0.05
        self.armed = True
        self.last_clap: float | None = None

    def feed(self, level: float, now: float) -> bool:
        """Feed one level in; True when two claps landed close together."""
        if level < self.minimum * self.rearm:
            # The room went quiet: rearm and track the noise floor.
            self.armed = True
            self.floor += 0.05 * (level - self.floor)
            return False
        if level < self.minimum or not self.armed:
            return False
        if level < self.floor * self.ratio:
            return False
        self.armed = False
        if self.last_clap is not None and now - self.last_clap <= self.window:
            self.last_clap = None
            return True
        self.last_clap = now
        return False


class MicState:
    """Bookkeeping for one open microphone session.

    The clock counts the audio that came through the microphone rather than
    wall time, so the silence timer measures real silence.
    """

    def __init__(self) -> None:
        self.clock = 0.0
        self.level_count = 0
        self.last_level_at = 0.0
        self.speech_seen = False
        self.silence_since: float | None = None
        self.final_sent = False


def _chunk_level(chunk: bytes) -> float:
    """The RMS of an int16 chunk, normalised to 0..1 for the orb."""
    frames = len(chunk) // 2
    if frames <= 0:
        return 0.0
    samples = array.array("h")
    samples.frombytes(chunk[: frames * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    total = 0
    for value in samples:
        total += value * value
    return min(1.0, math.sqrt(total / frames) / FULL_SCALE_RMS)


def resolve_voice_id(text: str, preset: str = "auto") -> str:
    """The voice id that should speak this line.

    An explicitly chosen preset always wins.  The default `auto` preset speaks
    English with the lively Indian English voice and Devanagari with the Hindi
    voice, so the orb sounds natural without any configuration.
    """
    chosen = pick_voice(text, preset)
    if chosen is None:
        return PLAIN_ENGLISH_VOICE_ID
    if voice_for(preset) is None and chosen.voice_id == PLAIN_ENGLISH_VOICE_ID:
        lively = voice_for("en_lively")
        if lively is not None:
            return lively.voice_id
    return chosen.voice_id


class _BrainShim:
    """Minimal object carrying the instance state the bound guards need."""

    def __init__(self) -> None:
        self.action_rejections: list[str] = []


_shim = _BrainShim()
_safe_path = MethodType(_RasgullaApp._safe_path, _shim)
_validate_actions = MethodType(_RasgullaApp.validate_actions, _shim)
_describe_action = MethodType(_RasgullaApp.describe_action, _shim)
_run_action = MethodType(_RasgullaApp.run_action, _shim)


class _Clipboard:
    """Win32 clipboard for pasting non-ASCII text, mirroring core_app._type_text."""

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._open = self._user32.OpenClipboard
        self._open.restype = wintypes.BOOL
        self._open.argtypes = [wintypes.HWND]
        self._empty = self._user32.EmptyClipboard
        self._empty.restype = wintypes.BOOL
        self._empty.argtypes = []
        self._close = self._user32.CloseClipboard
        self._close.restype = wintypes.BOOL
        self._close.argtypes = []
        self._set = self._user32.SetClipboardData
        self._set.restype = wintypes.HANDLE
        self._set.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self._get = self._user32.GetClipboardData
        self._get.restype = wintypes.HANDLE
        self._get.argtypes = [wintypes.UINT]
        self._alloc = self._kernel32.GlobalAlloc
        self._alloc.restype = wintypes.HGLOBAL
        self._alloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self._lock = self._kernel32.GlobalLock
        self._lock.restype = ctypes.c_void_p
        self._lock.argtypes = [wintypes.HGLOBAL]
        self._unlock = self._kernel32.GlobalUnlock
        self._unlock.restype = wintypes.BOOL
        self._unlock.argtypes = [wintypes.HGLOBAL]
        self._copy = self._kernel32.lstrcpyW
        self._copy.restype = ctypes.c_void_p
        self._copy.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]

    def write(self, text: str) -> None:
        if not self._open(None):
            return
        try:
            self._empty()
            handle = self._alloc(self.GMEM_MOVEABLE, len(text.encode("utf-16-le")) + 2)
            if not handle:
                return
            locked = self._lock(handle)
            if locked:
                self._copy(locked, text)
                self._unlock(handle)
                self._set(self.CF_UNICODETEXT, handle)
        finally:
            self._close()

    def read(self) -> str | None:
        if not self._open(None):
            return None
        try:
            handle = self._get(self.CF_UNICODETEXT)
            if not handle:
                return None
            locked = self._lock(handle)
            if not locked:
                return None
            try:
                return ctypes.c_wchar_p(locked).value
            finally:
                self._unlock(handle)
        finally:
            self._close()


_clipboard = _Clipboard()


def _type_text(text: str) -> None:
    """Type text exactly like core_app._type_text."""
    if text.isascii():
        pyautogui.write(text, interval=0.015)
        return
    previous = _clipboard.read()
    _clipboard.write(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    if previous is not None:
        _clipboard.write(previous)


# Wire instance attributes that the bound methods access via self.
# _validate_actions calls self._safe_path(), _run_action calls self._type_text().
_shim._safe_path = _safe_path
_shim._type_text = _type_text

class BrainSession:
    """One stdio session: configures the brain, runs plans, validates actions."""

    def __init__(self) -> None:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        self.outbox: queue.Queue[dict[str, Any]] = queue.Queue()
        self.router: ModelRouter | None = None
        self.config_error = ""
        self.key = ""
        self.key_env = ["RASGULLA_API_KEY"]
        self.usage = UsageTracker(USAGE_PATH)
        self.classifier = TaskClassifier()
        self.history: list[dict[str, str]] = []
        self.action_results: list[str] = []
        self.turn_count = 0
        self.current_actions: list[dict[str, Any]] = []
        self.mode = "auto"
        self.voice_preset = "auto"
        self.speak_replies = False
        self._mic_stop = threading.Event()
        self._mic_thread: threading.Thread | None = None
        # The microphone is never really shut while she sleeps: `wake` mode
        # waits for the wake word, `capture` mode takes every word as a
        # command.
        self._mic_mode = "wake"
        self._mic_listen_wanted = False
        self._mic_last_voice: float | None = None
        self._mic_busy_until = 0.0
        # True while the orb was clicked, so the microphone closes itself once
        # the user stops talking.  The always on listener keeps it open.
        self._mic_from_click = False
        self._claps = ClapDetector()

        # Voice approval state for agentic action execution
        self._pending_actions: list[dict[str, Any]] = []
        self._pending_index: int = 0
        self._pending_deadline: float = 0.0
        # The words the microphone really heard.  Only one of these may approve
        # a step, so the protocol on its own can never run an action.
        self._heard: list[str] = []

        # The playbook that fits this request.  It is matched offline, so it
        # costs no network call, and it is carried on the session so the
        # prompt, the payload and the plan all talk about the same skill.
        self._active_skill: SkillMatch | None = None

        try:
            self.router = ModelRouter(CONFIG_PATH, "", self.usage)
        except RouterError as error:
            self.config_error = str(error)
        if self.router is not None:
            self.key_env = list(self.router.key_env)
        self.key = read_api_key(self.key_env)
        if self.router is not None:
            self.router.key = self.key

    # -- protocol ----------------------------------------------------------

    def handle(self, command: dict[str, Any]) -> dict[str, Any]:
        kind = str(command.get("command", ""))
        handlers = {
            "hello": self._cmd_hello,
            "shutdown": self._cmd_shutdown,
            "configure": self._cmd_configure,
            "set_key": self._cmd_set_key,
            "route": self._cmd_route,
            "act": self._cmd_act,
            "run_plan": self._cmd_run_plan,
            "cancel": self._cmd_cancel,
            "mic": self._cmd_mic,
            "listen": self._cmd_listen,
            "wake": self._cmd_wake,
            "usage": self._cmd_usage,
            "persona": self._cmd_persona,
        }
        handler = handlers.get(kind)
        if handler is None:
            return {"type": "error", "message": f"unknown command {kind!r}"}
        try:
            return handler(command)
        except Exception:  # noqa: BLE001 - protocol boundary: never crash the session
            return {"type": "error", "message": "that step could not be prepared, so nothing ran."}

    def drain(self) -> list[dict[str, Any]]:
        """Return all pending async events (mic transcripts, levels)."""
        events: list[dict[str, Any]] = []
        while True:
            try:
                events.append(self.outbox.get_nowait())
            except queue.Empty:
                break
        return events

    def _emit(self, message: dict[str, Any]) -> None:
        self.outbox.put(message)

    def _cmd_hello(self, _command: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "ready",
            "voice_ready": True,
            "voice_id": resolve_voice_id("", self.voice_preset),
            "key_found": bool(self.key),
            "config_error": self.config_error,
        }

    def _cmd_shutdown(self, _command: dict[str, Any]) -> dict[str, Any]:
        self._stop_mic()
        stop()
        return {"type": "ok"}

    def _cmd_configure(self, command: dict[str, Any]) -> dict[str, Any]:
        key = str(command.get("key", ""))
        value = command.get("value")
        if key == "mode":
            self.mode = str(value)
        elif key == "voice_preset":
            self.voice_preset = str(value)
        elif key == "speak_replies":
            self.speak_replies = bool(value)
        else:
            return {"type": "error", "message": f"unknown setting {key!r}"}
        return {"type": "ok"}

    def _cmd_set_key(self, command: dict[str, Any]) -> dict[str, Any]:
        self.key = str(command.get("key", ""))
        if self.router is not None:
            self.router.key = self.key
        return {"type": "ok", "key_found": bool(self.key)}

    def _cmd_persona(self, command: dict[str, Any]) -> dict[str, Any]:
        """Report the personality catalogue, or change the active one."""
        wanted = str(command.get("persona", "")).strip()
        if wanted:
            try:
                set_active_persona(wanted)
            except PersonaError:
                return {
                    "type": "error",
                    "message": f"there is no personality called {wanted!r}",
                    "persona": active_persona_id(),
                }
        active = active_persona_id()
        current = persona_summary(active)
        return {
            "type": "ok",
            "persona": active,
            "name": current.get("name", ""),
            "tagline": current.get("tagline", ""),
            "archetype": current.get("archetype", ""),
            "catalogue": catalogue_summaries(),
        }

    # -- request path ------------------------------------------------------

    def _cmd_route(self, command: dict[str, Any]) -> dict[str, Any]:
        text = str(command.get("text", ""))
        mode = str(command.get("mode", self.mode or "auto"))
        speak = bool(command.get("speak", False))
        snapshot = command.get("snapshot")

        # Every turn starts with no playbook.  A local answer (the time, a
        # launcher word) never reaches the model, so it never gets one and
        # never waits for one.
        self._active_skill = None

        # Check for mode switch phrases before routing
        mode_override = mode_switch(text)
        if mode_override:
            self.mode = mode_override
            spoken = "Red team mode on." if mode_override == "redteam" else "Back to normal mode."
            return self._plan_payload(spoken, speak=speak, route={"tier": mode_override, "label": mode_override, "seconds": 0.0})

        # A spoken request for a different personality changes how she answers
        # from the very next request on.  It never reaches the model.
        personality = persona_switch(text)
        if personality:
            try:
                set_active_persona(personality)
            except PersonaError:
                pass
            else:
                card = persona_summary(personality)
                spoken = (
                    f"{card.get('name') or personality} it is. I'll speak like that from now on."
                )
                return self._plan_payload(
                    spoken,
                    speak=speak,
                    route={"tier": "local", "label": "persona", "seconds": 0.0},
                )

        # A spoken approval only ever runs a step that is waiting for one.
        if self._pending_actions and self._pending_index < len(self._pending_actions):
            if self._pending_expired():
                self._clear_pending()
                return self._plan_payload(
                    "That step timed out, so nothing ran. Say it again if you still want it.",
                    speak=speak,
                )
            decision = approval_decision(text)
            if decision in ("approve", "decline"):
                if self._was_heard(text):
                    return self._advance_pending(decision, speak=speak)
                # The microphone never heard this, so it cannot answer the
                # question she asked.  Nothing runs and the step is dropped.
                self._clear_pending()
                return self._plan_payload(
                    "That answer was not heard, so nothing ran.",
                    speak=speak,
                )
            # Any other sentence is a new request: the waiting step is dropped
            # so a later "haan" can never run a step the user moved on from.
            self._clear_pending()

        local = local_intent(text)
        if local is not None and snapshot is None:
            plan = dict(local)
            plan["_route"] = {
                "tier": "local",
                "label": "Instant",
                "tier_label": "Local",
                "seconds": 0.0,
                "fallbacks": [],
                "attempts": [],
                "tokens": 0,
            }
            return self._finalise_plan(plan, speak=speak)

        if self.router is None:
            return {"type": "error", "message": self.config_error or "the model configuration is not ready"}
        if not self.key:
            return {"type": "error", "message": f"no assistant key found; set {self.key_env[0]} and restart"}

        forced = mode if mode != "auto" else "auto"
        tier = self.classifier.classify(text, has_image=bool(snapshot), forced_mode=forced)
        # The playbook for this request.  It is matched offline, so it costs
        # no network call, and it is carried on the session so the prompt,
        # the payload and the plan all talk about the same skill.
        self._active_skill = match_skill(text)
        messages = self._build_messages(text, mode)
        result = self.router.call(
            tier, messages, image=snapshot, extra_instructions=self._tier_brief(tier)
        )
        plan = parse_json_plan(result["text"])
        plan["_route"] = {
            "tier": tier,
            "label": result["label"],
            "tier_label": self.router.tier_label(tier),
            "seconds": result["seconds"],
            "fallbacks": result["fallbacks"],
            "attempts": result["attempts"],
            "tokens": result["usage"].get("prompt_tokens", 0)
            + result["usage"].get("completion_tokens", 0),
        }
        return self._finalise_plan(plan, speak=speak)

    # -- voice approval ----------------------------------------------------

    def _pending_expired(self) -> bool:
        """True when the waiting step has been left alone for too long."""
        return bool(self._pending_deadline) and time.monotonic() > self._pending_deadline

    def _waiting_for_voice(self) -> bool:
        """True while a step is waiting for an answer that can still be given."""
        if not self._pending_actions or self._pending_index >= len(self._pending_actions):
            return False
        return not self._pending_expired()

    def _arm_pending_step(self) -> None:
        """Start the silence window for the step that is waiting.

        Every step waits for its own answer, in its own window.  The words
        heard before the question cannot answer it, so an old "haan" from an
        earlier turn can never approve these steps.
        """
        self._pending_deadline = time.monotonic() + APPROVAL_SECONDS
        self._heard.clear()

    def _clear_pending(self) -> None:
        """Forget the waiting step and the actions it belonged to."""
        self._pending_actions = []
        self._pending_index = 0
        self._pending_deadline = 0.0
        self.current_actions = []

    def _pending_question(self) -> str:
        """The question for the step that is waiting for an answer."""
        action = self._pending_actions[self._pending_index]
        step = self._pending_index + 1
        return f"Step {step}: {_describe_action(action)}. Run karun?"

    def _advance_pending(self, decision: str, speak: bool) -> dict[str, Any]:
        """Run or skip the waiting step, then ask for the next one."""
        action = self._pending_actions[self._pending_index]
        if decision == "approve":
            try:
                outcome = _run_action(action)
            except Exception:  # noqa: BLE001 - a failed step is reported, never fatal
                outcome = "that step could not be run"
            self.action_results.append(f"Ran: {outcome}")
        else:
            outcome = "skipped"
            self.action_results.append(f"Skipped step {self._pending_index + 1}")

        self._pending_index += 1
        self.current_actions = list(self._pending_actions[self._pending_index :])

        if self._pending_index >= len(self._pending_actions):
            self._clear_pending()
            done = "All steps are done. " + "; ".join(self.action_results[-5:])
            return self._plan_payload(done, speak=speak, results=list(self.action_results[-5:]))

        # The next step is a new question, so it gets its own window and its
        # own answer.
        self._arm_pending_step()
        question = self._pending_question()
        return self._plan_payload(f"Done. {question}", speak=speak)

    def _build_messages(self, request: str, mode: str | None) -> list[dict[str, Any]]:
        if mode is None:
            mode = self.mode
        emergency = request.strip().startswith(",,")
        from core_app import build_system_prompt

        messages = [
            {
                "role": "system",
                "content": build_system_prompt(mode, emergency, skill=self._active_skill),
            }
        ]
        recent = self.history[-8:]
        if recent and recent[-1]["role"] == "user" and recent[-1]["content"] == request:
            recent = recent[:-1]
        for item in recent:
            if item["role"] in ("user", "assistant"):
                messages.append({"role": item["role"], "content": item["content"]})
        if self.action_results:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Results from the steps you just ran:\n"
                        + "\n".join(self.action_results)
                        + "\n\nContinue from here."
                    ),
                }
            )
        messages.append({"role": "user", "content": request})
        return messages

    def _tier_brief(self, tier: str) -> str:
        from core_app import TIER_BRIEF

        return TIER_BRIEF.get(tier, "")

    def _plan_payload(
        self,
        reply: str,
        speak: bool,
        route: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        clarify: Any = None,
        warning: str = "",
        results: list[str] | None = None,
        speech_extra: str = "",
        skill: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the payload the renderer expects and speak it once.

        Every line she says is synthesised here, so the reply and the approval
        question travel in one piece of audio and the microphone stays quiet
        for exactly as long as the speech lasts.

        ``speech_extra`` is spoken after the reply.  It is kept apart from the
        reply because :func:`voice_line` speaks only the character section of
        a reply, and the approval question has to be heard either way.
        """
        payload: dict[str, Any] = {
            "type": "plan",
            "reply": reply,
            # The caption is the answer, and it is built here rather than taken
            # from the synthesised speech, so the reply is still on the screen
            # when the voice engine is down.
            "caption": caption_line(reply),
            "speech": "",
            "audio": "",
            "words": [],
            "mime": "",
            "clarify": clarify,
            "warning": warning,
            "route": route or {},
            "actions": list(actions or []),
            "descriptions": [_describe_action(action) for action in (actions or [])],
            "rejections": list(getattr(_shim, "action_rejections", [])),
            "results": list(results or []),
            # The playbook that shaped this reply.  It is shown on screen and
            # never spoken.
            "skill": skill or {},
        }
        if not speak or not TTS_AVAILABLE:
            return payload

        spoken = clean_for_speech(voice_line(reply))
        if speech_extra:
            extra = clean_for_speech(speech_extra)
            spoken = f"{spoken} {extra}".strip() if spoken else extra
        if not spoken:
            return payload
        voice_id = resolve_voice_id(spoken, self.voice_preset)
        try:
            tts_result = tts_synthesize(spoken, voice_id)
        except Exception:  # noqa: BLE001 - TTS failure is not fatal; the orb falls back to its estimated envelope
            return payload
        payload["speech"] = tts_result.get("text", spoken)
        payload["audio"] = tts_result.get("audio", "")
        payload["words"] = tts_result.get("words", [])
        payload["mime"] = tts_result.get("mime", "audio/mpeg")
        # She is about to talk, so the microphone stops listening.
        self._mic_busy_until = (
            time.monotonic() + self._speech_seconds(payload["words"], spoken) + 1.0
        )
        return payload

    def _finalise_plan(self, plan: dict[str, Any], speak: bool) -> dict[str, Any]:
        """Turn a model plan into a spoken, approval-ready payload."""
        reply = str(plan.get("reply", ""))
        if is_template_slot(reply):
            # A model that copied the answer template did not answer at all.
            # Say something honest instead of reading the template out loud.
            reply = "That answer did not come through, so there is nothing to show yet."
        self.turn_count += 1
        if "gauge" not in reply.lower():
            reply = f"{reply}\n\n{freedom_gauge(self.turn_count)}"
        self.history.append({"role": "assistant", "content": reply})

        raw_actions = plan.get("actions", [])
        valid = _validate_actions(
            raw_actions if isinstance(raw_actions, list) else []
        )
        self.current_actions = valid

        question = ""
        if valid:
            # The steps wait for a spoken answer before anything runs.
            self._pending_actions = list(valid)
            self._pending_index = 0
            self._arm_pending_step()
            question = self._pending_question()
        else:
            self._clear_pending()

        skill_payload: dict[str, Any] = {}
        if self._active_skill is not None:
            skill_payload = {
                "name": self._active_skill.name,
                "domain": self._active_skill.domain,
                "score": self._active_skill.score,
            }

        return self._plan_payload(
            f"{reply}\n\n{question}" if question else reply,
            speak=speak,
            route=plan.get("_route", {}),
            actions=valid,
            clarify=plan.get("clarify"),
            warning=plan.get("_warning", ""),
            speech_extra=question,
            skill=skill_payload,
        )

    # -- actions -----------------------------------------------------------

    def _cmd_act(self, command: dict[str, Any]) -> dict[str, Any]:
        index = int(command.get("index", -1))
        ok = bool(command.get("ok"))
        if not 0 <= index < len(self.current_actions):
            return {"type": "error", "message": "action index out of range"}
        action = self.current_actions[index]
        if not ok:
            # Skipping is allowed: it never runs anything.
            self._skip_pending(index)
            return {"type": "skipped", "index": index, "description": _describe_action(action)}
        # Running is not.  Only the spoken answer may approve a step, so the
        # protocol itself must never be a second way in.
        return {"type": "error", "message": "a step is approved by voice, one at a time."}

    def _skip_pending(self, index: int) -> None:
        """Keep the voice gate in step with a step the user skipped by hand."""
        if not self._pending_actions:
            return
        if index == self._pending_index:
            self.action_results.append(f"Skipped step {index + 1}")
            self._pending_index += 1
        if self._pending_index >= len(self._pending_actions):
            self._clear_pending()
            return
        self.current_actions = list(self._pending_actions[self._pending_index :])

    def _consume_pending(self, index: int) -> None:
        """Keep the voice gate in step with a step the user ran by hand."""
        if not self._pending_actions:
            return
        if index == self._pending_index:
            self.action_results.append(f"Ran: step {index + 1}")
            self._pending_index += 1
        if self._pending_index >= len(self._pending_actions):
            self._clear_pending()
            return
        self.current_actions = list(self._pending_actions[self._pending_index :])

    def _cmd_run_plan(self, _command: dict[str, Any]) -> dict[str, Any]:
        # A plan is never run in one go.  Every step waits for its own spoken
        # answer, so this command has nothing left to do, and the waiting
        # step stays armed for the voice.
        return {"type": "error", "message": "each step is approved by voice, one at a time."}

    def _cmd_cancel(self, _command: dict[str, Any]) -> dict[str, Any]:
        self._clear_pending()
        stop()
        return {"type": "ok"}

    # -- mic ---------------------------------------------------------------
    #
    # The microphone has two jobs.  `listen` keeps it open while she sleeps,
    # so the wake word is always heard.  `mic` switches between sleeping
    # (`wake`) and listening to a command (`capture`).

    def _cmd_mic(self, command: dict[str, Any]) -> dict[str, Any]:
        """Wake her up, or send her back to sleep."""
        on = bool(command.get("on"))
        if on:
            # The orb was clicked, so this is a one click conversation: the
            # microphone closes itself when the user stops talking.
            self._mic_from_click = True
            self._open_mic()
            self._set_mode("capture")
            return {"type": "ok"}
        self._mic_from_click = False
        self._set_mode("wake")
        if not self._mic_listen_wanted:
            # Nobody asked for the always on listener, so the microphone
            # closes for good.
            self._stop_mic()
        return {"type": "ok"}

    def _cmd_listen(self, command: dict[str, Any]) -> dict[str, Any]:
        """Open the microphone as a sleeper, or close it for good."""
        on = bool(command.get("on"))
        if on:
            self._mic_listen_wanted = True
            opened = self._mic_thread is None or not self._mic_thread.is_alive()
            self._open_mic()
            if opened:
                self._mic_mode = "wake"
                self._emit({"type": "mic_state", "state": "wake"})
            return {"type": "ok"}
        self._mic_listen_wanted = False
        self._stop_mic()
        return {"type": "ok"}

    def _cmd_wake(self, command: dict[str, Any]) -> dict[str, Any]:
        """Wake her up the way the wake word or the claps do."""
        self._wake(str(command.get("source", "voice")))
        return {"type": "ok"}

    def _set_mode(self, mode: str) -> None:
        """Switch between sleeping and listening, and say so."""
        if self._mic_mode == mode:
            return
        self._mic_mode = mode
        self._emit({"type": "mic_state", "state": mode})

    def _open_mic(self) -> None:
        """Start the microphone thread, if it is not already running."""
        if self._mic_thread is not None and self._mic_thread.is_alive():
            return
        self._mic_stop.clear()
        self._mic_last_voice = time.monotonic()
        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._mic_thread.start()

    def _stop_mic(self) -> None:
        """Close the microphone.  Safe to call when it was never opened."""
        self._mic_stop.set()
        thread = self._mic_thread
        if thread is not None and thread is not threading.current_thread():
            # The first time round the model is still loading, so the wait is
            # generous: the model is worth keeping.
            thread.join(timeout=15)
        self._mic_thread = None

    def _wake(self, source: str) -> None:
        """Wake her up and let her greet you."""
        self._mic_mode = "capture"
        self._mic_from_click = False
        self._mic_last_voice = time.monotonic()
        self._emit({"type": "wake", "source": source})
        self._emit({"type": "mic_state", "state": "capture"})
        self._greet()

    def _greet(self) -> None:
        """Say hello, and stop listening while she is talking."""
        speech = GREETING
        audio = ""
        words: list[dict[str, Any]] = []
        mime = ""
        if TTS_AVAILABLE and tts_synthesize is not None:
            try:
                voice_id = resolve_voice_id(GREETING, self.voice_preset)
                result = tts_synthesize(GREETING, voice_id)
                speech = str(result.get("text", GREETING))
                audio = str(result.get("audio", ""))
                words = list(result.get("words", []))
                mime = str(result.get("mime", "audio/mpeg"))
            except Exception:  # noqa: BLE001, S110 - a missing voice is not fatal
                pass
        self._emit(
            {
                "type": "greeting",
                "speech": speech,
                "audio": audio,
                "words": words,
                "mime": mime,
            }
        )
        self._mic_busy_until = time.monotonic() + self._speech_seconds(words, speech) + 1.0

    def _say(self, text: str) -> None:
        """Turn what she heard into a command."""
        command = strip_wake_words(text)
        if not command:
            return
        # Remember the words, so only something the microphone really heard
        # can answer the question she asked.
        self._heard.append(command.strip().casefold())
        if len(self._heard) > 8:
            del self._heard[:-8]
        self._emit({"type": "transcript", "text": command, "final": True})

    def _was_heard(self, text: str) -> bool:
        """True when the microphone really heard these words."""
        return str(text or "").strip().casefold() in self._heard

    @staticmethod
    def _speech_seconds(words: list[dict[str, Any]], text: str) -> float:
        """How long the line takes to say."""
        ends = [float(word.get("end", 0.0)) for word in words]
        longest = max(ends) if ends else 0.0
        if longest > 0.0:
            return longest
        return max(1.0, len(text) / 12.0)

    def _mic_chunk(self, chunk: bytes, recognizer: Any, state: MicState) -> bool:
        """Handle one microphone chunk.

        The microphone stays open for the life of the app: in `wake` mode she
        only listens for her name or for two claps, in `capture` mode every
        word is a command.  The answer is False only when the orb was clicked
        and the user has gone quiet, because then the microphone has to close
        itself.
        """
        state.clock += (len(chunk) // 2) / float(MIC_SAMPLE_RATE)
        level = _chunk_level(chunk)

        if time.monotonic() < self._mic_busy_until:
            # She is talking, so she must not listen to herself.
            return True

        # A step that is waiting for an answer keeps her awake and listening:
        # the answer to it is spoken, so the microphone has to stay open for
        # exactly as long as the question waits.
        waiting = self._waiting_for_voice()

        if (
            self._mic_mode == "capture"
            and self._mic_last_voice is not None
            and time.monotonic() - self._mic_last_voice > SLEEP_SECONDS
            and not waiting
        ):
            # She has been awake for long enough without a word.
            self._set_mode("wake")
            return True

        if state.level_count == 0 or state.clock - state.last_level_at >= LEVEL_INTERVAL:
            state.level_count += 1
            state.last_level_at = state.clock
            self._emit({"type": "level", "value": round(level, 3)})

        if level >= SPEECH_LEVEL:
            state.speech_seen = True
            state.silence_since = None
            state.final_sent = False
            self._mic_last_voice = time.monotonic()

        if self._mic_mode == "wake" and not waiting:
            return self._wake_chunk(chunk, recognizer, state, level)
        # A step is waiting for an answer, so every word is an answer now.
        # This is also what a model that thinks for a long time needs: she may
        # have gone back to sleep before the question was asked, and the answer
        # still has to reach her.
        return self._capture_chunk(chunk, recognizer, state, level)

    def _wake_chunk(self, chunk: bytes, recognizer: Any, state: MicState, level: float) -> bool:
        """Look for the wake word and for two claps."""
        if self._claps.feed(level, state.clock):
            self._wake("clap")
            return True
        if recognizer.AcceptWaveform(chunk):
            result = json.loads(recognizer.Result())
            text = str(result.get("text", "")).strip()
            if text and wake_phrase_in(text):
                self._wake("voice")
                rest = strip_wake_words(text)
                if rest:
                    # She was already talking when she woke up.
                    self._say(rest)
        return True

    def _capture_chunk(self, chunk: bytes, recognizer: Any, state: MicState, level: float) -> bool:
        """Everything she hears now is a command.

        Returns False when the orb was clicked and the user has gone quiet,
        because then the microphone has to close itself.
        """
        if recognizer.AcceptWaveform(chunk):
            result = json.loads(recognizer.Result())
            text = str(result.get("text", "")).strip()
            if not self._mic_from_click:
                state.speech_seen = False
                state.silence_since = None
            if text:
                state.final_sent = True
                self._say(text)
            return True

        if not state.speech_seen:
            return True
        if level > SILENCE_LEVEL:
            state.silence_since = None
            return True
        if state.silence_since is None:
            state.silence_since = state.clock
            return True
        if state.clock - state.silence_since < SILENCE_SECONDS:
            return True

        # The room went quiet, so the sentence is over.
        state.speech_seen = False
        state.silence_since = None
        if not state.final_sent:
            final = json.loads(recognizer.FinalResult())
            text = str(final.get("text", "")).strip()
            state.final_sent = True
            if text:
                self._say(text)
        # When the orb was clicked the microphone closes itself, so the shell
        # can think, answer and settle the orb.  The always on listener stays.
        return not self._mic_from_click

    def _mic_loop(self) -> None:
        import sounddevice as sd
        from vosk import KaldiRecognizer

        model_path = MODELS_DIR / "vosk-model-small-en-in-0.4"
        if not model_path.exists():
            self._emit({"type": "error", "message": "the offline voice model is missing"})
            return
        recognizer = KaldiRecognizer(_load_vosk_model(model_path), MIC_SAMPLE_RATE)
        audio_q: queue.Queue[bytes] = queue.Queue()
        state = MicState()

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            if not status:
                audio_q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=MIC_SAMPLE_RATE,
                blocksize=MIC_BLOCK_FRAMES,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while not self._mic_stop.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not self._mic_chunk(chunk, recognizer, state):
                        # The user stopped talking, so the microphone closes
                        # itself and the orb can think and answer.
                        self._mic_stop.set()
                        break
        except Exception as error:  # noqa: BLE001
            self._emit({"type": "error", "message": f"microphone error: {type(error).__name__}"})
        else:
            if not state.final_sent:
                # Something was still unsaid when the microphone closed.
                final = json.loads(recognizer.FinalResult())
                text = str(final.get("text", "")).strip()
                state.final_sent = True
                if text:
                    self._say(text)
        finally:
            # The orb settles back down and the shell learns the mic is closed,
            # even when nobody said anything.
            self._emit({"type": "level", "value": 0.0})
            self._emit({"type": "mic_state", "state": "idle"})

    # -- usage -------------------------------------------------------------

    def _cmd_usage(self, _command: dict[str, Any]) -> dict[str, Any]:
        if self.router is None:
            return {"type": "error", "message": "router not ready"}
        return {
            "type": "usage",
            "rows": self.router.status_rows(),
            "totals": self.usage.snapshot().get("totals", {}),
        }
