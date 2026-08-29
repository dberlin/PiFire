"""Pure causal evaluation contracts for grey-box incumbent/challenger forecasts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from common.control_trace import AmbientSource
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    CheckStatus,
    FitRequest,
    FitResult,
    FitStatus,
    FitWindowIdentity,
    LearningStatus,
)
from controller.model_learning.evaluation import (
    CompletedForecastOrigin,
    EvaluationConfig,
    ForecastOrigin,
    evaluate_forecasts,
)

_INCUMBENT = "1" * 64
_CHALLENGER = "2" * 64


def _origin(
    sequence: int,
    horizon: int,
    *,
    incumbent_error: float = 2.0,
    challenger_error: float = 1.0,
    role_generation: int = 4,
    candidate_generation: int = 9,
    phase: str = "heating",
) -> CompletedForecastOrigin:
    observed = 100.0
    forecast = ForecastOrigin(
        origin_sequence=sequence,
        origin_time_s=sequence * 25.0,
        horizon_steps=horizon,
        role_generation=role_generation,
        candidate_generation=candidate_generation,
        incumbent_digest=_INCUMBENT,
        challenger_digest=_CHALLENGER,
        incumbent_prediction_c=observed - incumbent_error,
        challenger_prediction_c=observed - challenger_error,
        temperature_band="middle",
        phase=phase,
        ambient_source=AmbientSource.CONFIGURED,
        calibration_fit=False,
    )
    return CompletedForecastOrigin(
        forecast=forecast,
        completion_time_s=(sequence + horizon) * 25.0,
        observed_temperature_c=observed,
    )


def _winning_window(*, generation: int = 9) -> tuple[CompletedForecastOrigin, ...]:
    return tuple(
        _origin(sequence, horizon, candidate_generation=generation)
        for horizon in (3, 15, 45, 90, 180)
        for sequence in range(4)
    )


def test_current_model_learning_vocabularies_expose_only_causal_progress() -> None:
    assert {value.value for value in CandidateOrigin} == {
        "passive-online",
        "operator-calibration",
        "cook-refit",
    }
    assert {value.value for value in ActivationPolicy} == {
        "causal-auto",
        "passive-auto",
        "cook-refit",
    }
    assert {value.value for value in LearningStatus} == {
        "warming",
        "collecting",
        "fitting",
        "evaluating",
        "interrupted",
        "qualified",
        "activating",
        "active",
        "fallback",
        "error",
        "schema-invalidated",
    }
    assert {value.value for value in FitStatus} == {"idle", "queued", "running", "succeeded", "failed", "stale"}
    assert {value.value for value in CheckStatus} == {"not-run", "pending", "passed", "failed"}


def test_fit_request_and_result_preserve_the_exact_window_origin_and_generations() -> None:
    window = FitWindowIdentity(
        session_id="session-a",
        cook_id="cook-a",
        first_observation_sequence=17,
        last_observation_sequence=81,
        configuration_digest="3" * 64,
        incumbent_digest=_INCUMBENT,
        role_generation=4,
    )
    request = FitRequest(
        request_id="fit-17-81",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        window=window,
        candidate_generation=9,
    )
    result = FitResult(
        request_id=request.request_id,
        origin=request.origin,
        window=request.window,
        candidate_generation=request.candidate_generation,
        status=FitStatus.SUCCEEDED,
        candidate_digest=_CHALLENGER,
    )

    assert result.window is window
    assert result.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert result.window.role_generation == 4
    assert result.candidate_generation == 9
    with pytest.raises(FrozenInstanceError):
        request.candidate_generation = 10  # type: ignore[misc]


def test_challenger_must_win_each_required_horizon_not_only_the_pooled_score() -> None:
    records = list(_winning_window())
    records[-1] = _origin(3, 180, incumbent_error=0.2, challenger_error=3.1)
    assert sum(record.challenger_error_c**2 for record in records) < sum(
        record.incumbent_error_c**2 for record in records
    )

    decision = evaluate_forecasts(
        tuple(records),
        role_generation=4,
        candidate_generation=9,
        prior_consecutive_wins=1,
        config=EvaluationConfig(required_consecutive_wins=2),
    )
    horizon_180 = next(score for score in decision.scores if score.horizon_steps == 180)

    assert not decision.accepted
    assert decision.consecutive_wins == 0
    assert decision.blockers == ("challenger-horizon-180",)
    assert horizon_180.challenger_rmse_c > horizon_180.incumbent_rmse_c
    assert {score.horizon_steps for score in decision.scores} == {3, 15, 45, 90, 180}


def test_two_complete_causal_windows_create_a_decision_without_transferring_ownership() -> None:
    first = evaluate_forecasts(
        _winning_window(),
        role_generation=4,
        candidate_generation=9,
        prior_consecutive_wins=0,
        config=EvaluationConfig(required_consecutive_wins=2),
    )
    second = evaluate_forecasts(
        _winning_window(),
        role_generation=4,
        candidate_generation=9,
        prior_consecutive_wins=first.consecutive_wins,
        config=EvaluationConfig(required_consecutive_wins=2),
    )

    assert not first.accepted
    assert first.consecutive_wins == 1
    assert second.accepted
    assert second.consecutive_wins == 2
    assert second.role_generation == 4
    assert second.candidate_generation == 9
    assert not hasattr(second, "incumbent")
    assert not hasattr(second, "challenger")


def test_forecasts_from_another_role_or_candidate_generation_cannot_join() -> None:
    mixed = _winning_window() + (
        _origin(99, 3, role_generation=3),
        _origin(100, 3, candidate_generation=10),
    )

    with pytest.raises(ValueError, match="generation"):
        evaluate_forecasts(
            mixed,
            role_generation=4,
            candidate_generation=9,
            prior_consecutive_wins=0,
            config=EvaluationConfig(),
        )


def test_origin_band_phase_and_ambient_are_frozen_at_forecast_time() -> None:
    completed = _origin(3, 15, phase="coasting")

    assert completed.phase == "coasting"
    assert completed.temperature_band == "middle"
    assert completed.ambient_source is AmbientSource.CONFIGURED
    assert completed.incumbent_error_c == pytest.approx(2.0)
    assert completed.challenger_error_c == pytest.approx(1.0)
