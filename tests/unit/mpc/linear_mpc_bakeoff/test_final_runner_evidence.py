"""Final runner evidence contracts that prevent scientific-evidence regressions."""

from math import isfinite
from collections.abc import Mapping
from typing import Any
from types import SimpleNamespace

import numpy as np
import pytest
from docs.superpowers.experiments.linear_mpc_bakeoff import runner as runner_module

from controller.linear_mpc.contracts import AffinePrediction  # pyright: ignore[reportImplicitRelativeImport]
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    ExperimentConfig,
    _adaptation_settings,
    _coast_braking_masks,
    _common_validation_horizon,
    _real_mak_evidence,
    _split_evidence,
    _validation_origins,
    _window_free_run_scores,
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


def test_quick_artifact_persists_one_validation_selected_horizon() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_experiment

    artifact = run_experiment(ExperimentConfig.quick())

    selection = artifact.config["horizon_selection"]
    assert selection["selected_horizon_s"] == 600
    assert len(selection["origins"]) == 24
    assert {row.mpc_horizon_s for row in artifact.scenarios} == {600}
    assert all(
        key.count(":") == 2 and key.split(":", 1)[0] in {"online", "frozen"}
        for arm in artifact.arms
        for key in arm.domain_median_scores
    )


def _record(samples: int, provenance: str) -> SignalRecord:
    return SignalRecord(
        time_s=np.arange(samples, dtype=np.float64) * 20.0,
        temp_c=np.full(samples, 100.0),
        q=np.full(samples, 0.5),
        ambient_c=np.full(samples, 20.0),
        provenance=provenance,
    )


def test_split_evidence_uses_source_chronology_and_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "_calibration_record",
        lambda plant, seed: _record(400 if plant == "MAKGrillSim" else 100, f"{plant}:{seed}"),
    )
    monkeypatch.setattr(runner_module, "_real_mak_record", lambda: _record(40, "requested-input-reconstruction"))

    splits = _split_evidence(ExperimentConfig(quick_mode=True, seeds=(2,)))

    assert splits["GrillSim:2"] == {
        "fit": [0, 35],
        "validation": [35, 75],
        "test": [75, 100],
        "provenance": "GrillSim:2",
    }
    assert splits["MAKGrillSim:2"] == {
        "fit": [0, 147],
        "validation": [147, 315],
        "test": [315, 400],
        "provenance": "MAKGrillSim:2",
    }
    assert splits["real-MAK"]["fit"][1] <= splits["real-MAK"]["validation"][0]
    assert splits["real-MAK"]["validation"][1] <= splits["real-MAK"]["test"][0]
    assert splits["real-MAK"]["provenance"] == "requested-input-reconstruction"


def test_real_mak_evidence_maps_validation_and_test_horizons(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _ConstantPrediction(100.0)
    monkeypatch.setattr(runner_module, "_real_mak_record", lambda: _record(40, "requested-input-reconstruction"))
    monkeypatch.setattr(runner_module, "_model_for_initialization", lambda arm, initialization: model)
    monkeypatch.setattr(runner_module, "_fit_model", lambda candidate, record: None)
    monkeypatch.setattr(
        runner_module,
        "_horizon_residuals",
        lambda candidate, record, *, starts, horizons_s: {
            horizon: ((2.0,) if horizon == 300 and len(horizons_s) == 2 else (1.0,) if horizon in {60, 300} else ())
            for horizon in horizons_s
        },
    )
    monkeypatch.setattr(runner_module, "_bakeoff_snapshot", lambda candidate: {"steady_gain": 1.0})
    _real_mak_evidence.cache_clear()
    try:
        evidence = _real_mak_evidence("scheduled-arx")
    finally:
        _real_mak_evidence.cache_clear()

    assert evidence["provenance"] == "requested-input-reconstruction"
    assert evidence["validation_candidate_scores"] == [2.0]
    assert evidence["diagnostics_c"] == {
        "60": 1.0,
        "300": 1.0,
        "900": None,
        "1800": None,
        "3600": None,
    }


def test_quick_simulator_diagnostics_cover_all_horizons_without_unmasked_coast_leakage() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_experiment

    artifact = run_experiment(ExperimentConfig.quick())

    for row in artifact.scenarios:
        if row.plant not in {"GrillSim", "MAKGrillSim"}:
            continue
        document = row.model_evidence["simulator_prediction_diagnostics"]
        split = artifact.splits[f"{row.plant}:{row.seed}"]
        assert document["boundaries"] == {name: list(split[name]) for name in ("fit", "validation", "test")}
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
                    residual for residual, selected in zip(origin["residuals_c"], mask, strict=True) if selected
                ]
        snapshot = row.model_evidence["batch_fit_snapshot"]
        assert isinstance(snapshot["steady_gain"], (int, float))
        assert isfinite(snapshot["steady_gain"])
        assert snapshot["steady_gain"] > 0.0

    for arm in artifact.arms:
        assert arm.simulator_diagnostics_available  # pyright: ignore[reportAttributeAccessIssue]
        assert arm.simulator_diagnostics_valid  # pyright: ignore[reportAttributeAccessIssue]
        assert arm.prediction_error == arm.simulator_diagnostics["aggregate"]["3600"]["rmse_c"]  # pyright: ignore[reportAttributeAccessIssue]


def test_scenario_envelope_restores_down_step_and_limits_600f_to_eligible_plant() -> None:
    scenarios = {item.name: item for item in SCENARIOS}

    assert "down-step" in scenarios
    assert scenarios["high-step-450f"].applicable_plants == (  # pyright: ignore[reportAttributeAccessIssue]
        "GrillSim",
        "MAKGrillSim",
    )
    assert scenarios["high-step-600f"].applicable_plants == ("GrillSim",)  # pyright: ignore[reportAttributeAccessIssue]


