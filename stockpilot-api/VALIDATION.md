# Recorded validation

Local run on 2026-11-03, Linux, Python 3.14.9. This records an actual execution, not a claim of deployment or a compatibility certification.

| Check | Result |
|---|---|
| `python -m unittest discover -s tests -v` | 32 tests passed |
| Offline `demo.py` | All six acceptance checks passed |
| `python -m pip check` | No broken requirements |
| Concurrent orders for one available unit | One reservation succeeds; the other receives 409 |
| Concurrent same-key creation | One order and one stock deduction |
| Concurrent cancellation | Stock released once |
| Partial-order and failed-cancellation rollback | No partial stock or ledger mutation |
| CSV formula mitigation | Dangerous text cell prefixed with apostrophe |
| Invalid/oversized resource IDs | Positive signed-64-bit bounds; 422 and zero writes |

The demo's reviewed result is saved in [examples/demo-report.json](examples/demo-report.json). Regenerate a fresh run with `python demo.py --output-dir demo-output` after installing development dependencies.

The dependency environment used FastAPI 0.162.0, Pydantic 2.14.2, Uvicorn 0.54.0, Starlette 1.10.0 and HTTPX 0.28.1. [requirements-lock.txt](requirements-lock.txt) records the complete resolved environment. Standard installation uses the bounded direct requirements; use the lock if reproducing this exact run.

Starlette 1.10 currently emits a deprecation warning for its HTTPX compatibility path, recommending `httpx2`. This does not fail the tested requests. The project uses HTTPX as documented in the current FastAPI testing guide; future dependency-family upgrades should rerun this suite.

The host's restricted network sandbox blocks the local event-loop wakeup sockets used by TestClient. The suite and demo were therefore run with the reviewed local-test permission that enables those sockets. Requests remained in-process and used temporary synthetic SQLite databases; no outside service was contacted. This environment detail does not change the test assertions or application behavior.

Not verified here: native Windows execution, Python 3.11 execution, public hosting, browser rendering of CDN-backed Swagger/ReDoc, high-load operation or disaster recovery. The project has not been published or deployed.
