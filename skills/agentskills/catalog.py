"""Loading and modelling of the skill catalogue."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

_INDEX_RESOURCE = "data/catalog.json"


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    domain: str
    summary: str
    description: str
    tags: tuple[str, ...]
    triggers: tuple[str, ...]
    steps: tuple[str, ...]
    when_to_use: str
    when_not_to_use: str
    inputs: tuple[str, ...]
    output_contract: str
    checks: tuple[str, ...]
    safety: str
    related: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Catalogue:
    skills: tuple[Skill, ...]
    version: str

    def by_domain(self, domain: str) -> tuple[Skill, ...]:
        return tuple(skill for skill in self.skills if skill.domain == domain)

    def get(self, name: str) -> Skill | None:
        for skill in self.skills:
            if skill.name == name:
                return skill
        return None

    def domains(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for skill in self.skills:
            seen.setdefault(skill.domain, None)
        return tuple(seen)


def _skill_from_dict(payload: dict[str, Any]) -> Skill:
    return Skill(
        name=str(payload["name"]),
        domain=str(payload["domain"]),
        summary=str(payload["summary"]),
        description=str(payload["description"]),
        tags=tuple(str(tag) for tag in payload.get("tags", ())),
        triggers=tuple(str(trigger) for trigger in payload.get("triggers", ())),
        steps=tuple(str(step) for step in payload.get("steps", ())),
        when_to_use=str(payload.get("when_to_use", "")),
        when_not_to_use=str(payload.get("when_not_to_use", "")),
        inputs=tuple(str(item) for item in payload.get("inputs", ())),
        output_contract=str(payload.get("output_contract", "")),
        checks=tuple(str(check) for check in payload.get("checks", ())),
        safety=str(payload.get("safety", "")),
        related=tuple(str(name) for name in payload.get("related", ())),
    )


def _index_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    override = os.environ.get("AGENTSKILLS_CATALOG")
    if override:
        return Path(override)
    resource = files("agentskills").joinpath(_INDEX_RESOURCE)
    return Path(str(resource))


def load_catalogue(path: Path | None = None) -> Catalogue:
    """Load the catalogue index from disk."""
    source = _index_path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read catalogue index at {source}: {exc}") from exc
    payload = cast(dict[str, Any], raw)
    entries = payload.get("skills")
    if not isinstance(entries, list):
        raise ValueError(f"catalogue index at {source} has no 'skills' list")
    skills = tuple(_skill_from_dict(cast(dict[str, Any], entry)) for entry in entries)
    return Catalogue(skills=skills, version=str(payload.get("version", "0.0.0")))


__all__ = ["Catalogue", "Skill", "load_catalogue"]
