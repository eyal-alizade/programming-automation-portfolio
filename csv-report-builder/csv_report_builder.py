"""Dependency-free sales CSV consolidation with exact Decimal amounts."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import date
from decimal import Decimal, localcontext
import csv
import json
import os
from pathlib import Path
import re
import sys
import tempfile

VERSION = "1.0.0"
REQUIRED = ("date", "item", "quantity", "unit_price")
COMPUTED = ("line_total", "source_file", "source_index", "source_row")
OUTPUT_FILES = ("consolidated.csv", "summary.csv", "report.json", "report.txt")
ALIASES = {"sale_date": "date", "product": "item", "qty": "quantity", "price": "unit_price"}
DuplicateKey = tuple[tuple[str, str], ...]
ItemTotals = dict[str, tuple[int, Decimal]]


class DataError(ValueError):
    """Invalid input; no report is published."""


@dataclass
class InputStats:
    index: int
    name: str
    delimiter: str
    data_rows_read: int = 0
    blank_rows_skipped: int = 0
    duplicate_rows_detected: int = 0
    duplicate_rows_removed: int = 0


@dataclass
class Record:
    day: str
    item: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    source_file: str
    source_index: int
    source_row: int
    extras: dict[str, str]


def protect_text(value: str) -> str:
    """Prefix potentially executable spreadsheet text with an apostrophe."""
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def canonical_header(value: str) -> str:
    name = re.sub(r"\s+", "_", value.strip().casefold())
    return ALIASES.get(name, name)


def parse_record(cells: list[str], headers: list[str], filename: str,
                 file_index: int, row_number: int) -> tuple[Record, DuplicateKey]:
    where = f"input #{file_index} {filename!r}, record {row_number}"
    if len(cells) != len(headers):
        raise DataError(f"{where}: expected {len(headers)} fields, received {len(cells)}")
    raw = dict(zip(headers, cells))
    day, item = raw["date"].strip(), raw["item"].strip()
    quantity, price = raw["quantity"].strip(), raw["unit_price"].strip()
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise DataError(f"{where}: date must use YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise DataError(f"{where}: invalid calendar date") from None
    if not item or "\x00" in item:
        raise DataError(f"{where}: item must be nonempty and contain no NUL characters")
    if not re.fullmatch(r"[0-9]{1,100}", quantity) or int(quantity) <= 0:
        raise DataError(f"{where}: quantity must be a positive integer, at most 100 digits")
    if not re.fullmatch(r"[0-9]{1,100}(?:\.[0-9]{1,2})?", price):
        raise DataError(f"{where}: unit_price must be nonnegative with at most two decimal places")
    amount = Decimal(price)
    count = int(quantity)
    # Precision follows the input size, rather than the default 28-digit context.
    with localcontext() as context:
        context.prec = len(amount.as_tuple().digits) + len(quantity) + 8
        total = amount * count
    extras = {k: v for k, v in raw.items() if k not in REQUIRED}
    record = Record(day, item, count, amount, total, filename, file_index, row_number, extras)
    # Exact decoded cells, including extra columns; aliases normalize only header names.
    duplicate_key = tuple(sorted(raw.items()))
    return record, duplicate_key


def load_inputs(paths: list[Path], delimiter: str | None = None,
                keep_duplicates: bool = False) -> tuple[list[Record], list[InputStats], list[str]]:
    records, stats, extra_headers, seen = [], [], [], set()
    for index, path in enumerate(paths, start=1):
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as source:
                sample = source.read(65536)
                source.seek(0)
                if not sample:
                    raise DataError(f"input #{index} {path.name!r}: empty file")
                selected = delimiter
                if selected is None:
                    try:
                        selected = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
                    except csv.Error:
                        raise DataError(f"input #{index} {path.name!r}: cannot detect delimiter; use --delimiter") from None
                reader = csv.reader(source, delimiter=selected, strict=True)
                try:
                    headers = [canonical_header(v) for v in next(reader)]
                except StopIteration:
                    raise DataError(f"input #{index} {path.name!r}: missing header") from None
                if any(not h for h in headers) or len(set(headers)) != len(headers):
                    raise DataError(f"input #{index} {path.name!r}: blank or duplicate header names after alias normalization")
                missing = set(REQUIRED) - set(headers)
                if missing:
                    raise DataError(f"input #{index} {path.name!r}: missing columns: {', '.join(sorted(missing))}")
                collision = set(headers) & set(COMPUTED)
                if collision:
                    raise DataError(f"input #{index} {path.name!r}: reserved output column: {', '.join(sorted(collision))}")
                for header in headers:
                    if header not in REQUIRED and header not in extra_headers:
                        extra_headers.append(header)
                info = InputStats(index, path.name, selected)
                for row_number, cells in enumerate(reader, start=2):
                    if not cells:
                        info.blank_rows_skipped += 1
                        continue
                    info.data_rows_read += 1
                    record, key = parse_record(cells, headers, path.name, index, row_number)
                    if key in seen:
                        info.duplicate_rows_detected += 1
                        if not keep_duplicates:
                            info.duplicate_rows_removed += 1
                            continue
                    seen.add(key)
                    records.append(record)
                stats.append(info)
        except UnicodeError:
            raise DataError(f"input #{index} {path.name!r}: input must be valid UTF-8 (BOM is supported)") from None
        except csv.Error as error:
            raise DataError(f"input #{index} {path.name!r}: malformed CSV: {error}") from None
    if not records:
        raise DataError("No data records were found; provide at least one valid sales record")
    return records, stats, extra_headers


def summarize(records: list[Record]) -> tuple[ItemTotals, Decimal, int]:
    grouped = {}
    with localcontext() as context:
        context.prec = max(len(r.line_total.as_tuple().digits) for r in records) + len(str(len(records))) + 8
        grand_total = Decimal(0)
        quantity = 0
        for record in records:
            count, amount = grouped.get(record.item, (0, Decimal(0)))
            grouped[record.item] = count + record.quantity, amount + record.line_total
            grand_total += record.line_total
            quantity += record.quantity
    return grouped, grand_total, quantity


def money(value: Decimal) -> str:
    return format(value, ".2f")


def build_report(records, stats, grouped, total, quantity, currency, keep_duplicates):
    return {
        "tool": "csv-report-builder", "version": VERSION,
        "currency": currency, "inputs": [asdict(s) for s in stats],
        "data_rows_read": sum(s.data_rows_read for s in stats),
        "blank_rows_skipped": sum(s.blank_rows_skipped for s in stats),
        "duplicate_rows_detected": sum(s.duplicate_rows_detected for s in stats),
        "duplicate_rows_removed": sum(s.duplicate_rows_removed for s in stats),
        "rows_exported": len(records), "items": len(grouped),
        "total_quantity": quantity, "grand_total": money(total),
        "keep_duplicates": keep_duplicates,
        "deduplication": "Exact decoded cells under normalized column names, including extra columns; source location excluded.",
        "csv_text_protection": "Potential spreadsheet formula text is prefixed with an apostrophe.",
        "validation": "Strict: every input record validated before report files are published.",
    }


def write_csv_reports(staging: Path, records: list[Record], extra_headers: list[str],
                      grouped: ItemTotals) -> None:
    """Write both spreadsheet reports into the unpublished staging directory."""
    with (staging / "consolidated.csv").open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow([protect_text(v) for v in (*REQUIRED, *COMPUTED, *extra_headers)])
        for record in records:
            writer.writerow([
                record.day, protect_text(record.item), record.quantity, money(record.unit_price),
                money(record.line_total), protect_text(record.source_file), record.source_index, record.source_row,
                *[protect_text(record.extras.get(header, "")) for header in extra_headers]])
    with (staging / "summary.csv").open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.writer(destination)
        writer.writerow(["item", "quantity", "total_amount"])
        for item in sorted(grouped, key=lambda value: (value.casefold(), value)):
            count, amount = grouped[item]
            writer.writerow([protect_text(item), count, money(amount)])


def report_text(report: dict, stats: list[InputStats]) -> str:
    lines = [
        "CSV Report Builder", f"Currency: {report['currency'] or 'not specified (all inputs must use one currency)'}",
        f"Input files: {len(stats)}", f"Data rows read: {report['data_rows_read']}",
        f"Duplicate rows detected: {report['duplicate_rows_detected']}",
        f"Duplicate rows removed: {report['duplicate_rows_removed']}",
        f"Rows exported: {report['rows_exported']}", f"Grouped items: {report['items']}",
        f"Total quantity: {report['total_quantity']}", f"Grand total: {report['grand_total']}", "",
        "Inputs (names only; source_index disambiguates repeated filenames):",
        *[f"  {info.index}: {json.dumps(info.name, ensure_ascii=False)} — {info.data_rows_read} data rows" for info in stats],
        "", report["deduplication"], report["csv_text_protection"], report["validation"],
    ]
    return "\n".join(lines) + "\n"


def write_outputs(output, paths, records, stats, extra_headers, currency=None,
                  keep_duplicates=False, overwrite=False):
    output = Path(output)
    if output.is_symlink():
        raise OSError("Output directory must not be a symbolic link")
    if output.exists() and (not overwrite or not output.is_dir()):
        raise OSError("Output already exists; select a NEW directory or explicitly pass --overwrite for an existing directory")
    resolved_targets = {(output / name).resolve() for name in OUTPUT_FILES}
    if any(path.resolve() in resolved_targets for path in paths):
        raise OSError("An input would be overwritten by a report file; choose another output directory")
    for name in OUTPUT_FILES:
        target = output / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise OSError(f"Unsafe output entry: {name}; expected a regular file or a new filename")
    grouped, total, quantity = summarize(records)
    report = build_report(records, stats, grouped, total, quantity, currency, keep_duplicates)
    output.parent.mkdir(parents=True, exist_ok=True)
    created, installed = False, []
    with tempfile.TemporaryDirectory(prefix=".csv-report-", dir=output.parent) as temporary:
        staging = Path(temporary)
        write_csv_reports(staging, records, extra_headers, grouped)
        (staging / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (staging / "report.txt").write_text(report_text(report, stats), encoding="utf-8")
        try:
            if not output.exists():
                output.mkdir(mode=0o700)
                created = True
            elif not overwrite:
                raise OSError("Output was created by another process; no report files were replaced")
            for name in OUTPUT_FILES:
                os.replace(staging / name, output / name)
                installed.append(name)
        except OSError:
            if created:
                for name in installed:
                    (output / name).unlink(missing_ok=True)
                try:
                    output.rmdir()
                except OSError:
                    pass
            raise
    return report


def delimiter_option(value: str) -> str | None:
    options = {"auto": None, "comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|", ",": ",", ";": ";", "|": "|"}
    if value not in options:
        raise argparse.ArgumentTypeError("choose auto, comma, semicolon, tab or pipe")
    return options[value]


def currency_option(value: str) -> str:
    value = value.upper()
    if not re.fullmatch(r"[A-Z]{3}", value):
        raise argparse.ArgumentTypeError("currency must be a three-letter label such as ILS or USD")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and consolidate sales CSV files into exact monetary reports.")
    parser.add_argument("inputs", type=Path, nargs="+", help="one or more UTF-8/BOM sales CSV files")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--delimiter", type=delimiter_option, default="auto", help="auto, comma, semicolon, tab or pipe (default: auto)")
    parser.add_argument("--currency", type=currency_option, help="one currency label for all files; no conversion is performed")
    parser.add_argument("--keep-duplicates", action="store_true", help="retain exact duplicate records")
    parser.add_argument("--overwrite", action="store_true", help="explicitly replace only the four report files in an existing directory")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    try:
        records, stats, extra_headers = load_inputs(args.inputs, args.delimiter, args.keep_duplicates)
        report = write_outputs(args.output, args.inputs, records, stats, extra_headers,
                               args.currency, args.keep_duplicates, args.overwrite)
    except DataError as error:
        print(f"Invalid CSV: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"Report I/O error: {error}", file=sys.stderr)
        return 3
    print(f"Exported {report['rows_exported']} rows; removed {report['duplicate_rows_removed']} exact duplicates.")
    print(f"Grand total: {report['grand_total']} {report['currency'] or '(currency not specified)'}")
    print(f"Reports: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
