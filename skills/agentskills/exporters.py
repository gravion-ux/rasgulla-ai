"""Export the catalogue for other agents, and pack it for distribution."""

from __future__ import annotations

import zipfile
from pathlib import Path


def export_skills(root: Path, out: Path, domain: str | None = None) -> int:
    """Copy generated skill directories into `out`, mirroring the skills/ layout."""
    written = 0
    for skill_file in sorted((root / "skills").glob("*/*/SKILL.md")):
        skill_domain = skill_file.parent.parent.name
        if domain is not None and skill_domain != domain:
            continue
        target = out / "skills" / skill_domain / skill_file.parent.name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(
            skill_file.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
        written += 1
    return written


def pack_catalogue(root: Path, out: Path) -> Path:
    """Write a zip containing the catalogue index and every skill file."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        index = root / "src" / "agentskills" / "data" / "catalog.json"
        if index.exists():
            archive.write(index, "catalog.json")
        for skill_file in sorted((root / "skills").glob("*/*/SKILL.md")):
            archive.write(
                skill_file,
                str(
                    Path("skills")
                    / skill_file.parent.parent.name
                    / skill_file.parent.name
                    / "SKILL.md"
                ),
            )
        for router in sorted((root / "routers").glob("*/SKILL.md")):
            archive.write(router, str(Path("routers") / router.parent.name / "SKILL.md"))
    return out


__all__ = ["export_skills", "pack_catalogue"]
