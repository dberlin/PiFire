import collections
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.control_trace import (
    ActuationMode,
    ControllerType,
    HorizonScorePayload,
    ModelEvaluationPayload,
    ModelObservationPayload,
    ResultStaleState,
    TraceEventKind,
)
from common.model_evidence import (
    ConfidenceDecisionEvidence,
    EvidenceKind,
    FallbackEvidence,
    ForecastOriginEvidence,
    ModelEvidenceRecord,
    RefreshDiagnosticsEvidence,
    SessionSummaryEvidence,
)
from common.persistence.model_evidence import ModelActivationState
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerLearningDiagnostics
from controller.model_learning.activation import (
    ActivationPhase,
    PreparedActivationRecord,
)
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin, FrameObservation
from controller.model_learning.evaluation import CompletedForecastOrigin, ForecastOrigin
from controller.mpc import Controller as MpcController
from controller.mpc_config import DEFAULT_MPC_CONFIG as MPC_DEFAULTS
from controller.runtime.control_trace_session import ControlTraceSession, TraceSessionContext
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    EvidenceSubmission,
    ModelPersistenceWorker,
)
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import (
    _MAX_PENDING_OBSERVATIONS,
    _MAX_PENDING_OUTPUTS,
    SyncControllerRunner,
    ThreadedControllerRunner,
    _freeze_evidence,
    build_runner,
)
from tests.unit.mpc._solver_fixtures import owned_pair
from tests.unit.runtime._persistence_helpers import _current_pair_descriptor


