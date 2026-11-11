# Rasgulla Orb - Design

Date: 2026-11-11
Status: approved by the user (4 decisions confirmed in the rebuild questionnaire)

## 1. Goal

Rebuild the Rasgulla desktop app UI as an Electron-style floating app with a
live 3D Speaking Orb as its face. The reference is the shipped page
`https://aqualang89.github.io/shipnotes-components/` and the user's WhatsApp
screen recording of the same page: black background, a brand header, a centred
title, a three-state segmented pill (LISTENING / THINKING / SPEAKING), a
particle sphere that reacts to sound, and live captions that materialise and
dissolve through particle dust under the orb.

## 2. Scope boundary - what is rebuilt and what is preserved

Rebuilt (UI only):

- The Tk panel, its widgets, layout and theming.
- The pet window (Tk `Toplevel` with chroma-keyed sprite frames).
- Audio playback (`winsound` through `voice_engine.play_wav`).

Preserved, reused verbatim from the canonical project root:

- `core_router.py` - tier routing, rate limiting, usage accounting,
  `parse_json_plan`, `redact`, `read_api_key`.
- `models.json` - the free-endpoint routing table, unchanged.
- `core_app.py` - the persona (`PERSONA`, `NEVER_SAY`, `REPLY_FORMAT`,
  `ACTION_SCHEMA`, `MODE_PROMPTS`, `TIER_BRIEF`, `EMERGENCY_PROMPT`,
  `FIRST_MESSAGE`), `build_system_prompt`, `voice_line`, `freedom_gauge`,
  `local_intent`, `search_query`, `is_link_local_url`, and the supervised
  action guards (`_safe_path`, `validate_actions`, `describe_action`,
  `run_action`).
- `voice_engine.py` - voice presets, `clean_for_speech`, `pick_voice`,
  `synthesize_wav` (used as the TTS fallback), `SAMPLE_LINE`.
- `pet_sprite.py` and `assets/pet-frames` - the 24 animation frames and the
  manifest; only the rendering and tint change.
- The behaviour line: the persona never crosses into malware, credential
  stealing, defence evasion, persistence, doxxing or taking other people's
  secrets. `,,` emergency mode removes ceremony only. The user's own data is
  always reported verbatim - never masked, summarised or truncated.

Not in scope: changing the personality text, adding models, raising the rate
limit above 20 RPM, adding provider names to the UI, or making the old Tk app
disappear. The old app stays on disk and runnable.

## 3. Architecture

Two processes, one JSON-per-line channel.

```
  +---------------------------+          stdin/stdout (NDJSON)
  |  Electron main process   |  <------> |  Python brain child      |
  |  app/main.js              |          |  app/brain/brain.py       |
  |  - owns both windows      |          |  - imports core_router    |
  |  - spawns / supervises    |          |  - imports core_app      |
  |  - relays IPC to renderer |          |  - imports voice_engine  |
  +---------------------------+          |  - mic capture + STT     |
          |                                        |
          | ipcMain / contextBridge                | pyautogui, subprocess
          v                                        v
  +---------------------------+          +---------------------------+
  |  Renderer (orb + chat)    |          |  Windows desktop          |
  |  app/renderer/*           |          |  work folder only         |
  +---------------------------+
          |
          +--> app/brain bridge --> pet overlay window
```

Why a child process over an in-process binding: the Python brain uses
`pyautogui`, `sounddevice`, `vosk` and `requests`, none of which belong in the
Electron renderer. A stdio child keeps the renderer sandboxed and gives the
main process a clean kill switch. No HTTP server, no port, no firewall prompt.

Why Electron rather than pywebview/WebView2: the user chose Electron after the
cost difference was stated. The WebView2 route was verified working first, so
this choice buys the Electron ecosystem, not new capability.

### 3.1 Files

```
app/
  package.json              name rasgulla-orb, main main.js, electron ^33
  main.js                   windows, protocol, brain supervision, IPC
  preload.js                contextBridge surface (no node in the renderer)
  renderer/
    index.html              orb + chat panel
    styles.css              dark theme, segmented pill, captions
    renderer.js             UI state machine, orb driving, audio playback
    pet.html                pet overlay
    pet.js                  frame animation, tint, bubble, drag
    pet.css
  brain/
    brain.py                stdio protocol loop
    session.py              headless shim that binds core_app methods
    tts.py                  edge-tts word timings -> mp3 + word list
    test_brain.py           unit tests for the bridge
  vendor/
    speaking-orb.js         vendored component (MIT)
    LICENSE-shipnotes-components.txt
```

### 3.2 The headless shim (`app/brain/session.py`)

`core_app.RasgullaApp` is a Tk class, but its guard logic does not need a
window. The shim binds the real functions to a plain object, so the guards run
unchanged:

- `_safe_path`, `validate_actions`, `describe_action`, `run_action` - bound
  directly from `RasgullaApp`.
