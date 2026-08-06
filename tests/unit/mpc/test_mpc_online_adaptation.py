"""Controller-level contracts for opt-in online scheduled-ARX adaptation."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
import re
from types import SimpleNamespace

import numpy as np
import pytest

from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.adaptation import AdaptationPolicy
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


class _SnapshotGreyModel:
    """A frozen, deliberately inferior grey-box origin with a restorable payload."""

    def track(self, observation):
        return ModelUpdate(90.0, observation.temp_c, observation.temp_c - 90.0, False)

    observe = track

    def affine_prediction(self, horizon_steps, q_previous, ambient_future):
        del q_previous, ambient_future
        return AffinePrediction(np.full(horizon_steps, 90.0), np.zeros((horizon_steps, horizon_steps)))

    def snapshot(self):
        return {
            "schema": "grey-box-adapter/v1",
            "state": [90.0],
            "transition": [[0.0]],
            "q_gain": [0.0],
            "ambient_gain": [0.0],
            "affine_offset": [90.0],
            "radiation_constant_gain": [0.0],
            "temperature_index": 0,
            "radiation_sigma": 0.0,
            "radiation_slope": 0.0,
            "chamber_origin_c": 90.0,
        }


def _seeded_candidate() -> _Candidate:
    model = _Candidate(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=1e-6))
    model.fit(tuple(replace(_frame(index), temp_c=100.0) for index in range(80)))
    theta = np.asarray((0.2, 0.1, 0.3, 0.1, 0.1, 0.0), dtype=np.float64)
    for candidate in model._candidates.values():
        for region in candidate.regions:
            region.theta = theta.copy()
            information = region.information_factor
            region.normal_rhs = information.T @ information @ theta
    return model


def _constant_prediction(temperature_c):
    def predict(horizon_steps, q_previous, ambient_future):
        del q_previous, ambient_future
        return AffinePrediction(np.full(horizon_steps, temperature_c), np.zeros((horizon_steps, horizon_steps)))

    return predict


def _controller_after_first_arx_promotion(monkeypatch):
    monkeypatch.setattr(Controller, "_new_grey_box_model", lambda _self: _SnapshotGreyModel())
    controller = _controller(online=True, n_horizon=3)
    controller._online = OnlineAdaptation(
        _SnapshotGreyModel(),
        _seeded_candidate(),
        AdaptationPolicy(min_effective_updates=0, required_consecutive_wins=1, evaluation_interval_s=1_000_000.0),
        accepted_sources=("controller",),
    )
    controller._linear_config = _LinearConfig()
    controller._linear_policy = _LinearPolicy()
    controller._x_hat = np.asarray([0.0])

    latest = None
    for index in range(80, 96):
        latest = replace(_frame(index), temp_c=100.0)
        controller.observe_frame(latest)
    assert latest is not None
    controller._online_next_evaluation_s = latest.frame_end_s
    event = controller._evaluate_online(latest)
    assert event["lifecycle"]["detail"] == "promotion"
    return controller


class _Coordinator:
    def __init__(self, incumbent, challenger):
        self.incumbent = incumbent
        self.challenger = challenger
        self._fallback = incumbent
        self.role_generation = 0
        self._effective_updates = 8
        self.policy = SimpleNamespace(evaluation_interval_s=1.0)
        self._lag_warmup_remaining = 0
        self.evaluate_calls = 0
        self.observations = []

    @property
    def lag_warmup_remaining(self):
        return self._lag_warmup_remaining

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
def test_active_safety_failures_roll_back_once_with_the_exact_lifecycle_reason(scripted_controller, failure, reason):
    controller = scripted_controller
    controller.observe_frame(_frame(0))
    controller.observe_frame(_frame(1))
    controller.observe_frame(_frame(2))

    if failure == "nonfinite-forecast":
        controller._online.incumbent.affine_prediction = lambda horizon_steps, *_args: AffinePrediction(
            np.full(horizon_steps, np.nan), np.zeros((horizon_steps, horizon_steps))
        )
    else:
        controller._online.incumbent = _Candidate(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))

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


def test_active_scheduled_arx_restart_holds_default_five_second_solves_until_lag_warmup_completes():
    source = _controller(online=True)
    frames = tuple(_frame(index) for index in range(80))
    active = ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3)))
    active.fit(frames)
    for index in range(80, 92):
        active.observe(_frame(index))

    outcomes = [source._online.observe(_frame(index), ambient_future=np.full(15, 20.0)) for index in range(92, 116)]
    assert any(outcome.gate.permitted for outcome in outcomes)
    fallback = source._online.incumbent
    source._online._previous_incumbent = fallback
    source._online._previous_incumbent_snapshot = fallback.snapshot()
    source._online._previous_incumbent_digest = OnlineAdaptation.model_digest(fallback)
    source._online.incumbent = active
    source._online.challenger = _seeded_candidate()
    source._online._role_generation = 3
    source._online._consecutive_wins = 2
    source._model_revision = 41
    source._applied_combustion_load = 0.4

    snapshot = source.get_model_snapshot()
    ownership = snapshot["online_adaptation"]
    rollback_owner = ownership["previous_incumbent_digest"]
    assert ownership["incumbent"]["schema"] == "scheduled-arx/v2"
    assert ownership["challenger"]["schema"] == "scheduled-arx/v2"
    assert ownership["previous_incumbent"]["schema"] == "grey-box-adapter/v1"
    restored = _controller(online=True)
    assert restored.restore_model(snapshot) is True
    restored._applied_combustion_load = 0.4

    restored_status = restored.get_status()["adaptation"]
    assert restored.cfg["control_period"] == pytest.approx(5.0)
    assert restored_status["active_model_kind"] == "scheduled-arx"
    assert restored_status["role_generation"] == 3
    assert restored._online.consecutive_wins == 2
    assert restored._online.previous_incumbent_digest == rollback_owner

    with pytest.raises(RuntimeError, match="lag history"):
        restored._online.incumbent.affine_prediction(4, 0.4, np.full(4, 20.0))

    fresh_outcomes = []
    for index in range(116, 132):
        for _ in range(4):
            command = restored.update(100.0)
            assert 0.0 <= command["cycle_ratio"] <= CYCLE["u_max"]
            status = restored.get_status()
            assert status["policy_failures"] == 0
            assert status["adaptation"]["active_model_kind"] == "scheduled-arx"
            assert status["adaptation"]["rollback_count"] == 0
        fresh_outcomes.append(restored.observe_frame(replace(_frame(index), role_generation=3)))

    assert fresh_outcomes[-1]["eligible"] is True
    prediction = restored._online.incumbent.affine_prediction(4, 0.4, np.full(4, 20.0))
    assert np.isfinite(prediction.free_output_c).all()
    assert np.isfinite(prediction.input_response_c).all()
    resumed = restored.update(100.0)
    assert 0.0 <= resumed["cycle_ratio"] <= CYCLE["u_max"]
    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert restored.get_status()["adaptation"]["role_generation"] == 3
    assert restored.get_status()["adaptation"]["rollback_count"] == 0
    assert restored._online.previous_incumbent_digest == rollback_owner


def test_promoted_arx_keeps_a_distinct_learning_challenger_and_can_promote_again(monkeypatch):
    controller = _controller_after_first_arx_promotion(monkeypatch)
    coordinator = controller._online
    first_incumbent = coordinator.incumbent
    first_challenger = coordinator.challenger
    first_snapshot = coordinator.snapshot()

    assert controller.get_status()["adaptation"]["promotion_count"] == 1
    assert isinstance(first_incumbent, ScheduledARX)
    assert isinstance(first_challenger, ScheduledARX)
    assert first_challenger is not first_incumbent
    assert first_snapshot["previous_incumbent"]["schema"] == "grey-box-adapter/v1"
    assert first_snapshot["previous_incumbent_digest"] == OnlineAdaptation.model_digest(_SnapshotGreyModel())

    incumbent_candidates = first_incumbent.snapshot()["candidates"]
    challenger_candidates = first_challenger.snapshot()["candidates"]
    monkeypatch.setattr(first_incumbent, "affine_prediction", _constant_prediction(90.0))
    monkeypatch.setattr(first_challenger, "affine_prediction", _constant_prediction(100.0))

    latest = None
    for index in range(96, 112):
        latest = replace(_frame(index, generation=1), temp_c=100.0)
        outcome = controller.observe_frame(latest)
        assert outcome["role_generation"] == 1
        assert controller.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert latest is not None

    assert first_incumbent.snapshot()["candidates"] == incumbent_candidates
    assert first_challenger.snapshot()["candidates"] != challenger_candidates
    # Seed one due boundary without creating a timestamp discontinuity that
    # would invalidate the accumulated origin window.
    coordinator._last_evaluation_s = latest.frame_end_s - coordinator.policy.evaluation_interval_s
    controller._online_next_evaluation_s = latest.frame_end_s
    second = controller._evaluate_online(latest)

    assert second["lifecycle"]["detail"] == "promotion"
    status = controller.get_status()["adaptation"]
    assert status["active_model_kind"] == "scheduled-arx"
    assert status["promotion_count"] == 2
    assert status["role_generation"] == 2


def test_active_arx_ownership_snapshot_round_trip_preserves_the_arx_challenger(monkeypatch):
    controller = _controller_after_first_arx_promotion(monkeypatch)
    snapshot = controller.get_model_snapshot()
    ownership = snapshot["online_adaptation"]
    assert ownership["incumbent"]["schema"] == "scheduled-arx/v2"
    assert ownership["challenger"]["schema"] == "scheduled-arx/v2"
    assert ownership["previous_incumbent"]["schema"] == "grey-box-adapter/v1"

    restored = _controller(online=True, n_horizon=3)
    assert restored.restore_model(snapshot) is True
    restored_ownership = restored.get_model_snapshot()["online_adaptation"]

    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert restored.get_status()["adaptation"]["role_generation"] == 1
    assert restored_ownership["incumbent"]["candidates"] == ownership["incumbent"]["candidates"]
    assert restored_ownership["challenger"]["candidates"] == ownership["challenger"]["candidates"]
    assert restored_ownership["previous_incumbent"] == ownership["previous_incumbent"]
    assert restored_ownership["previous_incumbent_digest"] == ownership["previous_incumbent_digest"]


def test_later_rollback_after_a_second_promotion_rewarms_the_restored_arx(monkeypatch):
    controller = _controller_after_first_arx_promotion(monkeypatch)
    coordinator = controller._online
    first_incumbent = coordinator.incumbent
    first_challenger = coordinator.challenger
    first_incumbent_prediction = first_incumbent.affine_prediction
    first_challenger_prediction = first_challenger.affine_prediction
    monkeypatch.setattr(first_incumbent, "affine_prediction", _constant_prediction(90.0))
    monkeypatch.setattr(first_challenger, "affine_prediction", _constant_prediction(100.0))

    latest = None
    for index in range(96, 112):
        latest = replace(_frame(index, generation=1), temp_c=100.0)
        controller.observe_frame(latest)
    assert latest is not None
    coordinator._last_evaluation_s = latest.frame_end_s - coordinator.policy.evaluation_interval_s
    controller._online_next_evaluation_s = latest.frame_end_s
    assert controller._evaluate_online(latest)["lifecycle"]["detail"] == "promotion"
    assert coordinator.role_generation == 2
    assert coordinator._previous_incumbent is first_incumbent
    monkeypatch.setattr(first_incumbent, "affine_prediction", first_incumbent_prediction)
    monkeypatch.setattr(first_challenger, "affine_prediction", first_challenger_prediction)
    for index in range(112, 128):
        controller.observe_frame(replace(_frame(index, generation=2), temp_c=100.0))
    assert coordinator.lag_warmup_remaining == 0

    class _FailingActivePolicy:
        def solve(self, *_args, **_kwargs):
            raise RuntimeError("active linear solve failed")

    controller._last_combustion_load = 0.4
    controller._linear_policy = _FailingActivePolicy()
    controller.update(100.0)
    rollback_command = controller.update(100.0)

    rolled_back = controller.get_status()
    assert rolled_back["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert rolled_back["adaptation"]["role_generation"] == 3
    assert rolled_back["adaptation"]["rollback_count"] == 1
    assert coordinator.incumbent is not first_incumbent
    assert coordinator.lag_warmup_remaining == coordinator.policy.max_delay_steps
    assert 0.0 <= rollback_command["cycle_ratio"] <= CYCLE["u_max"]
    assert rolled_back["policy_failures"] == 0

    controller._linear_policy = _LinearPolicy()
    warmup_command = controller.update(100.0)
    assert 0.0 <= warmup_command["cycle_ratio"] <= CYCLE["u_max"]
    assert controller.trace_diagnostics().policy_kind == "nlp"
    assert controller.get_status()["policy_failures"] == 0

    for index in range(128, 144):
        if coordinator.lag_warmup_remaining:
            command = controller.update(100.0)
            assert 0.0 <= command["cycle_ratio"] <= CYCLE["u_max"]
            assert controller.trace_diagnostics().policy_kind == "nlp"
        controller.observe_frame(replace(_frame(index, generation=3), temp_c=100.0))
        assert controller.get_status()["policy_failures"] == 0

    resumed_command = controller.update(100.0)
    assert 0.0 <= resumed_command["cycle_ratio"] <= CYCLE["u_max"]
    assert controller.trace_diagnostics().policy_kind == "linear-mpc"
    assert controller.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert controller.get_status()["policy_failures"] == 0


def test_pre_audit_v1_snapshot_keeps_learned_state_and_drops_only_old_evaluation(monkeypatch):
    controller = _controller_after_first_arx_promotion(monkeypatch)
    snapshot = controller.get_model_snapshot()
    payload = snapshot["online_adaptation"]
    payload["schema"] = "online-adaptation/v1"

    current_evaluation = payload["last_evaluation"]
    payload.update(
        {
            "eligible_updates": 37,
            "rejected_updates": 11,
            "promotion_count": 7,
            "rollback_count": 2,
            "last_lifecycle_reason": "promotion",
            "last_lifecycle": None,
            "last_evaluation": {
                key: current_evaluation[key]
                for key in (
                    "decision_id",
                    "evaluated_at_s",
                    "role_generation",
                    "promoted",
                    "committed",
                    "consecutive_wins",
                    "rejection_reasons",
                    "incumbent_prediction_score",
                    "challenger_prediction_score",
                    "incumbent_braking_score",
                    "challenger_braking_score",
                    "sample_count",
                    "prospective_digest",
                )
            },
        }
    )
    ownership = {
        key: payload[key]
        for key in (
            "incumbent",
            "challenger",
            "previous_incumbent",
            "previous_incumbent_digest",
            "role_generation",
            "effective_updates",
            "consecutive_wins",
        )
    }

    restored = _controller(online=True, n_horizon=3)
    assert restored.restore_model(snapshot) is True

    restored_snapshot = restored.get_model_snapshot()["online_adaptation"]
    restored_status = restored.get_status()["adaptation"]
    assert restored_snapshot["incumbent"] == ownership["incumbent"]
    assert restored_snapshot["challenger"] == ownership["challenger"]
    assert restored_snapshot["previous_incumbent"] == ownership["previous_incumbent"]
    assert restored_snapshot["previous_incumbent_digest"] == ownership["previous_incumbent_digest"]
    assert restored_snapshot["role_generation"] == ownership["role_generation"]
    assert restored_snapshot["effective_updates"] == ownership["effective_updates"]
    assert restored_snapshot["consecutive_wins"] == ownership["consecutive_wins"]
    assert restored_status["eligible_updates"] == 37
    assert restored_status["rejected_updates"] == 11
    assert restored_status["promotion_count"] == 7
    assert restored_status["rollback_count"] == 2
    assert restored_status["last_evaluation_outcome"] is None


def test_pre_ownership_change_v1_promoted_snapshot_clones_an_arx_challenger(monkeypatch):
    controller = _controller_after_first_arx_promotion(monkeypatch)
    snapshot = controller.get_model_snapshot()
    payload = snapshot["online_adaptation"]
    payload["schema"] = "online-adaptation/v1"

    active_arx = payload["incumbent"]
    rollback_owner = payload["previous_incumbent"]
    rollback_digest = payload["previous_incumbent_digest"]
    payload["challenger"] = rollback_owner

    restored = _controller(online=True, n_horizon=3)
    assert restored.restore_model(snapshot) is True

    ownership = restored.get_model_snapshot()["online_adaptation"]
    assert restored.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
    assert ownership["incumbent"] == active_arx
    assert ownership["challenger"]["schema"] == "scheduled-arx/v2"
    assert ownership["challenger"]["candidates"] == active_arx["candidates"]
    assert ownership["previous_incumbent"] == rollback_owner
    assert ownership["previous_incumbent_digest"] == rollback_digest
