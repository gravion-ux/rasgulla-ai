# Rasgulla AI

> A local-first AI-assisted security research workbench for code understanding, safe test generation, and reproducible debugging.

**Status:** MVP scaffold / research prototype. Not production-certified.

## Features
- Source-code inventory and heuristic risk hints.
- Explainable findings with file/line evidence.
- Safe local test-plan generation.
- Provider-neutral model adapter interface (no API key required for static mode).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
# Windows: .venv\\Scripts\\activate
pip install -e ".[dev]"
pytest
```

## Safety and scope
The MVP performs static inspection only. It does not execute generated code, exploit targets, access networks, collect credentials, or bypass model safeguards. Treat findings as hypotheses requiring human validation.

## Roadmap
- [ ] Stable public API and versioned schemas
- [ ] More unit, integration and adversarial tests
- [ ] Reproducible benchmarks and sample datasets
- [ ] Signed releases and dependency review
- [ ] Documentation and contribution guide

## License
MIT. See [LICENSE](LICENSE).