- `_build_messages` - bound directly; it only needs `self.history` and
  `self.action_results`, which the shim provides as plain lists, and `mode` is
  always passed in so the Tk variable is never touched.
- `_type_text` - the one method the shim replaces, because the original needs a
  Tk root for the clipboard. The shim uses `pyautogui.write` for ASCII and the
  Win32 clipboard API (ctypes) plus a paste hotkey for everything else, which
  is what the original did.

`validate_actions` and `run_action` therefore enforce exactly the same rules as
the old app: only the six safe apps, only http/https links, no link-local
addresses, navigation keys and the fixed safe-hotkey table only, typed text
capped at 500 characters with sensitive patterns refused, clicks inside the
screen, scroll between -20 and 20, waits up to 5 seconds, and file paths
resolved inside the work folder with a blocked-extension list.

## 4. Bridge protocol

Newline-delimited JSON in both directions. Every message is one object on one
line; stdout carries protocol only, so all logging goes to stderr.

Main to brain:

| command | fields | meaning |
| --- | --- | --- |
| `hello` | - | handshake, returns `ready` |
| `ask` | `text`, `mode`, `speak` | run the request through the router |
| `approve` | `index`, `ok` | answer a per-action confirmation |
| `run_plan` | `approved` (list of indexes) | run the approved actions |
| `cancel` | - | stop the current turn |
| `mic` | `on` | start or stop the local microphone |
| `set` | `key`, `value` | runtime settings (voice preset, mode) |
| `usage` | - | refresh the usage rows |
| `shutdown` | - | exit the child |

Brain to main:

| type | fields | meaning |
| --- | --- | --- |
| `ready` | `voice_ready`, `key_found` | handshake result |
| `state` | `state` | orb state: listening / thinking / searching / speaking / done |
| `level` | `value` | microphone level 0..1, about 20 messages a second |
| `transcript` | `text`, `final` | local speech-to-text result |
| `reply` | `text`, `speech`, `route` | the full formatted reply and the spoken line |
| `plan` | `actions`, `descriptions`, `rejections` | proposed actions, already validated |
| `confirm` | `index`, `description` | per-action confirmation request |
| `action_result` | `index`, `text` | result of one executed action |
| `speech` | `audio` (base64), `words`, `text`, `mime` | audio plus per-word timings |
| `usage` | `rows`, `totals` | model health rows and totals |
| `log` | `message` | neutral status line |
| `error` | `message` | redacted failure |

## 5. Window design

### 5.1 Orb window

Frameless, transparent, always on top, hidden from the taskbar, not
resizable by the user. Two sizes, animated between them:

- Compact: 440 x 660. Orb, state pill, captions, one input line.
- Expanded: 980 x 700. Orb column on the left, chat panel on the right.

The header carries the brand mark on the left and, on the right, a pin toggle
(always-on-top on/off), a collapse button and a close button. The pin toggle
exists because an always-on-top chat panel fights with every other window.

Layout, top to bottom: header, title strip ("Your work partner needs a face."),
the three-state segmented pill (LISTENING / THINKING / SPEAKING, active segment
lit), the orb canvas, the caption strip, and the input row.

The orb is the vendored custom element:

    speaking-orb state="listening" level="0.3" particles="12000"

### 5.2 Chat panel (same window, right side)

- Transcript of the full formatted replies, verbatim.
- Plan card with one row per proposed action, each with an approve/skip toggle,
  plus "Run approved plan".
- Confirmation card for the per-action gate.
- Footer line with the route caption (tier, model nickname, seconds, tokens)
  and a usage drawer with the model health rows.

### 5.3 Pet overlay

A second frameless, transparent, always-on-top window, 200 x 240, parked at
the bottom right. It loads the existing 24 frames from `assets/pet-frames`
through the custom protocol, removes the chroma key colour on a canvas, and
maps the remaining pixels onto the amber-to-pink gradient of the new theme.
Idle and work frame sets switch with the orb state. Right-click plays a random
idle line, double-click focuses the orb window, and the window can be dragged.

The sprite is re-tinted, not redrawn: the frames stay the user's own asset.

## 6. Visual language

- Background: `#08070b` to `#120b16`, with a soft radial glow behind the orb.
- Primary accent: amber `#f5a524`. Secondary accent: pink `#ec4899`.
- Text: `#f4eef5` for body, `#a89bb0` for secondary.
- Fonts: Segoe UI for chrome, Segoe UI Semibold for headings.
- State colours: listening = amber, thinking = violet `#8b5cf6`,
  speaking = pink, searching = cyan `#22d3ee`.
- Captions: 20 px, white, one to three lines, centred under the orb.

## 7. Speech pipeline

The orb speaks only the `🎭character:` line. `core_app.voice_line` extracts
exactly that section and is reused unchanged.

