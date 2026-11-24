"""Canonical types for catalogue entries and domain profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SkillSpec:
    """A single skill, authored by hand in a domain module."""

    name: str
    summary: str
    when: str
    steps: tuple[str, ...]
    tags: tuple[str, ...]
    triggers: tuple[str, ...]
    related: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainProfile:
    """Domain-wide framing reused by every skill in that domain."""

    domain: str
    title: str
    when_not_to_use: str
    inputs: tuple[str, ...]
    output_contract: str
    checks: tuple[str, ...]
    safety: str


def spec(
    name: str,
    summary: str,
    when: str,
    steps: tuple[str, ...],
    tags: tuple[str, ...],
    triggers: tuple[str, ...],
    related: tuple[str, ...] = (),
) -> SkillSpec:
    """Build a SkillSpec. The only sanctioned way to author catalogue entries."""
    return SkillSpec(
        name=name,
        summary=summary,
        when=when,
        steps=steps,
        tags=tags,
        triggers=triggers,
        related=related,
    )


__all__ = ["DomainProfile", "SkillSpec", "spec"]
