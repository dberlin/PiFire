"""CLI contracts for the linear-MPC bake-off runner."""

from __future__ import annotations

import json
import re
import os
import subprocess
import sys
from pathlib import Path
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _read_artifact_document
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _select_validation_horizon
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _source_revision
from docs.superpowers.experiments.linear_mpc_bakeoff.artifact import ExperimentArtifact
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import load_artifact
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_tiny_matrix


def test_quick_mode_writes_requested_output_and_table(tmp_path: Path) -> None:
    output = tmp_path / "quick.manifest.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "docs.superpowers.experiments.linear_mpc_bakeoff",
            "--quick",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_NO_SYNC": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert "arm" in completed.stdout
    assert load_artifact(output).to_document()["schema_version"] == 1
    # Full has 1,080 rows versus quick's 144; eight quick bundles bound its transport.
    assert output.stat().st_size * 8 < 100 * 1024 * 1024

def test_source_revision_is_a_plain_commit_id() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", _source_revision())

def test_validation_horizon_selection_uses_only_validation_scores() -> None:
    selection = _select_validation_horizon({600: (1.0,), 800: (0.995,), 1000: (0.991,)})

    assert selection["selected_horizon_s"] == 600
    assert selection["validation_scores"] == {"600": 1.0, "800": 0.995, "1000": 0.991}
    assert selection["tie_rationale"] == "600 seconds is within 1% of the validation best"


def test_resume_requires_existing_checkpoint(tmp_path: Path) -> None:
    output = tmp_path / "missing.manifest.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "docs.superpowers.experiments.linear_mpc_bakeoff",
            "--quick",
            "--resume",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_NO_SYNC": "1"},
    )

    assert completed.returncode != 0
    assert "checkpoint" in completed.stderr.lower()


def test_cli_rejects_non_manifest_output_before_running(tmp_path: Path) -> None:
    output = tmp_path / "unsafe.json.gz"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "docs.superpowers.experiments.linear_mpc_bakeoff",
            "--quick",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "UV_NO_SYNC": "1"},
    )
    assert completed.returncode == 2
    assert ".manifest.json" in completed.stderr
    assert not output.exists()


def test_quick_resume_consumes_partial_checkpoint_to_clean_equivalent_artifact(tmp_path: Path) -> None:
    output = tmp_path / "resume.manifest.json"
    run_tiny_matrix(tmp_path / "partial", resume=False, interrupt_after=3, output=output)
    checkpoint = output.with_name("resume.checkpoint.manifest.json")
    partial = _read_artifact_document(checkpoint)
    assert not output.exists()

    completed = subprocess.run(
        [sys.executable, "-m", "docs.superpowers.experiments.linear_mpc_bakeoff", "--quick", "--resume", "--output", str(output)],
        check=False, capture_output=True, text=True, env={**os.environ, "UV_NO_SYNC": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    restored = load_artifact(output)
    assert len(restored.scenarios) + len(restored.failures) == 144
    key = lambda row: (row["arm"], row["initialization"], row["plant"], row["scenario"], row["mode"], row["seed"])
    restored_rows = {key(row.to_document()): row.to_document() for row in restored.scenarios}
    assert all(restored_rows[key(row)] == row for row in partial["rows"])
    assert not checkpoint.exists()
