# Red-team Agentic Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Rasgulla into a working agentic red-teamer with voice-only approvals, fixing the broken agentic path while restoring free-only multi-provider auto-switch.

**Architecture:** This is a 4-task TDD implementation in `app/brain/session.py`, `core_router.py`, `models.json` and `app/brain/test_session.py`. Each task is independently testable without Electron. The renderer already forwards plan events via `brain:event` channels.

**Tech Stack:** Python 3.11, pytest, ruff, Electron (unchanged), edge-tts, Vosk.

## Global Constraints

- **Orb design frozen**: `app/renderer/index.html` and `app/renderer/styles.css` are NOT modified.
- **Action allow-list**: launch, open_url, type_text, key, hotkey, click, scroll, wait, read_file, write_file only.
- **Free-only, 20 RPM cap**: enforced from config.
- **No provider branding**: nvidia/nim/ai/openai/etc. never appears in UI strings.
- **Persona hard line**: no malware, evasion, persistence, doxxing, stealing credentials.
- **Voice**: edge-tts Indian English (`en-IN-NeerjaExpressiveNeural` default).

---

### Task 1: Repair the agentic path (shim wiring + neutral errors)

**Files:**
- Modify: `app/brain/session.py:283-294` (add instance attributes, modify error handling in `handle()`)
- Test: `app/brain/test_session.py` (new tests)

**Interfaces:**
- Consumes: Nothing (fixes existing broken code)
- Produces: Working `_type_text` binding; neutral error messages from `handle()`

- [ ] **Step 1: Write the failing test**

```python
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

def test_handle_returns_neutral_error():
    """handle() must return plain English for unexpected exceptions."""
    session = BrainSession()
    # Trigger an error path that should return neutral text, not exception name
    reply = session.handle({"command": "route", "text": "test", "mode": "invalid_mode_for_testing"})
    # Should not contain Python exception names
    error_msg = str(reply.get("message", ""))
    check("no Python exception name in reply", "AttributeError" not in error_msg and "TypeError" not in error_msg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest app/brain/test_session.py::test_shim_type_text_binding -v`
Expected: FAIL with `AttributeError` or missing attribute

- [ ] **Step 3: Write minimal implementation**

In `app/brain/session.py`, after line 383 (after `_type_text` function definition):
```python
# Wire clipboard-aware typing to the shim for action execution
_shim._type_text = _type_text
```

Modify `handle()` to return neutral error text (around line 452):
```python
except Exception as error:
    return {"type": "error", "message": "that step could not be prepared, so nothing ran."}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest app/brain/test_session.py::test_shim_type_text_binding app/brain/test_session.py::test_handle_returns_neutral_error -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/brain/session.py app/brain/test_session.py
git commit -F commit_msg.txt
```

---

### Task 2: Multi-provider free auto-switch in core_router + models.json

**Files:**
- Modify: `models.json` (new providers block, free-only chains)
- Modify: `core_router.py` (ProviderRejected error, Provider class, reasoning field fallback, multi-endpoint routing)
- Modify: `test_rasgulla.py` (update routing-table assertions for multi-provider)
- Test: `test_rasgulla.py::test_routing_table`, `test_rasgulla.py::test_redaction`

**Interfaces:**
- Consumes: `models.json` config with providers block
- Produces: `ModelRouter` that accepts dict chain entries, falls back across providers

- [ ] **Step 1: Write the failing test**

Add to `test_rasgulla.py` after line 102:
```python
def test_free_only_chains_are_multi_provider() -> None:
    """Every tier chain must have at least 3 free models across at least 2 providers."""
    section("Free-only multi-provider chains")
    config = json.loads((APP_DIR / "models.json").read_text(encoding="utf-8"))
    tiers = config.get("tiers", {})
    for tier, spec in tiers.items():
        chain = spec.get("chain", [])
        # Extract model IDs from dict entries
        models = []
        for entry in chain:
            if isinstance(entry, dict):
                models.append(entry.get("model", ""))
            else:
                models.append(entry)
        check(f"tier '{tier}' has at least 3 free models", len(models) >= 3)
        # Check providers field exists in config
        providers = config.get("providers", {})
        check(f"providers block exists", bool(providers))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe test_rasgulla.py::test_free_only_chains_are_multi_provider -v`
Expected: FAIL (no providers block, chains are strings)

- [ ] **Step 3: Write models.json with providers block**