class _ConstantPrediction:
    def __init__(self, temperature_c: float) -> None:
        self._temperature_c: float = temperature_c

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: np.ndarray,
    ) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(
            np.full(horizon_steps, self._temperature_c),
            np.zeros((horizon_steps, horizon_steps)),
        )


class _RunnerModel(_ConstantPrediction):
    def snapshot(self) -> dict[str, object]:
        return {
            "schema": "unit",
            "steady_gain": 1.0,
            "plausibility_bounds": {"max_dc_gain_c_per_q": 10.0},
        }


class _FakeController:
    def __init__(self, config: object) -> None:
        del config

    def solve(self, prediction: AffinePrediction, **_: object) -> SimpleNamespace:
        return SimpleNamespace(
            sequence_q=np.full(prediction.free_output_c.size, 0.5),
            objective=0.0,
            kkt_residual=0.0,
            iterations=1,
            hessian_condition=1.0,
        )


class _RecordingOnlineManager:
    observations: list[object] = []

    def __init__(self, *, incumbent: object, challenger: object, **_: object) -> None:
        self.incumbent = incumbent
        self.challenger = challenger
        self.role_generation = 0

    def observe(self, observation: object, *, braking: bool) -> SimpleNamespace:
        del braking
        self.observations.append(observation)
        reasons = []
        if observation.safety_inhibited:
            reasons.append(SimpleNamespace(value="safety"))
        if observation.manual_override:
            reasons.append(SimpleNamespace(value="manual"))
        return SimpleNamespace(gate=SimpleNamespace(permitted=not reasons, reasons=tuple(reasons)))

    def snapshot(self) -> dict[str, object]:
        return {"policy": {}}


def _install_fast_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    model = _RunnerModel(100.0)
    calibration = _record(20, "unit-calibration")
    monkeypatch.setattr(
        runner_module,
        "_fitted_model",
        lambda arm, plant, seed, initialization: (
            model,
            0.0,
            {60: [1.0]},
            {60: [1.0]},
            calibration,
        ),
    )
    monkeypatch.setattr(runner_module, "_simulator_prediction_diagnostics", lambda *args: {})
    monkeypatch.setattr(runner_module, "LinearMPC", _FakeController)
    monkeypatch.setattr(
        runner_module,
        "_independent_box_qp_reference",
        lambda *args: {"reference_converged": False, "reference_failure": "unit"},
    )


def test_runner_forwards_override_gates_without_updating_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _run_scenario

    _install_fast_runner(monkeypatch)
    _RecordingOnlineManager.observations = []
    monkeypatch.setattr(runner_module, "OnlineAdaptation", _RecordingOnlineManager)
    definition = next(item for item in SCENARIOS if item.name == "override-window")

    row = _run_scenario(
        definition,
        plant="GrillSim",
        seed=2,
        mode="online",
        duration_s=140,
        arm="scheduled-arx",
        initialization="correct",
        horizon_s=60,
    )

    rejections = [item for item in row.promotion_history if item["kind"] == "update-rejection"]
    assert {"safety", "manual"} <= {reason for item in rejections for reason in item["reasons"]}
    assert all(not item["incumbent_updated"] and not item["challenger_updated"] for item in rejections)
    assert any(observation.safety_inhibited for observation in _RecordingOnlineManager.observations)
    assert any(observation.manual_override for observation in _RecordingOnlineManager.observations)


def test_window_scores_use_untouched_multi_horizon_role_predictions() -> None:
    samples: list[Mapping[str, Any]] = [
        {
            "ambient_c": 20.0,
            "braking": index in {4, 8},
            "challenger": _ConstantPrediction(100.0),
            "frame_s": index * 20,
            "incumbent": _ConstantPrediction(105.0),
            "q": 0.5,
            "q_previous": 0.5,
            "temp_c": 100.0,
            "window_id": "window-1",
        }
        for index in range(15)
    ]

    scores, evidence = _window_free_run_scores(samples)

    assert scores.candidate_prediction_score == 0.0
    assert scores.incumbent_prediction_score == 5.0
    assert set(evidence["horizon_metrics"]) == {"60", "300"}
    assert len(evidence["horizon_metrics"]["60"]["origin_frame_ids"]) == 13
    assert evidence["horizon_metrics"]["300"]["origin_frame_ids"] == [0]
    assert evidence["score_frame_ids"] == list(range(0, 300, 20))
    assert evidence["braking_or_coast_sample_count"] == 2


def test_adaptation_settings_use_training_bounds_and_arm_alignment() -> None:
    state_policy, state_alignment = _adaptation_settings(
        "state-space",
        {"plausibility_bounds": {"max_steady_gain_c_per_q": 42.0}},
    )
    arx_policy, arx_alignment = _adaptation_settings(
        "scheduled-arx",
        {"plausibility_bounds": {"max_dc_gain_c_per_q": 24.0}},
    )

    assert state_policy.max_gain == 42.0
    assert state_alignment.value == "measured"
    assert arx_policy.max_gain == 24.0
    assert arx_alignment.value == "not-applicable"


def test_coast_and_braking_masks_classify_each_future_frame() -> None:
    record = SignalRecord(
        time_s=np.arange(5, dtype=np.float64) * 20.0,
        temp_c=np.full(5, 100.0),
        q=np.asarray([0.8, 0.8, 0.4, 0.0, 0.2]),
        ambient_c=np.full(5, 20.0),
        provenance="unit",
    )

    coast, braking = _coast_braking_masks(record, start=2, steps=3)

    assert coast == [False, True, False]
    assert braking == [True, True, False]
