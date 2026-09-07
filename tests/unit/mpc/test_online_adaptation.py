"""Pure causal evaluation contracts for grey-box incumbent/challenger forecasts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from common.control_trace import AmbientSource
from common.mpc_learning import MPC_FORECAST_HORIZONS, forecast_horizon_spec
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    CheckStatus,
    FitRequest,
    FitResult,
    FitStatus,
    LearningStatus,
)
from controller.model_learning.evaluation import (
    CompletedForecastOrigin,
    EvaluationConfig,
    ForecastOrigin,
    evaluate_forecasts,
)
from tests.unit.common._model_challenger_helpers import _corpus

_INCUMBENT = "1" * 64
_CHALLENGER = "2" * 64


def _origin(
    sequence: int,
    horizon_seconds: int,
    *,
    incumbent_error: float = 2.0,
    challenger_error: float = 1.0,
    role_generation: int = 4,
    candidate_generation: int = 9,
    phase: str = "heating",
) -> CompletedForecastOrigin:
    observed = 100.0
    horizon = forecast_horizon_spec(horizon_seconds)
    forecast = ForecastOrigin(
        origin_sequence=sequence,
        origin_time_s=sequence * 20.0,
        horizon_seconds=horizon.seconds,
        prediction_steps=horizon.prediction_steps,
        observation_frames=horizon.observation_frames,
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
        completion_time_s=sequence * 20.0 + horizon.seconds,
        observed_temperature_c=observed,
    )


def _winning_window(*, generation: int = 9) -> tuple[CompletedForecastOrigin, ...]:
    return tuple(
        _origin(sequence, horizon.seconds, candidate_generation=generation)
        for horizon in MPC_FORECAST_HORIZONS
        for sequence in range(4)
    )


def test_current_model_learning_vocabularies_expose_only_causal_progress() -> None:
    assert {value.value for value in CandidateOrigin} == {
        "passive-online",
        "operator-calibration",
    }
    assert {value.value for value in ActivationPolicy} == {"causal-auto"}
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
    }
    assert {value.value for value in FitStatus} == {"idle", "queued", "running", "succeeded", "failed", "stale"}
    assert {value.value for value in CheckStatus} == {"not-run", "pending", "passed", "failed"}


def test_fit_request_and_result_preserve_exact_corpus_origin_and_generations() -> None:
    corpus = _corpus("online-adaptation")
    request = FitRequest(
        request_id="fit-corpus",
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        fit_corpus=corpus,
        configuration_digest="3" * 64,
        parent_incumbent_digest=_INCUMBENT,
        parent_incumbent_generation=4,
        candidate_generation=9,
    )
    result = FitResult(
        request=request,
        status=FitStatus.SUCCEEDED,
        candidate_digest=_CHALLENGER,
    )

    assert result.request is request
    assert request.fit_corpus is corpus
    assert request.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert request.parent_incumbent_generation == 4
    assert request.candidate_generation == 9
    with pytest.raises(FrozenInstanceError):
        request.candidate_generation = 10  # type: ignore[misc]


def test_challenger_must_win_each_required_horizon_not_only_the_pooled_score() -> None:
    records = list(_winning_window())
    records[-1] = _origin(3, 600, incumbent_error=0.2, challenger_error=3.1)
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
    horizon_600 = next(score for score in decision.scores if score.horizon_seconds == 600)

    assert not decision.accepted
    assert decision.consecutive_wins == 0
    assert decision.blockers == ("challenger-horizon-600",)
    assert horizon_600.challenger_rmse_c > horizon_600.incumbent_rmse_c
    assert {score.horizon_seconds for score in decision.scores} == {100, 200, 300, 400, 600}


def test_challenger_must_meet_absolute_rmse_limit_at_every_horizon() -> None:
    records = tuple(
        _origin(
            sequence,
            horizon.seconds,
            incumbent_error=4.0,
            challenger_error=3.0,
        )
        for horizon in MPC_FORECAST_HORIZONS
        for sequence in range(4)
    )

    decision = evaluate_forecasts(
        records,
        role_generation=4,
        candidate_generation=9,
        prior_consecutive_wins=1,
        config=EvaluationConfig(required_consecutive_wins=2),
    )

    assert not decision.accepted
    assert decision.consecutive_wins == 0
    assert decision.blockers == tuple(f"absolute-rmse-{horizon.seconds}" for horizon in MPC_FORECAST_HORIZONS)


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
        _origin(99, 100, role_generation=3),
        _origin(100, 100, candidate_generation=10),
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
    completed = _origin(3, 200, phase="coasting")

    assert completed.phase == "coasting"
    assert completed.temperature_band == "middle"
    assert completed.ambient_source is AmbientSource.CONFIGURED
    assert completed.incumbent_error_c == pytest.approx(2.0)
    assert completed.challenger_error_c == pytest.approx(1.0)
