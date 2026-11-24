"""Deterministic BM25 ranking over the skill catalogue."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from agentskills.catalog import Catalogue, Skill

FIELD_WEIGHTS: dict[str, int] = {
    "name": 3,
    "triggers": 3,
    "tags": 2,
    "description": 2,
    "domain": 1,
}

K1 = 1.5
B = 0.75

_COMPOUND_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class ScoredSkill:
    skill: Skill
    score: float
    matched: tuple[str, ...]
    fields: tuple[str, ...]


def tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, keep hyphenated compounds whole."""
    tokens: list[str] = []
    for compound in _COMPOUND_RE.findall(text.lower()):
        tokens.append(compound)
        if "-" in compound:
            tokens.extend(compound.split("-"))
    return tokens


def _field_tokens(skill: Skill) -> dict[str, list[str]]:
    return {
        "name": tokenize(skill.name),
        "triggers": [token for trigger in skill.triggers for token in tokenize(trigger)],
        "tags": [token for tag in skill.tags for token in tokenize(tag)],
        "description": tokenize(skill.description),
        "domain": tokenize(skill.domain),
    }


def _document(fields: dict[str, list[str]]) -> list[str]:
    document: list[str] = []
    for field, weight in FIELD_WEIGHTS.items():
        document.extend(fields[field] * weight)
    return document


def _first_matching_field(fields: dict[str, list[str]], term: str) -> str:
    for field in FIELD_WEIGHTS:
        if term in fields[field]:
            return field
    return "description"


def route(catalogue: Catalogue, query: str, top: int = 8) -> list[ScoredSkill]:
    """Rank catalogue skills against a free-form task description."""
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms or not catalogue.skills:
        return []

    fields_by_skill = {skill.name: _field_tokens(skill) for skill in catalogue.skills}
    documents = {name: _document(fields) for name, fields in fields_by_skill.items()}
    lengths = {name: len(document) for name, document in documents.items()}
    average_length = sum(lengths.values()) / len(lengths)
    frequencies = {name: Counter(document) for name, document in documents.items()}
    document_frequency: Counter[str] = Counter()
    for counter in frequencies.values():
        document_frequency.update(counter.keys())

    total = len(documents)
    results: list[ScoredSkill] = []
    for skill in catalogue.skills:
        counter = frequencies[skill.name]
        length = lengths[skill.name]
        fields = fields_by_skill[skill.name]
        score = 0.0
        matched: list[str] = []
        hit_fields: list[str] = []
        for term in terms:
            count = counter.get(term, 0)
            if count == 0:
                continue
            idf = math.log(
                1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = count + K1 * (1 - B + B * length / average_length)
            score += idf * (count * (K1 + 1)) / denominator
            matched.append(term)
            hit_fields.append(_first_matching_field(fields, term))
        if score > 0:
            results.append(
                ScoredSkill(
                    skill=skill,
                    score=round(score, 6),
                    matched=tuple(matched),
                    fields=tuple(hit_fields),
                )
            )

    results.sort(key=lambda item: (-item.score, item.skill.name))
    return results[:top]


__all__ = ["FIELD_WEIGHTS", "ScoredSkill", "route", "tokenize"]