def _frame(index: int) -> FrameObservation:
    return FrameObservation(
        frame_start_s=index * 20.0,
        frame_end_s=(index + 1) * 20.0,
        temp_c=100.0,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.25,
        realized_q=0.25,
        requested_auger_duty=0.25,
        delivered_on_s=5.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=1,
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


def test_frozen_observation_evidence_counts_real_eligibility_and_rejections():
    accepted = _freeze_evidence(
        {"eligible": True, "rejection_reasons": (), "forecast_origin_evidence": ()},
        "session",
        "cook",
        _frame(1),
    )
    rejected = _freeze_evidence(
        {
            "eligible": False,
            "rejection_reasons": ("lid-open", "discontinuity"),
            "forecast_origin_evidence": (),
        },
        "session",
        "cook",
        _frame(2),
    )

    accepted_summary = accepted[0].payload
    rejected_summary = rejected[0].payload
    assert isinstance(accepted_summary, SessionSummaryEvidence)
    assert accepted_summary.accepted_observations == 1
    assert accepted_summary.rejected_observations == 0
    assert accepted_summary.rejection_reasons == ()
    assert isinstance(rejected_summary, SessionSummaryEvidence)
    assert rejected_summary.accepted_observations == 0
    assert rejected_summary.rejected_observations == 1
    assert rejected_summary.rejection_reasons == ("lid-open", "discontinuity")
    assert accepted[0].evidence_id != rejected[0].evidence_id


def test_retired_refresh_evidence_is_frozen_only_as_schema_two_audit_history():
    evaluation = SimpleNamespace(
        decision_id="d" * 64,
        evaluated_at_ms=21_000,
        role_generation=4,
        challenger_digest="b" * 64,
        incumbent_digest="a" * 64,
        rejection_reasons=("rank-deficient",),
        consecutive_wins=0,
    )
    records = _freeze_evidence(
        {
            "eligible": False,
            "rejection_reasons": ("rank-deficient",),
            "forecast_origin_evidence": (),
            "evaluation_payload": evaluation,
            "refresh_diagnostics_evidence": RefreshDiagnosticsEvidence(
                accepted=False,
                reason="rank-deficient",
            ),
        },
        "session",
        "cook",
        _frame(1),
    )

    refresh = next(record for record in records if record.kind is EvidenceKind.REFRESH_DIAGNOSTICS)
    assert refresh.schema_version == 2
    assert refresh not in [record for record in records if record.schema_version == 3]


class FakeCore:
    """Deterministic core. update() records temps, returns a fixed dict, and
    sets `updated` so tests synchronize on a real event, not a sleep."""

    def __init__(self, period=0.01, commands_fan=False, ratio=0.5):
        self._period = period
        self._commands_fan = commands_fan
        self._ratio = ratio
        self.target = None
        self.updates = []
        self.updated = threading.Event()
        self.tag = "core-a"
        self.activation_calls = []
        self._activation_terminated = False

    def get_control_period(self):
        return self._period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return True

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def set_target(self, sp):
        self.target = sp

    def update(self, temp):
        self.updates.append(temp)
        self.updated.set()
        return {"cycle_ratio": self._ratio, "fan": None}

    def set_output(self, applied):
        pass

    def get_status(self):
        return None

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None

    def get_model_snapshot(self):
        return None

    def restore_model(self, snapshot):
        return True

    @property
    def activation_terminated(self):
        return self._activation_terminated

    def restore_activation(self, persisted, records):
        self.activation_calls.append(("restore", persisted, tuple(records)))
        return False

    def activation_runtime_failure(self, reason):
        self.activation_calls.append(("fallback", reason))
        return False

    def rollback_activation(self, reason):
        self.activation_calls.append(("rollback", reason))
        return False

    def drain_activation_events(self):
        events = tuple(self.activation_events)
        self.activation_events.clear()
        self.activation_calls.append(("drain", events))
        return events

    def submit_activation_confidence(self, record):
        self.activation_calls.append(("confidence", record))
        receipt = DurableActivationReceipt(accepted=True)
        receipt._complete(durable=True)
        return receipt

    def advance_activation(self):
        self.activation_calls.append(("advance",))
        return True

    def terminate_mpc_activation(self, reason):
        self._activation_terminated = True


def test_threaded_runner_captures_fallback_evidence_from_an_ordinary_compute():
    fallback = ModelEvidenceRecord(
        evidence_id="fallback:7:1000:digest",
        kind=EvidenceKind.FALLBACK,
        session_id="runtime",
        cook_id=None,
        timestamp_ms=1_000,
        role_generation=8,
        model_digest="d" * 64,
        provenance_digest=None,
        payload=FallbackEvidence(
            decision_id="decision-a",
            reason="non-finite-forecast",
            failed_digest="d" * 64,
            failed_generation=7,
            last_safe_command=0.4,
            fallback_kind="grey-box",
        ),
    )

    class FallbackCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.events = []
            self.drained = threading.Event()

        def update(self, temp):
            self.events.append(fallback)
            return super().update(temp)

        def drain_activation_events(self):
            events = tuple(self.events)
            self.events.clear()
            self.drained.set()
            return events

    core = FallbackCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(100.0)
        assert core.updated.wait(2.0)
        assert runner.drain_activation_events() == (fallback,)
        assert runner.drain_activation_events() == ()
    finally:
        runner.stop()


class BlockingCore(FakeCore):
    """update() blocks on `gate` so a test can observe latest() not blocking
    while a solve is in flight."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def update(self, temp):
        self.entered.set()
        self.gate.wait(2.0)
        return super().update(temp)


class CloseAwarePeriodWait:
    """Blocks the runner worker until stop() closes the injected wait."""

    def __init__(self):
        self.waiting = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def __call__(self, _seconds):
        self.waiting.set()
        self.release.wait()

    def close(self):
        self.closed.set()
        self.release.set()


def test_runners_retire_generation_bound_context_until_rebound() -> None:
    class NoOutcomeCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.observed = threading.Event()

        def observe_frame(self, _observation):
            self.observed.set()

    class ProcessingBarrier:
        def __init__(self):
            self.calls = 0
            self.first_waiting = threading.Event()
            self.completed_cycle = threading.Event()
            self.release = threading.Event()

        def __call__(self, _seconds):
            self.calls += 1
            if self.calls == 1:
                self.first_waiting.set()
            else:
                self.completed_cycle.set()
            assert self.release.wait(2.0)
            self.release.clear()

        def close(self):
            self.release.set()

    sync = SyncControllerRunner(NoOutcomeCore())
    sync.bind_evidence_context(0, "session-sync", "cook-sync")
    sync.retire_evidence_context(0)
    sync_submission = sync.observe_frame(_frame(0))

    assert sync.drain_observation_outcomes().terminal_drops == ()

    sync.bind_evidence_context(0, "replacement-sync", "cook-sync")
    sync_drops = sync.drain_observation_outcomes().terminal_drops
    assert [drop.submission_sequence for drop in sync_drops] == [sync_submission.submission_sequence]

    threaded_core = NoOutcomeCore()
    barrier = ProcessingBarrier()
    threaded = ThreadedControllerRunner(
        threaded_core,
        wait_for_period=barrier,
    )
    try:
        assert barrier.first_waiting.wait(2.0)
        threaded.bind_evidence_context(0, "session-threaded", "cook-threaded")
        threaded.retire_evidence_context(0)
        threaded_submission = threaded.observe_frame(_frame(1))
        assert threaded_submission is not None
        barrier.release.set()
        assert threaded_core.observed.wait(2.0)
        assert barrier.completed_cycle.wait(2.0)

        assert threaded.drain_observation_outcomes().terminal_drops == ()

        threaded.bind_evidence_context(0, "replacement-threaded", "cook-threaded")
        threaded_drops = threaded.drain_observation_outcomes().terminal_drops
        assert [drop.submission_sequence for drop in threaded_drops] == [threaded_submission.submission_sequence]
    finally:
        threaded.stop()


def test_threaded_runner_solves_submitted_temp():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)  # thread ran update(70.0)
        assert 70.0 in core.updates
        out = r.latest()
        assert out.cycle_ratio == 0.5 and out.fan is None
        assert r.control_period() == 0.01
        assert r.wants_async() is True
    finally:
        r.stop()


def test_threaded_result_retains_consumed_temperature_after_newer_submit():
    class FirstBlockedCore(FakeCore):
        def __init__(self):
            super().__init__(period=0.01)
            self.first_entered = threading.Event()
            self.first_release = threading.Event()
            self.second_release = threading.Event()

        def update(self, temp):
            self.updates.append(temp)
            if len(self.updates) == 1:
                self.first_entered.set()
                self.first_release.wait(2.0)
            else:
                self.second_release.wait(2.0)
            return {"cycle_ratio": self._ratio, "fan": None}

    core = FirstBlockedCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.first_entered.wait(2.0)
        runner.submit(80.0)
        core.first_release.set()
        assert _wait_for(lambda: runner.latest().revision == 1)
        assert runner.latest().input_temperature == 70.0
    finally:
        core.first_release.set()
        core.second_release.set()
        runner.stop()


def test_threaded_result_retains_owned_learning_snapshot_across_repolls():
    class LearningCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.learning_state = {"generation": 0, "gates": [{"passed": False}]}
            self.learning_calls = 0

        def update(self, temp):
            self.learning_state["generation"] += 1
            return super().update(temp)

        def get_learning_diagnostics(self):
            self.learning_calls += 1
            return ControllerLearningDiagnostics(schema_version=1, state=self.learning_state)

    core = LearningCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.updated.wait(2.0)
        assert _wait_for(lambda: runner.latest().revision == 1)
        result = runner.latest()

        core.learning_state["generation"] = 2
        core.learning_state["gates"][0]["passed"] = True
        repolled = runner.latest()

        assert result.learning is not None
        assert repolled.learning is not None
        assert result.learning.as_json() == {
            "generation": 1,
            "gates": [{"passed": False}],
        }
        assert repolled.learning.as_json() == result.learning.as_json()
        assert core.learning_calls == 1
    finally:
        runner.stop()


def test_threaded_mpc_completed_update_captures_one_aligned_learning_status_snapshot(monkeypatch):
    class _UpdateGate:
        def __init__(self):
            self.before_update = threading.Event()
            self.release_update = threading.Event()
            self.after_update = threading.Event()
            self.release_stop = threading.Event()
            self.waits = 0

        def __call__(self, _seconds):
            self.waits += 1
            if self.waits == 1:
                self.before_update.set()
                assert self.release_update.wait(2.0)
            else:
                self.after_update.set()
                assert self.release_stop.wait(2.0)

        def close(self):
            self.release_update.set()
            self.release_stop.set()

    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    core.set_target(110.0)
    original_capability = core.get_learning_diagnostics
    capability_calls = 0

    def counted_capability():
        nonlocal capability_calls
        capability_calls += 1
        state = original_capability().as_json()
        state["capture_sequence"] = capability_calls
        return ControllerLearningDiagnostics(schema_version=1, state=state)

    monkeypatch.setattr(core, "get_learning_diagnostics", counted_capability)
    gate = _UpdateGate()
    runner = ThreadedControllerRunner(core, wait_for_period=gate)
    capability_calls = 0
    try:
        assert gate.before_update.wait(2.0)
        runner.submit(100.0)
        gate.release_update.set()
        assert gate.after_update.wait(2.0)
        result = runner.latest()

        assert capability_calls == 1
        assert result.learning is not None
        assert result.learning.as_json()["capture_sequence"] == 1
        assert result.status is not None
        assert runner.controller_state()["learning"] == result.learning.as_json()
    finally:
        runner.stop()


def test_threaded_runner_repoll_preserves_completed_revision_and_advances_age():
    core = FakeCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.updated.wait(2.0)
        first = runner.latest()
        second = runner.latest()
        assert second.revision == first.revision >= 1
        assert second.result_age_seconds >= first.result_age_seconds
        assert first.solve_duration_seconds == first.solve_end_monotonic - first.solve_start_monotonic
    finally:
        runner.stop()


def test_threaded_result_recursively_freezes_nested_status_across_repolls():
    class _NestedStatusCore(FakeCore):
        def __init__(self):
            super().__init__()
            self.status = {"nested": {"samples": [1.0]}}

        def get_status(self):
            return self.status

    core = _NestedStatusCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(70.0)
        assert core.updated.wait(2.0)
        assert _wait_for(lambda: runner.latest().revision >= 1)
        result = runner.latest()
        core.status["nested"]["samples"].append(2.0)
        core.status["nested"]["extra"] = 3.0

        repolled = runner.latest()
        state = runner.controller_state()
        assert repolled.revision == result.revision
        assert repolled.result_age_seconds >= result.result_age_seconds
        assert state["nested"] == {"samples": [1.0]}
        assert {key: state[key] for key in ("pending_dropped", "pending_observations", "dropped_observations")} == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
        assert state["result_age_seconds"] >= repolled.result_age_seconds
        assert json.loads(json.dumps(state)) == state
        state["nested"]["samples"].append(3.0)
        assert result.status == {"nested": {"samples": (1.0,)}}
        assert repolled.status == {"nested": {"samples": (1.0,)}}
        current = runner.controller_state()
        assert current["nested"] == {"samples": [1.0]}
        assert {key: current[key] for key in ("pending_dropped", "pending_observations", "dropped_observations")} == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
    finally:
        runner.stop()


def test_threaded_runner_latest_does_not_block_during_solve():
    core = BlockingCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.entered.wait(2.0)  # thread is inside a blocked update()
        # latest() must return promptly (the default snapshot), not wait for the solve.
        out = r.latest()
        assert out.cycle_ratio == 0.0  # initial default; solve has not stored yet
        core.gate.set()  # let the solve finish
        assert core.updated.wait(2.0)
        assert r.latest().cycle_ratio == 0.5
    finally:
        core.gate.set()
        r.stop()


def test_threaded_runner_publishes_one_atomic_quality_snapshot_without_blocking():
    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class PeriodBarrier:
        def __init__(self):
            self.release = threading.Event()
            self.first_waiting = threading.Event()
            self.first_publish_complete = threading.Event()
            self.recovery_publish_complete = threading.Event()
            self.calls = 0
            self.lock = threading.Lock()

        def __call__(self, seconds):
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                self.first_waiting.set()
            elif call == 2:
                self.first_publish_complete.set()
            elif call == 3:
                self.recovery_publish_complete.set()
            assert self.release.wait(2.0)
            self.release.clear()

    class BarrierCore(FakeCore):
        def __init__(self, clock):
            super().__init__(period=5.0)
            self.clock = clock
            self.entered = threading.Event()
            self.solve_duration = 6.0

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def update(self, temp):
            self.entered.set()
            self.clock.advance(self.solve_duration)
            return super().update(temp)

    clock = Clock()
    warnings = []
    barrier = PeriodBarrier()
    core = BarrierCore(clock)
    runner = ThreadedControllerRunner(
        core,
        monotonic_clock=clock,
        wall_clock=clock,
        warning_callback=warnings.append,
        wait_for_period=barrier,
    )
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.submit(70.0)
        barrier.release.set()
        assert core.entered.wait(2.0)
        assert barrier.first_publish_complete.wait(2.0)
        completed = runner.latest()
        assert completed.revision == 1
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
        assert completed.solve_duration_seconds == 6.0
        assert completed.deadline_miss_count == completed.consecutive_deadline_miss_count == 1

        clock.advance(10.0)
        stale = runner.latest()
        assert stale.revision == completed.revision
        assert stale.result_age_seconds == 10.0
        assert stale.stale_state is ResultStaleState.STALE
        assert warnings == [ResultStaleState.STALE]
        assert runner.latest() is stale
        assert warnings == [ResultStaleState.STALE]
        clock.advance(1.0)
        aged = runner.latest()
        assert aged.revision == stale.revision
        assert aged.result_age_seconds == 11.0
        assert aged.stale_state is ResultStaleState.STALE
        assert warnings == [ResultStaleState.STALE]

        core.solve_duration = 0.0
        barrier.release.set()
        assert barrier.recovery_publish_complete.wait(2.0)
        recovered = runner.latest()
        assert recovered.revision == completed.revision + 1
        assert recovered.deadline_miss_count == 1
        assert recovered.consecutive_deadline_miss_count == 0
        assert recovered.stale_state is ResultStaleState.FRESH
        assert recovered.recovered is True
        assert warnings == [ResultStaleState.STALE, ResultStaleState.FRESH]
        assert runner.latest() is recovered
        assert recovered.recovered is True
        assert warnings == [ResultStaleState.STALE, ResultStaleState.FRESH]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_reconfigure_atomically_refreshes_capabilities_and_stale_budget(monkeypatch):
    import controller.runtime.runner as runner_module

    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class PeriodBarrier:
        def __init__(self):
            self.release = threading.Event()
            self.first_waiting = threading.Event()
            self.swapped_waiting = threading.Event()
            self.calls = 0

        def __call__(self, seconds):
            self.calls += 1
            if self.calls == 1:
                self.first_waiting.set()
            elif self.calls == 2:
                self.swapped_waiting.set()
            assert self.release.wait(2.0)
            self.release.clear()

    class Core(FakeCore):
        def __init__(self, *, period, commands_fan, mode):
            super().__init__(period=period, commands_fan=commands_fan)
            self._mode = mode

        def actuation_mode(self):
            return self._mode

    clock = Clock()
    barrier = PeriodBarrier()
    replacement = Core(period=2.0, commands_fan=True, mode=ActuationMode.FRAMED_PULSE)
    monkeypatch.setattr(runner_module, "_build_core", lambda *args, **kwargs: (replacement, "Active"))
    runner = ThreadedControllerRunner(
        Core(period=5.0, commands_fan=False, mode=ActuationMode.FRAMED_PULSE),
        monotonic_clock=clock,
        wall_clock=clock,
        wait_for_period=barrier,
    )
    try:
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
        assert barrier.first_waiting.wait(2.0)
        assert runner.reconfigure({}, {}) == "Active"
        runner.submit(70.0)
        barrier.release.set()
        assert barrier.swapped_waiting.wait(2.0)
        assert runner.control_period() == 2.0
        assert runner.commands_fan() is True
        assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE

        clock.advance(4.0)
        stale = runner.latest()
        assert stale.revision == 1
        assert stale.stale_state is ResultStaleState.STALE
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_stop_terminates_thread():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    thread = r._thread
    assert thread.is_alive()
    r.stop()
    assert not thread.is_alive()
    r.stop()  # idempotent


def test_threaded_runner_stop_closes_blocking_period_wait_before_joining():
    wait_for_period = CloseAwarePeriodWait()
    runner = ThreadedControllerRunner(FakeCore(), wait_for_period=wait_for_period)
    thread = runner._thread
    try:
        assert wait_for_period.waiting.wait(2.0)
        runner.stop()
        assert wait_for_period.closed.is_set()
        assert not thread.is_alive()
    finally:
        wait_for_period.close()
        runner.stop()


def test_threaded_runner_set_target_and_reconfigure_applied_by_thread():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)
        r.set_target(225)
        # target is applied on the thread's next iteration; observe via the core
        deadline = threading.Event()
        for _ in range(200):
            if core.target == 225:
                break
            deadline.wait(0.01)
        assert core.target == 225
    finally:
        r.stop()


def test_threaded_runner_never_exposes_core_internals_before_first_result():
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        assert r.controller_state() == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
    finally:
        r.stop()


class _StatusBeforeSolveCore(FakeCore):
    """get_status() answers immediately, before any update() -- exercises the
    runner's seeding of the *initial* snapshot in __init__, not just the
    post-solve reseed in _loop."""

    def __init__(self):
        super().__init__()
        self.leaky = object()  # would appear in the snapshot if __init__ used __dict__

    def get_status(self):
        return {"safe": 1.0}


def test_threaded_runner_seeds_initial_snapshot_from_get_status():
    core = _StatusBeforeSolveCore()
    r = ThreadedControllerRunner(core)
    try:
        snap = r.controller_state()
        assert snap["safe"] == 1.0
        assert "leaky" not in snap  # get_status() seeded it, not core.__dict__
    finally:
        r.stop()


class _RaisingStatusCore(FakeCore):
    """get_status() raises -- this runs before the core has proven itself with
    a successful update(), outside _build_core's guard, so __init__ must not
    let it propagate and kill the control process the way an unguarded
    constructor once did."""

    def get_status(self):
        raise RuntimeError("boom")


def test_threaded_runner_survives_get_status_raising_during_init():
    core = _RaisingStatusCore()
    r = ThreadedControllerRunner(core)
    try:
        assert r.controller_state() == {
            "pending_dropped": 0,
            "pending_observations": 0,
            "dropped_observations": 0,
        }
    finally:
        r.stop()


def test_controller_state_pending_dropped_survives_hold_s_cycle_ratio_mutation():
    # HoldMode._on_auger_on reads controller_state() and adds "cycle_ratio"
    # before publishing to MQTT. pending_dropped is a field only the threaded
    # runner adds to that payload; pin that it survives the mutation
    # unchanged and defaults to 0 when nothing has been dropped -- an
    # additive dict key is a shape a JSON-payload consumer can ignore, not
    # one that breaks it.
    core = FakeCore()
    r = ThreadedControllerRunner(core)
    try:
        r.submit(70.0)
        assert core.updated.wait(2.0)
        state = r.controller_state()
        assert state["pending_dropped"] == 0
        state["cycle_ratio"] = 0.42
        assert state["pending_dropped"] == 0
        assert state["cycle_ratio"] == 0.42
    finally:
        r.stop()


def test_build_runner_selects_threaded_for_wants_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    core = FakeCore()  # wants_async() -> True

    monkeypatch.setattr(runner_mod, "_build_core", lambda *a, **k: (core, "Active"))
    r, status = build_runner({}, {})
    try:
        assert isinstance(r, ThreadedControllerRunner)
        assert status == "Active"
    finally:
        r.stop()


def test_build_runner_selects_sync_for_non_async_core(monkeypatch):
    import controller.runtime.runner as runner_mod

    class SyncCore(FakeCore):
        def wants_async(self):
            return False

    monkeypatch.setattr(runner_mod, "_build_core", lambda *a, **k: (SyncCore(), "Active"))
    r, _status = build_runner({}, {})
    assert isinstance(r, SyncControllerRunner)
    r.stop()  # no-op


def test_hold_teardown_stops_threaded_runner(hold_cycle):
    core = FakeCore()
    runner = ThreadedControllerRunner(core)
    thread = runner._thread
    hold = hold_cycle(runner, controller="pid_sp")

    try:
        hold.setup()
        hold.teardown(70.0)
        assert not thread.is_alive()
    finally:
        runner.stop()


class _OrderRecordingCore(FakeCore):
    """Records the interleaving of set_output and update calls."""

    def __init__(self):
        super().__init__(period=0.01, ratio=0.5)
        self.calls = []
        self.lock = threading.Lock()
        self.snapshot = {"revision": 1}
        self.restored = []

    def update(self, temp):
        with self.lock:
            self.calls.append(("update", temp))
        return 0.5

    def set_output(self, applied):
        with self.lock:
            self.calls.append(("set_output", applied.timestamp))

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return 0.01

    def commands_fan(self):
        return False

    def wants_async(self):
        return True

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return None

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None

    def get_model_snapshot(self):
        # Cached, not rebuilt per call: a runner that handed this object out
        # as-is would let a caller's mutation reach back into the core.
        return self.snapshot

    def restore_model(self, snapshot):
        with self.lock:
            self.restored.append(snapshot)
        return True


def _wait_for(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_applied_outputs_replay_in_submission_fifo_before_update():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        # Submission order is the actuator's causal order even if timestamps regress.
        runner.set_output(AppliedOutput(0.2, OutputSource.CONTROLLER, 20.0))
        runner.set_output(AppliedOutput(0.1, OutputSource.CONTROLLER, 10.0))
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(i for i, c in enumerate(calls) if c[0] == "update")
        reports = [c for c in calls[:first_update] if c[0] == "set_output"]
        assert reports == [("set_output", 20.0), ("set_output", 10.0)]
    finally:
        runner.stop()


def test_restore_model_is_applied_on_the_worker_thread():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model({"revision": 7}) is True
        runner.submit(212.0)
        assert _wait_for(lambda: core.restored == [{"revision": 7}])
    finally:
        runner.stop()


def test_restore_model_deep_copies_the_snapshot_on_the_way_in():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        snapshot = {"revision": 7, "online_adaptation": {"challenger": {"params": [1.0]}}}
        assert runner.restore_model(snapshot) is True
        snapshot["online_adaptation"]["challenger"]["params"][0] = 999.0
        runner.submit(212.0)
        assert _wait_for(
            lambda: core.restored == [{"revision": 7, "online_adaptation": {"challenger": {"params": [1.0]}}}]
        )
    finally:
        runner.stop()


def test_restore_model_rejects_none_without_touching_the_core():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_model(None) is False
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        assert core.restored == []
    finally:
        runner.stop()


def test_get_model_snapshot_reads_the_worker_s_snapshot():
    core = _OrderRecordingCore()
    runner = ThreadedControllerRunner(core)
    try:
        core.snapshot = {"revision": 4}
        runner.submit(212.0)
        assert _wait_for(lambda: runner.get_model_snapshot() == {"revision": 4})
    finally:
        runner.stop()


def test_get_model_snapshot_returns_a_deep_copy_not_the_core_s_object():
    core = _OrderRecordingCore()
    core.snapshot = {"revision": 1, "online_adaptation": {"challenger": {"params": [1.0]}}}
    runner = ThreadedControllerRunner(core)
    try:
        runner.submit(212.0)
        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        snapshot = runner.get_model_snapshot()
        assert snapshot is not core.snapshot
        snapshot["online_adaptation"]["challenger"]["params"][0] = 999.0
        expected = {"revision": 1, "online_adaptation": {"challenger": {"params": [1.0]}}}
        assert core.snapshot == expected
        assert runner.get_model_snapshot() == expected
    finally:
        runner.stop()


class _BlockedWorkerCore(_OrderRecordingCore):
    """A core whose update() blocks on `gate` until the test lets it through,
    then re-arms so a subsequent update() blocks again. This holds the
    worker's loop at "inside one iteration's update()" for as long as the
    test needs, so appends made meanwhile are provably undrained."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.gate = threading.Event()
        self._released = False

    def update(self, temp):
        self.entered.set()
        if not self._released:
            self.gate.wait()
            self.gate.clear()
        return super().update(temp)

    def release(self):
        """Stop blocking for good, for teardown. Setting `gate` alone can
        race: the currently-blocked call clears it on its way out, so the
        *next* call would re-block and leave stop()'s join() to time out on a
        thread that is never coming back."""
        self._released = True
        self.gate.set()


def _threaded_runner_with_blocked_worker():
    core = _BlockedWorkerCore()
    runner = ThreadedControllerRunner(core)
    runner.submit(212.0)
    assert core.entered.wait(2.0)  # worker is now stuck inside update()
    return runner, core


def _output(ratio, timestamp):
    return AppliedOutput(ratio=ratio, source=OutputSource.CONTROLLER, timestamp=timestamp)


def _drain_once(runner):
    """Let the worker's current blocked update() return, forcing its next loop
    iteration -- which drains _pending_outputs before calling update() again --
    to run, then leave the worker blocked again inside that next update()."""
    core = runner._core
    core.entered.clear()
    core.gate.set()
    assert core.entered.wait(2.0)  # the next update() call has begun and re-armed


def test_completed_frame_does_not_overtake_older_output_dispatch():
    class _BlockedDispatchCore(_BlockedWorkerCore):
        def observe_frame(self, observation):
            with self.lock:
                self.calls.append(("observe_frame", observation.frame_end_s))
            return {"eligible": False}

    core = _BlockedDispatchCore()
    runner = ThreadedControllerRunner(core)
    runner.submit(212.0)
    assert core.entered.wait(2.0)
    try:
        runner.set_output(_output(ratio=0.1, timestamp=10.0))
        runner.complete_frame(_output(ratio=0.2, timestamp=20.0), _frame(0))
        core.release()
        assert _wait_for(lambda: ("observe_frame", 20.0) in core.calls)
        with core.lock:
            dispatches = [call for call in core.calls if call[0] in {"set_output", "observe_frame"}]
        assert dispatches == [
            ("set_output", 10.0),
            ("set_output", 20.0),
            ("observe_frame", 20.0),
        ]
    finally:
        core.release()
        runner.stop()


@pytest.mark.parametrize("operation", ("restore", "rollback", "fallback"))
@pytest.mark.parametrize("transition_first", (False, True))
def test_role_transition_and_completed_frame_share_one_causal_fifo(operation, transition_first):
    class _RoleCore(_BlockedWorkerCore):
        def __init__(self):
            super().__init__()
            self.role = 0
            self.role_events = []

        def observe_frame(self, observation):
            self.role_events.append(("observation", observation.role_generation, self.role))
            return {"eligible": False}

        def restore_activation(self, _persisted, _records):
            self.role = 1
            self.role_events.append(("restore", self.role))
            return True

        def rollback_activation(self, _reason):
            self.role = 1
            self.role_events.append(("rollback", self.role))
            return True

        def activation_runtime_failure(self, _reason):
            self.role = 1
            self.role_events.append(("fallback", self.role))
            return True

    core = _RoleCore()
    runner = ThreadedControllerRunner(core)
    runner.submit(212.0)
    assert core.entered.wait(2.0)

    persisted = ModelActivationState(
        active_snapshot_json="{}",
        rollback_snapshot_json="{}",
        evidence_decision_id=f"decision-{transition_first}",
        controller_configuration_digest="d" * 64,
        role_generation=1,
        transaction_id=f"txn-{transition_first}",
    )

    def queue_transition():
        if operation == "restore":
            assert runner.restore_activation(persisted, ())
        elif operation == "rollback":
            assert runner.rollback_activation("operator")
        else:
            assert runner.activation_runtime_failure("confidence")

    try:
        if transition_first:
            queue_transition()
        submission = runner.complete_frame(
            _output(ratio=0.2, timestamp=20.0),
            replace(_frame(0), role_generation=int(transition_first)),
        )
        assert submission is not None
        if not transition_first:
            queue_transition()
        core.release()
        assert _wait_for(lambda: len(core.role_events) == 2)
        if transition_first:
            assert core.role_events == [
                (operation, 1),
                ("observation", 1, 1),
            ]
        else:
            assert core.role_events == [
                ("observation", 0, 0),
                (operation, 1),
            ]
    finally:
        core.release()
        runner.stop()


def test_deadline_fallback_waits_behind_frame_queued_during_the_solve():
    class _Clock:
        def __init__(self):
            self.value = 0.0
            self.lock = threading.Lock()

        def __call__(self):
            with self.lock:
                return self.value

        def advance(self, seconds):
            with self.lock:
                self.value += seconds

    class _DeadlineCore(_OrderRecordingCore):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock
            self.solve_count = 0
            self.second_solve_entered = threading.Event()
            self.release_second_solve = threading.Event()
            self.role = 0
            self.role_events = []

        def update(self, temp):
            self.solve_count += 1
            if self.solve_count == 2:
                self.second_solve_entered.set()
                assert self.release_second_solve.wait(2.0)
            if self.solve_count <= 2:
                self.clock.advance(0.02)
            return super().update(temp)

        def observe_frame(self, observation):
            self.role_events.append(("observation", observation.role_generation, self.role))
            return {"eligible": False}

        def activation_runtime_failure(self, reason):
            self.role = 1
            self.role_events.append(("fallback", reason, self.role))
            return True

    clock = _Clock()
    core = _DeadlineCore(clock)
    runner = ThreadedControllerRunner(
        core,
        monotonic_clock=clock,
        wall_clock=clock,
    )
    try:
        runner.submit(212.0)
        assert core.second_solve_entered.wait(2.0)
        submission = runner.complete_frame(
            _output(ratio=0.2, timestamp=20.0),
            _frame(0),
        )
        assert submission is not None
        core.release_second_solve.set()
        assert _wait_for(lambda: len(core.role_events) == 2)
        assert core.role_events == [
            ("observation", 0, 0),
            ("fallback", "deadline-threshold", 1),
        ]
    finally:
        core.release_second_solve.set()
        runner.stop()


def test_a_stalled_worker_bounds_the_backlog_and_counts_the_drops():
    runner, core = _threaded_runner_with_blocked_worker()
    try:
        for i in range(_MAX_PENDING_OUTPUTS + 50):
            runner.set_output(_output(ratio=0.3, timestamp=float(i)))

        assert len(runner._pending_dispatches) == _MAX_PENDING_OUTPUTS
        assert runner._pending_dropped == 50
        assert runner.controller_state()["pending_dropped"] == 50

        # the survivors are the newest, and the oldest are what went
        oldest = min(payload.timestamp for operation, payload in runner._pending_dispatches if operation == "output")
        assert oldest == 50.0
    finally:
        core.release()
        runner.stop()


def test_the_backlog_stays_bounded_after_a_drain():
    runner, core = _threaded_runner_with_blocked_worker()
    try:
        for i in range(10):
            runner.set_output(_output(ratio=0.3, timestamp=float(i)))
        _drain_once(runner)

        for i in range(_MAX_PENDING_OUTPUTS + 25):
            runner.set_output(_output(ratio=0.3, timestamp=float(100 + i)))
        assert len(runner._pending_dispatches) == _MAX_PENDING_OUTPUTS
        assert isinstance(runner._pending_dispatches, collections.deque)
    finally:
        core.release()
        runner.stop()


class _ObservationBarrier:
    def __init__(self):
        self.calls = 0
        self.first_waiting = threading.Event()
        self.release = threading.Event()

    def __call__(self, seconds):
        self.calls += 1
        if self.calls == 1:
            self.first_waiting.set()
        assert self.release.wait(2.0)
        self.release.clear()


class _ObservationRecordingCore(_OrderRecordingCore):
    def __init__(self):
        super().__init__()
        self.observations = []
        self.learner_lock_free = []
        self.runner = None

    def observe_frame(self, observation):
        acquired = self.runner._lock.acquire(blocking=False)
        if acquired:
            self.runner._lock.release()
        self.learner_lock_free.append(acquired)
        with self.lock:
            self.observations.append(observation)
            self.calls.append(("observe_frame", observation.frame_end_s))


def test_threaded_runner_delivers_frame_observations_in_timestamp_order_before_update():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(1))
        runner.observe_frame(_frame(0))
        runner.observe_frame(_frame(2))
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: len(core.observations) == 3 and ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(index for index, call in enumerate(calls) if call == ("update", 212.0))
        assert calls[:first_update] == [
            ("observe_frame", 20.0),
            ("observe_frame", 40.0),
            ("observe_frame", 60.0),
        ]
        assert core.learner_lock_free == [True, True, True]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_drains_outcomes_for_exact_delivered_observations():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"role_generation": observation.role_generation, "eligible": False}

    core = OutcomeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    runner.bind_evidence_context(0, "session", "cook")
    try:
        assert barrier.first_waiting.wait(2.0)
        later = _frame(1)
        earlier = _frame(0)
        later_sequence = runner.observe_frame(later)
        earlier_sequence = runner.observe_frame(earlier)
        runner.submit(212.0)
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 2)

        outcomes = runner.drain_observation_outcomes()

        assert [(item.submission_sequence, item.observation) for item in outcomes] == [
            (earlier_sequence.submission_sequence, earlier),
            (later_sequence.submission_sequence, later),
        ]
        assert runner.drain_observation_outcomes().envelopes == ()
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_withholds_completed_unbound_generation_until_bound():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"role_generation": observation.role_generation, "eligible": False}

    core = OutcomeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        submission = runner.observe_frame(_frame(0))
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 1)

        assert runner.drain_observation_outcomes().envelopes == ()

        runner.bind_evidence_context(0, "generation-zero", "cook")
        drained = runner.drain_observation_outcomes()
        assert [(item.submission_sequence, item.configuration_generation, item.observation) for item in drained] == [
            (submission.submission_sequence, 0, _frame(0))
        ]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_withholds_unbound_terminal_drop_until_its_generation_binds():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        submission = runner.observe_frame(_frame(0))
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 1)

        assert runner.drain_observation_outcomes().terminal_drops == ()

        runner.bind_evidence_context(0, "generation-zero", "cook")
        drops = runner.drain_observation_outcomes().terminal_drops
        assert [(drop.submission_sequence, drop.configuration_generation, drop.reason) for drop in drops] == [
            (submission.submission_sequence, 0, "runner-no-observation-outcome")
        ]
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_isolates_observation_failure_and_marks_the_next_frame_discontinuous():
    barrier = _ObservationBarrier()

    class RaisingCore(_ObservationRecordingCore):
        def __init__(self):
            super().__init__()
            self.failed = False
            self.failures = []

        def observe_frame(self, observation):
            super().observe_frame(observation)
            if not self.failed:
                self.failed = True
                raise FloatingPointError("learner failed")

        def observation_failure(self, observation, error):
            self.failures.append((observation, error))
            return {
                "role_generation": observation.role_generation,
                "eligible": False,
                "rejection_reasons": ("learner-exception",),
            }

    core = RaisingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    runner.bind_evidence_context(0, "session", "cook")
    try:
        assert barrier.first_waiting.wait(2.0)
        first = _frame(0)
        first_submission = runner.observe_frame(first)
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: ("update", 212.0) in core.calls)
        first_drain = runner.drain_observation_outcomes()
        assert len(first_drain.envelopes) == 1
        assert first_drain.envelopes[0].submission_sequence == first_submission.submission_sequence
        assert first_drain.envelopes[0].outcome["rejection_reasons"] == ("learner-exception",)
        assert core.failures and isinstance(core.failures[0][1], FloatingPointError)
        assert runner._thread.is_alive()

        runner.observe_frame(_frame(1))
        barrier.release.set()
        assert _wait_for(lambda: len(core.observations) == 2)
        assert core.observations[1].continuous is False
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_evicts_oldest_timestamps_and_marks_the_next_retained_frame():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        observations = [
            _frame(30),
            *(_frame(index) for index in range(29)),
            _frame(31),
            _frame(32),
            _frame(1),
        ]
        for observation in observations:
            runner.observe_frame(observation)

        assert runner.controller_state()["pending_observations"] == _MAX_PENDING_OBSERVATIONS
        assert runner.controller_state()["dropped_observations"] == 3
        barrier.release.set()

        assert _wait_for(lambda: len(core.observations) == _MAX_PENDING_OBSERVATIONS)
        retained_indexes = [*range(2, 29), 30, 31, 32]
        assert [observation.frame_end_s for observation in core.observations] == [
            float((index + 1) * 20) for index in retained_indexes
        ]
        assert core.observations[0].continuous is False
        assert all(observation.continuous for observation in core.observations[1:])
        assert next(observation for observation in observations if observation.frame_end_s == 60.0).continuous is True
        assert runner.controller_state()["pending_observations"] == 0
        assert runner.controller_state()["dropped_observations"] == 3
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_stop_flushes_accepted_observation_outcome():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"role_generation": observation.role_generation, "eligible": False}

    core = OutcomeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    runner.bind_evidence_context(0, "session", "cook")
    assert barrier.first_waiting.wait(2.0)
    submission = runner.observe_frame(_frame(0))
    errors = []
    stopper = threading.Thread(target=lambda: errors.append(runner.stop()))
    stopper.start()
    assert _wait_for(lambda: runner._stop_event.is_set())
    barrier.release.set()
    assert runner.observe_frame(_frame(1)) is None
    stopper.join(2.0)
    assert not stopper.is_alive()
    assert errors == [None]
    drain = runner.drain_observation_outcomes()
    assert [(item.submission_sequence, item.observation.frame_end_s) for item in drain] == [
        (submission.submission_sequence, 20.0)
    ]


