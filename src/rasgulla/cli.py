"""Command-line entry point for Rasgulla AI.

Problems are reported as one-line messages instead of Python tracebacks so the
tool can be used directly in CI pipelines.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import available_adapters, get_adapter
from .analyzer import analyze_path

EXIT_OK = 0
EXIT_INVALID_INPUT = 1
EXIT_USAGE = 2

NOTICE = (
    "Findings are heuristic hypotheses for human review. Rasgulla AI performs "
    "static inspection only: it does not execute code, exploit targets, access "
    "networks, or collect credentials."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rasgulla",
        description="Local-first static risk analysis and offline test-plan generation.",
    )
    parser.add_argument("path", type=Path, help="Local source directory or file")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument(
        "--test-plan",
        action="store_true",
        help="Include a local, offline test plan in the report",
    )
    parser.add_argument(
        "--adapter",
        default="static",
        help=(
            "Model adapter used for the test plan (default: static). "
            f"Available: {', '.join(available_adapters())}"
        ),
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=50,
        help="Maximum number of test-plan entries kept in the report (default: 50)",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with status 1 when at least one finding is reported",
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"rasgulla: path does not exist: {args.path}", file=sys.stderr)
        return EXIT_USAGE

    if args.max_steps < 0:
        print("rasgulla: --max-steps must not be negative", file=sys.stderr)
        return EXIT_USAGE

    try:
        adapter = get_adapter(args.adapter)
    except ValueError as exc:
        print(f"rasgulla: {exc}", file=sys.stderr)
        return EXIT_USAGE

    findings = analyze_path(args.path)
    report = {
        "tool": "Rasgulla AI",
        "mode": "static-analysis",
        "schema_version": 1,
        "findings": findings,
        "notice": NOTICE,
    }
    if args.test_plan:
        plan = adapter.generate_test_plan(findings)
        report["adapter"] = adapter.name
        report["test_plan"] = plan[: args.max_steps]

    rendered = json.dumps(report, indent=2)
    if args.output:
        try:
            args.output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(
                f"rasgulla: cannot write report to {args.output}: {exc.strerror or exc}",
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT
    print(rendered)

    if args.fail_on_findings and findings:
        return EXIT_INVALID_INPUT
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
