"""Behavioral contracts for bake-off evidence and recommendation."""

from __future__ import annotations

import json
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.artifact import (
    ArmEvidence,
    ArmFailure,
    ExperimentArtifact,
    MatrixKey,
    recommend,
)


def artifact_with_scores(
    *, arx: float = 10.0, state_space: float = 12.0, dmc: float = 13.0,
    state_prediction: float = 3.0,
) -> ExperimentArtifact:
    return ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={"synthetic": {"fit": [0, 1], "validation": [2], "test": [3]}},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence("scheduled-arx", {"GrillSim": arx}, 3.0, 3.0, 3.0, 1.0, 0.0, 2.0, 10.0),
            ArmEvidence("dmc", {"GrillSim": dmc}, 4.0, 4.0, 4.0, 1.0, 0.0, 2.0, 10.0),
            ArmEvidence(
                "state-space", {"GrillSim": state_space}, state_prediction, state_prediction,
                state_prediction, 1.0, 0.0, 2.0, 10.0,
            ),
        ),
        source_revision="abc123",
        environment={"python": "test", "numpy": "test"},
    )


def artifact_with_timings(*, projected_solve_p99_ms: float) -> ExperimentArtifact:
    return ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx", {"GrillSim": 10.0}, 1.0, 1.0, 1.0, 1.0, 0.0,
                raw_solve_p99_ms=projected_solve_p99_ms / 5.0,
                projected_solve_p99_ms=projected_solve_p99_ms,
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )


def test_runtime_only_disqualifies_beyond_five_times_budget() -> None:
    artifact = artifact_with_timings(projected_solve_p99_ms=200.0)
    assert recommend(artifact).arms["scheduled-arx"].valid

    artifact = artifact_with_timings(projected_solve_p99_ms=251.0)
    assert not recommend(artifact).arms["scheduled-arx"].valid
    assert recommend(artifact).arms["scheduled-arx"].reasons == ("runtime beyond hard limits",)


def test_unisolated_runtime_evidence_is_deferred_not_a_disqualifier() -> None:
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx",
                {"GrillSim": 10.0},
                prediction_error=1.0,
                before_mae=1.0,
                after_mae=1.0,
                recovery_improvement_ratio=1.0,
                recovery_improvement_delta=0.0,
                raw_solve_p99_ms=100.0,
                runtime_validity="not_measured",
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    evidence = artifact.to_document()["arms"]["scheduled-arx"]

    assert evidence["runtime_validity"] == "not_measured"
    assert recommend(artifact).arms["scheduled-arx"].valid


def test_unmeasured_timing_cannot_change_frontier_or_selection() -> None:
    def artifact(arx_timing: float, dmc_timing: float) -> ExperimentArtifact:
        return ExperimentArtifact(
            config={"control_budget_ms": 50.0},
            seeds=(2,),
            splits={},
            model_snapshots={},
            scenarios=(),
            arms=(
                ArmEvidence("scheduled-arx", {"GrillSim": 10.0}, 1.0, 1.0, 1.0, 1.0, 0.0, arx_timing, runtime_validity="not_measured"),
                ArmEvidence("dmc", {"GrillSim": 10.0}, 1.0, 1.0, 1.0, 1.0, 0.0, dmc_timing, runtime_validity="not_measured"),
            ),
            source_revision="abc123",
            environment={"python": "test"},
        )

    baseline = recommend(artifact(1.0, 1_000_000.0))
    reversed_timing = recommend(artifact(1_000_000.0, 1.0))

    assert baseline.pareto_frontier == reversed_timing.pareto_frontier
    assert baseline.selected_arm == reversed_timing.selected_arm


