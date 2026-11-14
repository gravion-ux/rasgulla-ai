# Rasgulla — red-team agentic release

Date: 2026-11-14
Status: design approved by the user on 2026-11-14. The orb design is explicitly **not** frozen: this release does not change it, and later redesigns stay allowed.

## 1. Why this release exists

A full debug pass over the project ran clean on every suite (brain session 97/97, brain tts 34/34, node 14/14, ruff clean, Tk 295/295, smoke 26/26, talk 9/9, wake 11/11) and still found three real breakages in the agentic path, because no suite covered them:

1. **The action path is dead in the Electron app.** `_BrainShim` receives `_safe_path`, `validate_actions`, `describe_action` and `run_action` as module-level names, but the last two call `self._safe_path(...)` and `self._type_text(...)`. Reproduced live: `AttributeError: '_BrainShim' object has no attribute '_safe_path'`. Any plan containing a file action returns an error instead of a plan, and approving a `type_text` action fails the same way.
2. **The remote model path is entirely dead.** The stored key is rejected with `403 Authorization failed` on every model, so Rasgulla can only answer local intents.
3. **The agentic loop is dormant in the UI.** The brain sends `actions`, `descriptions` and `rejections` with every plan, but the renderer ignores them, so there is no way to approve or reject a step. Part 4 fixes this inside the brain, which is why the face never has to grow an approval panel.

The release fixes all three and turns Rasgulla into a working agentic red-teamer with voice-driven approvals.

## 2. What is not changing

- **The orb design.** `app/renderer/index.html` markup and `app/renderer/styles.css` are not touched. The face stays exactly as it is. This is not a freeze: the design can be redesigned later.
- **The persona hard line.** No malware, no evasion of defences, no persistence, no doxxing, no taking other people's credentials or secrets. The red-team mode changes her voice, routing and depth, never that line.
- **The action allow-list.** launch, open_url, type_text, key, hotkey, click, scroll, wait, read_file, write_file. No shell, no install, no logins, no passwords, no messaging, no form submits, no deletes, no security-setting changes.
- **Supervised execution.** Plan first, one approval per action, files stay inside the work folder, screenshots off by default.
- **Free endpoints only, 20 RPM cap.**
- **No provider branding** in any user-visible string, log or error.
- **The Tk app keeps working** with the same behaviour it has now.

## 3. Part 1 — Repair the agentic path

**Root cause.** The shim was built by binding the Tk methods to an instance, but the bound names were never attached to that instance, so every `self.` lookup inside those methods failed.

**Fix.** Give `_BrainShim` the instance attributes the methods actually read:

- `_safe_path`: the already-bound method, attached as an attribute.
- `_type_text`: the clipboard-aware implementation that already exists as a module-level function in `session.py` (ASCII typed directly, non-ASCII pasted through the Win32 clipboard with the clipboard restored afterwards).

**Error surface.** `handle()` currently returns raw exception text such as `AttributeError: ...` to the UI. A failure in the plan path must produce a neutral sentence instead, for example: "that step could not be prepared, so nothing ran."

**Tests.**

- A file action inside the work folder validates and is returned in the plan.
- A file action outside the work folder is rejected with a reason, not dropped in silence.
- An approved `type_text` action runs through the shim without an AttributeError.
- The plan path never returns a raw Python exception name.

## 4. Part 2 — Multi-provider auto-switch, free only

**Goal.** The remote brain works again, on free models only, and moves to the next provider by itself when one provider refuses.

**Config shape.** `models.json` grows a `providers` block. Each provider carries its endpoint, the environment names its key is read from, and how its free models are recognised. A chain entry names its provider and its model. Plain string entries keep working, so a legacy single-endpoint config still loads.

**Router behaviour.**

- A 401 or 403 drops that whole provider for the session; the next provider in the chain answers.
- A 429 puts the provider into a cooldown and the next provider answers.
- A missing model retires that model only, as it does today.
- The free-only rule is enforced from the config: a model that is not on its provider's free list, or does not carry the provider's free suffix, is never called. A paid model cannot be reached even by accident.
- `key_found` is true when at least one provider has a key.
- **The reasoning field is read as a fallback.** The free models answer with a reasoning field and often leave the content field empty, because the thinking eats the token budget first. The router already falls back to one spelling of that field; it has to fall back to the other spelling too, or every free model reads as an empty response.

**Providers.** Three are configured, all with keys that are stored in the user registry and never printed or logged:

1. The free-router service. Its catalogue lists seventeen free models; the free router itself and four of them answered a real request in about one second.
2. The flash-family service on its free tier. Three flash models answered; two older ones are gone for new users and one model is outside the free tier, so the chain carries only the three that work.
3. The build-catalog service from the old config. Its key is rejected today, so the chain carries it last and the auto-switch drops it after the first refusal instead of burning attempts on it.

**Naming.** Provider names appear in `models.json` and in router code only. The redaction list gains the new names, so an error that leaks a provider name is rewritten before it reaches the user.

