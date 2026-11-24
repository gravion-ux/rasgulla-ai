"""Repository-wide validation of the generated skill corpus."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentskills.frontmatter import parse_frontmatter
from agentskills.generation import SECTION_ORDER

PLACEHOLDER_MARKERS: tuple[str, ...] = ("TBD", "TODO", "FIXME", "XXX", "lorem")
REQUIRED_FRONTMATTER: tuple[str, ...] = ("name", "description", "domain", "tags", "triggers")
MAX_DESCRIPTION_LENGTH = 1024

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_CATALOG_ENTRY_RE = re.compile(r"^- `([a-z0-9-]+)` — ", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Finding:
    severity: str
    path: str
    message: str


def _sections(body: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(body))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections.append((match.group(1), body[start:end].strip()))
    return sections


def validate_skill_file(path: Path, expected_name: str) -> list[Finding]:
    """Validate one SKILL.md file."""
    findings: list[Finding] = []
    if not path.exists():
        return [Finding("error", str(path), "file is missing")]
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return [Finding("error", str(path), "file is empty")]

    try:
        fields, body = parse_frontmatter(text)
    except ValueError as exc:
        return [Finding("error", str(path), f"frontmatter is invalid: {exc}")]

    for key in REQUIRED_FRONTMATTER:
        if key not in fields:
            findings.append(Finding("error", str(path), f"frontmatter is missing '{key}'"))

    name = str(fields.get("name", ""))
    if name != expected_name:
        findings.append(
            Finding("error", str(path), f"name '{name}' does not match directory '{expected_name}'")
        )

    description = str(fields.get("description", ""))
    if len(description) > MAX_DESCRIPTION_LENGTH:
        findings.append(
            Finding(
                "error",
                str(path),
                f"description is {len(description)} characters, limit is {MAX_DESCRIPTION_LENGTH}",
            )
        )
    if "Use when" not in description:
        findings.append(
            Finding(
                "error",
                str(path),
                "description must state a trigger clause containing 'Use when'",
            )
        )

    sections = _sections(body)
    headings = [heading for heading, _ in sections]
    if headings[: len(SECTION_ORDER)] != list(SECTION_ORDER):
        missing = [heading for heading in SECTION_ORDER if heading not in headings]
        detail = f" (missing: {', '.join(missing)})" if missing else ""
        findings.append(
            Finding(
                "error",
                str(path),
                f"the nine sections are missing or out of order{detail}",
            )
        )
    for heading, content in sections:
        if heading in SECTION_ORDER and not content:
            findings.append(Finding("error", str(path), f"section '## {heading}' is empty"))

    for marker in PLACEHOLDER_MARKERS:
        if marker in text:
            findings.append(Finding("error", str(path), f"contains placeholder marker '{marker}'"))

    return findings


def validate_repository(root: Path) -> list[Finding]:
    """Validate every skill, the routers, the master skill and the indexes."""
    findings: list[Finding] = []
    skills_root = root / "skills"
    if not skills_root.exists():
        return [Finding("error", str(skills_root), "skills directory is missing")]

    leaf_files = sorted(skills_root.glob("*/*/SKILL.md"))
    seen_names: dict[str, Path] = {}
    for skill_file in leaf_files:
        expected = skill_file.parent.name
        findings.extend(validate_skill_file(skill_file, expected))
        if expected in seen_names:
            findings.append(
                Finding("error", str(skill_file), f"duplicate skill name '{expected}'")
            )
        else:
            seen_names[expected] = skill_file

    master = skills_root / "master" / "SKILL.md"
    if not master.exists():
        findings.append(Finding("error", str(master), "master skill is missing"))
    else:
        findings.extend(validate_skill_file(master, "master"))

    appendix = skills_root / "master" / "APPENDIX.md"
    if not appendix.exists():
        findings.append(Finding("error", str(appendix), "master appendix is missing"))

    routers_root = root / "routers"
    if not routers_root.exists():
        findings.append(Finding("error", str(routers_root), "routers directory is missing"))
    else:
        router_names: dict[str, Path] = {}
        for router in sorted(routers_root.glob("*/SKILL.md")):
            router_name = f"router-{router.parent.name}"
            findings.extend(validate_skill_file(router, router_name))
            if router_name in router_names:
                findings.append(
                    Finding("error", str(router), f"duplicate router name '{router_name}'")
                )
            else:
                router_names[router_name] = router

    index = root / "src" / "agentskills" / "data" / "catalog.json"
    if not index.exists():
        findings.append(Finding("error", str(index), "catalogue index is missing"))
        return findings

    try:
        payload = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding("error", str(index), f"catalogue index is unreadable: {exc}"))
        return findings

    entries = payload.get("skills", [])
    indexed = {str(entry.get("name", "")) for entry in entries}
    on_disk = set(seen_names)
    if indexed != on_disk:
        missing = sorted(on_disk - indexed)
        extra = sorted(indexed - on_disk)
        findings.append(
            Finding("error", str(index), f"index is out of sync (missing={missing}, extra={extra})")
        )

    descriptions: dict[str, str] = {}
    triggers: dict[str, str] = {}
    for entry in entries:
        skill_name = str(entry.get("name", ""))
        description = str(entry.get("description", ""))
        if description in descriptions:
            findings.append(
                Finding(
                    "error",
                    str(index),
                    f"duplicate description shared by '{descriptions[description]}' "
                    f"and '{skill_name}'",
                )
            )
        else:
            descriptions[description] = skill_name
        for trigger in entry.get("triggers", []):
            trigger_text = str(trigger)
            if trigger_text in triggers:
                findings.append(
                    Finding(
                        "error",
                        str(index),
                        f"duplicate trigger '{trigger_text}' shared by "
                        f"'{triggers[trigger_text]}' and '{skill_name}'",
                    )
                )
            else:
                triggers[trigger_text] = skill_name

    for entry in entries:
        skill_name = str(entry.get("name", ""))
        for related in entry.get("related", []):
            related_name = str(related)
            if related_name not in indexed:
                findings.append(
                    Finding(
                        "error",
                        str(index),
                        f"'{skill_name}' relates to unknown skill '{related_name}'",
                    )
                )

    domains = {str(entry.get("domain", "")) for entry in entries}
    router_domains = {path.parent.name for path in routers_root.glob("*/SKILL.md")}
    missing_routers = sorted(domains - router_domains)
    extra_routers = sorted(router_domains - domains)
    if missing_routers or extra_routers:
        findings.append(
            Finding(
                "error",
                str(routers_root),
                "routers do not cover the domains "
                f"(missing={missing_routers}, extra={extra_routers})",
            )
        )

    catalog = root / "catalog" / "CATALOG.md"
    if not catalog.exists():
        findings.append(Finding("error", str(catalog), "catalogue mirror is missing"))
    else:
        listed = set(_CATALOG_ENTRY_RE.findall(catalog.read_text(encoding="utf-8")))
        if listed != on_disk:
            missing = sorted(on_disk - listed)
            extra = sorted(listed - on_disk)
            findings.append(
                Finding(
                    "error",
                    str(catalog),
                    "catalogue mirror is out of sync "
                    f"(missing={missing}, extra={extra})",
                )
            )

    return findings


def format_findings(findings: Sequence[Finding]) -> str:
    """Render findings as a human-readable report."""
    if not findings:
        return "No problems found."
    lines = []
    for finding in findings:
        lines.append(f"{finding.severity.upper()}: {finding.path}: {finding.message}")
    return "\n".join(lines)


__all__ = ["Finding", "format_findings", "validate_repository", "validate_skill_file"]
