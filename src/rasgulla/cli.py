import argparse
import json
from pathlib import Path

from .analyzer import analyze_path


def main():
    parser = argparse.ArgumentParser(description="Rasgulla AI static security research MVP")
    parser.add_argument("path", type=Path, help="Local source directory or file")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    if not args.path.exists():
        parser.error("path does not exist")
    report = {"tool": "Rasgulla AI", "mode": "static-analysis", "findings": analyze_path(args.path)}
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)

if __name__ == "__main__":
    main()
