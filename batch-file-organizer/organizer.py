#!/usr/bin/env python3
"""Preview, organize and undo top-level file moves using only the standard library."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

VERSION = 1
JOURNAL_DIR = ".organizer-manifests"


class OrganizerError(Exception):
    """An actionable validation or safety error."""


def absolute(path: str | Path) -> Path:
    # Path.absolute() preserves '..'. Check the original components before
    # abspath() collapses them, otherwise link/../folder may select a different
    # directory from the one the operating system would resolve.
    candidate = Path(os.path.expanduser(path)).absolute()
    check_no_symlinks(candidate)
    return Path(os.path.abspath(candidate))


def check_no_symlinks(path: Path) -> None:
    """Check existing components before resolving or creating any directories."""
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise OrganizerError(f"Symlinks are not allowed in paths: {component}")


def roots(source: str | Path, target: str | Path) -> tuple[Path, Path]:
    source, target = absolute(source), absolute(target)
    check_no_symlinks(source)
    check_no_symlinks(target)
    if not source.is_dir():
        raise OrganizerError("Source must be an existing directory.")
    if target.exists() and not target.is_dir():
        raise OrganizerError("Target must be a directory or a new directory path.")
    if source == target or source in target.parents or target in source.parents:
        raise OrganizerError("Source and target must be separate, non-nested directories.")
    existing_parent = target
    while not existing_parent.exists():
        existing_parent = existing_parent.parent
    if not existing_parent.is_dir():
        raise OrganizerError("The target's existing parent must be a directory.")
    if source.stat().st_dev != existing_parent.stat().st_dev:
        raise OrganizerError("Source and target must be on the same filesystem.")
    return source, target


def fingerprint(path: Path) -> dict:
    """Hash a stable regular file without following symlinks."""
    check_no_symlinks(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise OrganizerError(f"Not a regular file: {path.name}")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    now = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(now):
        raise OrganizerError(f"File changed while reading: {path.name}")
    return {"size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest}


def grouping(path: Path, method: str) -> str:
    if method == "date":
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m")
    extension = path.suffix[1:].lower()
    if not extension:
        return "no-extension"
    return extension if re.fullmatch(r"[a-z0-9]{1,16}", extension) else "other-extension"


def build_plan(source: Path, target: Path, method: str) -> tuple[list[dict], list[str]]:
    entries, skipped = [], []
    for path in sorted(source.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if "\\" in path.name or ":" in path.name:
            skipped.append(f"{path.name}: unsupported filename character")
            continue
        if path.is_symlink() or not path.is_file():
            skipped.append(f"{path.name}: not a regular top-level file")
            continue
        try:
            relative = Path(grouping(path, method)) / path.name
            destination = target / relative
            check_no_symlinks(destination)
            if destination.exists():
                skipped.append(f"{path.name}: destination already exists")
                continue
            if destination.parent.exists() and not destination.parent.is_dir():
                skipped.append(f"{path.name}: destination group is not a directory")
                continue
            entries.append({"name": path.name, "relative_target": relative.as_posix(),
                            "fingerprint": fingerprint(path), "status": "pending"})
        except (OSError, OrganizerError, ValueError, OverflowError) as error:
            skipped.append(f"{path.name}: {error}")
    return entries, skipped


def move_exclusive(source: Path, destination: Path, expected: dict) -> None:
    """Hard-link then unlink: destination creation atomically refuses overwrite."""
    check_no_symlinks(source)
    check_no_symlinks(destination)
    if fingerprint(source) != expected:
        raise OrganizerError(f"File changed since planning: {source.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, destination, follow_symlinks=False)
    try:
        if fingerprint(destination) != expected or not os.path.samefile(source, destination):
            raise OrganizerError(f"File changed during move: {source.name}")
        source.unlink()
    except BaseException:
        # Remove only the extra link this function created; preserve the source.
        if destination.exists() and source.exists() and os.path.samefile(source, destination):
            destination.unlink()
        raise


def save_manifest(path: Path, document: dict) -> None:
    """Publish a complete JSON journal with atomic replacement and file fsync."""
    check_no_symlinks(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def organize(source_arg: str, target_arg: str, method: str, apply: bool) -> int:
    source, target = roots(source_arg, target_arg)
    entries, skipped = build_plan(source, target, method)
    print("APPLY — moving files." if apply else "DRY RUN — no files will be changed.")
    for item in entries:
        print(f"{'MOVE' if apply else 'WOULD MOVE'} {item['name']} -> {item['relative_target']}")
    for reason in skipped:
        print(f"SKIP {reason}")
    print(f"Planned: {len(entries)} | Skipped: {len(skipped)}")
    if not apply or not entries:
        return 0
    journal_dir = target / JOURNAL_DIR
    check_no_symlinks(journal_dir)
    journal_dir.mkdir(parents=True, exist_ok=True)
    manifest = journal_dir / f"{uuid.uuid4().hex}.json"
    document = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "source": str(source), "target": str(target), "method": method,
                "entries": entries}
    save_manifest(manifest, document)  # Persist intent before the first move.
    print(f"Manifest: {manifest}", flush=True)
    moved, failed = 0, 0
    for item in entries:
        try:
            move_exclusive(source / item["name"], target / item["relative_target"],
                           item["fingerprint"])
        except (OSError, OrganizerError) as error:
            item["status"] = "skipped"
            failed += 1
            print(f"SKIP {item['name']}: {error}")
        else:
            item["status"] = "moved"
            moved += 1
        # If this fails, stop. The previous pending record still permits recovery.
        save_manifest(manifest, document)
    print(f"Moved: {moved} | Failed: {failed}")
    return 1 if failed else 0


def load_manifest(path: Path) -> tuple[dict, Path, Path]:
    check_no_symlinks(path)
    with path.open(encoding="utf-8") as stream:
        document = json.load(stream)
    if (not isinstance(document, dict) or type(document.get("version")) is not int
            or document["version"] != VERSION):
        raise OrganizerError("Unsupported manifest format or version.")
    if not all(isinstance(document.get(key), str) for key in ("source", "target")):
        raise OrganizerError("Manifest roots must be paths.")
    if not all(Path(document[key]).is_absolute() for key in ("source", "target")):
        raise OrganizerError("Manifest roots must be absolute paths.")
    source, target = roots(document["source"], document["target"])
    if path.parent != target / JOURNAL_DIR or not target.is_dir():
        raise OrganizerError("Keep the manifest in its original target journal directory.")
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise OrganizerError("Manifest entries must be a list.")
    seen_source, seen_target = set(), set()
    for item in entries:
        if not isinstance(item, dict):
            raise OrganizerError("Invalid manifest entry.")
        name, relative, expected = item.get("name"), item.get("relative_target"), item.get("fingerprint")
        if (not isinstance(name, str) or not name or name in {".", ".."}
                or "/" in name or "\\" in name or ":" in name):
            raise OrganizerError("Unsafe original filename in manifest.")
        if not isinstance(relative, str) or "\\" in relative:
            raise OrganizerError("Unsafe destination in manifest.")
        parts = relative.split("/")
        if (len(parts) != 2 or parts[1] != name or parts[0] in {"", ".", "..", JOURNAL_DIR}
                or ":" in parts[0]):
            raise OrganizerError("Unsafe destination in manifest.")
        if name in seen_source or relative in seen_target:
            raise OrganizerError("Duplicate paths in manifest.")
        seen_source.add(name)
        seen_target.add(relative)
        if (not isinstance(expected, dict) or set(expected) != {"size", "mtime_ns", "sha256"}
                or type(expected["size"]) is not int or expected["size"] < 0
                or type(expected["mtime_ns"]) is not int
                or not isinstance(expected["sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", expected["sha256"])
                or item.get("status") not in ("pending", "moved", "skipped", "undone")):
            raise OrganizerError("Invalid fingerprint or status in manifest.")
    return document, source, target


def undo(manifest_arg: str, apply: bool) -> int:
    manifest = absolute(manifest_arg)
    document, source, target = load_manifest(manifest)
    print("APPLY — restoring files." if apply else "DRY RUN — no files will be changed.")
    restored, refused = 0, 0
    for item in document["entries"]:
        if item["status"] in {"skipped", "undone"}:
            continue
        original, current = source / item["name"], target / item["relative_target"]
        try:
            check_no_symlinks(original)
            check_no_symlinks(current)
            if original.exists():
                # Pending entries may have never moved, including interrupted runs.
                if item["status"] == "pending" and not current.exists():
                    continue
                raise OrganizerError("original path already exists")
            if not current.is_file():
                raise OrganizerError("organized file is missing or not a regular file")
            if fingerprint(current) != item["fingerprint"]:
                raise OrganizerError("organized file changed; refusing to restore")
            print(f"{'RESTORE' if apply else 'WOULD RESTORE'} {item['relative_target']} -> {item['name']}")
            if apply:
                move_exclusive(current, original, item["fingerprint"])
                item["status"] = "undone"
            restored += 1
        except (OSError, OrganizerError) as error:
            refused += 1
            print(f"SKIP {item['name']}: {error}")
            continue
        if apply:
            save_manifest(manifest, document)
    print(f"{'Restored' if apply else 'Would restore'}: {restored} | Refused: {refused}")
    return 1 if refused else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    organize_parser = subparsers.add_parser("organize", help="Group top-level files")
    organize_parser.add_argument("source")
    organize_parser.add_argument("target")
    organize_parser.add_argument("--by", choices=("extension", "date"), default="extension",
                                 help="Group by extension (default) or UTC modification month")
    organize_parser.add_argument("--apply", action="store_true", help="Perform moves; default is dry run")
    undo_parser = subparsers.add_parser("undo", help="Restore unchanged files using their manifest")
    undo_parser.add_argument("manifest")
    undo_parser.add_argument("--apply", action="store_true", help="Perform restores; default is dry run")
    args = parser.parse_args(argv)
    try:
        if args.command == "organize":
            return organize(args.source, args.target, args.by, args.apply)
        return undo(args.manifest, args.apply)
    except (OSError, OrganizerError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
