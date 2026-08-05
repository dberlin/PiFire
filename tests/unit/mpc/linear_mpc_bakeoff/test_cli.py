"""CLI contracts for the linear-MPC bake-off runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_tiny_matrix


def test_quick_mode_writes_requested_output_and_table(tmp_path: Path) -> None:
    output = tmp_path / "quick.json"
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
    assert json.loads(output.read_text(encoding="utf-8"))["schema_version"] == 1


def test_resume_requires_existing_checkpoint(tmp_path: Path) -> None:
    output = tmp_path / "missing.json"
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


def test_quick_resume_consumes_partial_checkpoint_to_clean_equivalent_artifact(tmp_path: Path) -> None:
    output = tmp_path / "resume.json"
    clean = run_tiny_matrix(tmp_path / "clean", resume=False)
    run_tiny_matrix(tmp_path / "partial", resume=False, interrupt_after=3, output=output)

    completed = subprocess.run(
        [sys.executable, "-m", "docs.superpowers.experiments.linear_mpc_bakeoff", "--quick", "--resume", "--output", str(output)],
        check=False, capture_output=True, text=True, env={**os.environ, "UV_NO_SYNC": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8")) == clean.to_document()
