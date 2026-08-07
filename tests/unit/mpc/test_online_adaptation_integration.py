"""Production-boundary integration coverage for live scheduled-ARX handoff."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, replace

import numpy as np
import pytest

from common.control_trace import (
    FramedPulseFramePayload,
    ModelEventType,
    ModelObservationPayload,
    MpcUpdatePayload,
    TraceEventKind,
)
from controller.applied_output import OutputSource
from controller.linear_mpc.adaptation import EvaluationRejectionReason, OnlineAdaptation
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import AffinePrediction, FrameObservation, ModelUpdate
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
        {"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25},
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


def test_restart_restores_one_win_but_rebuilds_a_post_shutdown_scoring_window(monkeypatch):
    first_hold, first_core, _first_runner, first_recorder, first_gate, first_worker_clock = _make_live_hold(monkeypatch)
    try:
        first_win = None
        for now in range(2, 4_203, 20):
            first_worker_clock.now = float(now)
            first_hold.ctx.clock.advance(2.0 if now == 2 else 20.0)
            first_hold.on_tick(float(now), 212.0, first_hold.grill.get_output_status())
            first_gate.advance()
            first_win = next(
                (
                    record.payload
                    for record in first_recorder.records
                    if record.event_kind is TraceEventKind.MODEL_EVALUATION
                    and record.payload.consecutive_wins == 1
                    and not record.payload.promoted
                    and record.payload.rejection_reasons == ()
                ),
                None,
            )
            if first_win is not None:
                break
        assert first_win is not None
        next_now = float(now + 20)
        first_worker_clock.now = next_now
        first_hold.ctx.clock.advance(20.0)
        first_hold.on_tick(next_now, 212.0, first_hold.grill.get_output_status())
        first_gate.advance()
        assert first_core._online is not None
        assert first_core._online._origins
        live_checkpoint = first_core.get_model_snapshot()
        assert live_checkpoint is not None
        assert live_checkpoint["online_adaptation"]["consecutive_wins"] == 1
        live_revision = live_checkpoint["revision"]
        assert first_core.get_status()["adaptation"]["active_model_kind"] == "grey-box"
        assert first_core.get_status()["adaptation"]["role_generation"] == 0
        persisted_store = first_hold._model_store
    finally:
        first_gate.close()
        first_hold.teardown(212.0)

    checkpoint = persisted_store.load("mpc")
    assert checkpoint is not None
    assert checkpoint["online_adaptation"]["consecutive_wins"] == 1
    assert checkpoint["online_adaptation"]["partial_origins"] == []
    assert checkpoint["revision"] > live_revision

    second_hold, second_core, _second_runner, second_recorder, second_gate, second_worker_clock = _make_live_hold(
        monkeypatch, model_store=persisted_store
    )
    try:
        restored = second_core.get_status()["adaptation"]
        assert restored["active_model_kind"] == "grey-box"
        assert restored["role_generation"] == 0
        assert restored["last_evaluation_outcome"]["consecutive_wins"] == 1
        assert second_core.get_model_snapshot()["revision"] == checkpoint["revision"]

        saw_lag_warmup = False
        promotion = None
        for now in range(1002, 2003, 20):
            second_worker_clock.now = float(now)
            second_hold.ctx.clock.advance(1002.0 if now == 1002 else 20.0)
            second_hold.on_tick(float(now), 212.0, second_hold.grill.get_output_status())
            second_gate.advance()
            saw_lag_warmup |= second_core.get_status()["adaptation"]["current_rejection_reason"] == "lag-warmup"
            promotion = next(
                (
                    record.payload
                    for record in second_recorder.records
                    if record.event_kind is TraceEventKind.MODEL_EVALUATION
                    and record.payload.promoted
                    and record.payload.committed
                ),
                None,
            )
            if promotion is not None:
                break

        assert saw_lag_warmup
        assert promotion is not None
        assert promotion.consecutive_wins == 2
        # The boundary origin survives its checkpoint, then the following
        # fifteen endpoints complete thirteen 3-step and one 15-step origin.
        # This exact fresh window proves no shutdown-crossing origin scored.
        completed_horizons = tuple(origin.horizon_steps for origin in promotion.completed_origins)
        assert promotion.sample_count == 14
        assert completed_horizons.count(3) == 13
        assert completed_horizons.count(15) == 1
        assert second_core.get_status()["adaptation"]["active_model_kind"] == "scheduled-arx"
        assert second_core.get_status()["adaptation"]["role_generation"] == 1
        assert second_core.get_model_snapshot()["revision"] > checkpoint["revision"]
    finally:
        second_gate.close()
        second_hold.teardown(212.0)