Rewrite `models.json`:
```json
{
  "endpoint": "https://openrouter.ai/api/v1/chat/completions",
  "key_env": ["RASGULLA_API_KEY", "OPENROUTER_API_KEY"],
  "defaults": {
    "requests_per_minute": 20,
    "timeout_seconds": 90,
    "deep_timeout_seconds": 200,
    "cooldown_rate_limit_seconds": 90,
    "cooldown_timeout_seconds": 300,
    "max_attempts_per_request": 4
  },
  "providers": {
    "openrouter": {
      "endpoint": "https://openrouter.ai/api/v1/chat/completions",
      "key_env": ["OPENROUTER_API_KEY", "RASGULLA_API_KEY"],
      "free_suffix": ":free",
      "free_models": ["openrouter/free"]
    },
    "gemini": {
      "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
      "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
      "free_models": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
    },
    "nvidia": {
      "endpoint": "https://integrate.api.nvidia.com/v1/chat/completions",
      "key_env": ["NVIDIA_API_KEY"],
      "free_models": []
    }
  },
  "tiers": {
    "chat": {
      "label": "Flash",
      "temperature": 0.55,
      "max_tokens": 4000,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "openrouter", "model": "nemotron-3.5-lightning:free"}
      ]
    },
    "code": {
      "label": "Abyss",
      "temperature": 0.2,
      "max_tokens": 4000,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"}
      ]
    },
    "reason": {
      "label": "Zenith",
      "temperature": 0.25,
      "max_tokens": 4000,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"}
      ]
    },
    "vision": {
      "label": "Super VL",
      "temperature": 0.2,
      "max_tokens": 2500,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"}
      ]
    },
    "creative": {
      "label": "Muse",
      "temperature": 0.9,
      "max_tokens": 4000,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "nvidia", "model": "nvidia/nemotron-3-ultra-550b-a55b"}
      ]
    },
    "security": {
      "label": "Ultra",
      "temperature": 0.3,
      "max_tokens": 4000,
      "chain": [
        {"provider": "openrouter", "model": "openrouter/free"},
        {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
        {"provider": "nvidia", "model": "nvidia/nemotron-3-ultra-55b-a55b"}
      ]
    }
  },
  "aliases": {
    "openrouter/free": "Auto",
    "nvidia/nemotron-3-ultra-550b-a55b": "Lightning",
    "gemini-3.5-flash-lite": "Lumen",
    "gemini-3.1-flash-lite": "Beacon",
    "gemini-flash-lite-latest": "Spark"
  }
}
```

- [ ] **Step 4: Implement Provider class and multi-endpoint routing in core_router.py**

Key changes needed in `core_router.py`:

1. Add new error class (after line 72):
```python
class ProviderRejected(RouterError):
    """Raised when a provider's key is rejected (401/403)."""
```

2. Add Provider class (after ProviderRejected):
```python
class Provider:
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.endpoint = str(config.get("endpoint", ""))
        self.key_env = [str(k) for k in config.get("key_env", [])]
        self.free_suffix = config.get("free_suffix", "")
        self.free_models = set(config.get("free_models", []))
        self.key = ""
        self.disabled = False  # Set after first 403
    
    def model_is_free(self, model_id: str) -> bool:
        if model_id in self.free_models:
            return True
        if self.free_suffix and model_id.endswith(self.free_suffix):
            return True
        return False
```

3. Add `reasoning` field fallback in `_request()` (around line 678-680):
```python
text = message.get("content") or ""
if not text.strip() and message.get("reasoning"):
    text = message["reasoning"]
```

4. Modify `ModelRouter` to handle dict chain entries and multi-provider routing (lines 515-546):
```python
# In __init__, add:
self.providers: dict[str, Provider] = {}

# After config loading, initialize providers:
for name, prov_config in raw.get("providers", {}).items():
    self.providers[name] = Provider(name, prov_config)

# In _request, select endpoint based on provider:
# Replace `self.endpoint` with provider-specific endpoint when dict entry
```

- [ ] **Step 5: Update test_routing_table for multi-provider chains**

Modify `test_routing_table()` in `test_rasgulla.py`:
- Update `user_picks` to use new aliases
- Check that chains are dict entries with provider/model keys
- Assert each tier has ≥3 models across ≥2 providers

- [ ] **Step 6: Run tests to verify**

Run: `.venv\Scripts\python.exe test_rasgulla.py::test_routing_table -v` and other routing tests
Expected: PASS

- [ ] **Step 7: Commit**

---

### Task 3: Red team on a spoken switch

**Files:**
- Modify: `app/brain/session.py:502-544` (`_cmd_route` and mode handling)
- Modify: `core_app.py:546-571` (`_build_messages` and system prompt)
- Test: `app/brain/test_session.py`