def test_threaded_runner_swap_delivers_pre_swap_observation_to_old_core():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"core": self.name, "role_generation": observation.role_generation, "eligible": False}

    old = OutcomeCore("old")
    new = OutcomeCore("new")
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = runner
    new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        submission = runner.observe_frame(_frame(0))
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        later = runner.observe_frame(_frame(1))
        runner.bind_evidence_context(0, "old-session", "cook")
        runner.bind_evidence_context(1, "new-session", "cook")
        barrier.release.set()
        assert _wait_for(lambda: len(old.observations) == 1 and len(new.observations) == 1)
        envelopes = {item.submission_sequence: item for item in runner.drain_observation_outcomes()}
        assert envelopes[submission.submission_sequence].outcome["core"] == "old"
        assert envelopes[submission.submission_sequence].configuration_generation == submission.configuration_generation
        assert envelopes[later.submission_sequence].outcome["core"] == "new"
        assert envelopes[later.submission_sequence].configuration_generation == later.configuration_generation
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_seeds_swapped_core_before_reserved_observation_and_solve():
    barrier = _ObservationBarrier()
    old = _ObservationRecordingCore()
    new = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = runner
    new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.set_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 20.0))
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: len(new.observations) == 1 and ("update", 212.0) in new.calls)
        with old.lock:
            assert ("set_output", 20.0) in old.calls
        with new.lock:
            calls = list(new.calls)
        assert calls.index(("set_output", 20.0)) < calls.index(("observe_frame", 20.0))
        assert calls.index(("observe_frame", 20.0)) < calls.index(("update", 212.0))
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_submission_reports_exact_out_of_order_input_eviction():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        first = runner.observe_frame(_frame(100))
        for index in range(30):
            submission = runner.observe_frame(_frame(index))
        assert submission.evicted_sequence == 2
        assert first.evicted_sequence is None
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_submission_reports_self_eviction_for_new_earliest_frame():
    barrier = _ObservationBarrier()
    core = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        for index in range(1, 31):
            runner.observe_frame(_frame(index))
        self_evicted = runner.observe_frame(_frame(0))
        assert self_evicted.evicted_sequence == self_evicted.submission_sequence
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_stop_drains_reserved_swap_generation():
    barrier = _ObservationBarrier()

    class OutcomeCore(_ObservationRecordingCore):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def observe_frame(self, observation):
            super().observe_frame(observation)
            return {"core": self.name, "role_generation": observation.role_generation, "eligible": False}

    old = OutcomeCore("old")
    new = OutcomeCore("new")
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = new.runner = runner
    assert barrier.first_waiting.wait(2.0)
    runner.observe_frame(_frame(0))
    with runner._lock:
        runner._pending_core = new
        runner._pending_controller_type = None
    runner.observe_frame(_frame(1))
    runner.bind_evidence_context(0, "old-session", "cook")
    runner.bind_evidence_context(1, "new-session", "cook")
    stopper = threading.Thread(target=runner.stop)
    stopper.start()
    barrier.release.set()
    stopper.join(2.0)
    assert not stopper.is_alive()
    outcomes = runner.drain_observation_outcomes().envelopes
    assert [item.outcome["core"] for item in outcomes] == ["old", "new"]


