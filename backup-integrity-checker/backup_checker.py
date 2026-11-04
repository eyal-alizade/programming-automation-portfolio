#!/usr/bin/env python3
"""Small, dependency-free ZIP backup and integrity checker. Python 3.11+."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import BinaryIO, Iterator
import zipfile
import zlib

CHUNK = 1024 * 1024
MANIFEST = "manifest.json"
FORMAT = "backup-integrity-checker"
MAX_MANIFEST = 16 * 1024 * 1024
DEFAULT_MAX_BYTES = 10 * 1024**3
DEFAULT_MAX_FILES = 100_000


class BackupError(Exception):
    """An operation cannot safely finish."""


class VerificationError(BackupError):
    """An archive or source does not match its integrity manifest."""


def safe_relative_path(value: object) -> str:
    """Use one portable, unambiguous spelling; ZIPs are never extracted."""
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise VerificationError("Invalid manifest or archive path")
    if "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise VerificationError(f"Unsupported path: {value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise VerificationError(f"Unsafe relative path: {value!r}")
    return value


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def source_root(source: Path) -> Path:
    if source.is_symlink():
        raise BackupError("Source directory must not be a symbolic link")
    root = source.resolve(strict=True)
    if not root.is_dir():
        raise BackupError("Source must be a directory")
    return root


def scan(root: Path) -> tuple[dict[str, os.stat_result], list[str]]:
    files: dict[str, os.stat_result] = {}
    skipped: list[str] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                if entry.is_symlink():
                    skipped.append(relative)
                elif entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    safe_relative_path(relative)
                    # Windows DirEntry.stat() omits device/inode identity. Use
                    # path metadata so the snapshot can be compared with fstat().
                    info = path.lstat()
                    if not stat.S_ISREG(info.st_mode):
                        raise BackupError(f"Source changed while scanning: {relative!r}")
                    files[relative] = info
                else:
                    raise BackupError(f"Unsupported special file: {relative!r}")

    visit(root)
    return files, skipped


def same_source_tree(before: dict[str, os.stat_result], skipped_before: list[str],
                     after: dict[str, os.stat_result], skipped_after: list[str]) -> bool:
    """Compare file membership and identity after capture or source comparison."""
    return (before.keys() == after.keys() and skipped_before == skipped_after
            and all(signature(before[path]) == signature(after[path]) for path in before))


@contextmanager
def open_regular(root: Path, relative: str, expected: os.stat_result) -> Iterator[BinaryIO]:
    """Refuse symlinks, and on Unix open each component with directory FDs."""
    safe_relative_path(relative)
    descriptors: list[int] = []
    descriptor: int | None = None
    try:
        if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            flags = os.O_RDONLY | os.O_NOFOLLOW
            directory = os.open(root, flags | os.O_DIRECTORY)
            descriptors.append(directory)
            parts = relative.split("/")
            for part in parts[:-1]:
                directory = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
                descriptors.append(directory)
            descriptor = os.open(parts[-1], flags, dir_fd=directory)
        else:
            # Portable best effort. Concurrent hostile filesystem changes are not supported.
            current = root
            for part in relative.split("/"):
                current = current / part
                if current.is_symlink():
                    raise BackupError(f"Source became a symbolic link: {relative!r}")
            descriptor = os.open(current, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or signature(before) != signature(expected):
                raise BackupError(f"Source changed before capture: {relative!r}")
            yield stream
            after = os.fstat(stream.fileno())
            current_info = (root / relative).lstat()
            if signature(before) != signature(after) or signature(after) != signature(current_info):
                raise BackupError(f"Source changed during capture: {relative!r}")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory in reversed(descriptors):
            os.close(directory)


def hash_stream(stream: BinaryIO, destination: BinaryIO | None = None,
                limit: int | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while block := stream.read(CHUNK):
        size += len(block)
        if limit is not None and size > limit:
            raise VerificationError("Uncompressed data exceeds verification limit")
        digest.update(block)
        if destination is not None:
            destination.write(block)
    return size, digest.hexdigest()


def zip_entry(name: str) -> zipfile.ZipInfo:
    entry = zipfile.ZipInfo(name)
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o600) << 16
    return entry


def create_backup(source: Path, archive: Path) -> dict:
    root = source_root(source)
    target = archive.absolute()
    # Resolving parents also catches an output directory symlink into the source.
    resolved_target = target.resolve(strict=False)
    if resolved_target.is_relative_to(root):
        raise BackupError("Archive must be outside the source directory to prevent backup recursion")
    if not target.parent.is_dir():
        raise BackupError("Archive parent directory must already exist")
    files, skipped = scan(root)
    if len(files) > DEFAULT_MAX_FILES:
        raise BackupError("Source exceeds supported file-count limit")
    manifest = {"format": FORMAT, "version": 1, "hash_algorithm": "sha256", "files": []}
    created = False
    owned_signature = None
    try:
        with target.open("xb") as raw:
            created = True
            owned_signature = (os.fstat(raw.fileno()).st_dev, os.fstat(raw.fileno()).st_ino)
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as output:
                for relative, info in files.items():
                    with open_regular(root, relative, info) as stream:
                        with output.open(zip_entry("files/" + relative), "w", force_zip64=True) as member:
                            size, digest = hash_stream(stream, member)
                    if size != info.st_size:
                        raise BackupError(f"Source size changed: {relative!r}")
                    manifest["files"].append({"path": relative, "size": size, "sha256": digest})
                current, current_skipped = scan(root)
                if not same_source_tree(files, skipped, current, current_skipped):
                    raise BackupError("Source tree changed during backup; retry with writers stopped")
                encoded = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                if len(encoded) > MAX_MANIFEST:
                    raise BackupError("Manifest exceeds supported size")
                output.writestr(zip_entry(MANIFEST), encoded)
    except BaseException:
        if created:
            try:
                current = target.lstat()
                if (current.st_dev, current.st_ino) == owned_signature:
                    target.unlink()
            except FileNotFoundError:
                pass
        raise
    return {"status": "ok", "files": len(files), "bytes": sum(i.st_size for i in files.values()),
            "skipped_symlinks": skipped}


def unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"Duplicate manifest field: {key!r}")
        result[key] = value
    return result


def parse_manifest(data: bytes, max_files: int, max_bytes: int) -> dict[str, dict]:
    try:
        manifest = json.loads(data.decode("utf-8"), object_pairs_hook=unique_json_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise VerificationError("Manifest is not valid UTF-8 JSON") from error
    if (not isinstance(manifest, dict)
            or set(manifest) != {"format", "version", "hash_algorithm", "files"}
            or manifest["format"] != FORMAT or type(manifest["version"]) is not int
            or manifest["version"] != 1 or manifest["hash_algorithm"] != "sha256"
            or not isinstance(manifest["files"], list)):
        raise VerificationError("Unsupported manifest format")
    if len(manifest["files"]) > max_files:
        raise VerificationError("Manifest exceeds file-count limit")
    entries = {}
    total = 0
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise VerificationError("Invalid manifest file entry")
        path = safe_relative_path(item["path"])
        if path in entries:
            raise VerificationError(f"Duplicate manifest path: {path!r}")
        if type(item["size"]) is not int or item["size"] < 0:
            raise VerificationError("Invalid manifest file size")
        if not isinstance(item["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"]):
            raise VerificationError("Invalid SHA-256 digest")
        entries[path] = item
        total += item["size"]
    if total > max_bytes:
        raise VerificationError("Manifest exceeds uncompressed-byte limit")
    return entries


def verify_backup(archive: Path, source: Path | None = None, *,
                  max_bytes: int = DEFAULT_MAX_BYTES, max_files: int = DEFAULT_MAX_FILES) -> dict:
    if max_bytes < 0 or max_files < 0:
        raise BackupError("Verification limits must be nonnegative")
    try:
        with zipfile.ZipFile(archive) as backup:
            infos = backup.infolist()
            if len(infos) > max_files + 1:
                raise VerificationError("Archive exceeds file-count limit")
            by_name = {}
            for info in infos:
                safe_relative_path(info.filename)
                if info.filename in by_name:
                    raise VerificationError(f"Duplicate archive member: {info.filename!r}")
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFREG) or info.is_dir() or info.flag_bits & 1:
                    raise VerificationError("Archive contains non-regular or encrypted entries")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise VerificationError("Unsupported ZIP compression method")
                by_name[info.filename] = info
            if MANIFEST not in by_name:
                raise VerificationError("Archive is missing manifest.json")
            if by_name[MANIFEST].file_size > MAX_MANIFEST:
                raise VerificationError("Manifest exceeds supported size")
            with backup.open(MANIFEST) as stream:
                data = stream.read(MAX_MANIFEST + 1)
            if len(data) > MAX_MANIFEST:
                raise VerificationError("Manifest exceeds supported size")
            entries = parse_manifest(data, max_files, max_bytes)
            expected = {MANIFEST} | {"files/" + p for p in entries}
            if expected != set(by_name):
                missing = sorted(expected - set(by_name))
                extra = sorted(set(by_name) - expected)
                raise VerificationError(f"Archive members differ; missing={missing!r}, unexpected={extra!r}")
            total = 0
            for path, item in entries.items():
                name = "files/" + path
                if by_name[name].file_size != item["size"]:
                    raise VerificationError(f"Size mismatch: {path!r}")
                with backup.open(name) as stream:
                    size, digest = hash_stream(stream, limit=min(item["size"], max_bytes - total))
                if size != item["size"] or digest != item["sha256"]:
                    raise VerificationError(f"SHA-256 mismatch: {path!r}")
                total += size
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, zlib.error) as error:
        raise VerificationError(f"Unreadable or corrupt ZIP: {error}") from error
    result = {"status": "ok", "files": len(entries), "bytes": total}
    if source is not None:
        root = source_root(source)
        current, skipped = scan(root)
        if current.keys() != entries.keys():
            raise VerificationError("Source file list differs from the manifest")
        for path, info in current.items():
            with open_regular(root, path, info) as stream:
                size, digest = hash_stream(stream, limit=max_bytes)
            if size != entries[path]["size"] or digest != entries[path]["sha256"]:
                raise VerificationError(f"Source content differs: {path!r}")
        after, after_skipped = scan(root)
        if not same_source_tree(current, skipped, after, after_skipped):
            raise BackupError("Source tree changed during comparison")
        result["source_matches"] = True
        result["skipped_symlinks"] = skipped
    return result


def generate_sample(directory: Path, large_mib: int = 0) -> None:
    if not 0 <= large_mib <= 1024:
        raise BackupError("Sample large-file size must be between 0 and 1024 MiB")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "reports").mkdir()
    (directory / "orders.csv").write_text(
        "order_id,customer,total,currency\nDEMO-001,Synthetic customer A,125,ILS\n"
        "DEMO-002,Synthetic customer B,240,ILS\n", encoding="utf-8")
    (directory / "settings.json").write_text(
        '{"workspace": "Synthetic demo", "contains_real_data": false}\n', encoding="utf-8")
    (directory / "reports" / "notes.txt").write_text(
        "SYNTHETIC PORTFOLIO DATA ONLY. No real customers or client work.\n", encoding="utf-8")
    if large_mib:
        with (directory / "synthetic-blocks.bin").open("xb") as output:
            block = bytes(range(256)) * (CHUNK // 256)
            for _ in range(large_mib):
                output.write(block)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Create a new ZIP outside the source tree")
    backup.add_argument("source", type=Path)
    backup.add_argument("archive", type=Path)
    verify = commands.add_parser("verify", help="Verify without extracting; optionally compare source")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--source", type=Path)
    verify.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    verify.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    sample = commands.add_parser("sample", help="Generate synthetic data in a new directory")
    sample.add_argument("directory", type=Path)
    sample.add_argument("--large-mib", type=int, default=0)
    demo = commands.add_parser("demo", help="Generate synthetic data, back up, then verify")
    demo.add_argument("--output-dir", type=Path, default=Path("demo-output"))
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.source, args.archive)
        elif args.command == "verify":
            result = verify_backup(args.archive, args.source, max_bytes=args.max_bytes, max_files=args.max_files)
        elif args.command == "sample":
            generate_sample(args.directory, args.large_mib)
            result = {"status": "ok", "synthetic_data": True}
        else:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            source = args.output_dir / "source"
            archive = args.output_dir / "sample-backup.zip"
            generate_sample(source)
            create_backup(source, archive)
            result = verify_backup(archive, source)
            result["synthetic_data"] = True
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (BackupError, OSError, ValueError, zipfile.LargeZipFile) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1 if isinstance(error, VerificationError) else 2


if __name__ == "__main__":
    raise SystemExit(main())
