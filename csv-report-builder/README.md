# CSV Report Builder

Consolidate sales exports, remove exact repeated records, and produce grouped totals without spreadsheet formulas or external packages.

**Portfolio demonstration by Eyal Alizade / אייל אליזאדה.** The bundled files are synthetic examples, not client data or a representation of paid client work.

## Run the demo

Python **3.11 or newer**, standard library only. Run from this project directory:

```bash
python -m csv_report_builder samples/sales-a.csv samples/sales-b.csv --output demo-output --currency ILS
python -m unittest discover -s tests -v
```

Expected result: **6 input records → 5 exported records, 1 exact duplicate removed, total 185.10 ILS**. Dates in the two synthetic source batches overlap to demonstrate duplicate detection.

The output directory must be new. To deliberately regenerate an existing demo directory, add `--overwrite`. The program replaces only its four report files and preserves unrelated files.

## Input contract

UTF-8 with or without BOM. Comma, semicolon, tab and pipe delimiters are detected per file. Quoted separators and multiline fields are handled by Python's CSV parser. Use `--delimiter comma`, `semicolon`, `tab` or `pipe` if detection is ambiguous.

| Column | Rule | Supported alias |
| --- | --- | --- |
| `date` | Valid `YYYY-MM-DD` calendar date | `sale_date` |
| `item` | Nonempty text; surrounding whitespace is trimmed for grouping | `product` |
| `quantity` | Positive integer | `qty` |
| `unit_price` | Nonnegative decimal, at most two places; decimal point, no currency symbol | `price` |

Header names are case-insensitive; spaces become underscores. Extra columns are preserved as a union across files. Missing extra cells are blank. Duplicate normalized headers and reserved output names are rejected. Numeric fields support at most 100 integer digits. Zero-priced items are valid; negative quantities/prices and returns are outside this sales-only contract.

Validation is strict: the first invalid record aborts with a filename, input index and record number before report publication. Empty input files, malformed field counts, invalid UTF-8 and an entirely header-only input set fail. Individual header-only files may accompany files containing sales records.

All inputs must use the same currency. `--currency ILS` labels the report; it performs no currency detection or conversion. Without the option, currency is explicitly unspecified.

## What is produced

| File | Contents |
| --- | --- |
| `consolidated.csv` | Canonical columns, exact line totals, input filename/index/record number, preserved extra columns |
| `summary.csv` | One row per item, summed quantity and exact amount |
| `report.json` | Per-file counts, duplicate counts, overall totals and processing policy |
| `report.txt` | Short readable totals and processing notes |

Money is parsed and calculated with `Decimal` and input-sized precision. Monetary values in JSON are decimal strings. CSVs use UTF-8 BOM for common spreadsheet applications. Filename-only provenance avoids writing local directory paths into reports; `source_index` distinguishes identically named input files. A record number counts parsed CSV records, including the header and blank records, rather than physical lines inside quoted fields.

## Duplicate and export behavior

Deduplication compares exact decoded cell values under normalized column names, including extra columns. Delimiter choice, quoting and source location do not distinguish a record. Different extra values are retained. Distinct numeric text such as `1.0` and `1.00` is not treated as an exact duplicate.

Identical rows can also represent legitimate separate sales. Use **`--keep-duplicates`** for that situation; detected and removed counts remain separate in the report. The tool does not infer order identities.

Potential spreadsheet formula text, including item names, extra values, exported headers and source filenames, is prefixed with an apostrophe. This intentionally changes those CSV display values to literal text. The JSON totals and record counts are unaffected.

Outputs are staged before publication. A new output directory is created exclusively; an existing directory is refused unless `--overwrite` is supplied. Inputs cannot be overwritten by generated filenames, and output symlinks are refused. Each replacement file is atomic, but replacement of four files is not a single filesystem transaction; an I/O failure during explicit overwrite may leave a mix of old and new reports.

## Verification and limits

```bash
python -m unittest discover -s tests -v
python -m csv_report_builder --help
```

Tests cover quoted/BOM/multiline CSV, delimiter selection, aliases, exact large-number arithmetic, duplicates, formula-like text, strict failure, blank rows, extra columns and output preservation. Tests create temporary files only.

The implementation keeps validated rows and duplicate keys in memory, so memory use grows with input size. It is a local reporting utility, not an accounting system, database importer or spreadsheet application. Spreadsheet UI rendering was not verified on Windows.

Contact: **Eyal Alizade / אייל אליזאדה** — **mr.pitronot@gmail.com**.