**Tests.**

- A config with two providers routes each chain entry to its own endpoint and key.
- A 403 on the first provider makes the second provider answer.
- A 429 on the first provider makes the second provider answer.
- A model that is not free is never called, even when it is first in the chain.
- A legacy string chain still loads and still calls the legacy endpoint.
- The brain reports a key when any provider has one, and no key when none does.
- The routing-table tests in the Tk suite are updated to the new free-only multi-provider table: every chain entry is free, every tier has at least three fallbacks across at least two providers, the user's old picks stay aliased, and no retired or refused model leads a tier.

## 5. Part 3 — Red team on a switch

**Commands.** Spoken, detected locally, no model call, instant:

- "red team on" (also "redteam on", "red team mode on") switches her into red-team mode.
- "red team off" (also "redteam off", "normal mode") switches back to auto.

**Behaviour.** The mode is sticky until it is flipped. While it is on:

- the system prompt uses the red-team mode prompt instead of the auto prompt, so her voice, framing and depth change;
- every request routes to the security tier, so the security brief leads the answer;
- a screenshot still routes to the vision tier, because the picture changes the work.

The renderer's request carries the mode `auto`, which now means "use the session's current mode" rather than "force auto". A caller that wants a specific mode still gets it. The renderer needs no change.

**Tests.**

- "red team on" sets the session mode and returns a spoken confirmation.
- "red team off" returns to auto.
- In red-team mode a request routes to the security tier and the system prompt is the red-team prompt.
- Outside it, a request routes exactly as before.
- The red-team prompt still contains the hard line, and the never-say rules still hold.

## 6. Part 4 — Voice approval loop

**State.** The brain holds the pending step. There is no UI to approve anything, so the answer is spoken.

**Flow.**

1. A plan arrives with one or more actions. She speaks the reply, then speaks the first step and asks to run it: "Step one: write notes.md in the work folder. Run karun?"
2. The next utterance decides:
   - approve: haan, ha, yes, ok, theek hai, run, run karo, kar do, chalao, approve, chal, karo. That one action runs, she reports the outcome, and if more steps remain she asks about the next one. Otherwise she says the plan is done.
   - decline: ruk, nahi, no, mat, skip, rehne do, cancel, stop, ruk jao. That step is skipped and she moves on, or says the plan is done.
   - anything else: the pending step is dropped and the utterance is treated as a new request, so she never gets stuck waiting.
3. A 25 second silence drops the pending step and she goes back to sleep.

**Safety.** The approval gate is unchanged: only validated actions can run, only inside the work folder, only the allow-listed kinds. Approval is per action, never for the whole plan at once.

**Busy guard.** While she speaks a step or a question the microphone stops listening, so she never approves herself.

**No renderer change.** The question travels as an ordinary plan event with its speech, and the answer travels back as an ordinary route command. The renderer already does both.

**Command priority.** The red-team switch is checked before the approval answer, so it works at any moment, including while a step is waiting for an answer.

**Tests.**

- A plan with an action produces a spoken question naming the first step.
- "haan" runs that one action and reports the outcome.
- "ruk" skips it and moves to the next step.
- An unrelated sentence cancels the pending step and is routed as a new request.
- The second step is only asked after the first one is answered.
- A pending step is dropped after the silence timeout.
- A rejected action (outside the work folder) never reaches the approval question.
- She never approves an action while she is still speaking.

## 7. Testing plan

- `app/brain/test_session.py` gains the Part 1, Part 3 and Part 4 cases.
- `test_rasgulla.py` (Tk) keeps every persona, guard and audit assertion, and its routing-table assertions are updated to the new free-only multi-provider table.
- `app/test_orb_smoke.py` keeps its current assertions and gains a live agentic probe: a plan with actions reaches the renderer, and a spoken approval runs a safe write inside the work folder.
- `app/test_orb_wake.py` and `app/test_orb_talk.py` stay green and unchanged.
- Every change is written test first, watched failing for the right reason, then implemented.

## 8. Delivery

Four commits, one per part, each with its own gate run:

1. Repair the agentic path.
2. Multi-provider free auto-switch.
3. Red team on a switch.
4. Voice approval loop.

The final gate runs everything: brain session, brain tts, node units, ruff, the Tk suite, and the smoke, talk, wake and live probes.

## 9. Risks and fallbacks

- **A free provider rate-limits.** The chain has at least three free fallbacks across two live providers, and the router switches on a 429 without losing the request.
- **A provider rejects the key.** The provider is dropped for the session and the next one answers. Nothing retries the dead provider.
- **Every provider is down.** Rasgulla falls back to the local intents, which always work, and says so plainly.
- **A model answers with reasoning instead of text.** The router already reads the reasoning field when the content field is empty. This is confirmed live on the free models and is why the router change includes that fallback.
- **The voice approval loop mishears.** Anything that is not clearly approve or decline is treated as a new request, so a misheard word can never run an action.
