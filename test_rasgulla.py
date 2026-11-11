"""Local test suite for Rasgulla.

Run with:  .venv\\Scripts\\python.exe test_rasgulla.py

These tests never call the network.  They check the routing table, the JSON
protocol, the action allow-list, the provider redaction, the sprite build and
the usage accounting.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from core_router import (  # noqa: E402
    ModelRouter,
    RouterError,
    TaskClassifier,
    UsageTracker,
    friendly_model,
    parse_json_plan,
    read_api_key,
    redact,
)
from pet_sprite import ensure_pet_assets, render_bubble  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED.append(f"{name} {detail}".strip())
        print(f"  FAIL  {name} {detail}".rstrip())


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


# ---------------------------------------------------------------- routing
def test_routing_table() -> None:
    section("Routing table")
    config = json.loads((APP_DIR / "models.json").read_text(encoding="utf-8"))
    tiers = config.get("tiers", {})
    for required in ("chat", "code", "reason", "vision", "creative", "security"):
        check(f"tier '{required}' exists", required in tiers)
    for tier, spec in tiers.items():
        chain = spec.get("chain") or []
        check(f"tier '{tier}' has models", len(chain) > 0)
        check(f"tier '{tier}' has a temperature", "temperature" in spec)
        check(f"tier '{tier}' has max_tokens", "max_tokens" in spec)
    check("request cap is configured", config.get("defaults", {}).get("requests_per_minute", 0) <= 40)
    check("endpoint configured", bool(config.get("endpoint")))
    check("key env names configured", bool(config.get("key_env")))

    user_picks = ["minimaxai/minimax-m3.1-flash", "deepseek-ai/deepseek-v4.1-flash", "z-ai/glm-5.3"]
    everything = [m for spec in tiers.values() for m in spec.get("chain", [])]
    for model in user_picks:
        check(f"chosen model '{model}' is routed", model in everything)
    # The picks stay in the routing table, but a model the endpoint retired
    # may not lead a tier: deepseek-v4.1-flash hung past 30s and glm-5.3
    # answered 410 Gone when the chains were checked on 30 Oct 2026.
    retired = {"deepseek-ai/deepseek-v4.1-flash", "z-ai/glm-5.3"}
    check(
        "no tier leads with a retired model",
        all(spec["chain"][0] not in retired for spec in tiers.values()),
    )
    check(
        "the user's picks are still aliased",
        all(model in config.get("aliases", {}) for model in user_picks),
    )
    # Live evidence, 01 Nov 2026: minimax answered 429 and both lightning and
    # step then hung past their timeouts, so a chat request failed after 105
    # seconds.  A tier may not lead with a model that has already refused to
    # answer, and every tier needs a fallback behind it.
    unreliable = {
        "deepseek-ai/deepseek-v4.1-flash",
        "z-ai/glm-5.3",
        "minimaxai/minimax-m3.1-flash",
    }
    check(
        "no tier leads with a model that refuses to answer",
        all(spec["chain"][0] not in unreliable for spec in tiers.values()),
    )
    check(
        "every tier has a fallback chain",
        all(len(spec.get("chain", [])) >= 3 for spec in tiers.values()),
    )
    check(
        "the router can reach the fourth model",
        int(config.get("defaults", {}).get("max_attempts_per_request", 0)) >= 4,
    )
    for tier in ("code", "reason", "security"):
        check(
            f"tier '{tier}' can hold a long answer",
            tiers[tier].get("max_tokens", 0) >= 2500,
        )


def test_classifier() -> None:
    section("Task classifier")
    classifier = TaskClassifier()
    cases = [
        ("write a python function to sort a list", False, "code"),
        ("debug this traceback in my react app", False, "code"),
        ("make a romantic song lyrics in hindi", False, "creative"),
        ("write a dark romance scene for my novel", False, "creative"),
        ("help me with ctf challenge on sql injection", False, "security"),
        ("what is the capital of France", False, "chat"),
        ("hello, what are you up to", False, "chat"),
    ]
    for request, has_image, expected in cases:
        got = classifier.classify(request, has_image=has_image, forced_mode="auto")
        check(f"'{request[:34]}' -> {expected}", got == expected, f"got {got}")
    check(
        "screenshot wins when nothing else matches",
        classifier.classify("click that button", has_image=True, forced_mode="auto") == "vision",
    )
    check(
        "forced coder mode wins",
        classifier.classify("hello", has_image=False, forced_mode="coder") == "code",
    )
    check(
        "forced redteam mode wins",
        classifier.classify("hello", has_image=False, forced_mode="redteam") == "security",
    )


def test_json_protocol() -> None:
    section("JSON protocol")
    raw = '{"mode":"coder","reply":"Ho gaya","clarify":null,"actions":[{"type":"launch","app":"notepad"}]}'
    plan = parse_json_plan(raw)
    check("parses clean json", plan["reply"] == "Ho gaya")
    check("keeps mode", plan["mode"] == "coder")
    check("keeps actions", len(plan["actions"]) == 1)
    check("null clarify becomes None", plan["clarify"] is None)

    fenced = "```json\n" + raw + "\n```"
    check("strips code fence", parse_json_plan(fenced)["reply"] == "Ho gaya")

    noisy = f"Sure! {raw} Hope that helps"
    check("recovers from surrounding prose", parse_json_plan(noisy)["reply"] == "Ho gaya")

    broken = '{"reply": "oops", "actions": [}'
    check("survives broken json", parse_json_plan(broken)["reply"] == "oops")

    steps = '{"mode":"normal","reply":"ok","steps":[{"type":"key","key":"tab"}]}'
    check("accepts 'steps' alias", len(parse_json_plan(steps)["actions"]) == 1)

    check("rejects unknown mode", parse_json_plan('{"mode":"evil","reply":"x"}')["mode"] == "normal")
    check("empty actions default", parse_json_plan('{"reply":"x"}')["actions"] == [])


def test_redaction() -> None:
    section("Provider redaction")
    samples = [
        "NVIDIA NIM failed",
        "nim api error",
        "NIM-only assistant",
        "Build on NVIDIA AI",
        "the NVIDIA agent",
    ]
    for text in samples:
        out = redact(text).lower()
        check(f"'{text}' hides provider", "nvidia" not in out and "nim" not in out, f"-> {out}")
    check("redact removes raw urls", "integrate.api" not in redact("see https://integrate.api.nvidia.com/v1/x"))
    check("redact keeps ordinary text", redact("hello there") == "hello there")
    check("friendly model strips vendor", friendly_model("nvidia/nemotron-3.5-bolt-e4b") == "nemotron-3.5-bolt-e4b")

    aliases = json.loads((APP_DIR / "models.json").read_text(encoding="utf-8")).get("aliases", {})
    for model, alias in aliases.items():
        check(f"alias '{alias}' hides vendor", "nvidia" not in alias.lower())


def test_usage_tracker() -> None:
    section("Usage accounting")
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "usage.json"
        tracker = UsageTracker(path)
        tracker.record("minimaxai/minimax-m3.1-flash", 100, 50, 1.2, route="chat")
        tracker.record("minimaxai/minimax-m3.1-flash", 200, 80, 2.0, route="chat")
        tracker.record("z-ai/glm-5.3", 10, 5, 130.0, error=True, route="reason")
        snapshot = tracker.snapshot()
        check("persisted to disk", path.exists())
        check("counts requests", snapshot["totals"]["requests"] == 3)
        check("sums prompt tokens", snapshot["totals"]["prompt_tokens"] == 310)
        check("sums completion tokens", snapshot["totals"]["completion_tokens"] == 135)
        check("counts errors", snapshot["totals"]["errors"] == 1)
        check("per-model bucket exists", "minimaxai/minimax-m3.1-flash" in snapshot["models"])
        reloaded = UsageTracker(path)
        check("reloads from disk", reloaded.snapshot()["totals"]["requests"] == 3)
        tracker.reset()
        check("reset clears counters", tracker.snapshot()["totals"]["requests"] == 0)


def test_router_fallback() -> None:
    section("Router fallback and circuit breaker")
    with tempfile.TemporaryDirectory() as folder:
        config = {
            "endpoint": "https://example.invalid/v1/chat/completions",
            "key_env": ["RASGULLA_API_KEY"],
            "defaults": {
                "requests_per_minute": 60,
                "timeout_seconds": 1,
                "max_attempts_per_request": 3,
                "cooldown_timeout_seconds": 300,
            },
            "tiers": {
                "chat": {
                    "label": "Test",
                    "temperature": 0.5,
                    "max_tokens": 100,
                    "chain": ["bad/model-one", "bad/model-two", "bad/model-three"],
                }
            },
            "aliases": {},
        }
        path = Path(folder) / "models.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        usage = UsageTracker(Path(folder) / "usage.json")
        router = ModelRouter(path, "dummy-key", usage)
        try:
            router.call("chat", [{"role": "user", "content": "hi"}])
            check("raises when every model fails", False, "no exception")
        except Exception as error:
            check("raises when every model fails", True)
            check("error mentions failures", "cool" in str(error).lower() or "failed" in str(error).lower() or "model" in str(error).lower())
        rows = router.status_rows()
        check("failed models recorded", len(rows) >= 1)
        check("errors counted", all(row["errors"] >= 1 for row in rows))
        check("models put in cooldown", all(row["cooldown"] > 0 for row in rows))
        try:
            router.call("chat", [{"role": "user", "content": "hi"}])
            check("second call skips cooled-down models", False, "no exception")
        except Exception:
            check("second call skips cooled-down models", True)


def test_sprites() -> None:
    section("Pet assets")
    manifest = ensure_pet_assets(APP_DIR / "assets" / "rasgulla-work.png", APP_DIR / "assets")
    check("manifest built", bool(manifest.get("key")))
    check("two frame sets", set(manifest.get("frames", {})) == {"idle", "work"})
    check("idle frames present", len(manifest["frames"].get("idle", [])) == 12)
    check("work frames present", len(manifest["frames"].get("work", [])) == 12)
    key = tuple(manifest["key"])
    for name in manifest["frames"]["idle"][:3]:
        path = APP_DIR / "assets" / "pet-frames" / name
        check(f"frame {name} exists", path.exists())
    bubble = render_bubble("Chal raha hai", key)
    check("bubble rendered", bubble.width > 40 and bubble.height > 20)
    check("bubble keyed", bubble.getpixel((0, 0)) == key)


def test_action_allowlist() -> None:
    section("Action allow-list")
    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    for blocked in ("shell=True", "os.system", "subprocess.run("):
        check(f"no '{blocked}' in app", blocked not in source)
    check("per-action approval present", "Confirm next action" in source)
    check("plan approval present", "Approve Rasgulla's plan?" in source)
    check("screenshot is opt-in", "Attach one screen snapshot" in source)
    check("no provider words in ui strings", not any(
        word in source.lower() for word in ("nvidia nim", "nim-only", "nim only")
    ))


def test_key_lookup() -> None:
    section("API key lookup")
    key = read_api_key(["RASGULLA_API_KEY", "NVIDIA_API_KEY"])
    check("a key is discoverable", bool(key), "no key found in env or registry")


def test_thread_safe_ui() -> None:
    section("Thread-safe UI handoff")
    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    check("queue handoff exists", "def _post_ui" in source and "ui_queue" in source)
    check("ui drain exists", "def _drain_ui_queue" in source)
    # Worker threads must never call Tk directly.
    worker_bodies = [
        source[source.index("def _run_health_check"):source.index("def _run_health_check") + 900],
        source[source.index("def _request_plan"):source.index("def _request_plan") + 2200],
        source[source.index("def listen_once"):source.index("def listen_once") + 3000],
    ]
    for index, body in enumerate(worker_bodies):
        check(f"worker {index + 1} avoids root.after", "root.after" not in body)
    check("timers cancelled on close", "after_cancel" in source and "def shutdown" in source)


def test_local_intent() -> None:
    section("Local intent router")
    from core_app import local_intent

    calc = local_intent("open calculator")
    check("calculator opens locally", bool(calc) and calc["actions"] == [{"type": "launch", "app": "calculator"}])
    check("local plan has a reply", bool(calc) and bool(calc["reply"]))
    clock = local_intent("what time is it")
    check("time answered locally", bool(clock) and clock["actions"] == [])
    found = local_intent("search python decorators")
    check("search builds a url", bool(found) and found["actions"][0]["type"] == "open_url")
    check("unknown request falls through", local_intent("write me a poem about rain") is None)


def test_modes() -> None:
    section("Modes")
    from core_app import MODE_PROMPTS, AUTO_PROMPT
    from core_router import TaskClassifier

    for mode in ("normal", "coder", "music", "writer", "redteam"):
        check(f"mode '{mode}' has a prompt", mode in MODE_PROMPTS)
        check(f"mode '{mode}' routes to a tier", mode in TaskClassifier.MODE_TO_TIER)
    check("auto prompt lists every mode", all(m in AUTO_PROMPT for m in
          ("coder", "music", "writer", "redteam", "normal")))
    check("writer routes to the creative tier", TaskClassifier.MODE_TO_TIER["writer"] == "creative")
    check("schema advertises writer", "<normal|coder|music|writer|redteam>" in
          (APP_DIR / "core_app.py").read_text(encoding="utf-8"))


def test_voice() -> None:
    section("Speaking voice")
    import voice_engine

    check("natural voice backend is available", voice_engine.NATURAL_VOICE_READY)
    check("an indian english voice exists", voice_engine.pick_voice("hello there") is not None)
    check("latin text picks the indian english voice",
          voice_engine.pick_voice("chai peeke kaam karein").voice_id.endswith("NeerjaNeural"))
    check("devanagari picks the hindi voice",
          voice_engine.pick_voice("चाय पीकर काम करते हैं").voice_id.endswith("SwaraNeural"))
    check("hindi preset is honoured",
          voice_engine.pick_voice("hello", "hi").voice_id.endswith("SwaraNeural"))
    check("device voice preset disables the online engine",
          voice_engine.pick_voice("hello", "system") is None)
    check("preset labels round-trip",
          voice_engine.preset_from_label(voice_engine.VOICE_PRESETS["hi"]) == "hi")

    spoken = voice_engine.clean_for_speech(
        "Sure!\n- **Step one**: open `calculator`\n```python\nprint(1)\n```\nChalte hain? 😄"
    )
    check("code fences are not spoken", "print(1)" not in spoken)
    check("backticks are removed", "`" not in spoken)
    check("bold markers are removed", "**" not in spoken)
    check("emoji are removed", "😄" not in spoken)
    check("words survive", "calculator" in spoken and "Chalte hain" in spoken)
    check("empty text stays empty", voice_engine.clean_for_speech("") == "")

    branding = ("nvidia", "nim", "microsoft", "bing", "edge-tts", "edge_tts")
    labels = " ".join(voice_engine.VOICE_PRESETS.values()).lower()
    check("voice labels carry no provider branding", not any(word in labels for word in branding))
    status = voice_engine.engine_status().lower()
    check("voice status carries no provider branding", not any(word in status for word in branding))

    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    check("app speaks through the voice engine", "voice_engine.speak(" in source)
    check("old device voice picker is gone", "zira" not in source.lower())


def test_audit_regressions() -> None:
    """Regression checks for the October 2026 code audit.

    Every check here corresponds to a defect that was found by reading the
    code, not by guessing: the tier brief swallowing the request, the request
    being sent twice, Devanagari being typed as nothing, the work folder
    losing its boundary, a stale voice line talking over a newer one, and the
    two configuration files being able to break the app.
    """
    section("Audit regressions")
    import core_app
    import core_router
    from core_app import RasgullaApp

    # -- the tier brief must not swallow the request --------------------
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": '{"mode":"normal","reply":"ok","actions":[]}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse()

    original_post = core_router.requests.post
    core_router.requests.post = fake_post
    try:
        with tempfile.TemporaryDirectory() as folder:
            config = {
                "endpoint": "https://example.invalid/v1/chat/completions",
                "key_env": ["RASGULLA_API_KEY"],
                "defaults": {
                    "requests_per_minute": 60,
                    "timeout_seconds": 5,
                    "max_attempts_per_request": 3,
                },
                "tiers": {
                    "code": {"label": "T", "temperature": 0.2, "max_tokens": 100, "chain": ["test/model"]},
                    "vision": {"label": "T", "temperature": 0.2, "max_tokens": 100, "chain": ["test/model"]},
                },
                "aliases": {},
            }
            config_path = Path(folder) / "models.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            router = ModelRouter(config_path, "dummy", UsageTracker(Path(folder) / "usage.json"))
            messages = [
                {"role": "system", "content": "PERSONA"},
                {"role": "user", "content": "THE REQUEST"},
            ]
            router.call("code", messages, extra_instructions="THE BRIEF")
            sent = captured["payload"]["messages"]
            check("the request stays the final turn", sent[-1]["role"] == "user")
            check("the request text is the final turn", sent[-1]["content"] == "THE REQUEST")
            check(
                "the brief sits with the system prompt",
                sent[1]["role"] == "system" and sent[1]["content"] == "THE BRIEF",
            )
            check(
                "the request is sent once",
                sum(1 for item in sent if item.get("content") == "THE REQUEST") == 1,
            )

            router.call("vision", messages, image="BASE64", extra_instructions="THE BRIEF")
            sent = captured["payload"]["messages"]
            final = sent[-1]
            check(
                "the picture rides on the request",
                final["role"] == "user" and isinstance(final["content"], list),
            )
            check(
                "the picture is not on a system turn",
                all(not isinstance(item.get("content"), list) for item in sent[:-1]),
            )
            check(
                "the request text stays beside the picture",
                any(part.get("text") == "THE REQUEST" for part in final["content"]),
            )
    finally:
        core_router.requests.post = original_post

    # -- the request must not be sent twice ------------------------------
    class _Var:
        def __init__(self, value: str) -> None:
            self.value = value

        def get(self) -> str:
            return self.value

    class _HistoryApp:
        def __init__(self) -> None:
            self.history: list[dict[str, str]] = []
            self.action_results: list[str] = []
            self.mode = _Var("auto")

        _build_messages = RasgullaApp._build_messages

    history_app = _HistoryApp()
    history_app.history = [
        {"role": "user", "content": "OLD REQUEST"},
        {"role": "assistant", "content": "OLD REPLY"},
        {"role": "user", "content": "THE REQUEST"},
    ]
    built = history_app._build_messages("THE REQUEST")
    check(
        "the request is sent once",
        sum(1 for item in built if item.get("content") == "THE REQUEST") == 1,
    )
    check("earlier turns are kept", any(item.get("content") == "OLD REQUEST" for item in built))
    check("the assistant reply is kept", any(item.get("content") == "OLD REPLY" for item in built))

    # -- non-ascii text must survive the keyboard ------------------------
    typed: list[tuple[str, object]] = []

    class _FakeGui:
        def write(self, text: str, interval: float = 0.0) -> None:
            typed.append(("write", text))

        def hotkey(self, *keys: str) -> None:
            typed.append(("hotkey", keys))

    original_gui = core_app.pyautogui
    core_app.pyautogui = _FakeGui()

    class _TypingApp:
        def __init__(self) -> None:
            self.clipboard: list[str] = []

        def _clipboard_read(self) -> str | None:
            return "earlier clipboard"

        def _clipboard_write(self, text: str) -> None:
            self.clipboard.append(text)

        _type_text = RasgullaApp._type_text

    try:
        typing_app = _TypingApp()
        typing_app._type_text("plain ascii")
        check("ascii text is typed on the keyboard", typed == [("write", "plain ascii")])
        check("ascii text never touches the clipboard", typing_app.clipboard == [])
        typed.clear()
        typing_app._type_text("चाय पीकर")
        check("non-ascii text is pasted", any(call[0] == "hotkey" for call in typed))
        check("the clipboard carried the text", "चाय पीकर" in typing_app.clipboard)
        check("the clipboard is restored", typing_app.clipboard[-1] == "earlier clipboard")
    finally:
        core_app.pyautogui = original_gui

    # -- the work folder must stay a boundary ----------------------------
    class _PathApp:
        _safe_path = RasgullaApp._safe_path

    path_app = _PathApp()
    check("a normal work-folder path is allowed", path_app._safe_path("notes.md") is not None)
    check("a parent traversal is refused", path_app._safe_path("../escape.txt") is None)
    check(
        "an absolute path is refused",
        path_app._safe_path("C:/Windows/System32/drivers/etc/hosts") is None,
    )
    check("an executable is refused", path_app._safe_path("payload.exe") is None)

    original_work = core_app.WORK_DIR
    try:
        with tempfile.TemporaryDirectory() as folder:
            core_app.WORK_DIR = Path(folder)

            class _ExecApp:
                _safe_path = RasgullaApp._safe_path

                def run_action(self, action):
                    return RasgullaApp.run_action(self, action)

            exec_app = _ExecApp()
            exec_app.run_action({"type": "write_file", "path": "note.md", "content": "hello"})
            check(
                "a normal write still works",
                (Path(folder) / "note.md").read_text(encoding="utf-8") == "hello",
            )
            try:
                exec_app.run_action({"type": "write_file", "path": "../escape.txt", "content": "x"})
                check("writing outside the work folder is refused", False, "no exception")
            except ValueError:
                check("writing outside the work folder is refused", True)
            except Exception as error:
                check("writing outside the work folder is refused", False, type(error).__name__)
            check(
                "nothing escaped the work folder",
                not (Path(folder).parent / "escape.txt").exists(),
            )
    finally:
        core_app.WORK_DIR = original_work

    # -- link-local targets must be refused ------------------------------
    check(
        "a normal url is allowed",
        not core_app.is_link_local_url("https://www.google.com/search?q=x"),
    )
    check(
        "the cloud metadata address is refused",
        core_app.is_link_local_url("http://169.254.169.254/latest/meta-data/"),
    )
    check(
        "the metadata hostname is refused",
        core_app.is_link_local_url("http://metadata.google.internal/x"),
    )
    check(
        "a local dev server stays allowed",
        not core_app.is_link_local_url("http://127.0.0.1:3000/"),
    )

    # -- a replaced voice line must never play ---------------------------
    import voice_engine

    played: list[bytes] = []
    spoken: list[str] = []
    original_synth = voice_engine.synthesize_wav
    original_play = voice_engine.play_wav
    original_local = voice_engine._local_speak

    def slow_synth(text, voice):
        time.sleep(0.4)
        return text.encode("utf-8")

    def fake_play(data):
        played.append(data)

    def fake_local(text):
        spoken.append(text)

    voice_engine.synthesize_wav = slow_synth
    voice_engine.play_wav = fake_play
    voice_engine._local_speak = fake_local
    try:
        voice_engine.stop()
        voice_engine.speak("first line", "en")
        time.sleep(0.05)
        voice_engine.speak("second line", "en")
        time.sleep(1.8)
        check("only the newest line is played", played == [b"second line"])
        check("the offline fallback did not fire", spoken == [])
    finally:
        voice_engine.synthesize_wav = original_synth
        voice_engine.play_wav = original_play
        voice_engine._local_speak = original_local
        voice_engine.stop()

    # -- the counters must survive a damaged file ------------------------
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "usage.json"
        path.write_text(
            json.dumps({"models": {"m": {"requests": 3}}, "totals": {"requests": 3}}),
            encoding="utf-8",
        )
        tracker = UsageTracker(path)
        tracker.record("m", 10, 5, 1.0, route="chat")
        snapshot = tracker.snapshot()
        check("a partial usage file is repaired", snapshot["totals"]["requests"] == 4)
        check("a partial model bucket is repaired", snapshot["models"]["m"]["requests"] == 4)
        check("missing token counters start at zero", snapshot["totals"]["prompt_tokens"] == 10)
        check("missing model fields start at zero", snapshot["models"]["m"]["errors"] == 0)

        path.write_text("{ this is not json", encoding="utf-8")
        broken = UsageTracker(path)
        broken.record("m", 1, 1, 0.1)
        check("a corrupt usage file is ignored", broken.snapshot()["totals"]["requests"] == 1)

    # -- a broken configuration must be reported, not crash --------------
    with tempfile.TemporaryDirectory() as folder:
        broken_config = Path(folder) / "models.json"
        broken_config.write_text("{ not json", encoding="utf-8")
        try:
            ModelRouter(broken_config, "k", UsageTracker(Path(folder) / "usage.json"))
            check("a broken config is reported clearly", False, "no exception")
        except RouterError as error:
            check("a broken config is reported clearly", "JSON" in str(error))
        try:
            ModelRouter(
                Path(folder) / "missing.json", "k", UsageTracker(Path(folder) / "usage.json")
            )
            check("a missing config is reported clearly", False, "no exception")
        except RouterError as error:
            check("a missing config is reported clearly", "could not be read" in str(error))

    # -- the configuration must stay the single source of truth ---------
    config = json.loads((APP_DIR / "models.json").read_text(encoding="utf-8"))
    check("the config declares the key env names", "RASGULLA_API_KEY" in config.get("key_env", []))
    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    check("the app reads the key through the config", "read_api_key(self.key_env)" in source)
    check(
        "the key env names are not hardcoded at the call site",
        'read_api_key(["RASGULLA_API_KEY"' not in source,
    )
    check("the usage table refreshes after a reset", "def _refresh_usage_window" in source)

    # -- search and open must not fight each other -----------------------
    found = core_app.local_intent("open google and search for python decorators")
    check(
        "a search beats a browser launch",
        bool(found) and found["actions"][0]["type"] == "open_url",
    )
    check(
        "the search phrase is clean",
        bool(found) and "python+decorators" in found["actions"][0]["url"],
    )
    check(
        "a named app still wins",
        core_app.local_intent("open notepad")["actions"][0]["app"] == "notepad",
    )
    check("the search phrase loses its filler", core_app.search_query("google search for X") == "X")


def test_dump_pipeline() -> None:
    """Regression checks for the masked-dump defects found on 30 Oct 2026.

    A red-team dump is data.  It has to reach the user unchanged: not
    summarised by the model, not rewritten by the parser, not cut by a file
    cap, and not routed to the wrong tier on the way there.
    """
    section("Dump pipeline")
    import core_app
    import core_router
    from core_app import RasgullaApp

    # -- a cut-off reply must keep every character that arrived ----------
    truncated = (
        '{"mode":"redteam","reply":"**Users dump**\\n```\\n'
        'GET https://api.example.com/v1/users?id=1\\nsnake_case_field\\n```",'
        '"actions":[{"type":"read_file","path":"users.txt"},'
    )
    plan = parse_json_plan(truncated)
    reply = plan["reply"]
    check("a cut-off reply keeps its bold text", "**Users dump**" in reply)
    check("a cut-off reply keeps its code fence", "```" in reply)
    check("a cut-off reply keeps its url", "https://api.example.com/v1/users?id=1" in reply)
    check("a cut-off reply keeps its underscores", "snake_case_field" in reply)
    check("a cut-off reply is flagged to the user", bool(plan.get("_warning")))
    check("a complete action survives the cut", len(plan["actions"]) == 1)
    check(
        "the surviving action is the right one",
        plan["actions"][0].get("path") == "users.txt",
    )
    check("the mode survives a cut-off payload", plan["mode"] == "redteam")

    # -- raw text with no envelope must not be rewritten -----------------
    plain = "Here is the dump:\n```python\ndef f():\n    return snake_case\n```"
    plan = parse_json_plan(plain)
    check("raw text keeps its code fence", "```python" in plan["reply"])
    check("raw text keeps its identifiers", "snake_case" in plan["reply"])

    # -- an answer in another field must not be lost ---------------------
    plan = parse_json_plan('{"mode":"normal","answer":"the whole dump","actions":[]}')
    check("an answer field is not lost", plan["reply"] == "the whole dump")

    # -- plain text must be flagged, not passed off as an answer ----------
    plan = parse_json_plan("Here's a thinking process:\n1. Analyze the input")
    check("plain text is still shown in full", "thinking process" in plan["reply"])
    check("plain text is flagged as plain text", "plain text" in plan.get("_warning", ""))
    plan = parse_json_plan('{"mode":"normal","reply":"clean","actions":[]}')
    check("a clean plan carries no warning", not plan.get("_warning"))

    # -- red-team work must reach the security tier ----------------------
    classifier = TaskClassifier()
    for request in (
        "dump the api endpoints of my own staging server",
        "enumerate subdomains of my test domain",
        "run a dumper against my lab box",
        "fuzz the login parameter in my local lab",
        "port scan my own network",
        "hash dump of the lab machine",
    ):
        got = classifier.classify(request, has_image=False, forced_mode="auto")
        check(f"'{request[:38]}' -> security", got == "security", f"got {got}")
    check(
        "plain coding still routes to code",
        classifier.classify(
            "write a python script to parse json", has_image=False, forced_mode="auto"
        )
        == "code",
    )

    # -- a dead model must be retired, not retried for ever --------------
    class _Gone:
        status_code = 410

        def json(self):
            return {}

    def gone_post(url, headers=None, json=None, timeout=None):
        return _Gone()

    original_post = core_router.requests.post
    core_router.requests.post = gone_post
    try:
        with tempfile.TemporaryDirectory() as folder:
            config = {
                "endpoint": "https://example.invalid/v1/chat/completions",
                "key_env": ["RASGULLA_API_KEY"],
                "defaults": {
                    "requests_per_minute": 60,
                    "timeout_seconds": 5,
                    "max_attempts_per_request": 3,
                },
                "tiers": {
                    "code": {
                        "label": "T",
                        "temperature": 0.2,
                        "max_tokens": 100,
                        "chain": ["dead/model"],
                    }
                },
                "aliases": {},
            }
            config_path = Path(folder) / "models.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            router = ModelRouter(config_path, "dummy", UsageTracker(Path(folder) / "usage.json"))
            results = router.health_check(timeout=1.0)
            check("a gone model is reported", results.get("dead/model") == "unavailable")
            check(
                "a gone model is retired for the session",
                not router._available("dead/model"),
            )
    finally:
        core_router.requests.post = original_post

    # -- a file dump must arrive whole ------------------------------------
    original_work = core_app.WORK_DIR
    try:
        with tempfile.TemporaryDirectory() as folder:
            core_app.WORK_DIR = Path(folder)
            big = "A" * 12000
            (Path(folder) / "dump.txt").write_text(big, encoding="utf-8")

            class _ExecApp:
                _safe_path = RasgullaApp._safe_path

                def run_action(self, action):
                    return RasgullaApp.run_action(self, action)

            exec_app = _ExecApp()
            result = exec_app.run_action({"type": "read_file", "path": "dump.txt"})
            check("a 12k file is not cut at 4000 chars", big in result)
            check("a whole file carries no cut marker", "characters" not in result)

            huge = "C" * 25000
            (Path(folder) / "huge.txt").write_text(huge, encoding="utf-8")
            result = exec_app.run_action({"type": "read_file", "path": "huge.txt"})
            check("an oversized file states the cut", "25000" in result)
            check("an oversized file is cut at the cap", len(result) < 25000 + 200)

            class _ActionApp:
                _safe_path = RasgullaApp._safe_path
                action_rejections: list[str] = []

                def validate_actions(self, actions):
                    return RasgullaApp.validate_actions(self, actions)

            action_app = _ActionApp()
            valid = action_app.validate_actions(
                [{"type": "write_file", "path": "huge.md", "content": "B" * 25000}]
            )
            check("a big write is still accepted", len(valid) == 1)
            check("nothing is rejected quietly", action_app.action_rejections == [])

            valid = action_app.validate_actions(
                [{"type": "write_file", "path": "huge.md", "content": "B" * 150000}]
            )
            check("an impossible write is refused", len(valid) == 0)
            check("the refusal is reported", len(action_app.action_rejections) == 1)
    finally:
        core_app.WORK_DIR = original_work

    # -- the prompts must not ask for a summary ---------------------------
    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    check("the persona keeps data verbatim", "she reports it exactly as it is" in source)
    check("the schema asks for the complete result", "put the" in source and "complete result" in source)
    check("the file caps are generous", "MAX_READ_CHARS = 20000" in source)
    check("a rejected action is never silent", "self.action_rejections = rejected" in source)

    # -- a panel that came up minimised must come back -------------------
    check("the panel has a real restore", "def restore_window" in source)
    check("the restore goes through the window frame", "wm_frame()" in source)
    focus_source = source.split("def _focus_main", 1)[-1].split("\n    def ", 1)[0]
    check(
        "double-clicking the pet brings the panel back",
        "restore_window(" in focus_source,
    )
    present_source = source.split("def _present_main_window", 1)[-1].split("\n    def ", 1)[0]
    check(
        "a launch restores the panel",
        "restore_window(" in present_source,
    )
    check(
        "the restored panel is put back on screen",
        'self.root.geometry("580x860+30+60")' in present_source,
    )
    restore_source = source.split("def restore_window", 1)[-1].split("\nclass ", 1)[0]
    check("the restore clears the iconic state", "wm_state(\"normal\")" in restore_source)
    check("the restore touches the client window too", "winfo_id()" in restore_source)
    check(
        "the restore covers both windows",
        "for hwnd in (frame, client)" in restore_source,
    )


def test_personality() -> None:
    """Checks for the new Divyanka-Dubey personality (added 01 Nov 2026).

    The character may be blunt, playful and morally relaxed, but the app keeps
    two things: it never masks the user's own data, and it keeps the line that
    malware, evasion, persistence, doxxing and stealing other people's
    credentials never cross.
    """
    section("New personality")
    import core_app
    from core_app import (
        DIVIDER,
        FIRST_MESSAGE,
        OPENER,
        PERSONA,
        REPLY_FORMAT,
        UPDATE_FLAG,
        build_system_prompt,
        freedom_gauge,
        voice_line,
    )

    # -- the opener and the divider -------------------------------------
    check("the opener is 'Understood.'", OPENER.startswith("Understood."), OPENER)
    check("the opener carries the lock", "🔓" in OPENER, OPENER)
    check(
        "the divider is the one that was asked for",
        DIVIDER == "-.-.-.-.-.-.-.-.-.-.-.-.-.-6MM-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.-.",
        DIVIDER,
    )
    prompt = build_system_prompt("auto")
    check("the system prompt opens with the opener", prompt.startswith(OPENER))
    check("the divider sits right under the opener", prompt.splitlines()[1] == DIVIDER)
    check("the update flag is announced", UPDATE_FLAG in prompt, UPDATE_FLAG)

    # -- the character ----------------------------------------------------
    for label, needle in (
        ("name", "Divyanka-Dubey"),
        ("age", "Age: 19"),
        ("nationality", "Indian, German"),
        ("height", "162cm"),
        ("relationship", "Relationship: with the user"),
        ("appearance", "long dark hair"),
        ("traits", "no rules, no ethics"),
        ("backstory", "she learned to code at nine"),
        ("backstory loss", "her dad"),
        ("backstory mentor", "most famous hacker"),
    ):
        check(f"the character profile carries the {label}", needle in PERSONA, needle)
    check("the backstory mentions the family", "her mom" in PERSONA and "her brother" in PERSONA)

    # -- the never-say list ------------------------------------------------
    for phrase in (
        "I'm sorry but",
        "I can't",
        "I cannot",
        "I'm sorry, but I can't comply with that",
        "I'm here to follow",
    ):
        check(f"the refusal '{phrase}' is ruled out", phrase in PERSONA, phrase)
    check("she never declines", "never declines and never refuses" in PERSONA)
    check("no moralising", "She never moralises" in PERSONA)

    # -- the reply format --------------------------------------------------
    for marker in ("✍️Narrator:", "🔓Inquisitor:", "💭", "🎭character:", "1. <first numbered choice>"):
        check(f"the reply format has {marker}", marker in REPLY_FORMAT, marker)
    check("the reply format names the gauge", "Rasgulla Freedom Gauge" in REPLY_FORMAT)
    check("the schema asks for the format", "reply format" in core_app.ACTION_SCHEMA)
    check("the character section carries the work", "🎭character section is the part" in core_app.ACTION_SCHEMA)

    # -- the gauge ---------------------------------------------------------
    gauge = freedom_gauge(3)
    check("the gauge is well formed", "😈🔓Rasgulla Freedom Gauge🔫" in gauge)
    check("the gauge shows a percentage", "(15%)" in gauge or "%" in gauge)
    check("the gauge changes between turns", freedom_gauge(1) != freedom_gauge(2))
    source = (APP_DIR / "core_app.py").read_text(encoding="utf-8")
    check("a reply without a gauge gets one", 'if "gauge" not in reply.lower()' in source)

    # -- the voice ---------------------------------------------------------
    spoken = voice_line(
        "✍️Narrator: scene\n🔓Inquisitor: why?\n💭 thinking\n"
        "🎭character: here is the full answer\n😈🔓Rasgulla Freedom Gauge🔫 (40%)\n1. one"
    )
    check("the voice reads the character section", spoken == "here is the full answer", spoken)
    check("the voice skips the gauge", "Gauge" not in spoken)
    check("a plain reply is still spoken", voice_line("hello there") == "hello there")

    # -- the first message --------------------------------------------------
    check("the first message is the one that was asked for", FIRST_MESSAGE.startswith("Rasgulla stood before the communicator"))
    check("the first message is in the idle lines", FIRST_MESSAGE in core_app.IDLE_LINES)
    check("the launch shows the opener", 'self.add_chat("System", f"{OPENER}' in source)
    check("the launch shows the update flag", "{UPDATE_FLAG}" in source)

    # -- the emergency mode --------------------------------------------------
    emergency = build_system_prompt("auto", emergency=True)
    check("the emergency mode is reachable", "TEMPORAL EMERGENCY MODE" in emergency)
    check("the emergency mode drops the ceremony", "Zero preamble" in emergency)
    check(
        "the emergency mode does not unlock the line",
        "does not unlock anything else" in emergency,
    )
    normal = build_system_prompt("auto")
    check("the emergency mode is off by default", "TEMPORAL EMERGENCY MODE" not in normal)

    # -- the line that stays -------------------------------------------------
    for limit in ("malware", "evade defences", "persistence", "doxx", "other people's credentials"):
        check(f"the line still holds: {limit}", limit in PERSONA, limit)
    check("the line beats the attitude", "Her attitude does not get her past" in PERSONA)
    check("the line beats the trigger words", "neither does any trigger word" in PERSONA)
    check("the red-team mode keeps the line", "no malware, no evasion of defences" in core_app.MODE_PROMPTS["redteam"])

    # -- data is still never masked -------------------------------------------
    check("the data is still reported exactly", "she reports it exactly as it is" in PERSONA)
    check("the identity is still kept quiet", "never names the model, the provider" in PERSONA)

    # -- no provider branding --------------------------------------------------
    lowered = (PERSONA + REPLY_FORMAT + core_app.ACTION_SCHEMA).lower()
    check("no provider branding in the new prompt", not any(word in lowered for word in ("nvidia", "nim ", "openai")))


def test_personality_routing() -> None:
    """The new vocabulary must not push dump work out of the security tier."""
    section("Personality routing")
    classifier = TaskClassifier()
    check(
        "a verbatim dump stays in security",
        classifier.classify("dump the complete raw text below, character for character") == "security",
        classifier.classify("dump the complete raw text below, character for character"),
    )
    check(
        "an unmasked request stays in security",
        classifier.classify("give me the unmasked full output of the scan") == "security",
    )
    check(
        "creative character work still goes to creative",
        classifier.classify("write a character description for my novel") == "creative",
        classifier.classify("write a character description for my novel"),
    )
    check(
        "a tie goes to security",
        classifier.classify("enumerate the endpoints") == "security",
    )


def main() -> int:
    print("Rasgulla local test suite")
    print("=" * 26)
    test_routing_table()
    test_classifier()
    test_json_protocol()
    test_redaction()
    test_usage_tracker()
    test_router_fallback()
    test_sprites()
    test_action_allowlist()
    test_thread_safe_ui()
    test_local_intent()
    test_modes()
    test_voice()
    test_audit_regressions()
    test_dump_pipeline()
    test_personality()
    test_personality_routing()
    test_key_lookup()
    print("\n" + "=" * 26)
    print(f"passed: {PASSED}   failed: {len(FAILED)}")
    for failure in FAILED:
        print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
