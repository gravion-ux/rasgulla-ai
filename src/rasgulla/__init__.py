"""Rasgulla AI: local-first static risk analysis for security research.

Public API
----------
``analyze_path``     findings for a directory or a single file
``analyze_file``     findings for one file
``build_test_plan``  deterministic offline test plan for a set of findings
``get_adapter``      model-adapter lookup
"""
from .adapters import (
    ADAPTERS,
    ModelAdapter,
    NullAdapter,
    StaticAdapter,
    available_adapters,
    get_adapter,
)
from .analyzer import Finding, analyze_file, analyze_path
from .testplan import build_test_plan

__all__ = [
    "ADAPTERS",
    "Finding",
    "ModelAdapter",
    "NullAdapter",
    "StaticAdapter",
    "__version__",
    "analyze_file",
    "analyze_path",
    "available_adapters",
    "build_test_plan",
    "get_adapter",
]

__version__ = "0.2.0"