**Interfaces:**
- Consumes: Text with "red team on"/"red team off" phrases
- Produces: Sticky `self.mode` state, mode-aware routing

- [ ] **Step 1: Write the failing test**

```python
def test_red_team_mode_switch():
    """Spoken phrases switch the session mode for agentic red-teaming."""
    session = BrainSession()
    # Start in auto mode
    check("starts in auto mode", session.mode == "auto")
    
    # Test "red team on" phrases
    for phrase in ["red team on", "redteam on", "red team mode on"]:
        session.handle({"command": "route", "text": phrase, "mode": "auto", "speak": False})
    check("red team mode activated", session.mode == "redteam")
    
    # Test "red team off" phrases
    for phrase in ["red team off", "redteam off", "normal mode"]:
        session.handle({"command": "route", "text": phrase, "mode": "auto", "speak": False})
    check("back to auto mode", session.mode == "auto")
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no mode switch logic)

- [ ] **Step 3: Implement mode switch logic in `_cmd_route`**

Add to `session.py` near line 502, before classifier:
```python
REDTEAM_ON_PHRASES = ("red team on", "redteam on", "red team mode on")
REDTEAM_OFF_PHRASES = ("red team off", "redteam off", "normal mode")

def mode_switch(text: str) -> str | None:
    normalized = text.lower()
    if any(phrase in normalized for phrase in REDTEAM_OFF_PHRASES):
        return "auto"
    if any(phrase in normalized for phrase in REDTEAM_ON_PHRASES):
        return "redteam"
    return None

# In _cmd_route, add after line 504:
mode_override = mode_switch(text)
if mode_override:
    self.mode = mode_override
```

- [ ] **Step 4: Implement `build_system_prompt` mode handling**

In `core_app.py`, modify `build_system_prompt` to handle mode changes:
```python
def build_system_prompt(mode: str, emergency: bool = False) -> str:
    parts = [PERSONA]
    if emergency:
        parts.append(EMERGENCY_PROMPT)
    if mode == "auto" or mode == "redteam":
        # "auto" from renderer means "use session's current mode"
        pass  # Mode will be set by session
    else:
        parts.append(MODE_PROMPTS.get(mode, MODE_PROMPTS["normal"]))
    parts.append(REPLY_FORMAT)
    parts.append(ACTION_SCHEMA)
    return "\n".join(parts)
```

- [ ] **Step 5: Run tests to verify**

- [ ] **Step 6: Commit**

---

### Task 4: Voice approval loop

**Files:**
- Modify: `app/brain/session.py` (new state fields, approval_decision, _attach_voice)
- Test: `app/brain/test_session.py`

**Interfaces:**
- Consumes: Pending actions list, user voice transcript
- Produces: Spoken approval questions, action execution, outcome reporting

- [ ] **Step 1: Write the failing test**

```python
def test_approval_decision_phrases():
    """Voice phrases map correctly to approve/decline/other."""
    from session import approval_decision
    
    check("haan approves", approval_decision("haan") == "approve")
    check("run karo approves", approval_decision("run karo") == "approve")
    check("theek hai approves", approval_decision("theek hai") == "approve")
    
    check("ruk declines", approval_decision("ruk") == "decline")
    check("nahi declines", approval_decision("nahi") == "decline")
    check("skip declines", approval_decision("skip") == "decline")
    
    check("unrelated sentence is other", approval_decision("hello world") == "other")
    check("empty is other", approval_decision("") == "other")

def test_voice_approval_state():
    """Brain state for pending action approval."""
    session = BrainSession()
    # Simulate a plan with an action
    session.current_actions = [{"type": "write_file", "path": "test.txt", "content": "hello"}]
    session._pending_index = 0
    session._pending_deadline = time.monotonic() + 25.0
    
    check("has pending actions", len(session.current_actions) == 1)
    check("pending index set", session._pending_index == 0)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no approval_decision function, no pending state)

- [ ] **Step 3: Implement approval_decision and state**

Add to `session.py`:
```python
APPROVE_PHRASES = ("haan", "ha", "yes", "ok", "theek hai", "run", "run karo", "kar do", "chalao", "approve", "chal", "karo")
DECLINE_PHRASES = ("ruk", "nahi", "no", "mat", "skip", "rehne do", "cancel", "stop", "ruk jao")

def approval_decision(text: str) -> str:
    """Return 'approve', 'decline', or 'other' based on spoken phrase."""
    normalized = text.lower().strip()
    if normalized in APPROVE_PHRASES or any(p in normalized for p in APPROVE_PHRASES):
        return "approve"
    if normalized in DECLINE_PHRASES or any(p in normalized for p in DECLINE_PHRASES):
        return "decline"
    return "other"

# In BrainSession.__init__, add after line 415:
self._pending_actions: list[dict[str, Any]] = []
self._pending_index: int = 0
self._pending_deadline: float = 0.0
```

