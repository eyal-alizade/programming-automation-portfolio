"""Behavioral safety tests. Every operation is isolated in TemporaryDirectory."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import errno
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import organizer
import sample_data


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "incoming"
        self.target = self.base / "organized"
        self.source.mkdir()

    def write(self, name="report.txt", content="Synthetic report"):
        path = self.source / name
        path.write_text(content, encoding="utf-8")
        return path

    def create_symlink(self, link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except NotImplementedError as error:
            self.skipTest(f"Creating symlinks is unavailable: {error}")
        except OSError as error:
            capability_errors = {
                errno.EPERM, errno.EACCES, errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
                getattr(errno, "ENOTSUP", errno.ENOSYS),
            }
            if (error.errno not in capability_errors
                    and getattr(error, "winerror", None) not in {50, 1314}):
                raise
            self.skipTest(f"Creating symlinks is unavailable: {error}")

    def run_cli(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = organizer.main(list(map(str, arguments)))
        return code, output.getvalue(), errors.getvalue()

    def apply(self):
        code, output, errors = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 0, errors + output)
        return next((self.target / organizer.JOURNAL_DIR).glob("*.json"))

    def test_default_dry_run_changes_nothing(self):
        original = self.write()
        code, output, _ = self.run_cli("organize", self.source, self.target)
        self.assertEqual(code, 0)
        self.assertIn("WOULD MOVE report.txt -> txt/report.txt", output)
        self.assertTrue(original.exists())
        self.assertFalse(self.target.exists())

    def test_extension_organization_and_undo_round_trip(self):
        original = self.write("report.TXT")
        metadata = original.stat()
        self.write("README", "No extension")
        manifest = self.apply()
        moved = self.target / "txt" / original.name
        self.assertEqual(moved.read_text(), "Synthetic report")
        self.assertEqual(moved.stat().st_mtime_ns, metadata.st_mtime_ns)
        self.assertTrue((self.target / "no-extension" / "README").exists())
        self.assertFalse(original.exists())
        code, output, _ = self.run_cli("undo", manifest)
        self.assertEqual(code, 0)
        self.assertIn("WOULD RESTORE", output)
        self.assertFalse(original.exists())
        self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 0)
        self.assertEqual(original.read_text(), "Synthetic report")
        self.assertFalse(moved.exists())
        self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 0)

    def test_date_uses_utc_modification_month(self):
        path = self.write()
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        os.utime(path, (timestamp, timestamp))
        code, output, errors = self.run_cli("organize", self.source, self.target, "--by", "date", "--apply")
        self.assertEqual(code, 0, errors)
        self.assertIn("2026-01/report.txt", output)
        self.assertTrue((self.target / "2026-01" / "report.txt").exists())

    def test_existing_destination_is_never_overwritten(self):
        original = self.write()
        destination = self.target / "txt" / original.name
        destination.parent.mkdir(parents=True)
        destination.write_text("Existing work")
        code, output, _ = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("destination already exists", output)
        self.assertEqual(destination.read_text(), "Existing work")
        self.assertTrue(original.exists())

    def test_collision_created_after_planning_is_refused(self):
        original = self.write()
        expected = organizer.fingerprint(original)
        self.target.mkdir()
        destination = self.target / original.name
        destination.write_text("Concurrent file")
        with self.assertRaises(FileExistsError):
            organizer.move_exclusive(original, destination, expected)
        self.assertEqual(destination.read_text(), "Concurrent file")
        self.assertTrue(original.exists())

    def test_nested_directories_are_not_traversed(self):
        nested = self.source / "nested"
        nested.mkdir()
        (nested / "private.txt").write_text("Leave untouched")
        self.write()
        self.apply()
        self.assertTrue((nested / "private.txt").exists())
        self.assertFalse((self.target / "txt" / "private.txt").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink support required")
    def test_symlink_file_is_ignored(self):
        external = self.base / "external.txt"
        external.write_text("Outside input")
        self.create_symlink(self.source / "link.txt", external)
        code, output, _ = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("not a regular top-level file", output)
        self.assertEqual(external.read_text(), "Outside input")
        self.assertTrue((self.source / "link.txt").is_symlink())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink support required")
    def test_symlinked_source_root_is_rejected(self):
        alias = self.base / "alias"
        self.create_symlink(alias, self.source, target_is_directory=True)
        code, _, errors = self.run_cli("organize", alias, self.target)
        self.assertEqual(code, 2)
        self.assertIn("Symlinks are not allowed", errors)

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink support required")
    def test_symlink_before_parent_traversal_in_source_is_rejected(self):
        original = self.write()
        external = self.base / "external"
        (external / "nested").mkdir(parents=True)
        (external / "incoming").mkdir()
        outside_file = external / "incoming" / "outside.txt"
        outside_file.write_text("Synthetic outside file")
        alias = self.base / "alias"
        self.create_symlink(alias, external / "nested", target_is_directory=True)
        supplied = alias / ".." / "incoming"
        self.assertEqual(supplied.resolve(), external / "incoming")
        code, _, errors = self.run_cli("organize", supplied, self.target, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("Symlinks are not allowed", errors)
        self.assertTrue(original.exists())
        self.assertTrue(outside_file.exists())
        self.assertFalse(self.target.exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink support required")
    def test_symlink_before_parent_traversal_in_target_is_rejected(self):
        original = self.write()
        external = self.base / "external"
        (external / "nested").mkdir(parents=True)
        alias = self.base / "alias"
        self.create_symlink(alias, external / "nested", target_is_directory=True)
        supplied = alias / ".." / "organized"
        self.assertEqual(supplied.resolve(), external / "organized")
        code, _, errors = self.run_cli("organize", self.source, supplied, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("Symlinks are not allowed", errors)
        self.assertTrue(original.exists())
        self.assertFalse(self.target.exists())
        self.assertFalse((external / "organized").exists())

    @unittest.skipUnless(hasattr(os, "symlink"), "Symlink support required")
    def test_symlinked_target_group_is_rejected(self):
        original = self.write()
        self.target.mkdir()
        external = self.base / "external"
        external.mkdir()
        self.create_symlink(self.target / "txt", external, target_is_directory=True)
        code, output, _ = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 0)
        self.assertIn("Symlinks are not allowed", output)
        self.assertTrue(original.exists())
        self.assertEqual(list(external.iterdir()), [])

    def test_equal_or_nested_roots_are_rejected(self):
        for target in (self.source, self.source / "nested", self.base):
            with self.subTest(target=target):
                code, _, errors = self.run_cli("organize", self.source, target, "--apply")
                self.assertEqual(code, 2)
                self.assertIn("non-nested", errors)

    def test_missing_source_and_file_target_are_rejected(self):
        self.assertEqual(self.run_cli("organize", self.base / "missing", self.target)[0], 2)
        self.target.write_text("Not a directory")
        self.assertEqual(self.run_cli("organize", self.source, self.target)[0], 2)

    def test_target_beneath_existing_file_is_rejected_before_preview(self):
        blocker = self.base / "file"
        blocker.write_text("Not a parent directory")
        code, _, errors = self.run_cli("organize", self.source, blocker / "output")
        self.assertEqual(code, 2)
        self.assertIn("existing parent", errors)

    def test_changed_source_after_plan_is_preserved(self):
        original = self.write()
        expected = organizer.fingerprint(original)
        original.write_text("Changed")
        with self.assertRaises(organizer.OrganizerError):
            organizer.move_exclusive(original, self.target / "txt" / original.name, expected)
        self.assertEqual(original.read_text(), "Changed")
        self.assertFalse(self.target.exists())

    def test_undo_refuses_changed_content_even_with_original_size_and_time(self):
        self.write(content="AAAA")
        manifest = self.apply()
        moved = self.target / "txt" / "report.txt"
        old = moved.stat()
        moved.write_text("BBBB")
        os.utime(moved, ns=(old.st_atime_ns, old.st_mtime_ns))
        code, output, _ = self.run_cli("undo", manifest, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("organized file changed", output)
        self.assertFalse((self.source / "report.txt").exists())
        self.assertEqual(moved.read_text(), "BBBB")

    def test_undo_refuses_existing_original_path(self):
        self.write()
        manifest = self.apply()
        replacement = self.write(content="New original")
        code, output, _ = self.run_cli("undo", manifest, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("original path already exists", output)
        self.assertEqual(replacement.read_text(), "New original")
        self.assertTrue((self.target / "txt" / "report.txt").exists())

    def test_undo_refuses_missing_organized_file(self):
        self.write()
        manifest = self.apply()
        (self.target / "txt" / "report.txt").unlink()
        code, output, _ = self.run_cli("undo", manifest, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("missing", output)
        self.assertFalse((self.source / "report.txt").exists())

    def test_unsafe_or_malformed_manifest_entries_are_rejected(self):
        self.write()
        manifest = self.apply()
        clean = json.loads(manifest.read_text())
        mutations = [
            ("relative_target", "../../outside.txt"),
            ("name", "../outside.txt"),
            ("name", "C:outside.txt"),
            ("status", ["moved"]),
            ("fingerprint", {}),
        ]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                document = json.loads(json.dumps(clean))
                document["entries"][0][key] = value
                manifest.write_text(json.dumps(document))
                self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 2)
        self.assertTrue((self.target / "txt" / "report.txt").exists())

    def test_duplicate_manifest_entries_are_rejected(self):
        self.write()
        manifest = self.apply()
        document = json.loads(manifest.read_text())
        document["entries"].append(document["entries"][0].copy())
        manifest.write_text(json.dumps(document))
        self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 2)

    def test_corrupt_json_is_reported_cleanly(self):
        self.write()
        manifest = self.apply()
        manifest.write_text("{broken")
        code, _, errors = self.run_cli("undo", manifest)
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", errors)
        self.assertNotIn("Traceback", errors)

    def test_failure_to_create_hard_link_preserves_source(self):
        original = self.write()
        with patch("organizer.os.link", side_effect=PermissionError("hard links unavailable")):
            code, output, _ = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 1)
        self.assertIn("hard links unavailable", output)
        self.assertTrue(original.exists())
        self.assertFalse((self.target / "txt" / original.name).exists())

    def test_journal_failure_after_move_can_be_recovered(self):
        self.write()
        real_save = organizer.save_manifest
        calls = 0

        def failing_save(path, document):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated journal write failure")
            return real_save(path, document)

        with patch("organizer.save_manifest", side_effect=failing_save):
            code, _, errors = self.run_cli("organize", self.source, self.target, "--apply")
        self.assertEqual(code, 2)
        self.assertIn("journal write failure", errors)
        manifest = next((self.target / organizer.JOURNAL_DIR).glob("*.json"))
        self.assertEqual(json.loads(manifest.read_text())["entries"][0]["status"], "pending")
        self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 0)
        self.assertTrue((self.source / "report.txt").exists())

    def test_pending_entry_that_never_moved_does_not_touch_source(self):
        original = self.write()
        entries, _ = organizer.build_plan(self.source, self.target, "extension")
        journal = self.target / organizer.JOURNAL_DIR
        journal.mkdir(parents=True)
        manifest = journal / "interrupted.json"
        organizer.save_manifest(manifest, {"version": 1, "source": str(self.source),
                                          "target": str(self.target), "entries": entries})
        self.assertEqual(self.run_cli("undo", manifest, "--apply")[0], 0)
        self.assertTrue(original.exists())

    def test_sample_generator_is_isolated_and_refuses_overwrite(self):
        sample = self.base / "synthetic"
        sample_data.generate(sample)
        self.assertEqual(len([p for p in sample.iterdir() if p.is_file()]), 5)
        self.assertTrue((sample / "nested" / "leave-me.txt").exists())
        before = (sample / "meeting.txt").read_bytes()
        with self.assertRaises(FileExistsError):
            sample_data.generate(sample)
        self.assertEqual((sample / "meeting.txt").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
