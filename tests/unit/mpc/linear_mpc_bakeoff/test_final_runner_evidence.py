"""Final runner evidence contracts that prevent scientific-evidence regressions."""

from __future__ import annotations
from dataclasses import replace

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
        assert real["diagnostics_c"]["900"] is None
        assert real["diagnostics_c"]["1800"] is None
        assert real["diagnostics_c"]["3600"] is None
    real_split = artifact.splits["real-MAK"]
    assert real_split["fit"][1] <= real_split["validation"][0] <= real_split["validation"][1]
    assert real_split["validation"][1] <= real_split["test"][0]
    assert all(
        key.count(":") == 2 and key.split(":", 1)[0] in {"online", "frozen"}
        for arm in artifact.arms
        for key in arm.domain_median_scores
    )


def test_quick_simulator_diagnostics_cover_all_horizons_without_unmasked_coast_leakage() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_experiment

    artifact = run_experiment(ExperimentConfig.quick())

    for row in artifact.scenarios:
        if row.plant not in {"GrillSim", "MAKGrillSim"}:
            continue
        document = row.model_evidence["simulator_prediction_diagnostics"]
        split = artifact.splits[f"{row.plant}:{row.seed}"]
        assert document["boundaries"] == {
            name: list(split[name]) for name in ("fit", "validation", "test")
        }
        diagnostics = document["diagnostics_c"]
        assert tuple(diagnostics) == ("60", "300", "900", "1800", "3600")
        for horizon, evidence in diagnostics.items():
            assert evidence is not None, horizon
            assert evidence["origins"]
            assert evidence["coast_braking_sample_count"] > 0
            for origin in evidence["origins"]:
                mask = origin["coast_or_braking_mask"]
                assert len(mask) == len(origin["residuals_c"])
                assert origin["coast_braking_residuals_c"] == [
                    residual
                    for residual, selected in zip(origin["residuals_c"], mask, strict=True)
                    if selected
                ]
        snapshot = row.model_evidence["batch_fit_snapshot"]
        assert snapshot["steady_gain"] != 0.0

    for arm in artifact.arms:
        assert arm.simulator_diagnostics_available
        assert arm.simulator_diagnostics_valid
        assert arm.prediction_error == arm.simulator_diagnostics["aggregate"]["3600"]["rmse_c"]


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


def test_online_evaluations_record_distinct_pre_assimilation_scores_and_refresh_work() -> None:
    from math import isfinite

    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "high-step-600f")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=620,
        arm="scheduled-arx",
        initialization="wrong-gain",
        horizon_s=600,
    )

    evaluations = [
        item for item in row.promotion_history if item["kind"] == "five-minute-evaluation"
    ]
    assert len(evaluations) == 2
    assert all(
        isfinite(item["candidate_prediction_score"])
        and isfinite(item["incumbent_prediction_score"])
        and set(item["horizon_metrics"]) >= {"60", "300"}
        for item in evaluations
    )
    assert all(
        len(item["score_frame_ids"]) == item["sample_count"]
        and item["score_frame_ids"] == sorted(item["score_frame_ids"])
        and item["score_role_generations"] == [item["score_role_generation"]]
        for item in evaluations
    )
    assert any(
        item["candidate_prediction_score"] != item["incumbent_prediction_score"]
        for item in evaluations
    )
    state_space_row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=620,
        arm="state-space",
        initialization="wrong-gain",
        horizon_s=600,
    )
    assert state_space_row.raw_refresh_ms
    assert all("iterations" in item and "kkt_residual" in item for item in row.solver_evidence)
    assert any(item.get("reference_method") == "scipy-l-bfgs-b" for item in row.solver_evidence)


def test_runner_clears_real_promotion_window_samples_and_role_generations() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "high-step-600f")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=1820,
        arm="scheduled-arx",
        initialization="wrong-pole",
        horizon_s=600,
    )
    evaluations = [
        item for item in row.promotion_history if item["kind"] == "five-minute-evaluation"
    ]
    promoted_index = next(index for index, item in enumerate(evaluations) if item["promoted"])
    assert evaluations[promoted_index - 1]["consecutive_wins"] == 1
    assert evaluations[promoted_index]["consecutive_wins"] == 2
    assert evaluations[promoted_index + 1]["score_role_generation"] == 1
    frame_sets = [set(item["score_frame_ids"]) for item in evaluations]
    assert all(left.isdisjoint(right) for left, right in zip(frame_sets, frame_sets[1:]))

