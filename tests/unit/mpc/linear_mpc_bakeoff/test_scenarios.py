"""Behavioral contracts for deterministic closed-loop bake-off scenarios."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    ExperimentConfig,
    _artifact_from_rows,
    run_experiment,
    run_tiny_matrix,
    run_tiny_scenario,
)


def test_fixed_fan_primary_scenario() -> None:
    result = run_tiny_scenario(plant="GrillSim", seed=2)

    assert set(result.fan_fraction) == {1.0}
    assert result.provenance == "simulated-fixed-fan"
    assert result.requested_q is not result.realized_q
    assert len(result.requested_q) == len(result.realized_q) == len(result.temperature_c)


def test_tiny_matrix_runs_both_plants_and_modes() -> None:
    artifact = run_experiment(ExperimentConfig.quick())

    assert {row.plant for row in artifact.scenarios} == {"GrillSim", "MAKGrillSim"}
    assert {row.mode for row in artifact.scenarios} == {"frozen", "online"}
    assert {row.scenario for row in artifact.scenarios} >= {"low-step", "lid-excursion"}
    assert all(row.solver_period_s == 20 for row in artifact.scenarios)


def test_resume_matches_clean_artifact(tmp_path: Path) -> None:
    clean = run_tiny_matrix(tmp_path / "clean", resume=False)
    interrupted_then_resumed = run_tiny_matrix(
        tmp_path / "resume", resume=True, interrupt_after=3
    )

    assert clean.canonical_document() == interrupted_then_resumed.canonical_document()


def test_checkpoint_is_atomic_and_does_not_leave_temporary_file(tmp_path: Path) -> None:
    output = tmp_path / "artifact.json"

    run_tiny_matrix(tmp_path, resume=False, output=output)

    assert output.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_checkpoint_matrix_covers_every_arm_and_wrong_initialization(tmp_path: Path) -> None:
    artifact = run_tiny_matrix(tmp_path, resume=False)

    assert {row.arm for row in artifact.scenarios} == {"scheduled-arx", "dmc", "state-space"}
    assert {row.initialization for row in artifact.scenarios} == {
        "wrong-gain",
        "wrong-pole",
        "wrong-delay",
    }
    assert all("requested_realized_duty_mae" in row.metrics for row in artifact.scenarios)
    assert all("recovery_improvement_ratio" in row.metrics for row in artifact.scenarios)


def test_recovery_evidence_keeps_chronological_before_and_after_residuals() -> None:
    result = run_tiny_scenario(plant="GrillSim", seed=2)
    metrics = result.metrics

    assert result.evidence_id == "scheduled-arx:2:wrong-gain"
    assert result.to_document()["evidence_id"] == result.evidence_id
    assert metrics["recovery_before_mae_c"] > 0.0
    assert metrics["recovery_after_mae_c"] > 0.0
    assert result.pre_recovery_residuals_c["600"]
    assert result.horizon_residuals_c["600"]
    assert result.to_document()["pre_recovery_residuals_c"]["600"] == list(result.pre_recovery_residuals_c["600"])
    assert metrics["recovery_improvement_delta_c"] == pytest.approx(
        metrics["recovery_before_mae_c"] - metrics["recovery_after_mae_c"]
    )
    assert metrics["recovery_improvement_ratio"] == pytest.approx(
        metrics["recovery_after_mae_c"] / metrics["recovery_before_mae_c"]
    )


def test_horizon_evidence_bootstraps_unique_model_origins_once_despite_duplicate_rows() -> None:
    config = ExperimentConfig.quick()
    artifact = run_experiment(config)
    duplicate_rows = [
        *artifact.scenarios,
        *(replace(row, scenario=f"duplicate-{row.scenario}") for row in artifact.scenarios),
    ]

    deduplicated = _artifact_from_rows(config, list(artifact.scenarios))
    duplicated = _artifact_from_rows(config, duplicate_rows)

    assert len({row.evidence_id for row in artifact.scenarios}) == 9
    for arm in ("scheduled-arx", "dmc", "state-space"):
        evidence = deduplicated.horizon_evidence[arm]["600"]
        duplicate_evidence = duplicated.horizon_evidence[arm]["600"]
        assert len(evidence["residuals_c"]) == 15
        assert duplicate_evidence["residuals_c"] == evidence["residuals_c"]
        assert duplicate_evidence["bootstrap_ci"] == evidence["bootstrap_ci"]