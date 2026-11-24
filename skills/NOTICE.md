# agent-skills

Vendored from https://github.com/gravion-ux/agent-skills

- Commit: a7f5e7591376f086a7b2412723cfc037e599644e (2026-11-22)
- Upstream package: `src/agentskills/` copied whole and unedited
- Licence: MIT — see `LICENSE` (Copyright (c) 2026 gravion-ux)

What is vendored: the Python package and its machine-readable index
(`data/catalog.json`, 1,211 skills across 42 domains).

What is NOT vendored: the generated `skills/**/SKILL.md` tree and the
`routers/**` tree. The JSON index already carries every section of every
skill (purpose, when to use, when NOT to use, inputs, procedure, output
contract, validation checklist, safety notes, related skills), so the
index alone is what Rasgulla reads at runtime.

This catalogue is read as **method**. It never widens Rasgulla's action
allow-list, never changes her hard line and never overrides the spoken
approval gate.
