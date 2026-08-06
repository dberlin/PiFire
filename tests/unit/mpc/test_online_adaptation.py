"""Behavioral contracts for production online scheduled-ARX adaptation."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from dataclasses import asdict, replace

import numpy as np
import pytest

from common.control_trace import ControlTraceRecord, ControllerType, TraceEventKind
from controller.runtime.modes.hold import HoldMode
from controller.linear_mpc.adaptation import (
    AdaptationPolicy,
    EvaluationRejectionReason,
    OnlineAdaptation,
    UpdateRejectionReason,
)
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation, ModelUpdate


class FixedAffineModel:
    """Small owned model fake exposing update and forecast decisions."""

    def __init__(self, bias: float = 0.0, *, digest: str = "model") -> None:
        self.bias = bias
        self.digest = digest
        self.observe_calls = 0
        self.track_calls = 0
        self.reset_calls = 0
        self._effective_updates = 30
        self._pole = 0.8
        self._gain = 1.0
        self._delay = 2

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        self.observe_calls += 1
        return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, True)

    def track(self, observation: FrameObservation) -> ModelUpdate:
        self.track_calls += 1
        return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, False)

    def reset_lag_history(self) -> None:
        self.reset_calls += 1

    def affine_prediction(self, horizon_steps: int, q_previous: float, ambient_future: np.ndarray) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(
            np.full(horizon_steps, self.bias, dtype=np.float64),
            np.zeros((horizon_steps, horizon_steps), dtype=np.float64),
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": "fixed-affine/v1",
            "digest": self.digest,
            "status": {
                "steady_gain": self._gain,
                "regions": [{"effective_samples": self._effective_updates, "poles": [self._pole]}],
            },
            "active_delay": self._delay,
        }


class HorizonAffineModel(FixedAffineModel):
    """Forecast fake with independently controlled 60 s and 300 s bias."""

    def __init__(self, biases: dict[int, float], *, digest: str) -> None:
        super().__init__(digest=digest)
        self._biases = biases

    def affine_prediction(self, horizon_steps: int, q_previous: float, ambient_future: np.ndarray) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(
            np.full(horizon_steps, self._biases[horizon_steps], dtype=np.float64),
            np.zeros((horizon_steps, horizon_steps), dtype=np.float64),
        )


def frame(index: int, *, temperature: float | None = None) -> FrameObservation:
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
        temp_c=float(index if temperature is None else temperature),
        setpoint_c=180.0,
        ambient_c=20.0,
        requested_q=0.2 + 0.2 * (index % 2),
        realized_q=0.2 + 0.2 * (index % 2),
        requested_auger_duty=0.2 + 0.2 * (index % 2),
        delivered_on_s=4.0,
        requested_fan_duty=1.0,
        actual_fan_duty=1.0,
        result_revision=0,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda value: replace(value, lid_open=True), UpdateRejectionReason.LID_OPEN),
        (lambda value: replace(value, safety_inhibited=True), UpdateRejectionReason.SAFETY),
        (lambda value: replace(value, manual_override=True), UpdateRejectionReason.MANUAL),
        (lambda value: replace(value, stale=True), UpdateRejectionReason.STALE),
        (lambda value: replace(value, skipped=True), UpdateRejectionReason.SKIPPED_OR_RESET),
        (lambda value: replace(value, output_source="seed"), UpdateRejectionReason.NON_CONTROLLER_SOURCE),
        (lambda value: replace(value, continuous=False), UpdateRejectionReason.DISCONTINUITY),
    ],
)
def test_each_hard_rejection_never_updates_coefficients(mutation, reason) -> None:
    challenger = FixedAffineModel()
    manager = OnlineAdaptation(FixedAffineModel(), challenger, AdaptationPolicy(excitation_window=2))

    outcome = manager.observe(mutation(frame(0)))

    assert outcome.gate.reasons == (reason,)
    assert challenger.observe_calls == 0


def test_unknown_actuation_clears_lag_warmup_without_updating() -> None:
    challenger = FixedAffineModel()
    manager = OnlineAdaptation(FixedAffineModel(), challenger)

    outcome = manager.observe(frame(0), actuation_known=False)

    assert outcome.gate.reasons == (UpdateRejectionReason.UNKNOWN_ACTUATION,)
    assert challenger.observe_calls == 0
    assert challenger.reset_calls == 1
    assert manager.lag_warmup_remaining == manager.policy.max_delay_steps


def test_unexcited_normal_sample_tracks_history_only() -> None:
    incumbent = FixedAffineModel()
    challenger = FixedAffineModel()
    manager = OnlineAdaptation(
        incumbent,
        challenger,
        AdaptationPolicy(excitation_window=3, min_input_variance=1.0),
    )
    assert manager.role_generation == 0

    outcome = manager.observe(frame(0))

    assert outcome.gate.reasons == (UpdateRejectionReason.INSUFFICIENT_EXCITATION,)
    assert incumbent.track_calls == challenger.track_calls == 1
    assert challenger.observe_calls == 0


def test_prequential_origins_align_interval_duty_to_future_temperature() -> None:
    incumbent = FixedAffineModel()
    challenger = FixedAffineModel()
    manager = OnlineAdaptation(
        incumbent,
        challenger,
        AdaptationPolicy(excitation_window=12),
    )

    manager.observe(frame(0, temperature=0.0))
    for index in range(1, 16):
        manager.observe(frame(index, temperature=float(index)))

    completed = manager.completed_origins
    three = next(origin for origin in completed if origin.horizon_steps == 3)
    fifteen = next(origin for origin in completed if origin.horizon_steps == 15)
    assert three.completion_time_s == 80.0
    assert fifteen.completion_time_s == 320.0
    assert three.origin_time_s == 20.0
    assert three.observed_temperature_c == 3.0
    assert fifteen.origin_time_s == 20.0
    assert fifteen.observed_temperature_c == 15.0
    assert three.observed_temperature_c != 0.0
    assert fifteen.observed_temperature_c != 0.0


def test_matured_origin_retains_braking_status_from_forecast_window() -> None:
    manager = OnlineAdaptation(
        FixedAffineModel(),
        FixedAffineModel(),
        AdaptationPolicy(excitation_window=2),
    )

    manager.observe(frame(0), braking=True)
    for index in range(1, 4):
        manager.observe(frame(index), braking=False)

    completed = next(
        origin for origin in manager.completed_origins if origin.origin_time_s == 20.0 and origin.horizon_steps == 3
    )
    assert completed.completion_time_s == 80.0
    assert completed.braking is True


def test_candidate_must_win_each_horizon_not_only_the_pooled_score() -> None:
    manager = OnlineAdaptation(
        HorizonAffineModel({3: 5.0, 15: 1.0}, digest="incumbent"),
        HorizonAffineModel({3: 0.0, 15: 2.0}, digest="challenger"),
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            required_consecutive_wins=1,
            evaluation_interval_s=300.0,
        ),
    )

    for index in range(16):
        manager.observe(frame(index, temperature=0.0))

    decision = manager.evaluate_due(at_s=320.0)
    scores = {score.horizon_steps: score for score in decision.horizon_scores}

    assert not decision.promoted
    assert EvaluationRejectionReason.PREDICTION in decision.reasons
    assert scores[3].challenger_rmse_c < scores[3].incumbent_rmse_c
    assert scores[15].challenger_rmse_c > scores[15].incumbent_rmse_c
    assert scores[3].sample_count > 0
    assert scores[15].sample_count > 0


def _winning_manager() -> OnlineAdaptation:
    return OnlineAdaptation(
        FixedAffineModel(bias=1.0, digest="incumbent"),
        FixedAffineModel(bias=0.0, digest="challenger"),
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            evaluation_interval_s=300.0,
        ),
    )


def _populate_one_window(manager: OnlineAdaptation, start_index: int = 0) -> None:
    for index in range(start_index, start_index + 16):
        manager.observe(
            frame(index, temperature=0.0),
            braking=index > start_index and index % 2 == 0,
        )


def test_evaluation_payload_round_trips_exact_coordinator_audit_evidence() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    decision = manager.evaluate_due(at_s=320.0)

    payload = HoldMode._model_evaluation_payload(asdict(decision))

    assert payload is not None
    record = ControlTraceRecord(
        ts_ms=320_000,
        session_id="adaptation-evidence",
        controller=ControllerType.MPC,
        event_kind=TraceEventKind.MODEL_EVALUATION,
        payload=payload,
    )
    restored = ControlTraceRecord.model_validate_json(record.model_dump_json()).payload

    assert restored.completed_origins == payload.completed_origins
    assert [
        (
            origin.origin_time_ms,
            origin.completion_time_ms,
            origin.horizon_steps,
            origin.generation,
            origin.observed_temperature_c,
            origin.incumbent_error_c,
            origin.challenger_error_c,
            origin.braking,
        )
        for origin in restored.completed_origins
    ] == [
        (
            int(origin.origin_time_s * 1_000),
            int(origin.completion_time_s * 1_000),
            origin.horizon_steps,
            origin.generation,
            origin.observed_temperature_c,
            origin.incumbent_error_c,
            origin.challenger_error_c,
            origin.braking,
        )
        for origin in decision.completed_origins
    ]
    assert restored.window_start_ms == min(origin.origin_time_s * 1_000 for origin in decision.completed_origins)
    assert restored.window_end_ms == max(origin.completion_time_s * 1_000 for origin in decision.completed_origins)
    assert restored.sample_count == len(restored.completed_origins) == decision.sample_count
    assert restored.incumbent_digest == decision.incumbent_digest == manager.model_digest(manager.incumbent)
    assert restored.challenger_digest == decision.challenger_digest == manager.model_digest(manager.challenger)
    assert restored.evaluation_duration_ms == decision.evaluation_duration_ms
    assert {score.horizon_steps: score.sample_count for score in restored.horizon_scores} == {
        horizon_steps: sum(origin.horizon_steps == horizon_steps for origin in decision.completed_origins)
        for horizon_steps in (3, 15)
    }


def test_promotion_requires_two_wins_and_prospective_commit() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    first = manager.evaluate_due(at_s=300.0)
    assert not first.promoted and first.consecutive_wins == 1
    _populate_one_window(manager, start_index=16)
    second = manager.evaluate_due(at_s=600.0)
    assert second.promoted and second.consecutive_wins == 2
    assert manager.role_generation == 0
    manager.prospective_model(second.decision_id)
    solve = SimpleNamespace(objective=0.0, kkt_residual=0.0)
    assert manager.commit_promotion(second.decision_id, solve)
    assert manager.role_generation == 1


@pytest.mark.parametrize(
    ("attribute", "value", "reason"),
    [
        ("_pole", 1.0, EvaluationRejectionReason.STABILITY),
        ("_gain", -1.0, EvaluationRejectionReason.GAIN),
        ("_delay", 16, EvaluationRejectionReason.DELAY),
        ("_effective_updates", 0, EvaluationRejectionReason.SAMPLES),
    ],
)
def test_model_promotion_gates_are_independent(attribute, value, reason) -> None:
    manager = _winning_manager()
    if attribute == "_effective_updates":
        manager._effective_updates = value
    else:
        setattr(manager.challenger, attribute, value)

    decision = manager.evaluate_due(at_s=300.0)

    assert reason in decision.reasons
    assert not decision.promoted


def test_prediction_braking_continuity_and_prospective_gates_are_independent() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    manager._scores.candidate_prediction_error = (
        manager._scores.incumbent_prediction_error + manager._scores.prediction_count
    )
    decision = manager.evaluate_due(at_s=300.0)
    assert EvaluationRejectionReason.PREDICTION in decision.reasons

    manager = _winning_manager()
    _populate_one_window(manager)
    manager._scores.candidate_braking_error = manager._scores.incumbent_braking_error + manager._scores.braking_count
    decision = manager.evaluate_due(at_s=300.0)
    assert EvaluationRejectionReason.BRAKING in decision.reasons

    manager = _winning_manager()
    _populate_one_window(manager)
    manager._scores.continuous = False
    decision = manager.evaluate_due(at_s=300.0)
    assert EvaluationRejectionReason.CONTINUITY in decision.reasons

    manager = _winning_manager()
    _populate_one_window(manager)
    manager.evaluate_due(at_s=300.0)
    _populate_one_window(manager, start_index=16)
    decision = manager.evaluate_due(at_s=600.0)
    manager.prospective_model(decision.decision_id)
    manager.reject_prospective(decision.decision_id)
    assert manager.role_generation == 0
    assert manager.consecutive_wins == 0


def test_stale_generation_cannot_promote() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    manager.evaluate_due(at_s=300.0)
    _populate_one_window(manager, start_index=16)
    decision = manager.evaluate_due(at_s=600.0)
    manager.prospective_model(decision.decision_id)
    manager._role_generation += 1

    assert not manager.commit_promotion(
        decision.decision_id,
        SimpleNamespace(objective=0.0, kkt_residual=0.0),
    )


def test_rollback_restores_exact_digest_and_advances_generation() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    manager.evaluate_due(at_s=300.0)
    _populate_one_window(manager, start_index=16)
    decision = manager.evaluate_due(at_s=600.0)
    manager.prospective_model(decision.decision_id)
    manager.commit_promotion(
        decision.decision_id,
        SimpleNamespace(objective=0.0, kkt_residual=0.0),
    )

    assert manager.rollback()
    assert manager.model_digest(manager.incumbent) == manager.last_rollback_digest
    assert manager.role_generation == 2
    restored = OnlineAdaptation.from_snapshot(
        manager.snapshot(),
        model_loader=lambda payload: FixedAffineModel(
            bias=0.0,
            digest=str(payload["digest"]),
        ),
    )
    assert restored.last_rollback_digest == restored.model_digest(restored.incumbent)


def test_rollback_after_a_second_promotion_rewarms_the_restored_prior_arx() -> None:
    first_incumbent = FixedAffineModel(bias=1.0, digest="grey-box")
    first_challenger = FixedAffineModel(bias=0.0, digest="first-arx")
    manager = OnlineAdaptation(
        first_incumbent,
        first_challenger,
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            evaluation_interval_s=300.0,
            required_consecutive_wins=1,
        ),
    )

    _populate_one_window(manager)
    first = manager.evaluate_due(at_s=300.0)
    assert first.promoted
    assert manager.commit_promotion(first.decision_id, SimpleNamespace(objective=0.0, kkt_residual=0.0))
    restored_on_later_rollback = manager.incumbent
    manager.incumbent.bias = 1.0
    manager.challenger.bias = 0.0

    _populate_one_window(manager, start_index=16)
    second = manager.evaluate_due(at_s=600.0)
    assert second.promoted
    assert manager.commit_promotion(second.decision_id, SimpleNamespace(objective=0.0, kkt_residual=0.0))
    assert manager.previous_incumbent_digest == manager.model_digest(restored_on_later_rollback)

    assert manager.rollback()
    assert manager.model_digest(manager.incumbent) == manager.model_digest(restored_on_later_rollback)
    assert manager.lag_warmup_remaining == manager.policy.max_delay_steps
    assert manager.incumbent.reset_calls == 1
    assert manager.challenger.reset_calls == 1


def test_snapshot_excludes_partial_discontinuous_origins() -> None:
    manager = _winning_manager()
    manager.observe(frame(0))
    manager.observe(replace(frame(1), continuous=False))
    snapshot = manager.snapshot()

    assert snapshot["partial_origins"] == []


def test_snapshot_restore_keeps_only_bounded_durable_state() -> None:
    manager = _winning_manager()
    _populate_one_window(manager)
    snapshot = manager.snapshot()

    restored = OnlineAdaptation.from_snapshot(
        snapshot,
        model_loader=lambda payload: FixedAffineModel(
            bias=0.0,
            digest=str(payload["digest"]),
        ),
    )

    assert restored.role_generation == manager.role_generation
    assert restored.snapshot()["partial_origins"] == []


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("score_aggregate", "candidate_prediction_error"), float("nan")),
        (("score_aggregate", "prediction_count"), -1),
        (("score_aggregate", "continuous"), 1),
        (("lag_warmup_remaining",), 16),
        (("excitation",), [0.1] * 13),
        (("previous_incumbent_digest",), "mismatch"),
        (("last_rollback_digest",), ""),
        (("last_rollback_digest",), 1),
    ],
)
def test_snapshot_rejects_malformed_bounded_state(path, value) -> None:
    manager = _winning_manager()
    snapshot = deepcopy(manager.snapshot())
    target = snapshot
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError):
        OnlineAdaptation.from_snapshot(
            snapshot,
            model_loader=lambda payload: FixedAffineModel(
                bias=0.0,
                digest=str(payload["digest"]),
            ),
        )
