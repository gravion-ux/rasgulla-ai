"""Turn the curated catalogue source into skill files, routers and indexes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from agentskills.catalog import Skill
from agentskills.spec import DomainProfile, SkillSpec

SECTION_ORDER: tuple[str, ...] = (
    "Purpose",
    "When to use",
    "When NOT to use",
    "Inputs required",
    "Procedure",
    "Output contract",
    "Validation checklist",
    "Safety notes",
    "Related skills",
)

_MAX_RELATED = 5
_MAX_SIBLINGS = 3
_NAME_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _name_tokens(name: str) -> set[str]:
    return set(_NAME_TOKEN_RE.findall(name))


def _sibling_names(entry: SkillSpec, entries: Sequence[SkillSpec]) -> tuple[str, ...]:
    """Pick deterministic sibling skills for the related-skills section."""
    mine = _name_tokens(entry.name)
    candidates = [other for other in entries if other.name != entry.name]

    def shared(other: SkillSpec) -> int:
        return len(mine & _name_tokens(other.name))

    candidates.sort(key=lambda other: (-shared(other), other.name))
    picked = [other.name for other in candidates if shared(other) > 0][:_MAX_SIBLINGS]
    if len(picked) < _MAX_SIBLINGS:
        for other in candidates:
            if other.name not in picked:
                picked.append(other.name)
            if len(picked) == _MAX_SIBLINGS:
                break
    return tuple(picked)


def build_skill(entry: SkillSpec, profile: DomainProfile, siblings: Sequence[str] = ()) -> Skill:
    """Assemble a complete Skill from a spec and its domain profile."""
    related: list[str] = []
    for name in (*entry.related, *siblings):
        if name != entry.name and name not in related:
            related.append(name)
    return Skill(
        name=entry.name,
        domain=profile.domain,
        summary=entry.summary,
        description=f"{entry.summary} Use when {entry.when}.",
        tags=entry.tags,
        triggers=entry.triggers,
        steps=entry.steps,
        when_to_use=f"Use when {entry.when}.",
        when_not_to_use=profile.when_not_to_use,
        inputs=profile.inputs,
        output_contract=profile.output_contract,
        checks=profile.checks,
        safety=profile.safety,
        related=tuple(related[:_MAX_RELATED]),
    )


def render_frontmatter(skill: Skill) -> str:
    """Render the YAML-subset frontmatter block for one skill."""
    tags = ", ".join(skill.tags)
    triggers = ", ".join(f'"{trigger}"' for trigger in skill.triggers)
    return (
        "---\n"
        f"name: {skill.name}\n"
        f"description: {skill.description}\n"
        f"domain: {skill.domain}\n"
        f"tags: [{tags}]\n"
        f"triggers: [{triggers}]\n"
        "---\n"
    )


def render_skill_markdown(skill: Skill) -> str:
    """Render one leaf skill file."""
    procedure = "\n".join(
        f"{index}. {step}" for index, step in enumerate(skill.steps, start=1)
    )
    checklist = "\n".join(f"- [ ] {check}" for check in skill.checks)
    inputs = "\n".join(f"- {item}" for item in skill.inputs)
    related = (
        "\n".join(f"- `{name}`" for name in skill.related)
        if skill.related
        else "- None. This skill stands alone."
    )
    title = skill.summary.rstrip(".")
    body = f"\n# {title}\n\n"
    body += f"## Purpose\n\n{skill.summary}\n\n"
    body += f"## When to use\n\n{skill.when_to_use}\n\n"
    body += f"## When NOT to use\n\n{skill.when_not_to_use}\n\n"
    body += f"## Inputs required\n\n{inputs}\n\n"
    body += f"## Procedure\n\n{procedure}\n\n"
    body += f"## Output contract\n\n{skill.output_contract}\n\n"
    body += f"## Validation checklist\n\n{checklist}\n\n"
    body += f"## Safety notes\n\n{skill.safety}\n\n"
    body += f"## Related skills\n\n{related}\n"
    return render_frontmatter(skill) + body


def render_router_markdown(domain: str, skills: Sequence[Skill], profile: DomainProfile) -> str:
    """Render one domain router skill."""
    lines: list[str] = []
    lines.append("---")
    lines.append(f"name: router-{domain}")
    lines.append(
        f"description: Route work in the {profile.title} domain to the right leaf "
        f"skill. Use when a task clearly belongs to {domain}."
    )
    lines.append(f"domain: {domain}")
    lines.append(f"tags: [router, routing, {domain}]")
    lines.append(
        f'triggers: ["route a {domain} task", "which {domain} skill should I use"]'
    )
    lines.append("---")
    lines.append("")
    lines.append(f"# {profile.title} router")
    lines.append("")
    lines.append("## Purpose")
    lines.append("")
    lines.append(
        f"List every leaf skill in the {domain} domain and say when to prefer each "
        "one, so the master skill can pick a chain without loading the whole catalogue."
    )
    lines.append("")
    lines.append("## When to use")
    lines.append("")
    lines.append(
        f"Use when the task is clearly inside {domain} and the master skill needs the "
        "structural neighbours of a shortlisted skill."
    )
    lines.append("")
    lines.append("## When NOT to use")
    lines.append("")
    lines.append(profile.when_not_to_use)
    lines.append("")
    lines.append("## Inputs required")
    lines.append("")
    for item in profile.inputs:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Procedure")
    lines.append("")
    lines.append("1. Read the task and the domain framing above.")
    lines.append("2. Scan the skill table below and shortlist at most five candidates.")
    lines.append("3. Drop any candidate whose When NOT to use clause rules it out.")
    lines.append("4. Order the survivors from most specific to most general.")
    lines.append("5. Recommend one primary skill and at most three supporting skills.")
    lines.append("")
    lines.append("## Output contract")
    lines.append("")
    lines.append(profile.output_contract)
    lines.append("")
    lines.append("## Validation checklist")
    lines.append("")
    for check in profile.checks:
        lines.append(f"- [ ] {check}")
    lines.append("")
    lines.append("## Safety notes")
    lines.append("")
    lines.append(profile.safety)
    lines.append("")
    lines.append("## Related skills")
    lines.append("")
    lines.append("- `master`")
    lines.append("")
    lines.append("## Skills in this domain")
    lines.append("")
    lines.append("| Skill | Use when |")
    lines.append("|---|---|")
    for skill in sorted(skills, key=lambda item: item.name):
        lines.append(f"| `{skill.name}` | {skill.when_to_use} |")
    lines.append("")
    return "\n".join(lines)


def render_index_json(skills: Sequence[Skill], version: str) -> str:
    """Render the packaged machine-readable index."""
    payload = {
        "version": version,
        "generated_from": "catalog/source",
        "skills": [
            {
                "name": skill.name,
                "domain": skill.domain,
                "summary": skill.summary,
                "description": skill.description,
                "tags": list(skill.tags),
                "triggers": list(skill.triggers),
                "steps": list(skill.steps),
                "when_to_use": skill.when_to_use,
                "when_not_to_use": skill.when_not_to_use,
                "inputs": list(skill.inputs),
                "output_contract": skill.output_contract,
                "checks": list(skill.checks),
                "safety": skill.safety,
                "related": list(skill.related),
            }
            for skill in sorted(skills, key=lambda item: item.name)
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def render_catalog_markdown(skills: Sequence[Skill], version: str) -> str:
    """Render the human-readable catalogue mirror."""
    by_domain: dict[str, list[Skill]] = {}
    for skill in skills:
        by_domain.setdefault(skill.domain, []).append(skill)
    lines: list[str] = []
    lines.append("# Catalogue")
    lines.append("")
    lines.append(f"Generated from `catalog/source` at version {version}.")
    lines.append("")
    lines.append(f"Total skills: {len(skills)} across {len(by_domain)} domains.")
    lines.append("")
    for domain in sorted(by_domain):
        lines.append(f"## {domain}")
        lines.append("")
        lines.append(f"Router: `routers/{domain}/SKILL.md` — {len(by_domain[domain])} skills.")
        lines.append("")
        for skill in sorted(by_domain[domain], key=lambda item: item.name):
            lines.append(f"- `{skill.name}` — {skill.summary}")
            lines.append(f"  - Triggers: {', '.join(skill.triggers)}")
        lines.append("")
    return "\n".join(lines)


def render_master_appendix(skills: Sequence[Skill], version: str) -> str:
    """Render the generated appendix of the master skill."""
    counts: dict[str, int] = {}
    for skill in skills:
        counts[skill.domain] = counts.get(skill.domain, 0) + 1
    lines: list[str] = []
    lines.append("# Master appendix")
    lines.append("")
    lines.append(
        f"Generated from `catalog/source` at version {version}. "
        f"{len(skills)} skills across {len(counts)} domains."
    )
    lines.append("")
    lines.append("## Domain map")
    lines.append("")
    lines.append("| Domain | Router | Skills |")
    lines.append("|---|---|---|")
    for domain in sorted(counts):
        lines.append(f"| `{domain}` | `routers/{domain}/SKILL.md` | {counts[domain]} |")
    lines.append("")
    lines.append("## Scoring reference")
    lines.append("")
    lines.append(
        "`agentskills route` scores every catalogue entry with a deterministic BM25 "
        "ranking. Field weights are name 3, triggers 3, tags 2, description 2 and "
        "domain 1; K1 is 1.5 and B is 0.75. Identical input always produces the "
        "identical ranking, and ties are broken by skill name ascending."
    )
    lines.append("")
    return "\n".join(lines)


def generate(
    root: Path,
    version: str,
    pairs: Sequence[tuple[SkillSpec, str]],
    profiles: Mapping[str, DomainProfile],
) -> list[Path]:
    """Write every generated artefact and return the paths written, sorted."""
    by_domain: dict[str, list[SkillSpec]] = {}
    for entry, domain in pairs:
        by_domain.setdefault(domain, []).append(entry)

    written: list[Path] = []
    skills: list[Skill] = []
    for domain in sorted(by_domain):
        entries = by_domain[domain]
        profile = profiles[domain]
        domain_skills = [
            build_skill(entry, profile, siblings=_sibling_names(entry, entries))
            for entry in entries
        ]
        domain_skills.sort(key=lambda item: item.name)
        for skill in domain_skills:
            path = root / "skills" / domain / skill.name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(render_skill_markdown(skill), encoding="utf-8", newline="\n")
            written.append(path)
        router_path = root / "routers" / domain / "SKILL.md"
        router_path.parent.mkdir(parents=True, exist_ok=True)
        router_path.write_text(
            render_router_markdown(domain, domain_skills, profile),
            encoding="utf-8",
            newline="\n",
        )
        written.append(router_path)
        skills.extend(domain_skills)

    index_path = root / "src" / "agentskills" / "data" / "catalog.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(render_index_json(skills, version), encoding="utf-8", newline="\n")
    written.append(index_path)

    catalog_path = root / "catalog" / "CATALOG.md"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        render_catalog_markdown(skills, version), encoding="utf-8", newline="\n"
    )
    written.append(catalog_path)

    appendix_path = root / "skills" / "master" / "APPENDIX.md"
    appendix_path.parent.mkdir(parents=True, exist_ok=True)
    appendix_path.write_text(
        render_master_appendix(skills, version), encoding="utf-8", newline="\n"
    )
    written.append(appendix_path)

    return sorted(written)


__all__ = [
    "SECTION_ORDER",
    "build_skill",
    "generate",
    "render_catalog_markdown",
    "render_frontmatter",
    "render_index_json",
    "render_master_appendix",
    "render_router_markdown",
    "render_skill_markdown",
]
