#!/usr/bin/env python3
"""Turn a coverage.json baseline into a risk-ranked gap report.

Usage:
    uv run python scripts/coverage_gaps.py [coverage.json] > docs/coverage/gap-report-<date>.md

Loads a coverage.py JSON report (produced with `--cov-report=json`) and, for
each source file, computes a risk-weighted "gap score" combining how
safety/hardware-critical the code is (by path prefix) with how much of it is
untested (missing lines + missing branches). Emits a ranked markdown table
(top 40) so reviewers can prioritize where new tests will do the most good.
"""

import json
import sys

# Risk weights: safety/control/hardware-command code first, then web, then
# rendering, then pure helpers. Checked in order; first prefix match wins.
RISK = [
    ("controller/", 5),
    ("common/api_commands", 5),
    ("grillplat/", 5),
    ("notify/", 4),
    ("common/", 3),
    ("blueprints/", 3),
    ("probes/", 3),
    ("file_mgmt/", 3),
    ("display/", 2),
]


def weight(path):
    for prefix, w in RISK:
        if prefix in path:
            return w
    return 1


def main():
    coverage_path = sys.argv[1] if len(sys.argv) > 1 else "coverage.json"
    data = json.load(open(coverage_path))

    rows = []
    for path, f in data["files"].items():
        s = f["summary"]
        missing = s.get("missing_lines", 0) + s.get("missing_branches", 0)
        rows.append(
            (
                weight(path) * missing,
                weight(path),
                path,
                s["percent_covered"],
                s.get("missing_lines", 0),
                s.get("missing_branches", 0),
            )
        )
    rows.sort(reverse=True)

    print("| Rank | File | Risk | Line% | Missing lines | Missing branches | Score |")
    print("|---|---|---|---|---|---|---|")
    for i, (score, w, path, pct, ml, mb) in enumerate(rows[:40], 1):
        print(f"| {i} | `{path}` | {w} | {pct:.1f} | {ml} | {mb} | {score} |")


if __name__ == "__main__":
    main()