def test_threaded_generation_specific_overflow_marks_only_reserved_generation():
    barrier = _ObservationBarrier()
    old = _ObservationRecordingCore()
    new = _ObservationRecordingCore()
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    old.runner = new.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(100))
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        for index in range(1, 32):
            runner.observe_frame(_frame(index))
        barrier.release.set()
        assert _wait_for(lambda: len(old.observations) == 1)
        assert old.observations[0].continuous is True
        barrier.release.set()
        assert _wait_for(lambda: len(new.observations) > 0)
        assert new.observations[0].continuous is False
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_runner_ignores_observations_for_a_core_without_a_learner():
    barrier = _ObservationBarrier()
    core = FakeCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()

        assert core.updated.wait(2.0)
        assert runner.controller_state()["pending_observations"] == 0
        assert runner.controller_state()["dropped_observations"] == 0
    finally:
        barrier.release.set()
        runner.stop()


class _DeliveryBlockingObservationCore(_ObservationRecordingCore):
    def __init__(self):
        super().__init__()
        self.first_delivery_started = threading.Event()
        self.release_first_delivery = threading.Event()

    def observe_frame(self, observation):
        super().observe_frame(observation)
        if len(self.observations) == 1:
            self.first_delivery_started.set()
            assert self.release_first_delivery.wait(2.0)


