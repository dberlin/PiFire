"""Behavioral contracts for bake-off evidence and recommendation."""

from __future__ import annotations

import json

from docs.superpowers.experiments.linear_mpc_bakeoff.artifact import (
    ArmEvidence,
    ArmFailure,
    ExperimentArtifact,
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
            ArmEvidence("scheduled-arx", {"GrillSim": arx}, 3.0, 3.0, 2.0, 10.0),
            ArmEvidence("dmc", {"GrillSim": dmc}, 4.0, 4.0, 2.0, 10.0),
            ArmEvidence("state-space", {"GrillSim": state_space}, state_prediction, state_prediction, 2.0, 10.0),
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
                "scheduled-arx", {"GrillSim": 10.0}, 1.0, 1.0,
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


def test_structured_failure_invalidates_only_allowed_reason() -> None:
    artifact = artifact_with_scores()
    artifact = artifact.with_failures((ArmFailure("dmc", "lid-excursion", "wrong-input-semantics", "q was realized"),))

    recommendation = recommend(artifact)

    assert not recommendation.arms["dmc"].valid
    assert recommendation.arms["dmc"].reasons == ("wrong input semantics",)
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
                raw_solve_p99_ms=1.0,
                raw_learner_ms=(6.0,),
                raw_refresh_ms=(1.0,),
            ),
        ),
        source_revision="abc123",
        environment={"python": "test"},
    )

    assert not recommend(artifact).arms["scheduled-arx"].valid

def document_has_failure(artifact: ExperimentArtifact, category: str) -> bool:
    return any(failure["category"] == category for failure in artifact.to_document()["failures"])
