"""Behavioral contracts for production online scheduled-ARX adaptation."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from dataclasses import replace

import numpy as np
import pytest

from common.control_trace import AmbientSource
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
                "alignment_error_c": getattr(self, "alignment_error_c", None),
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
            np.full(horizon_steps, self._biases.get(horizon_steps, self._biases[3]), dtype=np.float64),
            np.zeros((horizon_steps, horizon_steps), dtype=np.float64),
        )


class StateSpaceAffineModel(FixedAffineModel):
    """State-space-shaped fake with independent refresh and cross-arm evidence."""

    def __init__(
        self,
        bias: float = 0.0,
        *,
        digest: str = "state-space",
        cross_arm_offset_c: float = 0.0,
        refresh_on_observe_call: int | None = None,
    ) -> None:
        super().__init__(bias, digest=digest)
        self.cross_arm_offset_c = cross_arm_offset_c
        self.refresh_on_observe_call = refresh_on_observe_call
        self.refreshes = 0

    @property
    def model_kind(self) -> str:
        return "innovation-state-space"

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        self.observe_calls += 1
        update = ModelUpdate(
            observation.temp_c + self.cross_arm_offset_c,
            observation.temp_c,
            -self.cross_arm_offset_c,
            True,
        )
        if self.observe_calls == self.refresh_on_observe_call:
            self.refreshes += 1
        return update

    def track(self, observation: FrameObservation) -> ModelUpdate:
        self.track_calls += 1
        return ModelUpdate(
            observation.temp_c + self.cross_arm_offset_c,
            observation.temp_c,
            -self.cross_arm_offset_c,
            False,
        )

    def snapshot(self) -> dict[str, object]:
        return {
            "schema": "innovation-state-space/v2",
            "model": {
                "poles": [self._pole],
                "steady_gain": self._gain,
                "delay": self._delay,
            },
            "diagnostics": {
                "attempts": [
                    {
                        "poles": [1.5],
                        "steady_gain": -5.0,
                        "delay": 99,
                        "alignment_error_c": 99.0,
                    }
                ]
            },
            "status": {
                "alignment_evidence": "measured",
                "alignment_error_c": 0.0,
                "refreshes": self.refreshes,
            },
        }


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
        observation_sequence=index,
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
    assert completed.phase == "coasting"


def test_completed_origin_keeps_classification_from_its_forecast_frame() -> None:
    manager = OnlineAdaptation(
        FixedAffineModel(),
        FixedAffineModel(),
        AdaptationPolicy(excitation_window=2),
    )
    manager.observe(
        replace(
            frame(0),
            temperature_band="origin-band",
            ambient_source=AmbientSource.MEASURED,
        )
    )
    for index in range(1, 4):
        manager.observe(frame(index))

    completed = next(
        origin for origin in manager.completed_origins if origin.origin_time_s == 20.0 and origin.horizon_steps == 3
    )
    assert completed.temperature_band == "origin-band"
    assert completed.ambient_source is AmbientSource.MEASURED

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


def test_state_space_gates_read_only_the_selected_model_and_canonical_status() -> None:
    manager = OnlineAdaptation(
        FixedAffineModel(bias=1.0, digest="incumbent"),
        StateSpaceAffineModel(bias=0.0, digest="challenger"),
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            required_consecutive_wins=1,
            evaluation_interval_s=300.0,
        ),
    )

    _populate_one_window(manager)
    decision = manager.evaluate_due(at_s=320.0)

    assert decision.promoted
    assert EvaluationRejectionReason.STABILITY not in decision.reasons
    assert EvaluationRejectionReason.GAIN not in decision.reasons
    assert EvaluationRejectionReason.DELAY not in decision.reasons
    assert decision.alignment_error_c == 0.0


@pytest.mark.parametrize(
    ("cross_arm_offset_c", "expected_promotion"),
    [(2.01, False), (2.0, True)],
)
def test_state_space_promotion_requires_latest_cross_arm_output_alignment(
    cross_arm_offset_c: float,
    expected_promotion: bool,
) -> None:
    manager = OnlineAdaptation(
        FixedAffineModel(bias=1.0, digest="incumbent"),
        StateSpaceAffineModel(
            bias=0.0,
            digest="challenger",
            cross_arm_offset_c=cross_arm_offset_c,
        ),
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            required_consecutive_wins=1,
            evaluation_interval_s=300.0,
        ),
    )

    _populate_one_window(manager)
    decision = manager.evaluate_due(at_s=320.0)

    assert decision.alignment_error_c == cross_arm_offset_c
    assert decision.state_aligned is expected_promotion
    assert decision.promoted is expected_promotion
    assert (EvaluationRejectionReason.STATE_ALIGNMENT in decision.reasons) is not expected_promotion


def test_state_space_refresh_defers_cross_arm_alignment_until_next_common_frame() -> None:
    challenger = StateSpaceAffineModel(
        bias=0.0,
        digest="challenger",
        refresh_on_observe_call=16,
    )
    manager = OnlineAdaptation(
        FixedAffineModel(bias=1.0, digest="incumbent"),
        challenger,
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            required_consecutive_wins=1,
            evaluation_interval_s=1.0,
        ),
    )

    _populate_one_window(manager)
    manager.observe(frame(16, temperature=0.0))
    same_frame = manager.evaluate_due(at_s=340.0)

    assert challenger.refreshes == 1
    assert same_frame.alignment_error_c is None
    assert not same_frame.state_aligned
    assert not same_frame.promoted
    assert EvaluationRejectionReason.STATE_ALIGNMENT in same_frame.reasons

    manager.observe(frame(17, temperature=0.0))
    post_refresh = manager.evaluate_due(at_s=360.0)

    assert post_refresh.alignment_error_c == 0.0
    assert post_refresh.state_aligned
    assert EvaluationRejectionReason.STATE_ALIGNMENT not in post_refresh.reasons


def test_state_space_refresh_discards_old_generation_scoring_before_new_evidence() -> None:
    challenger = StateSpaceAffineModel(
        bias=0.0,
        digest="challenger",
        refresh_on_observe_call=19,
    )
    manager = OnlineAdaptation(
        FixedAffineModel(bias=1.0, digest="incumbent"),
        challenger,
        AdaptationPolicy(
            excitation_window=2,
            min_effective_updates=1,
            required_consecutive_wins=2,
            evaluation_interval_s=1.0,
        ),
    )

    _populate_one_window(manager)
    eligible = manager.evaluate_due(at_s=320.0)
    assert eligible.consecutive_wins == 1
    for index in range(16, 19):
        manager.observe(frame(index, temperature=0.0))

    assert not eligible.promoted
    assert manager.consecutive_wins == 1
    assert manager.completed_origins
    assert manager._origins
    assert manager._scores.prediction_count > 0
    assert manager._latest_cross_arm_prediction is not None

    manager.observe(frame(19, temperature=0.0))

    assert challenger.refreshes == 1
    assert manager.completed_origins == ()
    assert manager._origins
    assert all(origin.origin_time_s >= 400.0 for origin in manager._origins)
    assert (
        manager._scores.incumbent_prediction_error,
        manager._scores.candidate_prediction_error,
        manager._scores.prediction_count,
        manager._scores.incumbent_braking_error,
        manager._scores.candidate_braking_error,
        manager._scores.braking_count,
    ) == (0.0, 0.0, 0, 0.0, 0.0, 0)
    assert manager._latest_cross_arm_prediction is None
    assert manager.consecutive_wins == 0
    with pytest.raises(ValueError, match="not a current prospective promotion"):
        manager.prospective_model(eligible.decision_id)
    same_frame = manager.evaluate_due(at_s=400.0)
    assert same_frame.alignment_error_c is None
    assert not same_frame.state_aligned
    assert not same_frame.promoted

    for index in range(20, 36):
        manager.observe(frame(index, temperature=0.0))
    repopulated = manager.evaluate_due(at_s=720.0)

    assert repopulated.completed_origins
    assert all(origin.origin_time_s >= 400.0 for origin in repopulated.completed_origins)
    assert repopulated.state_aligned


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


def test_measured_alignment_uses_an_exact_inclusive_two_degree_gate() -> None:
    """A state-space evidence boundary must neither promote early nor change roles."""
    manager = _winning_manager()
    manager.challenger.alignment_error_c = 2.0
    _populate_one_window(manager)
    accepted = manager.evaluate_due(at_s=300.0)

    assert EvaluationRejectionReason.STATE_ALIGNMENT not in accepted.reasons
    assert accepted.state_aligned
    assert accepted.alignment_error_c == 2.0
    assert manager.consecutive_wins == 1
    assert manager.role_generation == 0

    manager.challenger.alignment_error_c = 2.0 + 1e-12
    _populate_one_window(manager, start_index=16)
    rejected = manager.evaluate_due(at_s=600.0)

    assert EvaluationRejectionReason.STATE_ALIGNMENT in rejected.reasons
    assert not rejected.state_aligned
    assert manager.consecutive_wins == 0
    assert manager.role_generation == 0