def test_threaded_runner_redrains_observations_enqueued_during_learner_delivery():
    barrier = _ObservationBarrier()
    core = _DeliveryBlockingObservationCore()
    runner = ThreadedControllerRunner(core, wait_for_period=barrier)
    core.runner = runner
    try:
        assert barrier.first_waiting.wait(2.0)
        runner.observe_frame(_frame(0))
        runner.submit(212.0)
        barrier.release.set()
        assert core.first_delivery_started.wait(2.0)

        runner.observe_frame(_frame(1))
        core.release_first_delivery.set()

        assert _wait_for(lambda: len(core.observations) == 2 and ("update", 212.0) in core.calls)
        with core.lock:
            calls = list(core.calls)
        first_update = next(index for index, call in enumerate(calls) if call == ("update", 212.0))
        assert calls[:first_update] == [("observe_frame", 20.0), ("observe_frame", 40.0)]
    finally:
        core.release_first_delivery.set()
        barrier.release.set()
        runner.stop()


def test_hold_submission_overflow_marks_exact_gap_and_rebuilds_online_learning_gates(hold_cycle, monkeypatch):
    """Dropped frame identity reaches the real learner as one discontinuity."""
    import controller.runtime.modes.hold as hold_module

    class _Recorder:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            pass

        def close(self):
            pass

    class _Gate:
        def __init__(self):
            self.waiting = threading.Event()
            self.release = threading.Event()
            self.closed = threading.Event()

        def __call__(self, _seconds):
            self.waiting.set()
            assert self.release.wait(2.0)

        def close(self):
            self.closed.set()
            self.release.set()

    class _ObservedMpcController(MpcController):
        def __init__(self):
            super().__init__(
                dict(MPC_DEFAULTS, enable_online_adaptation=False),
                "C",
                {"u_min": 0.1, "u_max": 0.9},
            )
            self.set_target(110.0)
            self.observed = []
            self._observed_condition = threading.Condition()

        def observe_frame(self, observation):
            outcome = {
                "role_generation": observation.role_generation,
                "eligible": observation.continuous,
                "rejection_reasons": () if observation.continuous else ("discontinuity",),
                "forecast_origin_evidence": (),
            }
            with self._observed_condition:
                self.observed.append((observation, outcome))
                self._observed_condition.notify_all()
            return outcome

        def wait_for_observations(self, count):
            with self._observed_condition:
                return self._observed_condition.wait_for(lambda: len(self.observed) >= count, timeout=10.0)

    def frame(index, realized_q):
        return replace(
            _frame(index),
            temp_c=100.0,
            requested_q=realized_q,
            baseline_q=realized_q,
            realized_q=realized_q,
            requested_auger_duty=realized_q,
        )

    recorder = _Recorder()
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda **_kwargs: recorder)
    gate = _Gate()
    core = _ObservedMpcController()
    runner = ThreadedControllerRunner(core, wait_for_period=gate)
    mode = hold_cycle(runner, controller="mpc")
    try:
        assert gate.waiting.wait(2.0)
        mode.setup()
        mode.control["cook_id"] = "hold-observation-overflow"
        trace = mode._control_trace
        assert trace is not None
        context = mode._trace_session_context()
        assert context is not None
        identity = trace.ensure_open(context, timestamp_ms=0)
        assert identity is not None
        learning = mode._hold_learning
        assert learning is not None
        learning.bind_generation(mode._runner_configuration_revision)

        for index in range(31):
            learning.submit_completed_observation(
                (index * 20, (index + 1) * 20),
                frame(index, 0.3),
            )
        assert runner.controller_state()["dropped_observations"] == 1

        gate.release.set()
        assert core.wait_for_observations(30)
        assert _wait_for(
            lambda: (
                learning.reconcile_outcomes(620.0) is None
                and len([record for record in recorder.records if isinstance(record.payload, ModelObservationPayload)])
                == 30
            )
        )

        initial = tuple(core.observed)
        assert [observation.frame_end_s for observation, _outcome in initial] == [
            float((index + 1) * 20) for index in range(1, 31)
        ]
        assert initial[0][0].continuous is False
        assert all(observation.continuous for observation, _outcome in initial[1:])
        assert initial[0][1]["rejection_reasons"] == ("discontinuity",)
        assert all(outcome["eligible"] for _observation, outcome in initial[1:])
    finally:
        gate.close()
        runner.stop()


