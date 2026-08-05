"""Final runner evidence contracts that prevent scientific-evidence regressions."""

from __future__ import annotations

from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    ExperimentConfig,
    _common_validation_horizon,
    _validation_origins,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.scenarios import SCENARIOS


def test_validation_origins_never_cross_the_validation_boundary() -> None:
    origins = _validation_origins(
        record_samples=600,
        validation_start=360,
        validation_end=480,
        horizon_steps=30,
        frame_steps=1,
    )

    assert origins
    assert all(origin + 30 <= 480 for origin in origins)
    assert all(origin >= 360 for origin in origins)


def test_each_candidate_horizon_has_its_own_validation_boundary_origins() -> None:
    for horizon_steps in (30, 40, 50):
        origins = _validation_origins(
            record_samples=600,
            validation_start=360,
            validation_end=480,
            horizon_steps=horizon_steps,
            frame_steps=1,
        )
        assert origins
        assert max(origins) + horizon_steps <= 480


def test_common_horizon_is_one_validation_only_choice_for_every_arm() -> None:
    choice = _common_validation_horizon(
        {
            "scheduled-arx:GrillSim:correct:0": {600: (0.999,), 800: (0.995,), 1000: (0.99,)},
            "dmc:MAKGrillSim:wrong-gain:0": {600: (0.999,), 800: (0.995,), 1000: (0.99,)},
        }
    )

    assert choice["selected_horizon_s"] == 600
    assert choice["pooled_validation_scores"] == {"600": 0.999, "800": 0.995, "1000": 0.99}


def test_quick_matrix_contract_includes_calibrated_and_wrong_initializations() -> None:
    config = ExperimentConfig.quick()
    assert config.initializations == ("correct", "wrong-gain", "wrong-pole", "wrong-delay")

def test_quick_artifact_persists_one_horizon_and_real_mak_provenance() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_experiment

    artifact = run_experiment(ExperimentConfig.quick())

    selected = artifact.config["horizon_selection"]["selected_horizon_s"]
    assert {row.mpc_horizon_s for row in artifact.scenarios} == {selected}
    for arm in artifact.arms:
        real = artifact.horizon_evidence[arm.name]["real"]
        assert real["provenance"] == "requested-input-reconstruction"
        assert set(real["diagnostics_c"]) == {"60", "300", "900", "1800", "3600"}
        assert real["diagnostics_c"]["60"] is not None
        assert real["diagnostics_c"]["300"] is not None
        assert real["diagnostics_c"]["900"] is not None
        assert real["diagnostics_c"]["1800"] is None
        assert real["diagnostics_c"]["3600"] is None
    assert all(
        key.count(":") == 2 and key.split(":", 1)[0] in {"online", "frozen"}
        for arm in artifact.arms
        for key in arm.domain_median_scores
    )


def test_scenario_envelope_restores_down_step_and_limits_600f_to_eligible_plant() -> None:
    scenarios = {item.name: item for item in SCENARIOS}

    assert "down-step" in scenarios
    assert scenarios["high-step-450f"].applicable_plants == ("GrillSim", "MAKGrillSim")
    assert scenarios["high-step-600f"].applicable_plants == ("GrillSim",)


def test_override_frames_are_recorded_as_non_updates_for_both_models() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "override-window")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=140,
        arm="scheduled-arx",
        initialization="correct",
        horizon_s=600,
    )

    rejections = [item for item in row.promotion_history if item["kind"] == "update-rejection"]
    assert {"safety", "manual"} <= {reason for item in rejections for reason in item["reasons"]}
    assert all(not item["incumbent_updated"] and not item["challenger_updated"] for item in rejections)
    assert row.model_evidence["batch_fit_snapshot"]
    assert row.model_evidence["final_active_snapshot"]


def test_online_refresh_is_a_five_minute_evaluation_and_solver_timing_is_solve_only() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "low-step")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=320,
        arm="scheduled-arx",
        initialization="correct",
        horizon_s=600,
    )

    assert len(row.raw_refresh_ms) == 1
    assert any(item["kind"] == "five-minute-evaluation" for item in row.promotion_history)
    assert all("iterations" in item and "kkt_residual" in item for item in row.solver_evidence)
    assert any(item.get("reference_method") == "scipy-l-bfgs-b" for item in row.solver_evidence)

