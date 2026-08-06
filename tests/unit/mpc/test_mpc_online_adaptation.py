"""Controller-level contracts for opt-in online scheduled-ARX adaptation."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
import re
from types import SimpleNamespace

import numpy as np
import pytest

from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.adaptation import EvaluationRejectionReason
from controller.linear_mpc.adaptation import OnlineAdaptation
from controller.applied_output import AppliedOutput, OutputSource
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation, ModelUpdate
from controller.mpc import Controller, _DEFAULTS


CYCLE = {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}

ADAPTATION_STATUS_KEYS = {
    "enabled",
    "active_model_kind",
    "role_generation",
    "eligible_updates",
    "rejected_updates",
    "current_rejection_reason",
    "active_delay",
    "effective_samples",
    "last_evaluation_s",
    "last_evaluation_outcome",
    "incumbent_prediction_score",
    "candidate_prediction_score",
    "promotion_count",
    "rollback_count",
    "learner_duration_seconds",
    "evaluation_duration_seconds",
    "linear_solve_duration_seconds",
}


def _controller(*, online: bool, **overrides):
    controller = Controller(dict(_DEFAULTS, enable_online_adaptation=online, **overrides), "C", dict(CYCLE))
    controller.set_target(110.0)
    return controller


def _frame(index: int, *, generation: int = 0, realized_q: float | None = None) -> FrameObservation:
    return FrameObservation(
        frame_start_s=float(index * 20),
        frame_end_s=float((index + 1) * 20),
        temp_c=100.0 + index,
        setpoint_c=110.0,
        ambient_c=20.0,
        requested_q=0.2 + 0.2 * (index % 2),
        realized_q=0.2 + 0.2 * (index % 2) if realized_q is None else realized_q,
        requested_auger_duty=0.2 + 0.2 * (index % 2),
        delivered_on_s=5.0,
        requested_fan_duty=1.0,
        actual_fan_duty=1.0,
        result_revision=index,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=generation,
    )


def test_opt_out_is_identical_to_explicit_false_across_commands_diagnostics_and_snapshots():
    implicit_config = dict(_DEFAULTS)
    implicit_config.pop("enable_online_adaptation")
    implicit = Controller(implicit_config, "C", dict(CYCLE))
    explicit = Controller(dict(_DEFAULTS, enable_online_adaptation=False), "C", dict(CYCLE))
    for controller in (implicit, explicit):
        controller.set_target(110.0)
        controller._adopt_model(
            {key: controller.cfg[key] for key in controller._MODEL_PARAM_KEYS},
            rmse=2.1,
            samples=1730,
            band_c=(40.0, 232.0),
        )

    for index, (temperature, ratio) in enumerate(((85.0, 0.2), (95.0, 0.45), (105.0, 0.3))):
        applied = AppliedOutput(
            ratio=ratio,
            source=OutputSource.CONTROLLER,
            timestamp=float(index * 20),
            requested=ratio,
        )
        implicit.set_output(applied)
        explicit.set_output(applied)
        assert implicit.update(temperature) == explicit.update(temperature)

        implicit_trace = asdict(implicit.trace_diagnostics())
        explicit_trace = asdict(explicit.trace_diagnostics())
        for timing_key in ("solve_start_monotonic", "solve_end_monotonic", "solve_duration_seconds"):
            implicit_trace.pop(timing_key)
            explicit_trace.pop(timing_key)
        assert implicit_trace == explicit_trace

    assert implicit.observe_frame(_frame(0)) is None
    assert explicit.observe_frame(_frame(0)) is None
    assert implicit._online is explicit._online is None
    assert json.dumps(implicit.get_model_snapshot(), allow_nan=False) == json.dumps(
        explicit.get_model_snapshot(), allow_nan=False
    )
    assert implicit.get_status()["adaptation"] == explicit.get_status()["adaptation"]
    assert set(implicit.get_status()["adaptation"]) == ADAPTATION_STATUS_KEYS
    json.dumps(implicit.get_status(), allow_nan=False)



def test_bootstrap_exposes_only_the_typed_realized_frame_outcome_and_keeps_grey_box_authority():
    controller = _controller(online=True)
    outcome = controller.observe_frame(_frame(0))

    assert set(outcome) == {
        "role_generation",
        "eligible",
        "rejection_reasons",
        "input_variance",
        "input_levels",
        "incumbent_innovation_c",
        "challenger_innovation_c",
        "effective_updates",
        "model_digest",
    }
    assert outcome["eligible"] is (outcome["rejection_reasons"] == ())
    assert outcome["role_generation"] == 0
    assert outcome["input_variance"] >= 0.0
    assert outcome["input_levels"] >= 0
    assert re.fullmatch(r"[0-9a-f]{64}", outcome["model_digest"])
    if outcome["eligible"]:
        assert outcome["incumbent_innovation_c"] is not None
        assert outcome["challenger_innovation_c"] is not None
    assert controller.get_status()["adaptation"]["active_model_kind"] == "grey-box"
    status = controller.get_status()["adaptation"]
    assert set(status) == ADAPTATION_STATUS_KEYS
    assert status["enabled"] is True
    assert status["effective_samples"] >= 0.0

    stale = controller.observe_frame(_frame(1, generation=1))
    assert stale["eligible"] is False
    assert stale["rejection_reasons"] == ("stale-generation",)
    stale_status = controller.get_status()["adaptation"]
    assert set(stale_status) == ADAPTATION_STATUS_KEYS
    assert stale_status["current_rejection_reason"] == "stale-generation"


class _Estimator:
    def update(self, _q, temperature):
        return np.array([0.0, float(temperature), 0.0])


class _GreyModel:
    def snapshot(self):
        return {"schema": "grey-box-adapter/v1", "origin": "test"}



class _GreyPolicyNet:
    def firing_rate_raw(self, _x_hat, _previous_load, _setpoint_c):
        return 0.5

class _Candidate(ScheduledARX):
    def affine_prediction(self, horizon_steps, q_previous, ambient_future):
        del q_previous, ambient_future
        return AffinePrediction(np.full(horizon_steps, 100.0), np.zeros((horizon_steps, horizon_steps)))


class _Coordinator:
    def __init__(self, incumbent, challenger):
        self.incumbent = incumbent
        self.challenger = challenger
        self._fallback = incumbent
        self.role_generation = 0
        self._effective_updates = 8
        self.policy = SimpleNamespace(evaluation_interval_s=1.0)
        self.evaluate_calls = 0
        self.observations = []

    @property
    def effective_updates(self):
        return self._effective_updates

    def observe(self, observation, **_kwargs):
        self.observations.append(observation)
        gate = SimpleNamespace(permitted=True, reasons=(), input_variance=0.04, input_levels=2)
        update = ModelUpdate(100.0, 100.0, 0.0, True)
        return SimpleNamespace(gate=gate, incumbent=update, challenger=update, effective_updates=8)

    def evaluate_due(self, at_s):
        self.evaluate_calls += 1
        promoted = self.evaluate_calls == 2
        return SimpleNamespace(
            decision_id=f"decision-{self.evaluate_calls}",
            evaluated_at_s=at_s,
            promoted=promoted,
            committed=False,
            consecutive_wins=self.evaluate_calls,
            reasons=(),
            incumbent_prediction_score=2.0,
            candidate_prediction_score=1.0,
            generation=self.role_generation,
            incumbent_braking_score=2.0,
            candidate_braking_score=1.0,
            sample_count=8,
            prospective_digest="a" * 64 if promoted else None,
        )

    def prospective_model(self, decision_id):
        assert decision_id == "decision-2"
        return self.challenger

    def commit_promotion(self, decision_id, solve):
        assert decision_id == "decision-2"
        assert solve.sequence_q[0] == pytest.approx(0.4)
        self.incumbent = self.challenger
        self.role_generation += 1
        return True

    def reject_prospective(self, _decision_id, _reason):
        return None

    def rollback(self):
        if not isinstance(self.incumbent, ScheduledARX):
            return False
        self.incumbent = self._fallback
        self.role_generation += 1
        return True

    def snapshot(self):
        return {
            "schema": "online-adaptation/v1",
            "role_generation": self.role_generation,
            "effective_updates": self._effective_updates,
        }


class _LinearPolicy:
    def solve(self, prediction, *, setpoint_c, q_previous, equilibrium_q):
        assert prediction.free_output_c.shape == (3,)
        assert setpoint_c == pytest.approx(110.0)
        assert equilibrium_q == pytest.approx(0.0)
        return SimpleNamespace(
            sequence_q=np.array([0.4, 0.4, 0.4]),
            objective=1.0,
            kkt_residual=0.0,
            iterations=1,
            hessian_condition=1.0,
        )


class _LinearConfig:
    horizon_steps = 3
    tolerance = 1e-6


@pytest.fixture
def scripted_controller(monkeypatch):
    monkeypatch.setattr(
        Controller,
        "_build_for",
        lambda self, cfg, **_kwargs: (_Estimator(), None, None, None),
    )
    monkeypatch.setattr(Controller, "_new_grey_box_model", lambda self: _GreyModel())
    monkeypatch.setattr(
        Controller,
        "_new_scheduled_arx",
        lambda self: _Candidate(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3))),
    )
    monkeypatch.setattr(
        Controller,
        "_new_online_adaptation",
        lambda self, incumbent, challenger: _Coordinator(incumbent, challenger),
    )
    monkeypatch.setattr(Controller, "_new_linear_policy", lambda self: (_LinearConfig(), _LinearPolicy()))
    controller = _controller(online=True, n_horizon=3)
    controller._x_hat = np.array([0.0])
    return controller


def test_online_linear_policy_uses_the_fixed_600_second_bakeoff_configuration():
    controller = _controller(online=True, n_horizon=3, Q_w=99.0, R_dQ=0.0001)
    config = controller._linear_config

    assert config.horizon_steps == 30
    assert config.temperature_weight == pytest.approx(1.0)
    assert config.terminal_weight == pytest.approx(4.0)
    assert config.move_weight == pytest.approx(0.05)
    assert config.tolerance == pytest.approx(1e-3)


def test_controller_promotes_only_after_two_evaluation_boundaries_and_then_uses_the_existing_allocator(
    scripted_controller,
):
    controller = scripted_controller
    controller._net = _GreyPolicyNet()
    measured = AppliedOutput(0.45, OutputSource.CONTROLLER, timestamp=0.0, requested=0.45)
    controller.set_output(measured)
    grey_command = controller.update(100.0)
    assert grey_command == {"cycle_ratio": pytest.approx(0.45), "fan": {"duty": None}}
    assert controller.trace_diagnostics().policy_kind == "net"

    revision_before = controller.get_model_snapshot()["revision"]
    bootstrap = controller.observe_frame(_frame(0, realized_q=0.5))
    first_evaluation = controller.observe_frame(_frame(1, realized_q=0.5))
    assert "evaluation" not in bootstrap
    assert first_evaluation["evaluation"]["consecutive_wins"] == 1
    assert first_evaluation["evaluation"]["rejection_reasons"] == ()
    assert first_evaluation["evaluation"]["prospective_digest"] is None
    assert controller._online.observations[0].realized_q == pytest.approx(0.5)
    assert controller.get_status()["adaptation"]["active_model_kind"] == "grey-box"

    controller._last_combustion_load = 0.37
    promoted = controller.observe_frame(_frame(2, realized_q=0.5))
    promoted_snapshot = controller.get_model_snapshot()
    lifecycle = promoted["lifecycle"]
    assert lifecycle["event"] == "adopt"
    assert lifecycle["detail"] == "promotion"
    assert lifecycle["role_generation"] == 1
    assert lifecycle["model_schema"] == "scheduled-arx/v2"
    assert re.fullmatch(r"[0-9a-f]{64}", lifecycle["snapshot_digest"])
    assert promoted_snapshot["revision"] == revision_before + 3

    assert promoted["role_generation"] == 0  # completed by the old role
    assert controller.get_status()["adaptation"]["role_generation"] == 1
    assert controller.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert controller.get_status()["last_combustion_load"] == pytest.approx(0.37)
    adaptation = controller.get_status()["adaptation"]
    assert set(adaptation) == ADAPTATION_STATUS_KEYS
    assert adaptation["effective_samples"] == 8
    assert adaptation["last_evaluation_outcome"]["committed"] is True
    assert controller.get_model_snapshot()["online_adaptation"]["last_evaluation"]["committed"] is True

    active_command = controller.update(100.0)
    assert active_command == {"cycle_ratio": pytest.approx(0.36), "fan": {"duty": None}}


def test_repeated_active_failure_holds_once_then_rolls_back_to_the_owned_grey_box(scripted_controller):
    controller = scripted_controller
    controller.observe_frame(_frame(0))
    controller.observe_frame(_frame(1))
    controller.observe_frame(_frame(2))
    controller._last_combustion_load = 0.4

    class _FailingActivePolicy:
        def solve(self, *_args, **_kwargs):
            raise RuntimeError("active linear solve failed")

    controller._linear_policy = _FailingActivePolicy()
    revision_before_rollback = controller.get_model_snapshot()["revision"]

    first = controller.update(100.0)
    assert controller.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert controller.get_status()["adaptation"]["rollback_count"] == 0

    second = controller.update(100.0)
    status = controller.get_status()["adaptation"]
    assert first == second
    assert status["active_model_kind"] == "grey-box"
    assert status["rollback_count"] == 1
    assert status["role_generation"] == 2
    json.dumps(controller.get_status(), allow_nan=False)
    assert controller.trace_diagnostics().model_lifecycle["detail"] == "repeated-solve-failure"
    assert controller.get_model_snapshot()["revision"] == revision_before_rollback + 1


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("nonfinite-forecast", "non-finite-forecast"),
        ("invalid-certificate", "invalid-kkt-certificate"),
    ],
)
def test_active_safety_failures_roll_back_once_with_the_exact_lifecycle_reason(
    scripted_controller, failure, reason
):
    controller = scripted_controller
    controller.observe_frame(_frame(0))
    controller.observe_frame(_frame(1))
    controller.observe_frame(_frame(2))

    if failure == "nonfinite-forecast":
        controller._online.incumbent.affine_prediction = lambda horizon_steps, *_args: AffinePrediction(
            np.full(horizon_steps, np.nan), np.zeros((horizon_steps, horizon_steps))
        )
    else:
        controller._online.incumbent = _Candidate(
            ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3))
        )

        class _InvalidCertificatePolicy:
            def solve(self, *_args, **_kwargs):
                return SimpleNamespace(
                    sequence_q=np.array([0.4, 0.4, 0.4]),
                    objective=1.0,
                    kkt_residual=1.0,
                    iterations=1,
                    hessian_condition=1.0,
                )

        controller._linear_policy = _InvalidCertificatePolicy()

    controller.update(100.0)
    adaptation = controller.get_status()["adaptation"]
    assert adaptation["active_model_kind"] == "grey-box"
    assert adaptation["rollback_count"] == 1
    assert controller.trace_diagnostics().model_lifecycle["detail"] == reason


def test_active_real_scheduled_arx_snapshot_restores_prediction_command_and_rollback_owner():
    source = _controller(online=True)
    frames = tuple(_frame(index) for index in range(80))
    active = ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))
    active.fit(frames)
    for index in range(80, 92):
        active.observe(_frame(index))

    # A real coordinator needs its lag and excitation windows before it may
    # retain forecast origins.  The alternating contiguous sequence leaves
    # recent horizons incomplete at the snapshot boundary.
    outcomes = [
        source._online.observe(_frame(index), ambient_future=np.full(15, 20.0))
        for index in range(92, 116)
    ]
    assert any(outcome.gate.permitted for outcome in outcomes)
    assert source._online._origins
    fallback = source._online.incumbent
    source._online._previous_incumbent = fallback
    source._online._previous_incumbent_snapshot = fallback.snapshot()
    source._online._previous_incumbent_digest = OnlineAdaptation.model_digest(fallback)
    source._online.incumbent = active
    source._online.challenger = fallback
    source._online._role_generation = 3
    source._online._consecutive_wins = 2
    source._model_revision = 41
    source._applied_combustion_load = 0.4

    snapshot = source.get_model_snapshot()
    restored = _controller(online=True)
    assert restored.restore_model(snapshot) is True
    restored._applied_combustion_load = 0.4

    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert restored.get_status()["adaptation"]["role_generation"] == 3
    assert restored._online.consecutive_wins == 2
    assert restored._online.previous_incumbent_digest == OnlineAdaptation.model_digest(fallback)
    assert restored._online._origins == []

    source_prediction = source._online.incumbent.affine_prediction(4, 0.4, np.full(4, 20.0))
    restored_prediction = restored._online.incumbent.affine_prediction(4, 0.4, np.full(4, 20.0))
    np.testing.assert_allclose(restored_prediction.free_output_c, source_prediction.free_output_c, atol=1e-12)
    np.testing.assert_allclose(
        restored_prediction.input_response_c, source_prediction.input_response_c, atol=1e-12
    )

    # Persistence intentionally drops partial scoring origins.  Make both
    # live coordinators start the next real frame from that persisted boundary.
    source._online._origins.clear()
    post_restore_frame = replace(_frame(116), role_generation=3)
    assert source.observe_frame(post_restore_frame) == restored.observe_frame(post_restore_frame)
    assert source.get_model_snapshot()["online_adaptation"] == restored.get_model_snapshot()["online_adaptation"]
    assert restored.update(100.0) == source.update(100.0)

    restored.refit_from_cook([])
    assert restored.get_model_snapshot()["revision"] == 42
