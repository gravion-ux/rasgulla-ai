"""Headless shim: bridges the stdio protocol to the real Rasgulla brain.

Reuses core_router, core_app (persona, guards, voice_line) and voice_engine
verbatim.  No Tk window is created; only pure functions and classes are
instantiated, so this module imports cleanly without a display.

The bound guards (_safe_path, validate_actions, describe_action, run_action)
run against a tiny shim instance that carries the instance state they need.
Everything else is module-level in core_app, so binding works unchanged.
"""

from __future__ import annotations

import ctypes
import json
import queue
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import MethodType
from typing import Any

APP_DIR = Path(__file__).resolve().parent.parent
BRAIN_DIR = Path(__file__).resolve().parent
for _p in (str(APP_DIR), str(BRAIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pyautogui
from core_app import (
    WORK_DIR,
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
    read_api_key,
)
from voice_engine import (
    clean_for_speech,
    pick_voice,
    stop,
)

try:  # tts lives next door in app/brain
    from tts import synthesize as tts_synthesize

    TTS_AVAILABLE = True
except Exception:  # noqa: BLE001 - TTS is optional at import time
    TTS_AVAILABLE = False
    tts_synthesize = None

MODELS_DIR = APP_DIR / "models"
CONFIG_PATH = APP_DIR / "models.json"
USAGE_PATH = APP_DIR / "usage.json"


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
            "usage": self._cmd_usage,
        }
        handler = handlers.get(kind)
        if handler is None:
            return {"type": "error", "message": f"unknown command {kind!r}"}
        try:
            return handler(command)
        except Exception as error:  # noqa: BLE001 - protocol boundary: never crash the session
            return {"type": "error", "message": f"{type(error).__name__}: {error}"}

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
            "key_found": bool(self.key),
            "config_error": self.config_error,
        }

    def _cmd_shutdown(self, _command: dict[str, Any]) -> dict[str, Any]:
        self._mic_stop.set()
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

    # -- request path ------------------------------------------------------

    def _cmd_route(self, command: dict[str, Any]) -> dict[str, Any]:
        text = str(command.get("text", ""))
        mode = str(command.get("mode", self.mode or "auto"))
        speak = bool(command.get("speak", False))
        snapshot = command.get("snapshot")

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

    def _build_messages(self, request: str, mode: str | None) -> list[dict[str, Any]]:
        if mode is None:
            mode = self.mode
        emergency = request.strip().startswith(",,")
        from core_app import build_system_prompt

        messages = [{"role": "system", "content": build_system_prompt(mode, emergency)}]
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

    def _finalise_plan(self, plan: dict[str, Any], speak: bool) -> dict[str, Any]:
        reply = str(plan.get("reply", ""))
        self.turn_count += 1
        if "gauge" not in reply.lower():
            reply = f"{reply}\n\n{freedom_gauge(self.turn_count)}"
        self.history.append({"role": "assistant", "content": reply})

        speech_text = voice_line(reply)
        spoken = clean_for_speech(speech_text) if speak else ""

        payload: dict[str, Any] = {
            "type": "plan",
            "reply": reply,
            "speech": "",
            "audio": "",
            "words": [],
            "mime": "",
            "clarify": plan.get("clarify"),
            "warning": plan.get("_warning", ""),
            "route": plan.get("_route", {}),
            "actions": [],
            "descriptions": [],
            "rejections": list(getattr(_shim, "action_rejections", [])),
        }
        raw_actions = plan.get("actions", [])
        valid = _validate_actions(
            raw_actions if isinstance(raw_actions, list) else []
        )
        self.current_actions = valid
        for action in valid:
            payload["actions"].append(action)
            payload["descriptions"].append(_describe_action(action))
        if speak and spoken and TTS_AVAILABLE:
            voice = pick_voice(spoken, self.voice_preset)
            voice_id = voice.voice_id if voice else "en-IN-NeerjaNeural"
            try:
                tts_result = tts_synthesize(spoken, voice_id)
                payload["speech"] = tts_result.get("text", spoken)
                payload["audio"] = tts_result.get("audio", "")
                payload["words"] = tts_result.get("words", [])
                payload["mime"] = tts_result.get("mime", "audio/mpeg")
            except Exception:  # noqa: BLE001, S110 - TTS failure is not fatal; the orb falls back to its estimated envelope
                pass
        return payload

    # -- actions -----------------------------------------------------------

    def _cmd_act(self, command: dict[str, Any]) -> dict[str, Any]:
        index = int(command.get("index", -1))
        ok = bool(command.get("ok"))
        if not 0 <= index < len(self.current_actions):
            return {"type": "error", "message": "action index out of range"}
        action = self.current_actions[index]
        if not ok:
            return {"type": "skipped", "index": index, "description": _describe_action(action)}
        outcome = _run_action(action)
        return {"type": "action_result", "index": index, "text": outcome}

    def _cmd_run_plan(self, command: dict[str, Any]) -> dict[str, Any]:
        indexes = command.get("approved", [])
        results: list[dict[str, Any]] = []
        for index in indexes:
            if not 0 <= index < len(self.current_actions):
                continue
            action = self.current_actions[index]
            outcome = _run_action(action)
            results.append({"index": index, "text": outcome})
        self.current_actions = []
        return {"type": "plan_done", "results": results}

    def _cmd_cancel(self, _command: dict[str, Any]) -> dict[str, Any]:
        self.current_actions = []
        stop()
        return {"type": "ok"}

    # -- mic ---------------------------------------------------------------

    def _cmd_mic(self, command: dict[str, Any]) -> dict[str, Any]:
        on = bool(command.get("on"))
        if on:
            if self._mic_thread is not None and self._mic_thread.is_alive():
                return {"type": "ok"}
            self._mic_stop.clear()
            self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
            self._mic_thread.start()
            return {"type": "ok"}
        self._mic_stop.set()
        thread = self._mic_thread
        self._mic_thread = None
        if thread is not None:
            thread.join(timeout=5)
        return {"type": "ok"}

    def _mic_loop(self) -> None:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model

        model_path = MODELS_DIR / "vosk-model-small-en-in-0.4"
        if not model_path.exists():
            self._emit({"type": "error", "message": "the offline voice model is missing"})
            return
        recognizer = KaldiRecognizer(Model(str(model_path)), 16000)
        audio_q: queue.Queue[bytes] = queue.Queue()

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            if not status:
                audio_q.put(bytes(indata))

        try:
            with sd.RawInputStream(
                samplerate=16000,
                blocksize=8000,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while not self._mic_stop.is_set():
                    try:
                        chunk = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if recognizer.AcceptWaveform(chunk):
                        result = json.loads(recognizer.Result())
                        text = result.get("text", "").strip()
                        if text:
                            self._emit({"type": "transcript", "text": text, "final": False})
        except Exception as error:  # noqa: BLE001
            self._emit({"type": "error", "message": f"microphone error: {type(error).__name__}"})
            return
        final = json.loads(recognizer.FinalResult())
        text = final.get("text", "").strip()
        if text:
            self._emit({"type": "transcript", "text": text, "final": True})

    # -- usage -------------------------------------------------------------

    def _cmd_usage(self, _command: dict[str, Any]) -> dict[str, Any]:
        if self.router is None:
            return {"type": "error", "message": "router not ready"}
        return {
            "type": "usage",
            "rows": self.router.status_rows(),
            "totals": self.usage.snapshot().get("totals", {}),
        }
