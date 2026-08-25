#!/usr/bin/env python3
"""Enforce strict per-file branch coverage from a coverage.py JSON report."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BranchCoverage:
    """Validated branch coverage counts for one source file."""

    path: str
    covered: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else 100.0 * self.covered / self.total


def _normalize_path(path: str) -> str:
    return path.removeprefix("./")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Require each named file to exceed a branch coverage minimum.")
    parser.add_argument("--coverage", required=True, type=Path, help="coverage.py JSON report")
    parser.add_argument("--minimum", required=True, type=float, help="exclusive percentage minimum")
    parser.add_argument("files", nargs="+", help="source files to check")
    return parser.parse_args()


def _read_branch_coverage(path: str, entry: object) -> BranchCoverage | str:
    if not isinstance(entry, dict):
        return "invalid file entry: expected an object"

    if "summary" not in entry:
        return "missing summary"
    summary = entry["summary"]
    if not isinstance(summary, dict):
        return "invalid summary: expected an object"

    for field in ("num_branches", "covered_branches"):
        if field not in summary:
            return f"missing {field}"

    covered = summary["covered_branches"]
    total = summary["num_branches"]
    counts_are_valid = type(covered) is int and type(total) is int and 0 <= covered <= total
    if not counts_are_valid:
        return "invalid covered_branches and num_branches counts"

    return BranchCoverage(path=path, covered=covered, total=total)


def main() -> int:
    args = _parse_args()
    requested_paths = [_normalize_path(path) for path in args.files]
    try:
        with args.coverage.open(encoding="utf-8") as coverage_file:
            report: Any = json.load(coverage_file)
    except json.JSONDecodeError:
        print("Invalid coverage JSON")
        print(f"Failed files: {', '.join(requested_paths)}")
        return 1

    files = report.get("files", {}) if isinstance(report, dict) else {}
    coverage_files = {_normalize_path(path): entry for path, entry in files.items()} if isinstance(files, dict) else {}

    failed: list[str] = []
    for path in requested_paths:
        if path not in coverage_files:
            print(f"FAIL {path}: missing from coverage report")
            failed.append(path)
            continue

        result = _read_branch_coverage(path, coverage_files[path])
        if isinstance(result, str):
            print(f"FAIL {path}: {result}")
            failed.append(path)
            continue

        comparison = f"{result.percent}% ({result.covered}/{result.total}) > {args.minimum}%"
        if result.percent > args.minimum:
            print(f"PASS {path}: {comparison}")
        else:
            print(f"FAIL {path}: requires {comparison}")
            failed.append(path)

    if failed:
        print(f"Failed files: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
