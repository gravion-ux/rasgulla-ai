"""A tiny parser for the YAML subset used by skill frontmatter."""

from __future__ import annotations

_FENCE = "---"


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a document into (frontmatter fields, body).

    Supports exactly the subset the catalogue uses: `key: value` and
    `key: [a, b, c]`. Raises ValueError when the block is missing or malformed.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError("missing frontmatter fence")
    fields: dict[str, object] = {}
    index = 1
    while index < len(lines) and lines[index].strip() != _FENCE:
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"malformed frontmatter line: {line!r}")
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            fields[key] = tuple(
                part.strip().strip("'\"") for part in raw[1:-1].split(",") if part.strip()
            )
        else:
            fields[key] = raw.strip("'\"")
    if index >= len(lines):
        raise ValueError("unterminated frontmatter block")
    return fields, "\n".join(lines[index + 1 :]).lstrip("\n")


__all__ = ["parse_frontmatter"]
