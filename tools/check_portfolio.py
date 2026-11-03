"""Run each project's real test suite without calling external services."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = (
    "deskbuddy-telegram-bot",
    "stockpilot-api",
    "batch-file-organizer",
    "csv-report-builder",
    "backup-integrity-checker",
)


def run_check(command: list[str], folder: Path, count_pattern: str) -> tuple[bool, int]:
    """Run one suite and count the tests its runner actually reports."""
    try:
        run = subprocess.run(
            command, cwd=folder, text=True, capture_output=True, check=False, timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: Test suite exceeded the 120-second limit.", flush=True)
        return False, 0
    except OSError as error:
        print(f"ERROR: Could not start test runner: {error}", flush=True)
        return False, 0
    print(run.stdout, end="", flush=True)
    print(run.stderr, end="", flush=True)
    match = re.search(count_pattern, run.stderr + run.stdout)
    count = int(match.group(1)) if match else 0
    return run.returncode == 0 and count > 0, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=PROJECTS, help="Run one project only.")
    parser.add_argument("--report", type=Path, help="Write a small JSON verification report.")
    args = parser.parse_args()
    selected = (args.project,) if args.project else PROJECTS
    results = []
    for project in selected:
        folder = ROOT / project
        tests = folder / "tests"
        print(f"\n[{project}]", flush=True)
        if not tests.is_dir() or not any(tests.glob("test*.py")):
            print("ERROR: No test suite found.", flush=True)
            results.append({"project": project, "passed": False, "tests_run": 0})
            continue
        python_ok, python_count = run_check(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            folder, r"Ran (\d+) tests? in",
        )
        javascript_ok, javascript_count = True, 0
        for script in sorted(tests.glob("test_*.mjs")):
            print(f"\n[JavaScript: {script.name}]", flush=True)
            node = shutil.which("node")
            if node is None:
                print("ERROR: Node.js is required for the frontend workflow tests.", flush=True)
                javascript_ok = False
                break
            passed, count = run_check(
                [node, str(script.relative_to(folder))], folder, r"(?:#|ℹ)\s+tests\s+(\d+)",
            )
            javascript_ok = javascript_ok and passed
            javascript_count += count
        results.append({
            "project": project,
            "passed": python_ok and javascript_ok,
            "python_tests_run": python_count,
            "javascript_tests_run": javascript_count,
            "tests_run": python_count + javascript_count,
        })
    all_passed = all(result["passed"] for result in results)
    report = {
        "verified_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "projects": results,
        "all_passed": all_passed,
        "total_tests_run": sum(result["tests_run"] for result in results),
        "total_python_tests_run": sum(result.get("python_tests_run", 0) for result in results),
        "total_javascript_tests_run": sum(result.get("javascript_tests_run", 0) for result in results),
        "external_services_used_by_checks": False,
        "github_actions_executed": False,
        "native_windows_verification": False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{'PASS' if all_passed else 'FAIL'}: {report['total_tests_run']} tests across {len(results)} projects.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