def test_threaded_runner_forwards_safety_cancellation_in_submission_order():
    class CancellationCore(FakeCore):
        def __init__(self):
            super().__init__(period=0.001)
            self.calibration_calls = []
            self.cancelled = threading.Event()

        def request_calibration(self, command):
            self.calibration_calls.append(("command", command))

        def cancel_calibration(self, reason):
            self.calibration_calls.append(("cancel", reason))
            self.cancelled.set()

    core = CancellationCore()
    runner = ThreadedControllerRunner(core)
    runner.request_calibration("operator-command")
    runner.cancel_calibration("lid-open")

    assert core.cancelled.wait(1.0)
    runner.stop()
    assert core.calibration_calls == [("command", "operator-command"), ("cancel", "lid-open")]


def test_threaded_reconfigure_transfers_fifo_calibration_operations_to_replacement():
    class CalibrationCore(FakeCore):
        def __init__(self, name):
            super().__init__(period=0.001)
            self.name = name
            self.calibration_calls = []

        def request_calibration(self, command):
            self.calibration_calls.append(("command", command))

        def cancel_calibration(self, reason):
            self.calibration_calls.append(("cancel", reason))

    barrier = _ObservationBarrier()
    old = CalibrationCore("old")
    new = CalibrationCore("new")
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    try:
        assert barrier.first_waiting.wait(2.0)
        with runner._lock:
            runner._pending_core = new
            runner._pending_controller_type = None
        runner.request_calibration("start")
        runner.request_calibration("pause")
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: new.calibration_calls == [("command", "start"), ("command", "pause")])
        assert old.calibration_calls == []
    finally:
        barrier.release.set()
        runner.stop()


