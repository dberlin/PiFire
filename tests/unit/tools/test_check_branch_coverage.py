"""CLI contract tests for the exact per-file branch coverage gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPOSITORY_ROOT / "scripts" / "check_branch_coverage.py"


def _invoke_gate(
    coverage_path: Path,
    *requested_files: str,
    minimum: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "--coverage",
            str(coverage_path),
            "--minimum",
            str(minimum),
            *requested_files,
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_gate(
    tmp_path: Path,
    files: dict[str, object],
    *requested_files: str,
    minimum: float = 90.0,
) -> subprocess.CompletedProcess[str]:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return _invoke_gate(coverage_path, *requested_files, minimum=minimum)


def _summary(covered: object, total: object) -> dict[str, object]:
    return {"summary": {"num_branches": total, "covered_branches": covered}}


def _output_lines(completed: subprocess.CompletedProcess[str]) -> list[str]:
    assert completed.stderr == ""
    return completed.stdout.splitlines()


def test_missing_coverage_file_entry_is_reported_and_rejected(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/present.py": _summary(10, 10)},
        "controller/missing.py",
    )

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert len(lines) == 2
    assert "controller/missing.py" in lines[0]
    assert "missing" in lines[0].lower()
    assert lines[-1] == "Failed files: controller/missing.py"


def test_coverage_exactly_equal_to_minimum_is_rejected(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/exact.py": _summary(9, 10)},
        "controller/exact.py",
        minimum=90.0,
    )

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert len(lines) == 2
    assert "controller/exact.py" in lines[0]
    assert "90.0%" in lines[0]
    assert "9/10" in lines[0]
    assert "> 90.0%" in lines[0]
    assert lines[-1] == "Failed files: controller/exact.py"


def test_coverage_strictly_above_minimum_is_accepted(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/above.py": _summary(901, 1000)},
        "controller/above.py",
        minimum=90.0,
    )

    assert completed.returncode == 0
    lines = _output_lines(completed)
    assert len(lines) == 1
    assert "controller/above.py" in lines[0]
    assert "90.1%" in lines[0]
    assert "901/1000" in lines[0]


def test_report_preserves_precision_needed_to_compare_close_operands(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/precise.py": _summary(9004, 10000)},
        "controller/precise.py",
        minimum=90.03,
    )

    assert completed.returncode == 0
    lines = _output_lines(completed)
    assert len(lines) == 1
    assert "controller/precise.py" in lines[0]
    assert "90.04%" in lines[0]
    assert "> 90.03%" in lines[0]
    assert "9004/10000" in lines[0]


def test_file_with_zero_branches_is_treated_as_fully_covered(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/no_branches.py": _summary(0, 0)},
        "controller/no_branches.py",
        minimum=99.9,
    )

    assert completed.returncode == 0
    lines = _output_lines(completed)
    assert len(lines) == 1
    assert "controller/no_branches.py" in lines[0]
    assert "100.0%" in lines[0]
    assert "0/0" in lines[0]


@pytest.mark.parametrize(
    ("entry", "missing_field"),
    [
        pytest.param({}, "summary", id="missing-summary"),
        pytest.param({"summary": None}, "summary", id="non-object-summary"),
        pytest.param(
            {"summary": {"covered_branches": 9}},
            "num_branches",
            id="missing-num-branches",
        ),
        pytest.param(
            {"summary": {"num_branches": 10}},
            "covered_branches",
            id="missing-covered-branches",
        ),
    ],
)
def test_malformed_or_missing_summary_fields_are_rejected(
    tmp_path: Path,
    entry: dict[str, object],
    missing_field: str,
) -> None:
    completed = _run_gate(tmp_path, {"controller/bad.py": entry}, "controller/bad.py")

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert len(lines) == 2
    assert "controller/bad.py" in lines[0]
    assert missing_field in lines[0]
    assert lines[-1] == "Failed files: controller/bad.py"


def test_malformed_json_has_a_stable_non_traceback_failure(tmp_path: Path) -> None:
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text("{not-json", encoding="utf-8")

    completed = _invoke_gate(
        coverage_path,
        "controller/second.py",
        "./controller/first.py",
        minimum=90.0,
    )

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert lines == [
        "Invalid coverage JSON",
        "Failed files: controller/second.py, controller/first.py",
    ]
    assert "Traceback" not in completed.stdout


@pytest.mark.parametrize(
    ("covered", "total"),
    [
        pytest.param(-1, 10, id="negative-covered"),
        pytest.param(11, 10, id="covered-exceeds-total"),
        pytest.param(1, -1, id="negative-total"),
        pytest.param(1, 0, id="covered-nonzero-when-total-zero"),
        pytest.param(9.0, 10, id="covered-float"),
        pytest.param(9, 10.0, id="total-float"),
        pytest.param("9", 10, id="covered-string"),
        pytest.param(9, "10", id="total-string"),
        pytest.param(True, 10, id="covered-bool"),
        pytest.param(9, False, id="total-bool"),
    ],
)
def test_invalid_branch_counts_are_rejected(
    tmp_path: Path,
    covered: object,
    total: object,
) -> None:
    completed = _run_gate(
        tmp_path,
        {"controller/bad_counts.py": _summary(covered, total)},
        "controller/bad_counts.py",
    )

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert len(lines) == 2
    assert "controller/bad_counts.py" in lines[0]
    assert "invalid" in lines[0].lower()
    assert "covered_branches" in lines[0]
    assert "num_branches" in lines[0]
    assert lines[-1] == "Failed files: controller/bad_counts.py"


@pytest.mark.parametrize(
    ("coverage_name", "requested_name"),
    [
        pytest.param("controller/normalized.py", "./controller/normalized.py", id="requested-path"),
        pytest.param("./controller/normalized.py", "controller/normalized.py", id="coverage-path"),
    ],
)
def test_leading_dot_slash_is_normalized_for_lookup_and_reporting(
    tmp_path: Path,
    coverage_name: str,
    requested_name: str,
) -> None:
    completed = _run_gate(
        tmp_path,
        {coverage_name: _summary(10, 10)},
        requested_name,
    )

    assert completed.returncode == 0
    lines = _output_lines(completed)
    assert len(lines) == 1
    assert "controller/normalized.py" in lines[0]
    assert "./controller/normalized.py" not in lines[0]
    assert "100.0%" in lines[0]


def test_failure_rows_and_final_listing_follow_requested_file_order(tmp_path: Path) -> None:
    completed = _run_gate(
        tmp_path,
        {
            "controller/low.py": _summary(9, 10),
            "controller/invalid.py": _summary(1, 0),
        },
        "controller/low.py",
        "./controller/missing.py",
        "controller/invalid.py",
    )

    assert completed.returncode == 1
    lines = _output_lines(completed)
    assert len(lines) == 4
    assert "controller/low.py" in lines[0]
    assert "controller/missing.py" in lines[1]
    assert "./controller/missing.py" not in lines[1]
    assert "controller/invalid.py" in lines[2]
    assert lines[-1] == ("Failed files: controller/low.py, controller/missing.py, controller/invalid.py")
