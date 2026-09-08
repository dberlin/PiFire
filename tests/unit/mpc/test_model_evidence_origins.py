"""Causal completed-origin contracts for model-neutral grey forecasts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from common.control_trace import AmbientSource
from common.mpc_learning import MPC_FORECAST_HORIZONS, forecast_horizon_spec
from controller.model_learning.contracts import FrameObservation
from controller.model_learning.evaluation import CausalForecastEvaluator, ForecastOrigin

_INCUMBENT = "a" * 64
_CHALLENGER = "b" * 64


def _frame(
    sequence: int,
    *,
    role_generation: int = 7,
    calibration_fit: bool = False,
    continuous: bool = True,
) -> FrameObservation:
    q = 0.25 if sequence % 2 else 0.75
    return FrameObservation(
        frame_start_s=sequence * 20.0,
        frame_end_s=(sequence + 1) * 20.0,
        wall_start_ms=round((sequence * 20.0) * 1_000),
        wall_end_ms=round(((sequence + 1) * 20.0) * 1_000),
        temp_c=100.0 + sequence,
        setpoint_c=180.0,
        ambient_c=20.0,
        requested_q=q,
        realized_q=q,
        requested_auger_duty=q,
        delivered_on_s=q * 20.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=sequence,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=continuous,
        role_generation=role_generation,
        observation_sequence=sequence,
        ambient_source=AmbientSource.CONFIGURED,
        calibration_stage="middle" if calibration_fit else None,
        calibration_fit=calibration_fit,
    )


def _origin(
    sequence: int,
    horizon_seconds: int,
    *,
    role_generation: int = 7,
    candidate_generation: int = 11,
    calibration_fit: bool = False,
) -> ForecastOrigin:
    frame = _frame(sequence, role_generation=role_generation, calibration_fit=calibration_fit)
    horizon = forecast_horizon_spec(horizon_seconds)
    return ForecastOrigin(
        origin_sequence=frame.observation_sequence,
        origin_time_s=frame.frame_end_s,
        horizon_seconds=horizon.seconds,
        prediction_steps=horizon.prediction_steps,
        observation_frames=horizon.observation_frames,
        role_generation=role_generation,
        candidate_generation=candidate_generation,
        incumbent_digest=_INCUMBENT,
        challenger_digest=_CHALLENGER,
        incumbent_prediction_c=120.0,
        challenger_prediction_c=110.0,
        temperature_band="middle",
        phase="heating",
        ambient_source=frame.ambient_source,
        calibration_fit=frame.calibration_fit,
    )


def test_forecast_origin_is_immutable_and_keeps_role_and_candidate_generations_distinct() -> None:
    origin = _origin(0, 100, role_generation=4, candidate_generation=9)

    assert origin.role_generation == 4
    assert origin.candidate_generation == 9
    with pytest.raises(FrozenInstanceError):
        origin.candidate_generation = 4  # type: ignore[misc]


def test_mpc_forecast_horizons_align_prediction_and_observation_clocks() -> None:
    assert tuple(
        (horizon.seconds, horizon.prediction_steps, horizon.observation_frames, horizon.maximum_rmse_c)
        for horizon in MPC_FORECAST_HORIZONS
    ) == (
        (100, 4, 5, 2.8),
        (200, 8, 10, 2.8),
        (300, 12, 15, 2.8),
        (400, 16, 20, 2.8),
        (600, 24, 30, 2.8),
    )
    assert all(horizon.prediction_steps * 25 == horizon.seconds for horizon in MPC_FORECAST_HORIZONS)
    assert all(horizon.observation_frames * 20 == horizon.seconds for horizon in MPC_FORECAST_HORIZONS)


def test_forecast_origin_rejects_mismatched_clock_dimensions() -> None:
    origin = _origin(0, 100)

    with pytest.raises(ValueError, match="horizon"):
        replace(origin, prediction_steps=5)


def test_origins_complete_once_at_the_exact_future_observation() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    for horizon in MPC_FORECAST_HORIZONS:
        evaluator.register(_origin(0, horizon.seconds))

    for sequence in range(1, 31):
        evaluator.observe(_frame(sequence))

    assert {item.horizon_seconds for item in evaluator.completed_origins} == {100, 200, 300, 400, 600}
    assert {item.completion_time_s - item.forecast.origin_time_s for item in evaluator.completed_origins} == {
        100.0,
        200.0,
        300.0,
        400.0,
        600.0,
    }
    count = len(evaluator.completed_origins)
    evaluator.observe(_frame(30))
    assert len(evaluator.completed_origins) == count


def test_generation_fence_expires_old_forecasts_without_relabeling_them() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    origin = _origin(0, 100)
    evaluator.register(origin)

    evaluator.set_generations(role_generation=8, candidate_generation=12)

    assert evaluator.pending_origins == ()
    assert origin.role_generation == 7
    assert origin.candidate_generation == 11
    with pytest.raises(ValueError, match="generation"):
        evaluator.register(_origin(1, 100, role_generation=7, candidate_generation=11))


def test_discontinuity_or_probe_target_discards_a_pending_validation_origin() -> None:
    for target in (
        replace(_frame(1), continuous=False),
        _frame(1, calibration_fit=True),
    ):
        evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
        evaluator.register(_origin(0, 100))
        evaluator.observe(target)
        for sequence in range(2, 6):
            evaluator.observe(_frame(sequence))

        assert evaluator.completed_origins == ()
        assert evaluator.pending_origins == ()


def test_probe_frame_is_eligible_for_fitting_but_forbidden_as_a_causal_origin() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    probe_origin = _origin(0, 100, calibration_fit=True)

    with pytest.raises(ValueError, match="probe.*causal|causal.*probe"):
        evaluator.register(probe_origin)