def test_threaded_public_reconfigure_transfers_queued_calibration_without_replay(monkeypatch):
    import controller.runtime.runner as runner_module

    class CalibrationCore(FakeCore):
        def __init__(self, name):
            super().__init__(period=0.001)
            self.name = name
            self.calibration_calls = []

        def request_calibration(self, command):
            self.calibration_calls.append(("command", command))

        def cancel_calibration(self, reason):
            self.calibration_calls.append(("cancel", reason))

    barrier = _ObservationBarrier()
    old = CalibrationCore("old")
    new = CalibrationCore("new")
    monkeypatch.setattr(
        runner_module,
        "_build_core",
        lambda settings, control, logger=None, model_persistence=None: (
            new,
            "Active",
        ),
    )
    runner = ThreadedControllerRunner(old, wait_for_period=barrier)
    try:
        assert barrier.first_waiting.wait(2.0)
        assert runner.reconfigure({"controller": {"selected": "mpc"}}, {}) == "Active"
        runner.request_calibration("start")
        runner.request_calibration("pause")
        runner.submit(212.0)
        barrier.release.set()

        assert _wait_for(lambda: new.calibration_calls == [("command", "start"), ("command", "pause")])
        assert old.calibration_calls == []
    finally:
        barrier.release.set()
        runner.stop()


class CloseAwareCore(FakeCore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.closed = 0

    def close(self):
        self.closed += 1


def test_threaded_reconfigure_closes_replaced_core_only_after_atomic_install(monkeypatch):
    import controller.runtime.runner as runner_module

    old = CloseAwareCore(period=0.001)
    new = CloseAwareCore(period=0.001, commands_fan=True)
    monkeypatch.setattr(
        runner_module,
        "_build_core",
        lambda settings, control, logger=None, model_persistence=None: (
            new,
            "Active",
        ),
    )
    runner = ThreadedControllerRunner(old)
    try:
        assert runner.reconfigure({"controller": {"selected": "mpc"}}, {}) == "Active"
        assert _wait_for(lambda: runner.configuration_revision() == 1)
        assert runner.commands_fan() is True
        assert old.closed == 1
        assert new.closed == 0
    finally:
        runner.stop()
    assert new.closed == 1


def test_threaded_reconfigure_closes_superseded_uninstalled_core(monkeypatch):
    import controller.runtime.runner as runner_module

    barrier = _ObservationBarrier()
    active = CloseAwareCore(period=0.001)
    first = CloseAwareCore(period=0.001)
    second = CloseAwareCore(period=0.001)
    builds = iter((first, second))
    monkeypatch.setattr(
        runner_module,
        "_build_core",
        lambda settings, control, logger=None, model_persistence=None: (
            next(builds),
            "Active",
        ),
    )
    runner = ThreadedControllerRunner(active, wait_for_period=barrier)
    try:
        assert barrier.first_waiting.wait(2.0)
        assert runner.reconfigure({"controller": {"selected": "mpc"}}, {}) == "Active"
        assert runner.reconfigure({"controller": {"selected": "mpc"}}, {}) == "Active"
        assert first.closed == 1
        assert second.closed == 0
        barrier.release.set()
        assert _wait_for(lambda: runner.configuration_revision() == 1)
        assert active.closed == 1
    finally:
        barrier.release.set()
        runner.stop()
    assert second.closed == 1


def test_threaded_stop_never_closes_a_core_under_a_live_timed_out_worker():
    core = BlockingCore()
    core.closed = 0
    core.close = lambda: setattr(core, "closed", core.closed + 1)
    runner = ThreadedControllerRunner(core)
    runner.submit(200.0)
    assert core.entered.wait(2.0)

    runner.stop()

    assert runner._thread.is_alive()
    assert core.closed == 0
    core.gate.set()
    runner._thread.join(timeout=2.0)
    assert not runner._thread.is_alive()
    assert core.closed == 1


def test_learning_lifecycle_dispatcher_drains_each_fit_off_the_controller_worker():
    class LearningCore(FakeCore):
        def __init__(self):
            super().__init__(period=0.001)
            self.pending = False
            self.submissions = 0
            self.deliveries = 0
            self.poll_threads = []
            self.condition = threading.Condition()

        def observe_frame(self, observation):
            with self.condition:
                if not self.pending:
                    self.pending = True
                    self.submissions += 1
                    self.condition.notify_all()
            return {
                "role_generation": observation.role_generation,
                "eligible": True,
                "rejection_reasons": (),
                "forecast_origin_evidence": (),
            }

        def poll_learning_off_path(self):
            with self.condition:
                self.poll_threads.append(threading.get_ident())
                if self.pending:
                    self.pending = False
                    self.deliveries += 1
                    self.condition.notify_all()

        def wait_for(self, predicate):
            with self.condition:
                return self.condition.wait_for(predicate, timeout=2.0)

    core = LearningCore()
    runner = ThreadedControllerRunner(core)
    try:
        runner.observe_frame(_frame(0))
        assert core.wait_for(lambda: core.submissions == 1 and core.deliveries == 1)
        runner.observe_frame(_frame(1))
        assert core.wait_for(lambda: core.submissions == 2 and core.deliveries == 2)
        assert core.poll_threads
        assert all(ident != runner._thread.ident for ident in core.poll_threads)
    finally:
        runner.stop()


def test_learning_process_starts_during_safe_construction_not_on_controller_worker(monkeypatch):
    import controller.model_learning.grey_runtime as grey_runtime_module

    instances = []

    class LazyLearning:
        def __init__(self, **_kwargs):
            self.start_thread = None
            self.observed_thread = None
            self.observed = threading.Event()
            instances.append(self)

        def start(self):
            if self.start_thread is None:
                self.start_thread = threading.get_ident()

        def observe_completed_frame(self, _frame, *, identifiability):
            if self.start_thread is None:
                self.start()
            self.observed_thread = threading.get_ident()
            self.observed.set()
            return type(
                "Observation",
                (),
                {
                    "history": type("History", (), {"accepted": True, "reasons": ()})(),
                    "completed_forecasts": (),
                    "request": None,
                },
            )()

        def register_causal_forecasts(self, *_args, **_kwargs):
            return ()

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return None

        def update_identity(self, *_args, **_kwargs):
            return None

        def close(self):
            return None

    monkeypatch.setattr(grey_runtime_module, "GreyLearningOrchestrator", LazyLearning)
    constructing_thread = threading.get_ident()
    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=True, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    runner = ThreadedControllerRunner(core)
    try:
        runner.observe_frame(_frame(0))
        assert instances[0].observed.wait(2.0), (
            runner._thread.is_alive(),
            runner.controller_state(),
            tuple(runner._pending_observations),
            instances[0].start_thread,
        )
        assert instances[0].start_thread == constructing_thread
        assert instances[0].observed_thread == runner._thread.ident
    finally:
        runner.stop()


def test_real_completed_forecast_survives_controller_to_runner_evidence_drain(monkeypatch):
    completed = CompletedForecastOrigin(
        forecast=ForecastOrigin(
            origin_sequence=0,
            origin_time_s=20.0,
            horizon_steps=3,
            role_generation=0,
            candidate_generation=1,
            incumbent_digest="a" * 64,
            challenger_digest="b" * 64,
            incumbent_prediction_c=99.0,
            challenger_prediction_c=101.0,
            temperature_band="below-target",
            phase="heating",
            ambient_source=_frame(0).ambient_source,
            calibration_fit=False,
        ),
        completion_time_s=80.0,
        observed_temperature_c=102.0,
    )
    forecast_evidence = ForecastOriginEvidence(
        origin_sequence=completed.origin_sequence,
        origin_time_ms=int(completed.forecast.origin_time_s * 1_000),
        completion_time_ms=int(completed.completion_time_s * 1_000),
        horizon_steps=completed.horizon_steps,
        incumbent_digest=completed.incumbent_digest,
        challenger_digest=completed.challenger_digest,
        incumbent_prediction_c=completed.forecast.incumbent_prediction_c,
        challenger_prediction_c=completed.forecast.challenger_prediction_c,
        observed_temperature_c=completed.observed_temperature_c,
        incumbent_error_c=completed.incumbent_error_c,
        challenger_error_c=completed.challenger_error_c,
        temperature_band=completed.temperature_band,
        phase=completed.forecast.phase,
        ambient_source=completed.ambient_source,
        calibration_fit=completed.calibration_fit,
    )
    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningRuntime.observe_frame",
        lambda _self, observation: {
            "role_generation": observation.role_generation,
            "eligible": True,
            "rejection_reasons": (),
            "model_digest": "a" * 64,
            "forecast_origin_evidence": (forecast_evidence,),
        },
    )
    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    runner = ThreadedControllerRunner(core)
    drained = []
    try:
        runner.bind_evidence_context(0, "session", "cook")
        runner.observe_frame(_frame(3))

        def collect():
            drained.extend(runner.drain_observation_outcomes().envelopes)
            return bool(drained)

        assert _wait_for(collect)
        assert drained[0].outcome.get("forecast_origin_evidence"), drained[0].outcome
        forecast_records = [record for record in drained[0].evidence if record.kind is EvidenceKind.FORECAST_ORIGIN]
        assert len(forecast_records) == 1
        assert isinstance(forecast_records[0].payload, ForecastOriginEvidence)
        assert forecast_records[0].payload.observed_temperature_c == 102.0
    finally:
        runner.stop()


