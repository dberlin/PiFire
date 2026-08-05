"""Behavioral contracts for deterministic closed-loop bake-off scenarios."""

from __future__ import annotations

from pathlib import Path

from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    ExperimentConfig,
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

    assert clean.to_json() == interrupted_then_resumed.to_json()


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
    assert all("wrong_model_recovery_mae_c" in row.metrics for row in artifact.scenarios)