def test_runner_constructs_real_arm_managers_with_snapshot_bounds_and_alignment() -> None:
    """Production wiring, rather than a test policy override, must admit fitted arms."""
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "high-step-600f")
    rows = {
        arm: _run_scenario(
            definition,
            plant="GrillSim",
            seed=2,
            mode="online",
            duration_s=320,
            arm=arm,
            initialization="wrong-gain",
            horizon_s=600,
        )
        for arm in ("scheduled-arx", "dmc", "state-space")
    }
    assert rows["scheduled-arx"].model_evidence["adaptation"]["alignment"] == "not-applicable"
    assert rows["dmc"].model_evidence["adaptation"]["alignment"] == "not-applicable"
    assert rows["state-space"].model_evidence["adaptation"]["alignment"] == "measured"
    for row in rows.values():
        assert row.model_evidence["adaptation"]["policy"]["max_gain"] >= abs(
            row.model_evidence["batch_fit_snapshot"]["steady_gain"]
        )
    assert all(
        item["plausible_gain"] and item["state_aligned"]
        for item in rows["dmc"].promotion_history
        if item["kind"] == "five-minute-evaluation"
    )


def test_runner_scores_untouched_multi_horizon_free_runs_and_clears_windows() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "high-step-600f")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=620,
        arm="scheduled-arx",
        initialization="wrong-gain",
        horizon_s=600,
    )
    evaluations = [
        item for item in row.promotion_history if item["kind"] == "five-minute-evaluation"
    ]
    assert evaluations
    assert all(set(item["horizon_metrics"]) >= {"60", "300"} for item in evaluations)
    assert all(item["score_frame_ids"] == sorted(item["score_frame_ids"]) for item in evaluations)
    assert set(evaluations[0]["score_frame_ids"]).isdisjoint(evaluations[1]["score_frame_ids"])



def test_downstep_transition_is_braking_without_coast_and_heating_is_excluded() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "down-step")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=120,
        arm="scheduled-arx",
        initialization="correct",
        horizon_s=600,
    )
    samples = row.model_evidence["free_run_classifications"]
    downstep = next(item for item in samples if item["frame_s"] == 80)
    heating = next(item for item in samples if item["frame_s"] == 40)
    assert downstep["braking"] and downstep["realized_duty"] > 0.05
    assert not heating["braking"]

def test_online_and_frozen_paths_track_identical_realized_history() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "low-step")
    common = dict(
        definition=definition,
        plant="GrillSim",
        seed=2,
        duration_s=320,
        arm="scheduled-arx",
        initialization="correct",
        horizon_s=600,
    )
    frozen = _run_scenario(mode="frozen", **common)
    online = _run_scenario(mode="online", **common)
    assert frozen.realized_q == online.realized_q
    assert frozen.model_evidence["runtime_tracking"] == online.model_evidence["runtime_tracking"]


def test_runner_classifies_realized_zero_duty_frames_as_coast() -> None:
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
    assert row.model_evidence["runtime_tracking"]["operating_state_counts"]["coast"] > 0



def test_wrong_model_recovery_requires_real_shadow_promotion() -> None:
    """A wrong model earns recovery evidence only from two managed free-run wins."""
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    definition = next(item for item in SCENARIOS if item.name == "high-step-600f")
    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=1820,
        arm="scheduled-arx",
        initialization="wrong-pole",
        horizon_s=600,
    )
    evaluations = [
        item for item in row.promotion_history if item["kind"] == "five-minute-evaluation"
    ]
    promoted = [item for item in evaluations if item["promoted"]]
    assert len(promoted) == 1
    assert promoted[0]["consecutive_wins"] == 2
    assert evaluations[evaluations.index(promoted[0]) - 1]["consecutive_wins"] == 1
    assert row.metrics["recovery_after_mae_c"] < row.metrics["recovery_before_mae_c"]
    assert row.metrics["recovery_improvement_ratio"] < 1.0
