"""A 1200-skill agent skill library with a deterministic master router."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[2] / "VERSION"

if _VERSION_FILE.is_file():
    __version__ = _VERSION_FILE.read_text(encoding="utf-8").strip()
else:
    try:
        __version__ = metadata.version("agentskills")
    except metadata.PackageNotFoundError:
        __version__ = "0.0.0"

__all__ = ["__version__"]
