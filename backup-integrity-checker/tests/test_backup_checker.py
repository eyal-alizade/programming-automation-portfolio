from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings
import zipfile

import backup_checker as app


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        app.generate_sample(self.source)
        self.archive = self.base / "backup.zip"

    def backup(self):
        return app.create_backup(self.source, self.archive)

    def rewrite(self, change):
        with zipfile.ZipFile(self.archive) as old:
            items = {info.filename: old.read(info) for info in old.infolist()}
        change(items)
        with zipfile.ZipFile(self.archive, "w") as output:
            for name, data in items.items():
                output.writestr(app.zip_entry(name), data)

    def change_manifest(self, change):
        def rewrite(items):
            manifest = json.loads(items[app.MANIFEST])
            change(manifest)
            items[app.MANIFEST] = json.dumps(manifest).encode()
        self.rewrite(rewrite)

    def test_round_trip_and_source_comparison(self):
        result = self.backup()
        self.assertEqual(result["files"], 3)
        self.assertTrue(app.verify_backup(self.archive, self.source)["source_matches"])
        with zipfile.ZipFile(self.archive) as archive:
            manifest = json.loads(archive.read(app.MANIFEST))
            self.assertEqual(manifest["version"], 1)
            self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["files"]))

    def test_round_trip_when_directory_entries_omit_file_identity(self):
        real_scandir = app.os.scandir

        @contextmanager
        def windows_directory_entries(directory):
            with real_scandir(directory) as entries:
                wrapped = []
                for entry in entries:
                    info = entry.stat(follow_symlinks=False)
                    cached = SimpleNamespace(
                        st_dev=0, st_ino=0, st_mode=info.st_mode,
                        st_size=info.st_size, st_mtime_ns=info.st_mtime_ns,
                        st_ctime_ns=info.st_ctime_ns)
                    wrapped.append(SimpleNamespace(
                        name=entry.name, path=entry.path,
                        is_symlink=entry.is_symlink, is_dir=entry.is_dir,
                        is_file=entry.is_file,
                        stat=lambda follow_symlinks=False, value=cached: value))
                yield iter(wrapped)

        with patch.object(app.os, "scandir", windows_directory_entries):
            self.assertEqual(self.backup()["files"], 3)
            self.assertTrue(app.verify_backup(self.archive, self.source)["source_matches"])

    def test_scan_rejects_file_that_changed_to_a_symlink(self):
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFLNK)):
            with self.assertRaisesRegex(app.BackupError, "changed while scanning"):
                app.scan(self.source)

    def test_refuses_overwrite_without_touching_existing_output(self):
        self.archive.write_bytes(b"keep me")
        with self.assertRaises(FileExistsError):
            self.backup()
        self.assertEqual(self.archive.read_bytes(), b"keep me")

    def test_rejects_recursive_output_inside_source(self):
        nested = self.source / "backups"
        nested.mkdir()
        with self.assertRaisesRegex(app.BackupError, "outside"):
            app.create_backup(self.source, nested / "backup.zip")
        self.assertFalse((nested / "backup.zip").exists())

    def test_modified_payload_is_detected_without_extraction(self):
        self.backup()
        self.rewrite(lambda files: files.__setitem__("files/orders.csv", b"bad"))
        with self.assertRaises(app.VerificationError):
            app.verify_backup(self.archive)
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["backup.zip", "source"])

    def test_same_size_payload_tamper_detected_by_hash(self):
        self.backup()
        self.rewrite(lambda files: files.__setitem__("files/orders.csv", b"x" * len(files["files/orders.csv"])))
        with self.assertRaisesRegex(app.VerificationError, "SHA-256"):
            app.verify_backup(self.archive)

    def test_missing_payload(self):
        self.backup()
        self.rewrite(lambda files: files.pop("files/orders.csv"))
        with self.assertRaisesRegex(app.VerificationError, "missing"):
            app.verify_backup(self.archive)

    def test_unexpected_payload(self):
        self.backup()
        self.rewrite(lambda files: files.__setitem__("files/extra.txt", b"unexpected"))
        with self.assertRaisesRegex(app.VerificationError, "unexpected"):
            app.verify_backup(self.archive)

    def test_missing_manifest(self):
        self.backup()
        self.rewrite(lambda files: files.pop(app.MANIFEST))
        with self.assertRaisesRegex(app.VerificationError, "missing manifest"):
            app.verify_backup(self.archive)

    def test_tampered_manifest_hash(self):
        self.backup()
        self.change_manifest(lambda manifest: manifest["files"][0].__setitem__("sha256", "0" * 64))
        with self.assertRaises(app.VerificationError):
            app.verify_backup(self.archive)

    def test_invalid_manifest_and_duplicate_json_fields(self):
        self.backup()
        self.rewrite(lambda files: files.__setitem__(app.MANIFEST, b'{"version":1,"version":1}'))
        with self.assertRaisesRegex(app.VerificationError, "Duplicate manifest field"):
            app.verify_backup(self.archive)

    def test_unsafe_archive_names(self):
        self.backup()
        for unsafe in ("../outside.txt", "/absolute.txt", "files/../escape", "C:/windows.txt", "files\\escape"):
            with self.subTest(path=unsafe):
                candidate = self.base / "unsafe.zip"
                with zipfile.ZipFile(candidate, "w") as archive:
                    archive.writestr(unsafe, b"do not extract")
                with self.assertRaises(app.VerificationError):
                    app.verify_backup(candidate)
        self.assertFalse((self.base / "outside.txt").exists())

    def test_unsafe_manifest_path(self):
        self.backup()
        self.change_manifest(lambda manifest: manifest["files"][0].__setitem__("path", "../outside"))
        with self.assertRaisesRegex(app.VerificationError, "Unsafe"):
            app.verify_backup(self.archive)

    def test_duplicate_archive_names(self):
        self.backup()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "a") as archive:
                archive.writestr(app.MANIFEST, b"{}")
        with self.assertRaisesRegex(app.VerificationError, "Duplicate archive"):
            app.verify_backup(self.archive)

    def test_symlink_archive_member_rejected(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            entry = zipfile.ZipInfo("files/link")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../outside")
        with self.assertRaisesRegex(app.VerificationError, "non-regular"):
            app.verify_backup(self.archive)

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "Symlinks unavailable")
    def test_source_symlinks_are_not_followed(self):
        secret = self.base / "outside.txt"
        secret.write_text("SYNTHETIC OUTSIDE DATA")
        try:
            (self.source / "link.txt").symlink_to(secret)
            (self.source / "loop").symlink_to(self.source, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks not permitted")
        result = self.backup()
        self.assertEqual(result["skipped_symlinks"], ["link.txt", "loop"])
        self.assertEqual(app.verify_backup(self.archive)["files"], 3)

    def test_modified_or_extra_source_detected(self):
        self.backup()
        (self.source / "orders.csv").write_text("Changed synthetic data")
        with self.assertRaisesRegex(app.VerificationError, "Source content"):
            app.verify_backup(self.archive, self.source)
        (self.source / "new.txt").write_text("new")
        with self.assertRaisesRegex(app.VerificationError, "Source file list"):
            app.verify_backup(self.archive, self.source)

    def test_changing_file_aborts_and_removes_partial_output(self):
        original = app.hash_stream

        def changing(stream, destination=None, limit=None):
            result = original(stream, destination, limit)
            with (self.source / "orders.csv").open("a") as changed:
                changed.write("changed during capture\n")
            return result

        with patch.object(app, "hash_stream", side_effect=changing):
            with self.assertRaisesRegex(app.BackupError, "changed"):
                self.backup()
        self.assertFalse(self.archive.exists())

    def test_streaming_large_file(self):
        block = bytes(range(256)) * (app.CHUNK // 256)
        large = self.source / "large.bin"
        with large.open("wb") as stream:
            for _ in range(12):
                stream.write(block)
        self.backup()
        self.assertGreater(app.verify_backup(self.archive, self.source)["bytes"], 12 * app.CHUNK)

    def test_verification_limits(self):
        self.backup()
        with self.assertRaisesRegex(app.VerificationError, "file-count"):
            app.verify_backup(self.archive, max_files=1)
        with self.assertRaisesRegex(app.VerificationError, "byte"):
            app.verify_backup(self.archive, max_bytes=1)

    def test_empty_directory_and_unicode_names(self):
        (self.source / "דוח.txt").write_text("נתונים סינתטיים בלבד", encoding="utf-8")
        (self.source / "empty").mkdir()
        self.backup()
        self.assertEqual(app.verify_backup(self.archive, self.source)["files"], 4)

    def test_corrupt_or_truncated_zip(self):
        self.backup()
        self.archive.write_bytes(self.archive.read_bytes()[:20])
        with self.assertRaisesRegex(app.VerificationError, "corrupt ZIP"):
            app.verify_backup(self.archive)

    def test_coherent_rewrite_is_not_authentication(self):
        # Document the threat-model boundary: an unsigned manifest can also be rewritten.
        self.backup()

        def change(files):
            body = b"coherently changed"
            files["files/orders.csv"] = body
            manifest = json.loads(files[app.MANIFEST])
            entry = next(item for item in manifest["files"] if item["path"] == "orders.csv")
            entry.update(size=len(body), sha256=hashlib.sha256(body).hexdigest())
            files[app.MANIFEST] = json.dumps(manifest).encode()

        self.rewrite(change)
        self.assertEqual(app.verify_backup(self.archive)["status"], "ok")
        with self.assertRaises(app.VerificationError):
            app.verify_backup(self.archive, self.source)


if __name__ == "__main__":
    unittest.main()
