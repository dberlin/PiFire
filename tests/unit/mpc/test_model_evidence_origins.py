"""Causal completed-origin contracts for model-neutral grey forecasts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from common.control_trace import AmbientSource
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
        frame_start_s=sequence * 25.0,
        frame_end_s=(sequence + 1) * 25.0,
        temp_c=100.0 + sequence,
        setpoint_c=180.0,
        ambient_c=20.0,
        requested_q=q,
        realized_q=q,
        requested_auger_duty=q,
        delivered_on_s=q * 25.0,
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
    horizon: int,
    *,
    role_generation: int = 7,
    candidate_generation: int = 11,
    calibration_fit: bool = False,
) -> ForecastOrigin:
    frame = _frame(sequence, role_generation=role_generation, calibration_fit=calibration_fit)
    return ForecastOrigin(
        origin_sequence=frame.observation_sequence,
        origin_time_s=frame.frame_end_s,
        horizon_steps=horizon,
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
    origin = _origin(0, 3, role_generation=4, candidate_generation=9)

    assert origin.role_generation == 4
    assert origin.candidate_generation == 9
    with pytest.raises(FrozenInstanceError):
        origin.candidate_generation = 4  # type: ignore[misc]


def test_origins_complete_once_at_the_exact_future_observation() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    for horizon in (3, 15, 45, 90, 180):
        evaluator.register(_origin(0, horizon))

    for sequence in range(1, 181):
        evaluator.observe(_frame(sequence))

    assert {item.horizon_steps for item in evaluator.completed_origins} == {3, 15, 45, 90, 180}
    assert {item.completion_time_s for item in evaluator.completed_origins} == {
        100.0,
        400.0,
        1_150.0,
        2_275.0,
        4_525.0,
    }
    count = len(evaluator.completed_origins)
    evaluator.observe(_frame(180))
    assert len(evaluator.completed_origins) == count


def test_generation_fence_expires_old_forecasts_without_relabeling_them() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    origin = _origin(0, 3)
    evaluator.register(origin)

    evaluator.set_generations(role_generation=8, candidate_generation=12)

    assert evaluator.pending_origins == ()
    assert origin.role_generation == 7
    assert origin.candidate_generation == 11
    with pytest.raises(ValueError, match="generation"):
        evaluator.register(_origin(1, 3, role_generation=7, candidate_generation=11))


def test_discontinuity_or_probe_target_discards_a_pending_validation_origin() -> None:
    for target in (
        replace(_frame(1), continuous=False),
        _frame(1, calibration_fit=True),
    ):
        evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
        evaluator.register(_origin(0, 3))
        evaluator.observe(target)
        evaluator.observe(_frame(2))
        evaluator.observe(_frame(3))

        assert evaluator.completed_origins == ()
        assert evaluator.pending_origins == ()


def test_probe_frame_is_eligible_for_fitting_but_forbidden_as_a_causal_origin() -> None:
    evaluator = CausalForecastEvaluator(role_generation=7, candidate_generation=11)
    probe_origin = _origin(0, 3, calibration_fit=True)

    with pytest.raises(ValueError, match="probe.*causal|causal.*probe"):
        evaluator.register(probe_origin)