1. The brain prepares the spoken text with `voice_engine.clean_for_speech`
   (markdown stripped, emoji removed, capped at 900 characters) and picks the
   voice with `voice_engine.pick_voice` (Devanagari goes to the Hindi voice,
   everything else to Indian English).
2. `edge_tts.Communicate(text, voice_id, boundary="WordBoundary")` streams
   audio chunks and one `WordBoundary` event per word. The brain collects the
   mp3 bytes and a word list of `w`, `start`, `end` offsets.
3. The brain sends base64 mp3 plus the word list to the renderer.
4. The renderer builds a Blob URL, wraps it in an `Audio` element and calls
   `orb.say(text, { audio, words })`. The orb then follows the real signal and
   lands each caption exactly on its word. Captions are rendered by the
   component's own particle-dust effect.
5. Fallback: if the neural path fails, `voice_engine.synthesize_wav` produces
   WAV bytes, which the renderer plays the same way, with the whole line as
   one caption. If both fail, the orb still runs on its estimated envelope and
   the caption shows the text.

Audio playback is the renderer's job, so the orb reacts to the same signal it
plays. The browser's own `speechSynthesis` is never used.

## 8. Request flow

1. The user types, or speaks. The brain transcribes locally with Vosk
   (English-India or Hindi model, 16 kHz mono) and sends the transcript back;
   the final transcript is sent to the model automatically, as in the old app.
2. The brain classifies the request with `TaskClassifier`, builds the messages
   with the bound `_build_messages`, and calls `router.call(...)` with the
   tier brief. The orb is in `thinking` during this.
3. `parse_json_plan` turns the model output into mode, reply, clarify and
   actions. If the model forgot the gauge line, `freedom_gauge` adds it.
4. The reply is sent to the renderer, which shows the full formatted reply.
   `voice_line` is applied and the spoken line goes out through the TTS path.
5. The actions are validated with the real `validate_actions`. Rejected
   actions are reported, never dropped silently. The orb moves to `searching`
   while actions run and back to `listening` when the turn ends.

## 9. Supervision and safety

- The model never touches the machine directly. Every action is validated,
  listed, approved by the user, confirmed again immediately before it runs,
  and then executed by the reused `run_action`.
- Screenshot attachment is off by default and never silent: the user is asked
  before a snapshot is taken.
- The model is never asked to log in, handle a password or OTP, send a
  message, submit a form, install software or change a security setting; that
  instruction stays in `ACTION_SCHEMA`.
- Files are read and written only inside the work folder.
- The renderer runs with `contextIsolation: true`, `nodeIntegration: false`
  and `sandbox: true`. The preload exposes a narrow, named API.
- No provider name, endpoint or raw model id ever reaches the UI: the router's
  `redact` and `friendly_model` are used for every user-visible string.

## 10. Error handling

| situation | behaviour |
| --- | --- |
| No API key | The window still opens; the footer says which variable is missing; the orb stays in `listening`. |
| Model failure | The error is redacted, shown in the transcript, and the orb returns to `listening`. |
| Rate limited | The router cools the model down and falls back; the fallback list is shown in the footer. |
| TTS failure | Fall back to the device path, then to the estimated envelope; the text is always captioned. |
| Brain process dies | The main process restarts it once and shows "reconnected" in the footer. |
| Bad action | Rejected by the real validator and reported in the plan card. |
| Mic unavailable | The transcript line reports it; typing still works. |

## 11. Testing and acceptance

- The existing suite stays green: `test_rasgulla.py` must still report 295
  passed and 0 failed, because none of the preserved modules changed.
- New Python tests (`app/brain/test_brain.py`) cover the protocol round trip,
  the reply split, the reused guards through the shim, and the word-timing
  parser against a recorded boundary stream.
- `main.js --probe` launches the real app, then runs a DOM probe and prints
  JSON: the custom element is defined, WebGL is live, the orb reaches each
  state, the caption text matches the spoken words, and the panel shows the
  full reply.
- Live run: a real request goes through the router, the orb speaks, the
  captions land on the words, and the plan card appears with actions validated.
- Ruff must be clean on the new Python files, using the same broad default rule
  set the CI would apply.

## 12. Risks

- The window manager can minimise a freshly launched window. The Electron
  window is shown with `show: false` and revealed only after
  `ready-to-show`, which avoids that class of bug. The old Tk self-minimise
  bug in the previous app is a separate, still-unfixed issue and is not
  claimed to be fixed here.
- Electron's first start is slower than the Tk app (Chromium boot). The orb
  window is revealed as soon as the renderer is ready, not when the brain is.
- The pet's chroma-key removal is done per pixel on load, once per frame set.

## 13. Delivery

The app lives inside the canonical project at `app/`, so the working Tk app is
untouched and revertable. The vendored component ships with its MIT licence
notice. The project gains a git repository with a small ignore file so the
rewrite can be reviewed and reverted; the identity is set for that repository
only.
