from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from footprint_review.analysis import review
from footprint_review.reporting import html_report, markdown, write_reports
from footprint_review.schema import InputError, load, public_url, validate

PROJECT = Path(__file__).resolve().parents[1]
DEMO = PROJECT / "footprint_review" / "demo.json"


class FootprintReviewTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(DEMO.read_text(encoding="utf-8"))

    def test_demo_reports_only_supported_differences_and_broken_observation(self):
        result = review(validate(self.data))
        self.assertEqual([item["kind"] for item in result["findings"]], ["reference_difference", "reported_broken_link"])
        self.assertEqual(result["findings"][0]["source_ids"], ["directory"])
        self.assertIn("old-address@studio.example", result["findings"][0]["evidence"][0])
        self.assertEqual(result["findings"][1]["source_ids"], ["directory"])

    def test_unobserved_contacts_are_unknown_without_becoming_findings(self):
        self.data["business"]["contacts"] = {}
        for source in self.data["sources"]:
            source["contacts"] = {}
            source["links"] = []
        result = review(validate(self.data))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["unknown_contacts"]), 3)
        self.assertIn("זו אינה קביעה שאין בעיות ברשת", html_report(result))

    def test_conflicting_sources_without_reference_do_not_choose_a_winner(self):
        self.data["business"]["contacts"]["email"] = None
        result = review(validate(self.data))
        difference = result["findings"][0]
        self.assertEqual(difference["kind"], "source_disagreement")
        self.assertEqual(difference["source_ids"], ["site", "directory"])
        self.assertIn("אינו קובע איזה מקור נכון", difference["description"])

    def test_phone_formatting_is_ignored_but_country_prefix_is_not_guessed(self):
        self.data["business"]["contacts"]["phone"] = "+1 (202) 555-0100"
        self.data["sources"][0]["contacts"]["phone"] = "+1 202 555 0100"
        self.data["sources"][1]["contacts"]["phone"] = "202-555-0100"
        differences = [item for item in review(validate(self.data))["findings"] if item["field"] == "phone"]
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0]["source_ids"], ["directory"])

    def test_website_case_and_trailing_slash_are_ignored_without_dropping_query(self):
        self.data["sources"][0]["contacts"]["website"] = "https://STUDIO.example"
        self.data["sources"][1]["contacts"]["website"] = "https://studio.example/?old=1"
        differences = [item for item in review(validate(self.data))["findings"] if item["field"] == "website"]
        self.assertEqual(differences[0]["source_ids"], ["directory"])

    def test_duplicate_source_ids_are_rejected(self):
        self.data["sources"][1]["id"] = "site"
        with self.assertRaisesRegex(InputError, "unique identifier"):
            validate(self.data)

    def test_owner_confirmation_is_required_and_not_inferred_from_truthy_text(self):
        for value in (False, "yes", 1):
            with self.subTest(value=value):
                self.data["business"]["owner_confirmed_reference"] = value
                with self.assertRaisesRegex(InputError, "must be true"):
                    validate(self.data)

    def test_bad_schema_types_and_dates_produce_input_errors(self):
        mutations = [
            lambda data: data.update(version=True),
            lambda data: data.update(data_kind=[]),
            lambda data: data.update(data_kind={}),
            lambda data: data.update(sources="wrong"),
            lambda data: data["sources"][0].update(observed_on="2026-02-30"),
            lambda data: data["sources"][0].update(observed_on="2999-01-01"),
            lambda data: data["sources"][1]["links"][0].update(state=[]),
            lambda data: data["sources"][1]["links"][0].update(state={}),
            lambda data: data["sources"][0].update(unexpected="x"),
            lambda data: data["sources"][0]["contacts"].update(email="invalid"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = copy.deepcopy(self.data)
                mutate(data)
                with self.assertRaises(InputError):
                    validate(data)

    def test_unsafe_and_nonpublic_shaped_urls_are_rejected(self):
        for url in (
            "javascript:alert(1)", "file:///etc/passwd", "https://user:password@example.com/",
            "http://127.0.0.1/", "http://[::1]/", "https://10.0.0.1/", "http://127.1/",
            "http://127.0.0.1./", "https://localhost/", "https://device.local/",
            "https://public.example:9000/", "https://public.example/<script>",
            'https://public.example/" onclick="alert(1)', "https://public.example/\npath",
        ):
            with self.subTest(url=url):
                with self.assertRaises(InputError):
                    public_url(url, "url")

    def test_html_and_markdown_escape_supplied_text(self):
        self.data["business"]["name"] = '<script>alert("test")</script>'
        self.data["sources"][1]["links"][0]["note"] = '<img src=x onerror="bad()"> [click](javascript:bad)'
        result = review(validate(self.data))
        rendered = html_report(result)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x", rendered)
        self.assertIn("default-src 'none'", rendered)
        self.assertNotIn("<script>", markdown(result))
        self.assertIn(r"\[click\]\(javascript:bad\)", markdown(result))

    def test_markdown_cannot_receive_a_url_with_an_html_delimiter(self):
        self.data["sources"][0]["url"] = "https://studio.example/contact><script>alert(1)</script>"
        with self.assertRaisesRegex(InputError, "invalid URL"):
            validate(self.data)

    def test_contact_values_keep_ltr_direction_inside_the_hebrew_table(self):
        self.data["sources"][0]["contacts"]["phone"] = "+1 (202) 555-0100"
        rendered = html_report(review(validate(self.data)))
        for value in ("https://studio.example/", "hello@studio.example", "+1 (202) 555-0100"):
            self.assertIn(f'<bdi dir="ltr">{value}</bdi>', rendered)
        self.assertIn("<td>לא תועד</td>", rendered)
        self.assertNotIn('<bdi dir="ltr">לא תועד</bdi>', rendered)

    def test_synthetic_label_and_limitations_appear_in_both_reports(self):
        result = review(validate(self.data))
        for rendered in (markdown(result), html_report(result)):
            self.assertIn("SYNTHETIC", rendered)
            self.assertIn("לא בוצעה בדיקה של עסק אמיתי", rendered)
            self.assertIn("אינו מחפש ברשת", rendered)
            self.assertIn("directory.example/studio", rendered)
            self.assertIn("2026-11-10", rendered)

    def test_public_observations_label_does_not_claim_owner_verification(self):
        self.data["data_kind"] = "public_observations"
        result = review(validate(self.data))
        for rendered in (markdown(result), html_report(result)):
            self.assertNotIn("SYNTHETIC", rendered)
            self.assertIn("הצהרת מזין הנתונים", rendered)
            self.assertIn("אינה מאמתת הרשאה", rendered)

    def test_new_directory_contains_reproducible_input_and_both_reports(self):
        result = review(validate(self.data))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report"
            write_reports(result, output)
            self.assertEqual({path.name for path in output.iterdir()}, {"report.md", "report.html", "observations.json"})
            self.assertEqual(load(output / "observations.json"), result["data"])

    def test_existing_directory_and_files_are_never_overwritten(self):
        result = review(validate(self.data))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report"
            output.mkdir()
            original = output / "report.html"
            original.write_text("keep me", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_reports(result, output)
            self.assertEqual(original.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(list(output.iterdir()), [original])

    def test_duplicate_json_keys_and_invalid_encoding_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "input.json"
            path.write_text('{"version": 1, "version": 1}', encoding="utf-8")
            with self.assertRaisesRegex(InputError, "duplicate key"):
                load(path)
            path.write_bytes(b"\xff\xfe")
            with self.assertRaisesRegex(InputError, "Invalid UTF-8 JSON"):
                load(path)

    def test_cli_works_from_project_directory_and_fails_cleanly_on_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "demo"
            command = [sys.executable, "-m", "footprint_review", "demo", "--output", str(output)]
            first = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertIn("3 supplied sources; 2 findings", first.stdout)
            original = (output / "report.html").read_bytes()
            second = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(second.returncode, 1)
            self.assertIn("ERROR:", second.stderr)
            self.assertNotIn("Traceback", second.stderr)
            self.assertEqual((output / "report.html").read_bytes(), original)

    def test_cli_rejects_unpaired_unicode_before_creating_output(self):
        invalid_inputs = []
        for path in (("business", "name"), ("sources", 0, "label"), ("sources", 1, "links", 0, "note")):
            data = copy.deepcopy(self.data)
            parent = data
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = "invalid " + chr(0xD800)
            invalid_inputs.append(data)
        bad_key = copy.deepcopy(self.data)
        bad_key[chr(0xDFFF)] = "invalid key"
        invalid_inputs.append(bad_key)
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for index, data in enumerate(invalid_inputs):
                with self.subTest(case=index):
                    source = folder / f"input-{index}.json"
                    source.write_text(json.dumps(data), encoding="utf-8")
                    original = source.read_bytes()
                    output = folder / f"report-{index}"
                    run = subprocess.run(
                        [sys.executable, "-m", "footprint_review", "report", str(source), "--output", str(output)],
                        cwd=PROJECT, capture_output=True, text=True, encoding="utf-8",
                    )
                    self.assertEqual(run.returncode, 1, run.stderr)
                    self.assertIn("ERROR:", run.stderr)
                    self.assertNotIn("Traceback", run.stderr)
                    self.assertFalse(output.exists())
                    self.assertEqual(source.read_bytes(), original)
            self.data["business"]["name"] = "עסק לדוגמה 😺"
            source = folder / "valid-unicode.json"
            source.write_text(json.dumps(self.data), encoding="utf-8")
            output = folder / "valid-report"
            run = subprocess.run(
                [sys.executable, "-m", "footprint_review", "report", str(source), "--output", str(output)],
                cwd=PROJECT, capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn(self.data["business"]["name"], (output / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