def test_hold_publishes_controller_evaluation_even_when_grey_observation_is_not_trace_valid(monkeypatch):
    evaluation = ModelEvaluationPayload(
        decision_id="c" * 64,
        evaluated_at_ms=80_000,
        role_generation=0,
        promoted=False,
        committed=False,
        consecutive_wins=0,
        rejection_reasons=("no-completed-window",),
        incumbent_prediction_score=None,
        challenger_prediction_score=None,
        incumbent_braking_score=None,
        challenger_braking_score=None,
        sample_count=0,
        prospective_digest=None,
        window_start_ms=80_000,
        window_end_ms=80_000,
        incumbent_digest="a" * 64,
        challenger_digest="b" * 64,
        completed_origins=(),
        horizon_scores=(
            HorizonScorePayload(3, None, None, 0),
            HorizonScorePayload(15, None, None, 0),
        ),
        evaluation_duration_ms=1.0,
        challenger_model_kind="grey-box",
    )
    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningRuntime.observe_frame",
        lambda _self, observation: {
            "role_generation": observation.role_generation,
            "eligible": True,
            "rejection_reasons": (),
            "model_digest": evaluation.challenger_digest,
            "forecast_origin_evidence": (),
            "evaluation_payload": evaluation,
            "confidence_accepted": False,
            "confidence_already_persisted": False,
        },
    )
    activation_confidence = []

    class _Worker(ModelPersistenceWorker):
        def __init__(self):
            pass

        def submit_evidence(self, _record):
            return SimpleNamespace(accepted=True)

        def submit_activation_confidence(self, record):
            activation_confidence.append(record)
            receipt = DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def submit_activation_phase(self, _record, *, expected_phase):
            receipt = DurableActivationReceipt(accepted=True)
            receipt._complete(durable=True)
            return receipt

        def flush_and_stop(self, *, timeout=0.1):
            return True

    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
        activation_persistence=_Worker(),
    )
    runner = SyncControllerRunner(core)
    recorded = []
    try:
        runner.bind_evidence_context(0, "session", "cook")
        observation = _frame(3)
        first_submission = runner.observe_frame(observation)
        first_drain = runner.drain_observation_outcomes()
        assert first_drain.envelopes[0].submission_sequence == first_submission.submission_sequence
        confidence = [
            record for record in first_drain.envelopes[0].evidence if record.kind is EvidenceKind.CONFIDENCE_DECISION
        ]
        assert len(confidence) == 1
        assert confidence[0].payload.decision_id == evaluation.decision_id
        assert confidence[0].payload.blocked is True
        assert confidence[0].payload.reason == "no-completed-window"

        class _TraceRecorder:
            def __init__(self):
                self.records = []
                self.flushes = []
                self.closed = False

            def record(self, record):
                self.records.append(record)

            def flush_due(self, now_ms):
                self.flushes.append(now_ms)

            def close(self):
                self.closed = True

        trace_warnings = []
        trace = ControlTraceSession(_TraceRecorder(), warning=trace_warnings.append)
        identity = trace.ensure_open(
            TraceSessionContext(
                controller=ControllerType.MPC,
                controller_config={},
                temperature_unit="C",
                control_period_seconds=1.0,
                fallback_model=None,
                runner_snapshot_fallback_safe=False,
                pulse_slot_seconds=10.0,
                pulse_frame_seconds=20.0,
                fan_authority=False,
                fan_pwm_capable=False,
                fan_min_duty=0.0,
                fan_max_duty=100.0,
                setpoint=120.0,
                ambient_temperature=20.0,
                software_version="test",
                build_version="test",
                cook_id="cook",
                runner_generation=0,
            ),
            timestamp_ms=0,
        )
        assert identity is not None
        normal_evidence = []

        class _EvidencePersistence:
            evidence_blocked = False
            failed = False

            def submit_evidence_batch(self, records):
                normal_evidence.extend(records)
                return EvidenceSubmission(accepted=True)

            def submit_checkpoint(self, name, snapshot):
                return True

            def flush_and_stop(self):
                return True

        class _Logger:
            def info(self, message):
                return None

            def warning(self, message):
                return None

            def error(self, message):
                return None

        persistence = _EvidencePersistence()
        learning = HoldLearningRuntime(
            runner=runner,
            model_store=None,
            persistence=persistence,
            trace=trace,
            controller_name="mpc",
            logger=_Logger(),
            initial_generation=0,
        )
        learning.bind_generation(0)
        learning.submit_completed_observation((60, 80), observation)

        def record(event_kind, payload, timestamp_ms):
            recorded.append((event_kind, payload, timestamp_ms))
            return True

        trace.record = record

        learning.reconcile_outcomes(100.0)
        learning.reconcile_outcomes(100.0)

        assert [item[0] for item in recorded] == [
            TraceEventKind.MODEL_OBSERVATION,
            TraceEventKind.MODEL_EVALUATION,
        ]
        assert isinstance(recorded[0][1], ModelObservationPayload)
        assert recorded[0][1].eligible is False
        assert recorded[0][1].rejection_reasons == ("observation-outcome-malformed",)
        assert recorded[1][1] == evaluation
        assert recorded[1][1] is not evaluation
        assert len(activation_confidence) == 1
        assert activation_confidence[0].payload.decision_id == evaluation.decision_id
        assert all(record.kind is not EvidenceKind.CONFIDENCE_DECISION for record in normal_evidence)
    finally:
        runner.stop()


def test_failed_active_recovery_terminalizes_before_configured_pair_can_update() -> None:
    core = MpcController(
        dict(MPC_DEFAULTS, enable_online_adaptation=False, control_period=0.001),
        "C",
        {"u_min": 0.1, "u_max": 0.9},
    )
    incumbent = core.active_control_pair.descriptor
    candidate_settings = dict(core.cfg)
    candidate_settings["theta"] = float(candidate_settings["theta"]) + 1.0
    candidate = core._pair_factory.descriptor(
        core._pair_factory.configured(
            candidate_settings,
            candidate_generation=incumbent.candidate_generation + 1,
            role_generation=incumbent.role_generation + 1,
        )
    )
    active = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent,
        candidate=candidate,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="failed-active-recovery",
    ).transition(ActivationPhase.ACTIVE)
    persisted = ModelActivationState(
        active_snapshot_json=json.dumps(active.candidate.to_dict()["configuration"]),
        rollback_snapshot_json=json.dumps(active.rollback.to_dict()["configuration"]),
        evidence_decision_id=active.decision_id,
        controller_configuration_digest=active.candidate.ownership_digest,
        role_generation=active.candidate.role_generation,
        phase=active.phase.value,
        transaction_id=active.transaction_id,
        incumbent_pair_json=json.dumps(active.incumbent.to_dict()),
        candidate_pair_json=json.dumps(active.candidate.to_dict()),
        rollback_pair_json=json.dumps(active.rollback.to_dict()),
        origin=active.origin.value,
        policy=active.policy.value,
        candidate_generation=active.candidate.candidate_generation,
        candidate_digest=active.candidate.model_digest,
        reason=None,
    )
    update_calls = []
    original_update = core.update
    core.update = lambda current: (update_calls.append(current), original_update(current))[1]
    core._pair_factory.restore = lambda _descriptor: (_ for _ in ()).throw(
        RuntimeError("candidate artifact unavailable")
    )
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.restore_activation(persisted, ())
        runner.submit(200.0)
        assert _wait_for(lambda: runner.mpc_activation_terminated)
        time.sleep(0.02)
        assert update_calls == []
        assert runner.latest().revision == 0
    finally:
        runner.stop()


class _RefusingRestoreCore(_OrderRecordingCore):
    """A core that queues like any other and then declines the snapshot."""

    def restore_model(self, snapshot):
        super().restore_model(snapshot)
        return False


def test_a_restore_the_core_refuses_is_reported_back_from_the_worker():
    """Queued is not adopted, so the worker's verdict has to travel back.

    `restore_model` returns True for accepted-for-restore; the core decides on
    the worker thread. Without a return path a refusal reaches nothing that can
    log it or correct the session's model provenance.
    """
    core = _RefusingRestoreCore()
    runner = ThreadedControllerRunner(core)
    try:
        assert runner.drain_restore_outcome() is None
        assert runner.restore_model({"revision": 7}) is True
        runner.submit(212.0)

        assert _wait_for(lambda: runner.drain_restore_outcome() is False)
        assert runner.drain_restore_outcome() is None
    finally:
        runner.stop()