def test_mixed_diagnostic_availability_is_not_a_zero_error_advantage() -> None:
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence("scheduled-arx", {"GrillSim": 10.0}, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0),
            ArmEvidence(
                "dmc", {"GrillSim": 10.0}, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0,
                simulator_diagnostics_available=True,
                simulator_gain_error_c_per_q=1.0,
                simulator_delay_error_s=1.0,
                simulator_coast_braking_error_c=1.0,
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    recommendation = recommend(artifact)

    assert not recommendation.arms["scheduled-arx"].valid
    assert recommendation.arms["scheduled-arx"].reasons == (
        "simulator diagnostics unavailable",
    )
    assert recommendation.selected_arm == "dmc"

def test_simplest_arm_wins_within_five_percent() -> None:
    artifact = artifact_with_scores(arx=10.4, state_space=10.0, dmc=12.0)

    assert recommend(artifact).selected_arm == "scheduled-arx"


def test_artifact_is_schema_valid_sorted_and_has_provenance() -> None:
    artifact = artifact_with_scores()
    document = artifact.to_document()

    assert document["schema_version"] == 1
    assert document["source_revision"] == "abc123"
    assert list(document["arms"]) == ["dmc", "scheduled-arx", "state-space"]
    assert json.loads(artifact.to_json()) == document


    artifact = artifact.with_failures((
        ArmFailure(
            "dmc",
            "lid-excursion",
            "wrong-input-semantics",
            "q was realized",
            MatrixKey("dmc", "wrong-delay", "GrillSim", "online", "lid-excursion", 2),
        ),
    ))

    recommendation = recommend(artifact)

    assert not recommendation.arms["dmc"].valid
    assert recommendation.arms["dmc"].reasons == ("wrong input semantics",)
    assert artifact.to_document()["failures"][0]["matrix_key"]["initialization"] == "wrong-delay"
    assert document_has_failure(artifact, "wrong-input-semantics")


def test_pareto_frontier_does_not_force_a_winner() -> None:
    artifact = artifact_with_scores(arx=10.0, state_space=9.0, dmc=12.0, state_prediction=4.0)

    recommendation = recommend(artifact)

    assert recommendation.selected_arm is None
    assert recommendation.pareto_frontier == ("scheduled-arx", "state-space")


def test_bootstrap_and_unavailable_horizons_are_preserved() -> None:
    artifact = artifact_with_scores()
    artifact = artifact.with_horizon_evidence(
        {"scheduled-arx": {"600": [1.0, 2.0], "real": None}}
    )

    evidence = artifact.to_document()["horizon_evidence"]["scheduled-arx"]
    assert evidence["600"]["bootstrap_ci"] == [1.0, 2.0]
    assert evidence["real"] is None


def test_arm_evidence_preserves_raw_distributions_and_horizon_residuals() -> None:
    artifact = artifact_with_scores().with_horizon_evidence(
        {
            "scheduled-arx": {
                "600": {"residuals_c": [1.0, 2.0], "bootstrap_ci": [1.0, 2.0]},
                "800": {"residuals_c": [2.0], "bootstrap_ci": [2.0, 2.0]},
                "1000": {"residuals_c": [3.0], "bootstrap_ci": [3.0, 3.0]},
                "real": None,
            }
        }
    )
    evidence = ArmEvidence(
        "scheduled-arx",
        {"GrillSim": 10.0},
        1.0,
        1.0,
        1.0,
        1.0,
        0.0,
        2.0,
        raw_learner_ms=(1.0, 2.0),
        raw_refresh_ms=(3.0, 4.0),
        raw_solve_ms=(5.0, 6.0),
    )

    document = artifact.to_document()
    assert document["horizon_evidence"]["scheduled-arx"]["600"]["residuals_c"] == [1.0, 2.0]
    assert evidence.to_document()["raw_timing_ms"]["learner"] == [1.0, 2.0]
    assert evidence.to_document()["raw_timing_ms"]["solve_p99"] == 5.99



def test_runtime_gates_apply_to_five_times_learner_and_refresh_p99() -> None:
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx",
                {"GrillSim": 10.0},
                1.0,
                1.0,
                1.0,
                1.0,
                0.0,
                raw_solve_p99_ms=1.0,
                raw_learner_ms=(6.0,),
                raw_refresh_ms=(1.0,),
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    assert not recommend(artifact).arms["scheduled-arx"].valid


def test_simulator_diagnostics_change_validity_and_selection_at_equal_control() -> None:
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx",
                {"GrillSim": 10.0},
                prediction_error=1.0,
                before_mae=1.0,
                after_mae=1.0,
                recovery_improvement_ratio=1.0,
                recovery_improvement_delta=0.0,
                raw_solve_p99_ms=1.0,
                simulator_diagnostics_available=True,
                simulator_gain_error_c_per_q=4.0,
                simulator_delay_error_s=40.0,
                simulator_coast_braking_error_c=3.0,
            ),
            ArmEvidence(
                "dmc",
                {"GrillSim": 10.0},
                prediction_error=0.5,
                before_mae=1.0,
                after_mae=1.0,
                recovery_improvement_ratio=1.0,
                recovery_improvement_delta=0.0,
                raw_solve_p99_ms=1.0,
                simulator_diagnostics_available=True,
                simulator_gain_error_c_per_q=0.1,
                simulator_delay_error_s=1.0,
                simulator_coast_braking_error_c=0.2,
            ),
            ArmEvidence(
                "state-space",
                {"GrillSim": 10.0},
                prediction_error=0.5,
                before_mae=1.0,
                after_mae=1.0,
                recovery_improvement_ratio=1.0,
                recovery_improvement_delta=0.0,
                raw_solve_p99_ms=1.0,
                simulator_diagnostics_available=True,
                simulator_gain_error_c_per_q=0.1,
                simulator_delay_error_s=1.0,
                simulator_coast_braking_error_c=0.2,
                simulator_diagnostics_valid=False,
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    recommendation = recommend(artifact)

    assert not recommendation.arms["state-space"].valid
    assert recommendation.pareto_frontier == ("dmc",)
    assert recommendation.selected_arm == "dmc"
def document_has_failure(artifact: ExperimentArtifact, category: str) -> bool:
    return any(failure["category"] == category for failure in artifact.to_document()["failures"])


def test_recommendation_uses_recovery_improvement_ratio_not_absolute_after_error() -> None:
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx",
                {"GrillSim": 10.0},
                prediction_error=1.0,
                before_mae=1.0,
                after_mae=1.0,
                recovery_improvement_ratio=1.0,
                recovery_improvement_delta=0.0,
                raw_solve_p99_ms=1.0,
                recovery_available=True,
            ),
            ArmEvidence(
                "dmc",
                {"GrillSim": 10.0},
                prediction_error=1.0,
                before_mae=100.0,
                after_mae=10.0,
                recovery_improvement_ratio=0.1,
                recovery_improvement_delta=90.0,
                raw_solve_p99_ms=1.0,
                recovery_available=True,
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    evidence = artifact.to_document()["arms"]["dmc"]

    assert evidence["before_mae"] == 100.0
    assert evidence["after_mae"] == 10.0
    assert evidence["recovery_improvement_ratio"] == 0.1
    assert evidence["recovery_improvement_delta"] == 90.0
    assert recommend(artifact).pareto_frontier == ("dmc",)



def test_target_miss_is_explicit_and_table_labels_timing_raw_only() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.artifact import render_table

    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(),
        arms=(
            ArmEvidence(
                "scheduled-arx",
                {"GrillSim": 10.0},
                7.0,
                1.0,
                1.0,
                1.0,
                0.0,
                1.0,
                runtime_validity="not_measured",
                target_missed=True,
                operational_consequence="not deployment-ready for 60-minute prediction",
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    document = artifact.to_document()["arms"]["scheduled-arx"]
    assert document["target_missed"] is True
    assert "not deployment-ready" in document["operational_consequence"]
    table = render_table(artifact)
    assert "not_measured/raw-only" in table
    assert "target_missed" in table


def test_normalized_artifact_round_trip_deduplicates_evidence_bundles() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import ScenarioResult

    common = dict(
        arm="scheduled-arx",
        plant="GrillSim",
        scenario="low-step",
        seed=2,
        initialization="correct",
        fan_fraction=(1.0,),
        requested_q=(0.0,),
        realized_q=(0.0,),
        temperature_c=(20.0,),
        target_c=(80.0,),
        metrics={"control_score": 1.0},
        model_evidence={"raw_origin": [1, 2, 3]},
    )
    artifact = ExperimentArtifact(
        config={"control_budget_ms": 50.0},
        seeds=(2,),
        splits={},
        model_snapshots={},
        scenarios=(
            ScenarioResult(mode="frozen", **common),
            ScenarioResult(mode="online", **common),
        ),
        arms=(ArmEvidence("scheduled-arx", {"GrillSim": 1.0}, 1.0, 1.0, 1.0, 1.0, 0.0, 1.0),),
        source_revision="abc123",
        environment={"python": "test"},
    )

    document = artifact.to_document()
    assert len(document["evidence_bundles"]) == 1
    assert all("model_evidence" not in row for row in document["scenarios"])
    restored = ExperimentArtifact.from_json(artifact.to_json())
    assert restored.to_document() == document


def test_gzip_artifact_transport_is_lossless_and_compact(tmp_path) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
        load_artifact,
        write_artifact_atomically,
    )

    artifact = artifact_with_scores()
    output = tmp_path / "evidence.json.gz"
    write_artifact_atomically(output, artifact)

    assert output.read_bytes()[:2] == b"\x1f\x8b"
    assert output.stat().st_size < len(artifact.to_json().encode())
    assert load_artifact(output).to_document() == artifact.to_document()


def test_gzip_transport_is_byte_identical_across_output_paths(tmp_path) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import write_artifact_atomically

    artifact = artifact_with_scores()
    left = tmp_path / "left.json.gz"
    right = tmp_path / "right.json.gz"
    write_artifact_atomically(left, artifact)
    write_artifact_atomically(right, artifact)

    assert left.read_bytes() == right.read_bytes()


def test_manifest_shards_are_bounded_deterministic_and_verified(tmp_path) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
        load_artifact,
        write_artifact_atomically,
    )

    artifact = artifact_with_scores()
    left = tmp_path / "left" / "evidence.manifest.json"
    right = tmp_path / "right" / "evidence.manifest.json"
    write_artifact_atomically(left, artifact, max_part_bytes=10)
    write_artifact_atomically(right, artifact, max_part_bytes=10)
    manifest = json.loads(left.read_text())
    assert len(manifest["parts"]) >= 3
    assert all(item["bytes"] <= 10 for item in manifest["parts"])
    assert load_artifact(left).to_document() == artifact.to_document()
    assert left.read_text() == right.read_text()
    stale = left.parent / f"{left.stem}.part9999.gz"
    stale.write_bytes(b"stale")
    write_artifact_atomically(left, artifact, max_part_bytes=10)
    assert not stale.exists()
    unsafe = json.loads(left.read_text())
    unsafe["parts"][0]["name"] = "../escape.gz"
    left.write_text(json.dumps(unsafe))
    with pytest.raises(ValueError, match="unsafe"):
        load_artifact(left)
    write_artifact_atomically(left, artifact, max_part_bytes=10)
    first = left.parent / manifest["parts"][0]["name"]
    first.write_bytes(first.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum"):
        load_artifact(left)
def test_loader_accepts_legacy_json_and_gzip_transport(tmp_path) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
        load_artifact,
        write_artifact_atomically,
    )

    artifact = artifact_with_scores()
    legacy = tmp_path / "legacy.json"
    legacy.write_text(artifact.to_json())
    compressed = tmp_path / "legacy.json.gz"
    write_artifact_atomically(compressed, artifact)
    assert load_artifact(legacy).to_document() == artifact.to_document()
    assert load_artifact(compressed).to_document() == artifact.to_document()
