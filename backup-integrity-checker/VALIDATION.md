# Recorded validation

Actual local run on 2026-11-03, Linux, Python 3.14.9:

- `python -m unittest discover -s tests -v`: **22 tests passed**.
- `python backup_checker.py demo --output-dir <new-temporary-directory>`: succeeded with three synthetic files, 235 total bytes and `source_matches: true`.
- A separate `verify <archive> --source <source>` invocation succeeded.

Tests include a 12 MiB streamed file, observed mid-capture modification, symlink skipping, unsafe archive paths, duplicate members, invalid/tampered manifests, missing/unexpected files and source drift. A test explicitly demonstrates that coherently rewriting both content and its unsigned manifest can pass archive-only verification.

No external libraries, network calls, source-data uploads, archive extraction or executable launch are used. Native Windows execution and Python 3.11 execution were not tested in this environment. This is not a restore test or a recovery guarantee.
