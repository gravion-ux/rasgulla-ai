# The "her" personality catalogue

These files are vendored from the **Her--Him** project by gravion-ux:
<https://github.com/gravion-ux/Her--Him>

Only the `her` catalogue is vendored here - the personality character cards and
its manifest. The `him` catalogue, the affect engine, the voice stack, the
memory layer, the guardrails, the server and the CLI are deliberately **not**
included.

- Upstream path: `src/herhim/data/her/`
- Upstream commit: `731efd48a234ff57421119c8bca56b6758784261`
- Licence: MIT - see `LICENSE` in this directory.

Each file is a Character Card v3 document with the `herhim` extension block.
Rasgulla reads the personality fields (`description`, `personality`, `scenario`,
`mes_example`, `tags`) and renders them into the system prompt as the
character's temperament. Everything operational - the reply format, the JSON
action plan, the approval gate and the line she does not cross - stays in
`core_app.py` and is never taken from these cards.