- [ ] **Step 4: Implement _attach_voice helper**

Add after `_speech_seconds`:
```python
def _attach_voice(payload: dict[str, Any], spoken: str, speak: bool) -> dict[str, Any]:
    """Add TTS audio/speech to payload, set mic busy guard."""
    if speak and spoken and TTS_AVAILABLE:
        voice_id = resolve_voice_id(spoken, _self.voice_preset if hasattr(_self, 'voice_preset') else "auto")
        try:
            tts_result = tts_synthesize(spoken, voice_id)
            payload["speech"] = tts_result.get("text", spoken)
            payload["audio"] = tts_result.get("audio", "")
            payload["words"] = tts_result.get("words", [])
            payload["mime"] = tts_result.get("mime", "audio/mpeg")
            _self._mic_busy_until = time.monotonic() + _self._speech_seconds(payload["words"], spoken) + 1.0
        except Exception:
            pass
    return payload
```

- [ ] **Step 5: Integrate into _finalise_plan**

Modify `_finalise_plan` to:
1. Store pending actions in `self._pending_actions`
2. Append approval question to spoken text: "Step one: <desc>. Run karun?"
3. Track `_pending_index`

- [ ] **Step 6: Handle approval in _cmd_route**

Add before the router call:
```python
# Check for approval/decline while a step is pending
if self._pending_actions and self._pending_index < len(self._pending_actions):
    decision = approval_decision(text)
    if decision == "approve":
        action = self._pending_actions[self._pending_index]
        outcome = self._run_action(action)
        self.action_results.append(f"Ran: {outcome}")
        self._pending_index += 1
        if self._pending_index >= len(self._pending_actions):
            return {"type": "plan_done", "results": [...]}
    elif decision == "decline":
        self.action_results.append(f"Skipped step {self._pending_index}")
        self._pending_index += 1
    else:
        # Clear pending, treat as new request
        self._pending_actions = []
```

- [ ] **Step 7: Run tests to verify**

- [ ] **Step 8: Commit**

---

### Task 5: Gate + live agentic probe

**Files:**
- Create: `app/test_orb_agent.py`
- Modify: `app/main.js` (new probe mode or extend --probe-live)

**Interfaces:**
- Consumes: Nothing (creates its own test plan with written-injected actions)
- Produces: Verification that plan payload reaches renderer and approval runs action

- [ ] **Step 1: Write the live agentic probe test**

```python
def test_orb_agentic_probe():
    """A plan with actions reaches the renderer, and spoken approval runs a safe write."""
    # Similar structure to test_orb_smoke.py but injects a write_file action
    # Sends "haan" approval, asserts file lands in work folder
    pass  # Implementation follows test_orb_smoke.py pattern
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL (no agentic probe mode)

- [ ] **Step 3: Implement --probe-agent mode in main.js**

Add to `app/main.js`:
```javascript
const AGENT_PROBE = process.argv.includes('--probe-agent');

if (AGENT_PROBE) {
  await runAgentProbe();
}

async function runAgentProbe() {
  // Inject a write_file plan, capture approval, verify file
}
```

- [ ] **Step 4: Verify probe runs**

- [ ] **Step 5: Commit**

---

## Final Gate

Run all verification suites:
1. `.venv\Scripts\python.exe app/brain/test_session.py` - 97+ passed
2. `.venv\Scripts\python.exe app/brain/test_tts.py` - 34+ passed
3. `.venv\Scripts\python.exe test_rasgulla.py` - 295+ passed
4. `node --test app/test_node_units.js` - 14 passed
5. `.venv\Scripts\python.exe -m ruff check app/brain/session.py app/brain/test_session.py...` - clean
6. `.venv\Scripts\python.exe app/test_orb_smoke.py` - passes
7. `.venv\Scripts\python.exe app/test_orb_live.py` - passes (OpenRouter/Gemini wired)
8. Electron `--probe-live` - succeeds

## Post-conditions

- All suites green
- Agentic loop functional with voice approval
- Red-team mode switchable via speech
- No provider branding in any visible output
- Orb design untouched