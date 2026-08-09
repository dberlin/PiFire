"""Production-boundary integration coverage for live scheduled-ARX handoff."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, replace

import numpy as np
import pytest

from common.control_trace import (
    AmbientSource,
    FramedPulseFramePayload,
    ModelEventType,
    ModelObservationPayload,
    MpcUpdatePayload,
    TraceEventKind,
)
from controller.applied_output import OutputSource
from controller.linear_mpc.adaptation import EvaluationRejectionReason, OnlineAdaptation
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import AffinePrediction, ModelUpdate
from controller.model_learning.contracts import FrameObservation
from controller.model_learning.evaluation import (
    CausalForecastEvaluator,
    EvaluationConfig,
    ForecastOrigin,
    evaluate_forecasts,
)
from controller.mpc import Controller, _DEFAULTS
from controller.runtime.modes.hold import HoldMode
from controller.runtime.runner import ThreadedControllerRunner
from controller.runtime.state import WorkCycleState
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.characterization.harness import make_ctx
from tests.fakes.probes import FakeProbes


class _CaptureRecorder:
    """Captures the exact typed records emitted by the production Hold path."""

    def __init__(self, *, warning):
        self.records = []

    def record(self, record):
        self.records.append(record)

    def flush_due(self, _now_ms):
        return None

    def close(self):
        return None


class _WorkerGate:
    """Advances a real runner worker one complete loop without sleeping."""

    def __init__(self):
        self._arrivals: queue.Queue[None] = queue.Queue()
        self._permits: queue.Queue[None] = queue.Queue()
        self._closed = threading.Event()

    def __call__(self, _period_s: float) -> None:
        self._arrivals.put(None)
        if not self._closed.is_set():
            self._permits.get()

    def advance(self) -> None:
        self._arrivals.get(timeout=2.0)
        self._permits.put(None)
        self._arrivals.get(timeout=2.0)
        self._arrivals.put(None)

    def close(self) -> None:
        self._closed.set()
        self._permits.put(None)


@dataclass
class _DeterministicClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


class _Estimator:
    def update(self, _load: float, temperature_c: float) -> np.ndarray:
        return np.asarray([0.0] * 8 + [float(temperature_c), 0.0])


class _AlternatingGreyBoxPolicy:
    def __init__(self):
        self._calls = 0

    def firing_rate_raw(self, _state, _previous_load: float, _setpoint_c: float) -> float:
        self._calls += 1
        return 0.25 if self._calls % 2 else 0.75


class _DeterministicGreyBox:
    """A deliberately inferior, immutable incumbent prediction seam."""

    def track(self, observation: FrameObservation) -> ModelUpdate:
        return ModelUpdate(90.0, observation.temp_c, observation.temp_c - 90.0, False)

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        return ModelUpdate(90.0, observation.temp_c, observation.temp_c - 90.0, False)

    def affine_prediction(self, horizon_steps: int, q_previous: float, ambient_future: np.ndarray) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(np.full(horizon_steps, 90.0), np.zeros((horizon_steps, horizon_steps)))

    def snapshot(self) -> dict[str, object]:
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


class _DeterministicScheduledARX(ScheduledARX):
    """A real ScheduledARX learner with a deterministic public forecast seam."""

    def affine_prediction(self, horizon_steps: int, q_previous: float, ambient_future: np.ndarray) -> AffinePrediction:
        del q_previous, ambient_future
        return AffinePrediction(np.full(horizon_steps, 100.0), np.eye(horizon_steps) * 0.1)


def _seed_physical_arx(model: ScheduledARX) -> ScheduledARX:
    """Supply stable, positive-gain initial sufficient statistics to real RLS."""

    theta = np.asarray((0.2, 0.1, 0.3, 0.1, 0.1, 0.0), dtype=np.float64)
    for candidate in model._candidates.values():
        for region in candidate.regions:
            region.theta = theta.copy()
            information = region.information_factor
            region.normal_rhs = information.T @ information @ theta
    return model


def _pretraining_frame(index: int) -> FrameObservation:
    start_s = -518.0 + 20.0 * index
    realized_q = 0.25 if index % 2 == 0 else 0.75
    return FrameObservation(
        frame_start_s=start_s,
        frame_end_s=start_s + 20.0,
        temp_c=90.0,
        setpoint_c=107.222222222,
        ambient_c=20.0,
        requested_q=realized_q,
        realized_q=realized_q,
        requested_auger_duty=realized_q,
        delivered_on_s=20.0 * realized_q,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=index + 1,
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


def _pretrain_real_coordinator(core: Controller) -> None:
    """Build evidence through the real coordinator without a role change."""

    coordinator = core._online
    assert isinstance(coordinator, OnlineAdaptation)
    assert coordinator.policy.min_effective_updates == 20
    assert core.get_status()["adaptation"]["active_model_kind"] == "grey-box"
    for index in range(26):
        coordinator.observe(
            _pretraining_frame(index),
            ambient_future=np.full(180, 20.0),
        )
    assert coordinator.effective_updates >= coordinator.policy.min_effective_updates
    checkpoint = coordinator.evaluate_due(2.0)
    assert checkpoint.promoted is False
    assert checkpoint.consecutive_wins == 0
    assert checkpoint.reasons == (EvaluationRejectionReason.PREDICTION,)
    assert core.get_status()["adaptation"]["active_model_kind"] == "grey-box"


def _make_live_hold(monkeypatch, *, model_store=None):
    """Wire production Hold, PulseScheduler, Threaded runner, and MPC together."""

    import controller.runtime.modes.hold as hold_module
    import controller.runtime.runner as runner_module

    monkeypatch.setattr(
        Controller,
        "_build_for",
        lambda self, _cfg, **_kwargs: (_Estimator(), _AlternatingGreyBoxPolicy(), None, None),
    )
    monkeypatch.setattr(Controller, "_new_grey_box_model", lambda self: _DeterministicGreyBox())
    monkeypatch.setattr(
        Controller,
        "_new_scheduled_arx",
        lambda self: _seed_physical_arx(
            _DeterministicScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=1e-6))
        ),
    )
    deterministic_from_snapshot = _DeterministicScheduledARX.from_snapshot
    monkeypatch.setattr(ScheduledARX, "from_snapshot", deterministic_from_snapshot)

    core = Controller(
        dict(_DEFAULTS, enable_online_adaptation=True, policy="net", control_period=19.0),
        "F",
        {"u_min": 0.1, "u_max": 0.9},
    )
    core.set_target(225.0)
    _pretrain_real_coordinator(core)
    worker_clock = _DeterministicClock()
    gate = _WorkerGate()
    runner = ThreadedControllerRunner(
        core,
        monotonic_clock=worker_clock,
        wall_clock=worker_clock,
        wait_for_period=gate,
    )
    recorder = _CaptureRecorder(warning=lambda _message: None)
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    monkeypatch.setattr(runner_module, "build_runner", lambda *_args, **_kwargs: (runner, "Active"))

    settings = base_settings()
    settings["controller"]["selected"] = "mpc"
    settings["controller"]["config"]["mpc"] = dict(
        _DEFAULTS,
        enable_online_adaptation=True,
        policy="net",
        control_period=19.0,
        T_amb=68.0,
    )
    control = base_control(mode="Hold")
    control["primary_setpoint"] = 225.0
    ctx, _grill, _notifier = make_ctx(
        settings,
        control,
        base_pellet_db(),
        FakeProbes().script([212.0] * 100),
    )
    hold = HoldMode(ctx, WorkCycleState())
    hold.settings = settings
    hold.control = control
    hold.state.manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
    hold._model_store = model_store
    hold.setup()
    hold.state.metrics = {"id": "live-arx-handoff"}

    runner.set_target(control["primary_setpoint"])
    if model_store is not None:
        gate.advance()
        gate.advance()
        restored = core.get_model_snapshot()["online_adaptation"]
        assert restored["consecutive_wins"] == 1
        restored_evaluation = core.get_status()["adaptation"]["last_evaluation_outcome"]
        assert restored_evaluation is not None
        assert restored_evaluation["consecutive_wins"] == 1
    runner.submit(212.0)
    gate.advance()
    return hold, core, runner, recorder, gate, worker_clock


def _record_indices(records, kind: TraceEventKind) -> list[int]:
    return [index for index, record in enumerate(records) if record.event_kind is kind]


def test_live_scheduled_arx_evaluations_trace_all_five_causal_horizons(monkeypatch):
    hold, core, runner, recorder, gate, worker_clock = _make_live_hold(monkeypatch)
    assert core._online is not None
    core._online.policy = replace(core._online.policy, required_consecutive_wins=99)
    try:
        for now in range(2, 4_203, 20):
            worker_clock.now = float(now)
            hold.ctx.clock.advance(2.0 if now == 2 else 20.0)
            hold.on_tick(float(now), 212.0, hold.grill.get_output_status())
            gate.advance()

        evaluations = [
            record.payload for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVALUATION
        ]
        assert evaluations
        assert all(
            {score.horizon_steps for score in evaluation.horizon_scores} == {3, 15, 45, 90, 180}
            for evaluation in evaluations
        )
        assert {origin.horizon_steps for evaluation in evaluations for origin in evaluation.completed_origins} >= {
            3,
            15,
            45,
            90,
        }
    finally:
        gate.close()
        runner.stop()


def test_restart_keeps_a_durable_grey_win_but_scores_only_fresh_post_restart_origins():
    config = EvaluationConfig(required_consecutive_wins=2)
    incumbent_digest = "1" * 64
    challenger_digest = "2" * 64

    def origin(sequence, horizon):
        frame = _pretraining_frame(sequence)
        return ForecastOrigin(
            origin_sequence=sequence,
            origin_time_s=frame.frame_end_s,
            horizon_steps=horizon,
            role_generation=0,
            candidate_generation=9,
            incumbent_digest=incumbent_digest,
            challenger_digest=challenger_digest,
            incumbent_prediction_c=100.0,
            challenger_prediction_c=90.0,
            temperature_band="near-target",
            phase="hold",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        )

    before_restart = CausalForecastEvaluator(role_generation=0, candidate_generation=9)
    for horizon in config.required_horizons:
        before_restart.register(origin(0, horizon))
    for sequence in range(1, max(config.required_horizons) + 1):
        before_restart.observe(_pretraining_frame(sequence))
    durable = evaluate_forecasts(
        before_restart.completed_origins,
        role_generation=0,
        candidate_generation=9,
        prior_consecutive_wins=0,
        config=config,
    )
    assert durable.consecutive_wins == 1
    assert durable.accepted is False

    after_restart = CausalForecastEvaluator(role_generation=0, candidate_generation=9)
    assert after_restart.pending_origins == ()
    assert after_restart.completed_origins == ()
    for horizon in config.required_horizons:
        after_restart.register(origin(1000, horizon))
    for sequence in range(1001, 1000 + max(config.required_horizons) + 1):
        after_restart.observe(_pretraining_frame(sequence))
    promoted = evaluate_forecasts(
        after_restart.completed_origins,
        role_generation=0,
        candidate_generation=9,
        prior_consecutive_wins=durable.consecutive_wins,
        config=config,
    )
    assert promoted.accepted is True
    assert promoted.consecutive_wins == 2
    assert {row.origin_sequence for row in after_restart.completed_origins} == {1000}
