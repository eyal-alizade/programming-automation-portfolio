import contextlib
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest

import csv_report_builder as app


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, text, name="input.csv", encoding="utf-8"):
        path = self.root / name
        path.write_text(text, encoding=encoding, newline="")
        return path

    def run_cli(self, paths, output=None, *options):
        output = output or self.root / "reports"
        stream, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(errors):
            result = app.main([*[str(p) for p in paths], "--output", str(output), *options])
        return result, stream.getvalue(), errors.getvalue(), output

    def test_bundled_synthetic_demo_totals_and_transparent_deduplication(self):
        samples = Path(__file__).parents[1] / "samples"
        result, _, errors, output = self.run_cli(sorted(samples.glob("*.csv")), None, "--currency", "ILS")
        self.assertEqual(result, 0, errors)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["data_rows_read"], 6)
        self.assertEqual(report["rows_exported"], 5)
        self.assertEqual(report["duplicate_rows_removed"], 1)
        self.assertEqual(report["grand_total"], "185.10")
        self.assertEqual(report["total_quantity"], 9)
        self.assertEqual(set(p.name for p in output.iterdir()), set(app.OUTPUT_FILES))

    def test_utf8_bom_semicolon_aliases_and_extra_column_are_preserved(self):
        source = self.source('sale_date;product;qty;price;Note\n2026-11-03;"מחברת; כחולה";2;0.10;"demo, only"\n', encoding="utf-8-sig")
        code, _, error, output = self.run_cli([source])
        self.assertEqual(code, 0, error)
        with (output / "consolidated.csv").open(encoding="utf-8-sig", newline="") as file:
            row = next(csv.DictReader(file))
        self.assertEqual(row["item"], "מחברת; כחולה")
        self.assertEqual(row["line_total"], "0.20")
        self.assertEqual(row["note"], "demo, only")

    def test_explicit_tab_delimiter_and_quoted_multiline_field(self):
        for index, newline in enumerate(("\n", "\r\n")):
            with self.subTest(newline=repr(newline)):
                item = f"A{newline}B"
                content = (
                    f"date\titem\tquantity\tunit_price{newline}"
                    f'2026-11-03\t"{item}"\t1\t12.34{newline}'
                )
                source = self.source(content, name=f"input-{index}.csv")
                self.assertEqual(source.read_bytes(), content.encode("utf-8"))
                code, _, error, output = self.run_cli(
                    [source], self.root / f"reports-{index}", "--delimiter", "tab",
                )
                self.assertEqual(code, 0, error)
                for report in ("summary.csv", "consolidated.csv"):
                    with (output / report).open(encoding="utf-8-sig", newline="") as file:
                        self.assertEqual(next(csv.DictReader(file))["item"], item)

    def test_decimal_totals_do_not_use_binary_float_or_default_precision(self):
        source = self.source("date,item,quantity,unit_price\n2026-11-03,A,3,0.10\n2026-11-03,B,123456789012345678901234567890,987654321098765432109876543210.99\n")
        records, _, _ = app.load_inputs([source], delimiter=",")
        self.assertEqual(records[0].line_total, Decimal("0.30"))
        expected_cents = 123456789012345678901234567890 * 98765432109876543210987654321099
        expected = str(expected_cents // 100) + "." + str(expected_cents % 100).zfill(2)
        self.assertEqual(app.money(records[1].line_total), expected)
        _, total, _ = app.summarize(records)
        self.assertEqual(app.money(total), str((expected_cents + 30) // 100) + "." + str((expected_cents + 30) % 100).zfill(2))

    def test_duplicate_detection_uses_extra_columns_and_can_keep_repeats(self):
        source = self.source("date,item,quantity,unit_price,reference\n2026-11-03,A,1,1.00,one\n2026-11-03,A,1,1.00,two\n2026-11-03,A,1,1.00,one\n")
        records, stats, _ = app.load_inputs([source], delimiter=",")
        self.assertEqual(len(records), 2)
        self.assertEqual(stats[0].duplicate_rows_removed, 1)
        kept, stats, _ = app.load_inputs([source], delimiter=",", keep_duplicates=True)
        self.assertEqual(len(kept), 3)
        self.assertEqual(stats[0].duplicate_rows_detected, 1)
        self.assertEqual(stats[0].duplicate_rows_removed, 0)

    def test_formula_like_text_is_escaped_in_headers_items_extras_and_filenames(self):
        source = self.source('date,item,quantity,unit_price,=note\n2026-11-03,"=1+1",1,1.00,"  @example"\n', name="=source.csv")
        code, _, error, output = self.run_cli([source], None, "--delimiter", "comma")
        self.assertEqual(code, 0, error)
        with (output / "consolidated.csv").open(encoding="utf-8-sig", newline="") as file:
            row = next(csv.DictReader(file))
        self.assertEqual(row["item"], "'=1+1")
        self.assertEqual(row["source_file"], "'=source.csv")
        self.assertEqual(row["'=note"], "'  @example")
        self.assertEqual(app.protect_text("\tvalue"), "'\tvalue")

    def test_invalid_row_aborts_before_creating_output(self):
        for field, value in (("date", "2026-02-30"), ("quantity", "0"), ("quantity", "1.5"), ("unit_price", "NaN"), ("unit_price", "-1"), ("unit_price", "1.234"), ("item", "")):
            with self.subTest(field=field, value=value):
                row = {"date": "2026-11-03", "item": "A", "quantity": "1", "unit_price": "1.00"}
                row[field] = value
                source = self.source(",".join(row) + "\n" + ",".join(row.values()) + "\n")
                code, _, error, output = self.run_cli([source], None, "--delimiter", "comma")
                self.assertEqual(code, 2)
                self.assertIn("record 2", error)
                self.assertFalse(output.exists())

    def test_malformed_field_count_and_duplicate_alias_headers_fail(self):
        for content in (
            "date,item,quantity,unit_price\n2026-11-03,A,1,1.00,extra\n",
            "date,item,quantity,qty,unit_price\n2026-11-03,A,1,1,1.00\n",
            "date,item,quantity,unit_price\n2026-11-03,\"unterminated,1,1.00\n",
            "date,item,quantity,unit_price,line_total\n2026-11-03,A,1,1.00,1.00\n",
        ):
            with self.subTest(content=content):
                code, _, _, output = self.run_cli([self.source(content)], None, "--delimiter", "comma")
                self.assertEqual(code, 2)
                self.assertFalse(output.exists())

    def test_empty_header_only_and_non_utf8_inputs_fail_without_outputs(self):
        for content in ("", "date,item,quantity,unit_price\n"):
            with self.subTest(content=content):
                code, _, _, output = self.run_cli([self.source(content)], None, "--delimiter", "comma")
                self.assertEqual(code, 2)
                self.assertFalse(output.exists())
        source = self.root / "binary.csv"
        source.write_bytes(b"\xff\xfe\x00\x00")
        code, _, error, output = self.run_cli([source], None, "--delimiter", "comma")
        self.assertEqual(code, 2)
        self.assertIn("UTF-8", error)
        self.assertFalse(output.exists())

    def test_existing_directory_is_untouched_without_explicit_overwrite(self):
        source = self.source("date,item,quantity,unit_price\n2026-11-03,A,1,1.00\n")
        output = self.root / "existing"
        output.mkdir()
        sentinel = output / "report.json"
        sentinel.write_text("leave me", encoding="utf-8")
        code, _, _, _ = self.run_cli([source], output)
        self.assertEqual(code, 3)
        self.assertEqual(sentinel.read_text(), "leave me")
        self.assertEqual(len(list(output.iterdir())), 1)

    def test_explicit_overwrite_replaces_reports_but_preserves_unrelated_files(self):
        source = self.source("date,item,quantity,unit_price\n2026-11-03,A,1,1.00\n")
        _, _, _, output = self.run_cli([source])
        unrelated = output / "notes.txt"
        unrelated.write_text("keep", encoding="utf-8")
        source.write_text("date,item,quantity,unit_price\n2026-11-03,A,2,1.00\n", encoding="utf-8")
        code, _, error, _ = self.run_cli([source], output, "--overwrite")
        self.assertEqual(code, 0, error)
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertEqual(json.loads((output / "report.json").read_text())["grand_total"], "2.00")

    def test_input_cannot_be_overwritten_even_with_explicit_flag(self):
        source = self.source("date,item,quantity,unit_price\n2026-11-03,A,1,1.00\n", name="consolidated.csv")
        before = source.read_bytes()
        code, _, error, _ = self.run_cli([source], self.root, "--overwrite")
        self.assertEqual(code, 3)
        self.assertIn("input would be overwritten", error)
        self.assertEqual(source.read_bytes(), before)

    def test_missing_file_is_an_io_error(self):
        code, _, _, output = self.run_cli([self.root / "missing.csv"])
        self.assertEqual(code, 3)
        self.assertFalse(output.exists())

    def test_blank_rows_are_reported_and_extra_columns_are_unioned(self):
        first = self.source("date,item,quantity,unit_price,note\n\n2026-11-03,A,1,1.00,test\n", "first.csv")
        second = self.source("date,item,quantity,unit_price,branch\n2026-11-03,B,1,2.00,north\n", "second.csv")
        code, _, error, output = self.run_cli([first, second])
        self.assertEqual(code, 0, error)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["blank_rows_skipped"], 1)
        with (output / "consolidated.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(rows[0]["branch"], "")
        self.assertEqual(rows[1]["note"], "")


if __name__ == "__main__":
    unittest.main()
