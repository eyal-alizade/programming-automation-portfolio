# Backup Integrity Checker

A local Python CLI that creates ZIP backups with a SHA-256 manifest and checks every archived file without extracting it. Optional source comparison answers a separate question: does the current source still match the captured files?

**Personal portfolio demonstration by Eyal Alizade (אייל אליזאדה).** All generated examples are synthetic. This is not client work, a certified backup service, encryption, or a recovery guarantee.

## Quick demo

Requires Python 3.11 or newer. No external dependencies, accounts, network access, or installation step.

```sh
python backup_checker.py demo --output-dir demo-output
python backup_checker.py verify demo-output/sample-backup.zip
python backup_checker.py verify demo-output/sample-backup.zip --source demo-output/source
```

Example final result:

```json
{"bytes": 235, "files": 3, "skipped_symlinks": [], "source_matches": true, "status": "ok", "synthetic_data": true}
```

The byte count depends on the sample generator version. A demo output directory must not already exist; use another directory for a new run.

## Use with your own files

```sh
python backup_checker.py backup ./documents ./documents-backup.zip
python backup_checker.py verify ./documents-backup.zip --source ./documents
python backup_checker.py verify ./documents-backup.zip --max-bytes 21474836480 --max-files 200000
```

The ZIP must be **outside** the source directory, including when output paths use symlinks. This prevents recursive backups. Its parent directory must already exist. Existing output files are never overwritten. Stop programs that write the source before capture.

Commands emit one JSON result. Exit codes: `0` success, `1` verification mismatch/invalid archive, `2` usage or operational error. Errors go to stderr. Missing or unreadable input is an operational error.

Generate a larger synthetic example:

```sh
python backup_checker.py sample sample-output --large-mib 12
python backup_checker.py backup sample-output sample-backup.zip
python backup_checker.py verify sample-backup.zip --source sample-output
```

## What is checked

- Each regular file is streamed in 1 MiB chunks and recorded with its relative path, byte length, and SHA-256 digest in `manifest.json`.
- Verification checks ZIP CRCs, manifest structure, exact member membership, sizes and SHA-256. Missing, unexpected and duplicate members fail verification.
- Absolute paths, traversal segments, Windows drive/alternate-stream syntax, backslashes, symlink archive entries, special files and encrypted members are rejected. Nothing is extracted or executed.
- Source symlinks are skipped and reported. Special source files fail capture. File identity, size and change timestamps are checked around reads; the source tree is scanned again before completion.
- Default verification limits are 100,000 files, 10 GiB of file data and a 16 MiB manifest. Streaming limits also apply while reading data. These are resource guards, not a promise of safe handling of every hostile ZIP.

Archive structure: `manifest.json` plus `files/<source-relative-path>`. Empty directories, permissions, executable bits, original timestamps and ownership are not preserved. ZIP entry timestamps are deliberately fixed; file content is the intended artifact.

## Limits and security model

Hashes detect accidental damage and changes relative to the included manifest. The manifest is **unsigned**: someone who changes both payload and manifest coherently can make an archive pass. Keep a trusted separate copy or independent authenticated system for stronger guarantees. Backups are not encrypted and may contain sensitive content.

There is no restore command. A successful check does not establish recoverability of an entire computer, application consistency, off-site retention or disaster recovery. This is not an atomic filesystem snapshot. Observed file changes abort capture and remove the partial output, but a forced process kill can leave an incomplete ZIP. Do not use an output until the command succeeds.

Directory-FD/no-follow file opens are used where supported. Other platforms perform best-effort symlink checks; a source concurrently modified by a hostile process is outside the supported model. Creation refuses more than 100,000 files. Verification can opt into higher counts for other compatible archives. Standard-library ZIP central-directory parsing still happens before application-level limits.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests use temporary directories and synthetic files. They cover round trips, source drift, overwritten-output prevention, source symlinks, changing files, 12 MiB streaming, corrupted ZIPs, unsafe paths, malformed/tampered manifests, missing/unexpected members, and the unsigned-manifest limitation.

## Author

Eyal Alizade / אייל אליזאדה · [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)

No license grant has been selected for this project.

עברית: [מדריך קצר](QUICKSTART_HE.md)
