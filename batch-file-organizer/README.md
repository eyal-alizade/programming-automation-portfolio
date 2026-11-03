# Batch File Organizer

**A safe-by-default file automation portfolio demo by Eyal Alizade / אייל אליזאדה.**

Preview a folder cleanup, organize its files by extension or modification month,
and restore unchanged files using a recorded manifest. Built with Python 3.11+
and its standard library. No installation, accounts, network access or third-party
packages are needed.

This is an original portfolio demonstration using synthetic files. It is **not
client work** and makes no claim about client results.

## Try the synthetic demo

Run these commands from this project directory. On Windows, use `py -3.11`
instead of `python` if that is your Python launcher.

```sh
python sample_data.py demo-input
python organizer.py organize demo-input demo-output
```

Expected preview:

```text
DRY RUN — no files will be changed.
WOULD MOVE meeting.txt -> txt/meeting.txt
WOULD MOVE orders.csv -> csv/orders.csv
WOULD MOVE README -> no-extension/README
WOULD MOVE report.TXT -> txt/report.TXT
WOULD MOVE settings.json -> json/settings.json
SKIP nested: not a regular top-level file
Planned: 5 | Skipped: 1
```

The preview does not create the target directory. To perform the moves:

```sh
python organizer.py organize demo-input demo-output --apply
```

The command prints a manifest path such as
`demo-output/.organizer-manifests/<run-id>.json` (the actual output uses an
absolute path). Copy the printed path for the following commands:

```sh
python organizer.py undo "demo-output/.organizer-manifests/<run-id>.json"
python organizer.py undo "demo-output/.organizer-manifests/<run-id>.json" --apply
```

Undo first previews the same five restores. Applying it restores each file to
its original location, preserving its content and modification time. Empty
output directories and the manifest are retained. A second undo does nothing.

After undo, try grouping by **UTC modification month**:

```sh
python organizer.py organize demo-input demo-output --by date
```

The five synthetic files are dated January 2026, so their destination group is
`2026-01/`. Date grouping uses modification time, not creation time or photo EXIF.
The sample generator requires a new directory and refuses to overwrite one.

## Behavior and safety

| Situation | Behavior |
| --- | --- |
| No `--apply` | Preview only, for organize and undo |
| Existing destination | Skip; never replace or rename the existing file |
| Source subdirectory | Skip; this tool processes top-level files only |
| Symlink file or path component | Skip the file or reject the root; never follow it |
| Identical or nested source/target | Reject before creating output |
| Changed or missing organized file | Refuse to undo that file |
| New file at the original location | Refuse to overwrite it during undo |
| Unsupported filename | Skip names containing backslash or colon |
| Different filesystem | Reject; moves require same-filesystem hard-link support |

Symlink checks apply to the supplied path before collapsing `..` components, so
a path such as `link/../incoming` cannot bypass root validation.

Extensions are case-insensitive: `.TXT` goes to `txt/`. Files without an
extension go to `no-extension/`; unusual extensions go to `other-extension/`.
Files are processed in a stable name order. Hidden regular files are included.

Moves create an exclusive hard link before removing the source name, so a
destination that appears after preview is also protected from overwrite. The
source content and modification time are hashed and checked again before moving.
Undo checks SHA-256, size and modification time; editing a file or even touching
its modification time prevents automatic restoration.

A JSON journal is written **before** the first move and updated atomically after
each result. A pending entry can be restored if a move completed before its
journal update. Each run gets a separate manifest. Keep it in its original
`.organizer-manifests` directory and keep both root directories in place.

### Practical limits

- Run one organizer instance at a time and pause other applications that modify
  these files. This utility is not a locking system against concurrent or hostile
  filesystem changes.
- Hard-link support is required; some network drives, removable filesystems and
  filesystem configurations do not support it. Failed moves retain the source.
- A whole batch is not one transaction. On interruption, already moved files stay
  moved; use the printed manifest to preview recovery. An interruption between
  link creation and source removal can leave both names. Undo refuses that
  collision; inspect both files manually before removing a duplicate.
- A manifest is an undo record, **not a backup**. It cannot recover deleted or
  corrupted contents. It contains local absolute paths and should not be shared
  or committed. Generated sample folders and journals are ignored by Git.
- Linux is exercised by the automated test suite. Windows and macOS behavior
  should be verified on the intended filesystem before use with important files.

Exit status: `0` for a completed preview/run (including ordinary planned skips),
`1` for failed moves or refused restores, `2` for validation or I/O errors.
`python organizer.py --help` lists commands.

## Verification

```sh
python -m unittest discover -s tests -v
```

Tests operate exclusively in temporary directories and cover previews, round-trip
undo, date grouping, collision protection, symlinks, nested roots, changed/missing
files, malformed manifests and interrupted journal recovery. They never touch
your downloads or personal documents.

## התחלה מהירה בעברית

זהו פרויקט תיק עבודות עם קבצים סינתטיים, ללא מידע של לקוחות.
נדרש Python 3.11 ומעלה.

```sh
python sample_data.py demo-input
python organizer.py organize demo-input demo-output
```

הפקודה מציגה תצוגה מקדימה בלבד. כדי להעביר את הקבצים הוסף `--apply`.
להחזרה השתמש בפקודת `undo` עם הנתיב לקובץ המניפסט שהודפס; גם היא מבצעת
תצוגה מקדימה עד שמוסיפים `--apply`. קובץ שהשתנה לא יוחזר אוטומטית.
אין דריסה של קבצים קיימים, ואין מעבר לתוך תיקיות משנה.

**Business inquiries:** [mr.pitronot@gmail.com](mailto:mr.pitronot@gmail.com)
