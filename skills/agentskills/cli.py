"""Command-line interface for the agent skills library."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from agentskills.catalog import Catalogue, load_catalogue
from agentskills.exporters import export_skills, pack_catalogue
from agentskills.router import route
from agentskills.validation import format_findings, validate_repository


def _add_root_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        default=".",
        help="repository root that holds skills/, routers/ and catalog/",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentskills", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list catalogue skills")
    listing.add_argument("--domain", default=None)
    listing.add_argument("--json", action="store_true")
    _add_root_option(listing)

    show = commands.add_parser("show", help="show one skill")
    show.add_argument("name")
    _add_root_option(show)

    routing = commands.add_parser("route", help="rank skills against a task")
    routing.add_argument("task")
    routing.add_argument("--top", type=int, default=8)
    routing.add_argument("--json", action="store_true")
    _add_root_option(routing)

    validate = commands.add_parser("validate", help="validate the repository")
    validate.add_argument("--json", action="store_true")
    _add_root_option(validate)

    export = commands.add_parser("export", help="copy skills into another agent's skills directory")
    export.add_argument("--out", required=True)
    export.add_argument("--domain", default=None)
    _add_root_option(export)

    pack = commands.add_parser("pack", help="write a distributable zip")
    pack.add_argument("--out", required=True)
    _add_root_option(pack)

    return parser


def _run_list(catalogue: Catalogue, args: argparse.Namespace) -> int:
    skills = catalogue.by_domain(args.domain) if args.domain else catalogue.skills
    if args.json:
        print(json.dumps([skill.name for skill in skills], indent=2))
    else:
        for skill in skills:
            print(f"{skill.domain:<24} {skill.name}")
    return 0


def _run_show(catalogue: Catalogue, args: argparse.Namespace) -> int:
    skill = catalogue.get(args.name)
    if skill is None:
        print(f"unknown skill: {args.name}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "name": skill.name,
                "domain": skill.domain,
                "description": skill.description,
                "tags": list(skill.tags),
                "triggers": list(skill.triggers),
                "steps": list(skill.steps),
                "related": list(skill.related),
            },
            indent=2,
        )
    )
    return 0


def _run_route(catalogue: Catalogue, args: argparse.Namespace) -> int:
    results = route(catalogue, args.task, top=args.top)
    if args.json:
        print(
            json.dumps(
                {
                    "query": args.task,
                    "results": [
                        {
                            "name": item.skill.name,
                            "domain": item.skill.domain,
                            "score": item.score,
                            "matched": list(item.matched),
                            "fields": list(item.fields),
                        }
                        for item in results
                    ],
                },
                indent=2,
            )
        )
    else:
        for index, item in enumerate(results, start=1):
            print(f"{index}. {item.skill.name}  [{item.skill.domain}]  score={item.score}")
            print(f"   matched: {', '.join(item.matched)}")
    return 0


def _run_validate(root: Path, args: argparse.Namespace) -> int:
    findings = validate_repository(root)
    if args.json:
        print(
            json.dumps(
                [
                    {"severity": f.severity, "path": f.path, "message": f.message}
                    for f in findings
                ],
                indent=2,
            )
        )
    else:
        print(format_findings(findings))
    return 1 if any(f.severity == "error" for f in findings) else 0


def _run_export(root: Path, args: argparse.Namespace) -> int:
    count = export_skills(root, Path(args.out), domain=args.domain)
    print(f"exported {count} skill(s) to {args.out}")
    return 0


def _run_pack(root: Path, args: argparse.Namespace) -> int:
    path = pack_catalogue(root, Path(args.out))
    print(f"wrote {path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command in {"list", "show", "route"}:
        catalogue = load_catalogue()
        handlers: dict[str, Callable[[], int]] = {
            "list": lambda: _run_list(catalogue, args),
            "show": lambda: _run_show(catalogue, args),
            "route": lambda: _run_route(catalogue, args),
        }
    else:
        handlers = {
            "validate": lambda: _run_validate(root, args),
            "export": lambda: _run_export(root, args),
            "pack": lambda: _run_pack(root, args),
        }

    handler = handlers.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")
    return handler()


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
