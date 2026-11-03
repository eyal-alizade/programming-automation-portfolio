#!/usr/bin/env python3
"""Create an isolated directory of synthetic portfolio demonstration files."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path


def generate(directory: Path) -> None:
    # Require a new directory: never mix generated files with personal documents.
    directory.mkdir(parents=True, exist_ok=False)
    samples = {
        "meeting.txt": "Synthetic meeting notes. No client information.\n",
        "orders.csv": "order_id,amount\nDEMO-001,120\nDEMO-002,85\n",
        "report.TXT": "Synthetic monthly report.\n",
        "settings.json": '{"portfolio_demo": true}\n',
        "README": "Synthetic file without an extension.\n",
    }
    timestamp = datetime(2026, 1, 15, 12, tzinfo=timezone.utc).timestamp()
    for name, content in samples.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
        os.utime(path, (timestamp, timestamp))
    (directory / "nested").mkdir()
    (directory / "nested" / "leave-me.txt").write_text("Nested files are intentionally ignored.\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="A NEW directory, for example demo-input")
    args = parser.parse_args()
    try:
        generate(args.directory)
    except OSError as error:
        parser.exit(2, f"ERROR: {error}\n")
    print(f"Created 5 synthetic files and an ignored nested directory in {args.directory}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
