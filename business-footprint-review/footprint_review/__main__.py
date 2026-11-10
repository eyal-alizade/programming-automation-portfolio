"""Command-line entry point for explicit, local-only observation reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import review
from .reporting import write_reports
from .schema import InputError, load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review recorded public-business observations. No network requests are made.")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generate a clearly labelled fictional example")
    demo.add_argument("--output", type=Path, required=True, help="A new directory for the reports")
    report = commands.add_parser("report", help="Review a JSON observation file")
    report.add_argument("input", type=Path)
    report.add_argument("--output", type=Path, required=True, help="A new directory for the reports")
    args = parser.parse_args(argv)
    source = Path(__file__).with_name("demo.json") if args.command == "demo" else args.input
    try:
        result = review(load(source))
        write_reports(result, args.output)
    except (InputError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Reviewed {len(result['data']['sources'])} supplied sources; {len(result['findings'])} findings for review.")
    print(f"Reports: {args.output}")
    print("No network requests were made. Open report.html or report.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
