"""Experiment-only state-space shadow contracts at the MPC controller boundary."""

from __future__ import annotations
from types import SimpleNamespace

import numpy as np
from common.control_trace import StateSpaceRefreshPayload

from controller.linear_mpc.contracts import FrameObservation
from controller.mpc import Controller, _DEFAULTS


CYCLE = {"u_min": 0.1, "u_max": 0.9}


class _Estimator:
    def update(self, _q, temperature):
        return np.array([0.0, float(temperature), 0.0])


class _GreyModel:
    def track(self, observation):
        from controller.linear_mpc.contracts import ModelUpdate

        return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, False)

    observe = track

    def affine_prediction(self, horizon_steps, _q_previous, _ambient_future):
        from controller.linear_mpc.contracts import AffinePrediction

        return AffinePrediction(np.full(horizon_steps, 100.0), np.zeros((horizon_steps, horizon_steps)))

    def snapshot(self):
        return {"schema": "grey-box-adapter/v1", "origin": "state-space-shadow-test"}


def _frame(index: int, *, generation: int = 0, continuous: bool = True) -> FrameObservation:
    loads = (0.1, 0.35, 0.7, 0.2, 0.85, 0.5, 0.25, 0.65)
    q = loads[index % len(loads)]
    previous = loads[(index - 1) % len(loads)]
    earlier = loads[(index - 2) % len(loads)]
    return FrameObservation(
        index * 20.0,
        (index + 1) * 20.0,
        80.0 + 12.0 * previous + 4.0 * earlier,
        110.0,
        20.0,
        q,
        q,
        q,
        5.0,
        1.0,
        1.0,
        index,
        "controller",
        False,
        False,
        False,
        False,
        False,
        False,
        continuous,
        generation,
        observation_sequence=index,
    )


def _controller(monkeypatch) -> Controller:
    monkeypatch.setattr(Controller, "_build_for", lambda _self, _cfg, **_kwargs: (_Estimator(), None, None, None))
    monkeypatch.setattr(Controller, "_new_grey_box_model", lambda _self: _GreyModel())
    controller = Controller(
        dict(_DEFAULTS, enable_online_adaptation=True),
        "C",
        dict(CYCLE),
        _online_challenger_kind="state-space",
    )
    controller.set_target(110.0)
    return controller


def test_private_state_space_challenger_shadows_on_the_existing_worker_without_command_authority(monkeypatch):
    controller = _controller(monkeypatch)

    assert controller._online_experiment_active is False
    assert controller._online_challenger_kind == "state-space"
    assert controller._online is not None
    assert controller._online.challenger.snapshot()["schema"] in {
        "innovation-state-space-shadow/v1",
        "innovation-state-space/v2",
    }
    assert controller._online.incumbent.snapshot()["schema"] == "scheduled-arx/v2"

    before = controller._last_combustion_load
    outcomes = [controller.observe_frame(_frame(index)) for index in range(20)]

    assert all(outcome is not None for outcome in outcomes)
    assert controller._last_combustion_load == before
    challenger = controller._online.challenger
    snapshot = challenger.snapshot()
    assert snapshot.get("schema") == "innovation-state-space/v2"
    evidence = controller._state_space_refresh_evidence(challenger)
    assert evidence is not None
    assert evidence["accepted"] is True
    assert evidence["alignment_error_c"] is None
    assert StateSpaceRefreshPayload(**evidence).alignment_error_c is None
    assert snapshot["status"]["last_refresh_time_s"] == 380.0

    for index in range(20, 35):
        controller.observe_frame(_frame(index))
    assert challenger.refresh_attempts == 1


def test_state_space_shadow_win_is_eligible_but_activation_gate_refuses_command_ownership(monkeypatch):
    controller = _controller(monkeypatch)
    assert controller._online is not None
    challenger = controller._online.challenger
    controller._online.evaluate_due = lambda at_s: SimpleNamespace(
        decision_id="state-space-win",
        evaluated_at_s=at_s,
        promoted=True,
        committed=False,
        consecutive_wins=2,
        reasons=(),
        incumbent_prediction_score=2.0,
        candidate_prediction_score=1.0,
        generation=0,
        incumbent_braking_score=2.0,
        candidate_braking_score=1.0,
        sample_count=30,
        prospective_digest="a" * 64,
    )
    controller._online_next_evaluation_s = 0.0

    event = controller._evaluate_online(_frame(0))

    assert event["evaluation"]["promoted"] is True
    assert controller._online.incumbent.snapshot()["schema"] == "scheduled-arx/v2"
    assert controller._online.challenger is challenger
    assert controller._online_last_lifecycle_reason == "experiment-activation-gate"


def test_state_space_shadow_discards_partial_and_fitted_history_at_a_queue_gap(monkeypatch):
    controller = _controller(monkeypatch)
    assert controller._online is not None
    shadow = controller._online.challenger

    for index in range(10):
        controller.observe_frame(_frame(index))
    assert shadow.snapshot()["effective_samples"] == 10
    controller.observe_frame(_frame(10, continuous=False))
    assert shadow.snapshot()["effective_samples"] == 0

    for index in range(11, 30):
        controller.observe_frame(_frame(index))
    assert shadow.snapshot()["schema"] == "innovation-state-space/v2"
    controller.observe_frame(_frame(30, continuous=False))
    assert shadow.snapshot()["schema"] == "innovation-state-space-shadow/v1"
    assert shadow.snapshot()["effective_samples"] == 0


def test_state_space_shadow_rejects_stale_generation_and_queue_gap_like_scheduled_arx(monkeypatch):
    controller = _controller(monkeypatch)

    stale = controller.observe_frame(_frame(0, generation=1))
    gap = controller.observe_frame(_frame(1, continuous=False))

    assert stale["rejection_reasons"] == ("stale-generation",)
    assert gap["eligible"] is False
    assert gap["rejection_reasons"] == ("discontinuity",)


def test_fitted_state_space_shadow_checkpoint_restarts_learning_at_a_new_cook_timestamp(monkeypatch):
    source = _controller(monkeypatch)
    for index in range(20):
        source.observe_frame(_frame(index))
    checkpoint = source.get_model_snapshot()
    assert checkpoint["online_adaptation"]["challenger"]["schema"] == "innovation-state-space/v2"

    restored = _controller(monkeypatch)
    assert restored.restore_model(checkpoint) is True
    assert restored._online.incumbent.snapshot()["schema"] == "scheduled-arx/v2"
    shadow = restored._online.challenger
    assert shadow.snapshot()["schema"] == "innovation-state-space-shadow/v1"
    assert shadow.snapshot()["effective_samples"] == 0

    warmup = [restored.observe_frame(_frame(index)) for index in range(10)]
    assert all("discontinuity" not in outcome["rejection_reasons"] for outcome in warmup)
    assert shadow.snapshot()["effective_samples"] == 10

    outcomes = [restored.observe_frame(_frame(index)) for index in range(10, 20)]

    assert all("discontinuity" not in outcome["rejection_reasons"] for outcome in outcomes)
    assert shadow.snapshot()["schema"] == "innovation-state-space/v2"
