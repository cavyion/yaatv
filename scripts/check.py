#!/usr/bin/env python3
"""Cross-platform check suite for yaatv contributors and CI.

Usage:
    python scripts/check.py           # Run full suite of checks
    python scripts/check.py --fast    # Fast checks only (ruff, mypy, unit tests)
    python scripts/check.py --skip-audit # Skip network-dependent pip-audit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_step(name: str, cmd: list[str]) -> bool:
    print(f"\n[ RUN ] {name}...")
    start = time.perf_counter()
    res = subprocess.run(cmd, cwd=REPO_ROOT)
    elapsed = time.perf_counter() - start
    if res.returncode == 0:
        print(f"[PASS] {name} ({elapsed:.2f}s)")
        return True
    else:
        print(f"[FAIL] {name} (exit code {res.returncode}, {elapsed:.2f}s)", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run yaatv developer checks.")
    parser.add_argument("--fast", action="store_true", help="Run only lint, typecheck, and unit tests.")
    parser.add_argument("--skip-audit", action="store_true", help="Skip pip-audit dependency scan.")
    parser.add_argument("--integration", action="store_true", help="Include tests requiring FFmpeg/FFprobe binaries.")
    args = parser.parse_args()

    py = sys.executable

    steps: list[tuple[str, list[str]]] = [
        ("Smoke test CLI", [py, "-m", "yaatv", "--version"]),
        ("Ruff linter", [py, "-m", "ruff", "check", "."]),
        ("Mypy type checker", [py, "-m", "mypy"]),
    ]

    if not args.fast:
        steps.append(("Bandit security scan", [py, "-m", "bandit", "-c", "pyproject.toml", "-r", "src"]))
        steps.append((
            "Dependency licenses",
            [py, "-m", "piplicenses", "--packages", "mutagen", "pillow", "--with-urls"],
        ))
        if not args.skip_audit:
            steps.append(("Pip audit", [py, "-m", "pip_audit", ".", "--strict"]))

    pytest_cmd = [py, "-m", "pytest"]
    if not args.integration:
        pytest_cmd.extend(["tests/test_cli.py", "-k", "not integration"])
    steps.append(("Pytest test suite", pytest_cmd))

    if not args.fast:
        steps.extend([
            ("Package build", [py, "-m", "build"]),
            ("Twine metadata check", [py, "-m", "twine", "check", "dist/*"]),
        ])

    failures: list[str] = []
    total_start = time.perf_counter()

    for name, cmd in steps:
        if not run_step(name, cmd):
            failures.append(name)

    total_elapsed = time.perf_counter() - total_start
    print("\n" + "=" * 50)

    if failures:
        print(f"FAILED: {len(failures)} check(s) failed in {total_elapsed:.2f}s:")
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print(f"SUCCESS: All {len(steps)} checks passed in {total_elapsed:.2f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

