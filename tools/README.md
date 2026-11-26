# Verification tools

The harnesses the integration plan and the release notes refer to. They are in
the repo so the plan can be re-run from a fresh clone instead of depending on a
scratch directory.

Run them with the project's own interpreter:

```powershell
& '.\.venv\Scripts\python.exe' -u tools\run_matrix.py
```

| script | what it proves |
| --- | --- |
| `run_matrix.py` | Every suite in one go: `test_rasgulla.py`, the brain session and TTS suites, the four orb suites, `live_probe_agentic.py` and `acceptance_probe.py`. Prints one line per suite and writes the full output to `work/matrix_*.txt`. Takes optional suite names to run a subset. |
| `ruff_baseline.py` | That the changes added no new lint findings. It extracts `HEAD` into `work/headbase`, lints both trees and diffs the counters. Exits non-zero on a new finding. |
| `measure_live_path.py` | The real request path against the live free endpoints: the system prompt, the tier brief and the chain from `models.json`. Prints per-attempt timings and the latency min/median/max. Takes a count and an optional tier. |
| `verify_chain_gate.py` | That an answer really reaches the user (the router accepts it, the reply parses, the caption carries the answer and the voice matches it) and that the chain walks to the next free model when the first one is rate limited. |
| `live_probe_agentic.py` | The live agentic release: the spoken mode switch, a real request that comes back as a renderer-shaped plan with every action held back, and the voice gate approving, declining and refusing. The action runner is replaced with a recorder, so nothing touches the machine. |
| `acceptance_probe.py` | The release criteria, offline, against the shipped code rather than a copy. |
| `verify_plan_calibration.py` | The plan's calibration table against the real catalogue: what `match_skill()` returns for every query, whether the cached scorer agrees with upstream `route()`, and the cost of one match. |
| `draft_skill_catalogue.py` | The catalogue module the plan describes, kept verbatim here so the calibration can be measured against the committed plan. |

`verify_plan_calibration.py` compares against the vendored catalogue under
`skills/`. Point `AGENT_SKILLS_SRC` at a separate checkout of
`gravion-ux/agent-skills` to compare against a different tree instead.
