import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import controller.runtime.runner as runner_module
from common.control_trace import ActuationMode, ResultStaleState
from common.model_evidence import SessionSummaryEvidence
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerLearningDiagnostics
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.pid_sp import Controller as PidSpController
from controller.runtime.runner import (
    ControllerUpdateResult,
    SyncControllerRunner,
    _build_core,
    _capture_completed_result,
    build_runner,
)
from tests.fakes.runner import FakeControllerRunner


def test_runner_import_does_not_load_optional_linear_mpc_dependencies():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['scipy'] = None; import controller.runtime.runner",
        ],
        cwd=Path(__file__).parents[3],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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


class _Core:
    def __init__(self):
        self.target = None
        self.period = 5.0

    def set_target(self, sp):
        self.target = sp

    def update(self, temp):
        return {"cycle_ratio": 0.4, "fan": {"duty": 60}}

    def get_control_period(self):
        return self.period

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def get_status(self):
        return {"target": self.target}

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None


def test_sync_runner_normalizes_dict_output():
    r = SyncControllerRunner(_Core())
    r.set_target(225)
    out = r.latest_from(200.0)
    assert isinstance(out, ControllerUpdateResult)
    assert out.cycle_ratio == 0.4
    assert out.fan == {"duty": 60}
    assert out.input_temperature == 200.0


def test_sync_runner_float_output_has_no_fan():
    class FloatCore(_Core):
        def update(self, temp):
            return 0.25

    out = SyncControllerRunner(FloatCore()).latest_from(190.0)
    assert out.cycle_ratio == 0.25 and out.fan is None


def test_sync_runner_forwards_safety_cancellation_without_an_operator_command():
    class CalibrationCore(_Core):
        def __init__(self):
            super().__init__()
            self.calls = []

        def request_calibration(self, command):
            self.calls.append(("command", command))

        def cancel_calibration(self, reason):
            self.calls.append(("cancel", reason))

    core = CalibrationCore()
    runner = SyncControllerRunner(core)

    runner.request_calibration("operator-command")
    runner.cancel_calibration("lid-open")

    assert core.calls == [("command", "operator-command"), ("cancel", "lid-open")]


def test_sync_runner_preserves_actuation_mode_and_reports_solve_quality():
    class Clock:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

        def advance(self, seconds):
            self.value += seconds

    class TimedCore(_Core):
        def __init__(self, clock):
            super().__init__()
            self.clock = clock
            self.duration = 6.0

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def update(self, temp):
            self.clock.advance(self.duration)
            return 0.25

    clock = Clock()
    core = TimedCore(clock)
    runner = SyncControllerRunner(core, monotonic_clock=clock, wall_clock=clock)

    first = runner.latest_from(190.0)
    assert runner.actuation_mode() is ActuationMode.FRAMED_PULSE
    assert first.solve_duration_seconds == 6.0
    assert first.result_age_seconds == 0.0
    assert first.deadline_miss_count == first.consecutive_deadline_miss_count == 1
    assert first.stale_state is ResultStaleState.FRESH

    core.duration = 0.0
    second = runner.latest_from(191.0)
    assert second.revision == first.revision + 1
    assert second.deadline_miss_count == 1
    assert second.consecutive_deadline_miss_count == 0
    assert runner.controller_state()["result_stale_state"] == ResultStaleState.FRESH.value


def test_sync_runner_result_revision_and_status_match_one_completed_update():
    class AtomicCore(_Core):
        def __init__(self):
            super().__init__()
            self.completed = 0

        def update(self, temp):
            self.completed += 1
            return 0.25

        def get_status(self):
            return {"completed": self.completed}

    runner = SyncControllerRunner(AtomicCore())
    first = runner.latest_from(190.0)
    second = runner.latest_from(191.0)

    assert (first.revision, first.status) == (1, {"completed": 1})
    assert (second.revision, second.status) == (2, {"completed": 2})
    assert first.solve_end_monotonic >= first.solve_start_monotonic
    assert first.solve_duration_seconds == first.solve_end_monotonic - first.solve_start_monotonic
    assert (first.input_temperature, second.input_temperature) == (190.0, 191.0)


def test_completed_result_owns_the_learning_snapshot_from_that_update():
    class LearningCore(_Core):
        def __init__(self):
            super().__init__()
            self.learning_state = {"generation": 6}
            self.learning_calls = 0

        def update(self, temp):
            self.learning_state["generation"] += 1
            return 0.25

        def get_learning_diagnostics(self):
            self.learning_calls += 1
            return ControllerLearningDiagnostics(schema_version=1, state=self.learning_state)

    core = LearningCore()
    monotonic_times = iter((10.0, 11.0))

    result = _capture_completed_result(
        core,
        225.0,
        7,
        monotonic_clock=lambda: next(monotonic_times),
        wall_clock=lambda: 100.0,
    )
    core.learning_state["generation"] = 8

    assert result.revision == 7
    assert result.learning is not None
    assert result.learning.as_json()["generation"] == 7
    assert core.learning_calls == 1


def test_sync_pid_sp_completed_update_captures_one_aligned_learning_status_snapshot(monkeypatch):
    core = PidSpController(
        {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.001},
        "F",
        {"u_min": 0.1, "u_max": 0.9},
    )
    core.set_target(225.0)
    original_capability = core.get_learning_diagnostics
    capability_calls = 0

    def counted_capability():
        nonlocal capability_calls
        capability_calls += 1
        state = original_capability().as_json()
        state["capture_sequence"] = capability_calls
        return ControllerLearningDiagnostics(schema_version=1, state=state)

    monkeypatch.setattr(core, "get_learning_diagnostics", counted_capability)
    runner = SyncControllerRunner(core)
    result = runner.latest_from(200.0)

    assert capability_calls == 1
    assert result.learning is not None
    assert result.learning.as_json()["capture_sequence"] == 1
    assert result.status is not None
    assert runner.controller_state()["learning"] == result.learning.as_json()


def test_completed_result_rejects_an_invalid_learning_capability_value():
    class InvalidLearningCore(_Core):
        def get_learning_diagnostics(self):
            return {"generation": 1}

    monotonic_times = iter((10.0, 11.0))

    with pytest.raises(TypeError, match="ControllerLearningDiagnostics or None"):
        _capture_completed_result(
            InvalidLearningCore(),
            225.0,
            1,
            monotonic_clock=lambda: next(monotonic_times),
            wall_clock=lambda: 100.0,
        )


def test_sync_controller_state_thaws_nested_completed_status_without_mutating_result():
    class NestedStatusCore(_Core):
        def __init__(self):
            super().__init__()
            self.status = {"nested": {"samples": [1.0]}}

        def get_status(self):
            return self.status

    core = NestedStatusCore()
    runner = SyncControllerRunner(core)
    result = runner.latest_from(190.0)
    core.status["nested"]["samples"].append(2.0)

    state = runner.controller_state()
    assert state["nested"] == {"samples": [1.0]}
    assert json.loads(json.dumps(state)) == state
    state["nested"]["samples"].append(3.0)
    assert result.status == {"nested": {"samples": (1.0,)}}
    assert runner.controller_state()["nested"] == {"samples": [1.0]}


class _RecordingLogger:
    def __init__(self):
        self.exceptions = []
        self.errors = []

    def exception(self, msg):
        self.exceptions.append(msg)

    def error(self, msg):
        self.errors.append(msg)


def test_build_runner_logs_on_load_failure_when_logger_given():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}
    logger = _RecordingLogger()

    runner, status = build_runner(settings, control, logger=logger)

    assert runner is None
    assert status == "Inactive"
    # Two exceptions, not one: build_runner now ALSO attempts the fallback
    # controller before giving up. This settings dict has no "pid" config
    # either, so the fallback fails too and the cycle really is Inactive.
    assert [
        "Error occurred loading controller module. Trace dump: ",
        "Error occurred building the [pid] controller. Trace dump: ",
    ] == logger.exceptions
    # And the user is told, rather than only the log.
    assert any("neither could the fallback" in msg for msg in logger.errors)


def test_build_runner_does_not_require_logger():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}

    runner, status = build_runner(settings, control)

    assert runner is None
    assert status == "Inactive"


def test_build_core_logs_on_load_failure_when_logger_given():
    settings = {"controller": {"selected": "does_not_exist", "config": {}}, "globals": {"units": "F"}, "cycle_data": {}}
    control = {"primary_setpoint": 225}
    logger = _RecordingLogger()

    core, status = _build_core(settings, control, logger=logger)

    assert core is None
    assert status == "Inactive"
    assert len(logger.exceptions) == 1


def test_non_mpc_local_core_keeps_neutral_optional_projections(monkeypatch) -> None:
    class LocalCore:
        def __init__(self, config, units, cycle_data, *, logger=None):
            del config, units, cycle_data, logger
            self.target = None

        def set_target(self, value):
            self.target = value

        def update(self, _temperature):
            return 0.25

        def wants_async(self):
            return False

        def commands_fan(self):
            return False

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def get_control_period(self):
            return None

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=LocalCore),
    )
    settings = {
        "controller": {"selected": "local", "config": {"local": {}}},
        "globals": {"units": "C"},
        "cycle_data": {},
    }

    core, status = _build_core(settings, {"primary_setpoint": 100})
    result = _capture_completed_result(
        core,
        90.0,
        1,
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 2.0,
    )

    assert status == "Active"
    assert result.diagnostics is None
    assert result.learning is None
    assert result.allocation is None
    assert result.baseline_allocation is None
    assert result.calibration is None


def test_build_core_never_returns_active_when_set_target_raises():
    """Pins the contract ControllerBase.get_status() (controller/base.py) relies
    on: a core only reaches "Active" -- and only then gets wrapped in a runner
    that might call get_status() -- if set_target() already succeeded on it,
    because construction and set_target() share the same try/except here."""
    import sys
    import types

    class _RaisesOnSetTarget:
        def __init__(self, config, units, cycle_data):
            pass

        def set_target(self, sp):
            raise RuntimeError("boom")

    fake_module = types.ModuleType("controller.faketype")
    fake_module.Controller = _RaisesOnSetTarget
    sys.modules["controller.faketype"] = fake_module
    try:
        settings = {
            "controller": {"selected": "faketype", "config": {"faketype": {}}},
            "globals": {"units": "F"},
            "cycle_data": {},
        }
        control = {"primary_setpoint": 225}

        core, status = _build_core(settings, control)

        assert core is None
        assert status == "Inactive"
    finally:
        del sys.modules["controller.faketype"]


def test_sync_runner_wants_async_reflects_core_and_stop_is_noop():
    from controller.runtime.runner import SyncControllerRunner

    class _Core:
        def __init__(self, wants):
            self._wants = wants

        def wants_async(self):
            return self._wants

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

    # Delegates to the core (not hardcoded): True core -> True, False core -> False.
    assert SyncControllerRunner(_Core(True)).wants_async() is True
    assert SyncControllerRunner(_Core(False)).wants_async() is False
    SyncControllerRunner(_Core(False)).stop()  # exists + harmless no-op for the sync runner


class _RecordingCore:
    def __init__(self, status=None):
        self.applied = []
        self._status = status
        self.restored = None
        self.snapshot = {"revision": 3, "K": 700.0}

    def update(self, temp):
        return 0.5

    def set_target(self, sp):
        pass

    def get_control_period(self):
        return None

    def commands_fan(self):
        return False

    def wants_async(self):
        return False

    def actuation_mode(self):
        return ActuationMode.FRAMED_PULSE

    def set_output(self, applied):
        self.applied.append(applied)

    def get_status(self):
        return self._status

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored = snapshot
        return True

    def trace_diagnostics(self):
        return None

    def trace_allocation(self):
        return None


class _RecordingMpcCoreWithoutObservationFailure(_RecordingCore):
    def __init__(
        self,
        config=None,
        units=None,
        cycle_data=None,
        *,
        activation_persistence=None,
        trajectory_repository=None,
        fit_partition_digest=None,
        grey_learning_process=None,
        logger=None,
    ):
        del (
            config,
            units,
            cycle_data,
            activation_persistence,
            trajectory_repository,
            fit_partition_digest,
            grey_learning_process,
            logger,
        )
        super().__init__()
        self.learning_calls = []
        self.observation_outcome = {
            "eligible": False,
            "rejection_reasons": ("recorded-rejection",),
            "forecast_origin_evidence": (),
        }
        self.schedule_result = True

    def estimator_seed_requirements(self) -> tuple[float, int]:
        self.learning_calls.append(("estimator_seed_requirements",))
        return 60.0, 8

    def bind_estimator_seed_source(
        self,
        source: Callable[[float, int], object] | None,
    ) -> None:
        self.learning_calls.append(("bind_estimator_seed_source", source))

    def bind_learning_identity(
        self,
        session_id: str,
        cook_id: str | None,
        role_generation: int,
    ) -> None:
        self.learning_calls.append(
            (
                "bind_learning_identity",
                session_id,
                cook_id,
                role_generation,
            )
        )

    def observe_frame(self, observation: FrameObservation) -> object:
        self.learning_calls.append(("observe_frame", observation))
        return self.observation_outcome

    def poll_learning_off_path(
        self,
        *,
        live_origin: CandidateOrigin | None = None,
    ) -> object:
        self.learning_calls.append(("poll_learning_off_path", live_origin))
        return object()

    def schedule_corpus_fit(self, origin: CandidateOrigin) -> bool:
        self.learning_calls.append(("schedule_corpus_fit", origin))
        return self.schedule_result

    def _schedule_corpus_fit_ticket(
        self,
        origin: CandidateOrigin,
    ) -> str | None:
        self.learning_calls.append(("_schedule_corpus_fit_ticket", origin))
        return "fit-ticket"

    def _consume_terminal_corpus_fit_ticket(
        self,
        ticket: str,
        origin: CandidateOrigin,
    ) -> bool:
        self.learning_calls.append(
            (
                "_consume_terminal_corpus_fit_ticket",
                ticket,
                origin,
            )
        )
        return True

    def fail_corpus_fit(
        self,
        ticket: str,
        error: BaseException | str,
    ) -> None:
        self.learning_calls.append(("fail_corpus_fit", ticket, error))

    def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
        self.learning_calls.append(("get_learning_diagnostics",))
        return ControllerLearningDiagnostics(schema_version=1, state={})


class _CompleteRecordingMpcCore(_RecordingMpcCoreWithoutObservationFailure):
    def observation_failure(
        self,
        observation: FrameObservation,
        error: BaseException,
    ) -> object:
        self.learning_calls.append(("observation_failure", observation, error))
        return self.observation_outcome


def test_sync_runner_installs_complete_mpc_learning_capability_once():
    core = _CompleteRecordingMpcCore()

    runner = SyncControllerRunner(core)

    assert runner._learning_core is core


def test_sync_runner_binds_mpc_identity_and_evidence_context_directly():
    core = _CompleteRecordingMpcCore()
    runner = SyncControllerRunner(core)
    observation = _frame(0)

    runner.bind_evidence_context(0, "session", "cook")
    runner.observe_frame(observation)
    envelope = runner.drain_observation_outcomes().envelopes[0]

    assert core.learning_calls == [
        ("bind_learning_identity", "session", "cook", 0),
        ("observe_frame", observation),
    ]
    assert [(record.session_id, record.cook_id) for record in envelope.evidence] == [("session", "cook")]


def test_sync_runner_observes_frame_through_mpc_capability_directly():
    core = _CompleteRecordingMpcCore()
    runner = SyncControllerRunner(core)
    observation = _frame(0)

    runner.observe_frame(observation)

    assert core.learning_calls == [("observe_frame", observation)]


def test_sync_runner_schedules_corpus_fit_through_mpc_capability_directly():
    core = _CompleteRecordingMpcCore()
    runner = SyncControllerRunner(core)

    scheduled = runner.schedule_corpus_fit(CandidateOrigin.PASSIVE_ONLINE)

    assert scheduled is True
    assert core.learning_calls == [("schedule_corpus_fit", CandidateOrigin.PASSIVE_ONLINE)]


def test_sync_runner_reads_and_binds_estimator_seed_through_mpc_capability_directly():
    core = _CompleteRecordingMpcCore()
    runner = SyncControllerRunner(core)

    def seed_source(window_seconds: float, minimum_samples: int) -> object:
        return (window_seconds, minimum_samples)

    requirements = runner.estimator_seed_requirements()
    runner.bind_estimator_seed_source(seed_source)

    assert requirements == (60.0, 8)
    assert core.learning_calls == [
        ("estimator_seed_requirements",),
        ("bind_estimator_seed_source", seed_source),
    ]


def test_sync_runner_buffers_non_mpc_evidence_without_learning_calls():
    runner = SyncControllerRunner(_RecordingCore())
    observation = _frame(0)

    submission = runner.observe_frame(observation)
    assert runner.drain_observation_outcomes().terminal_drops == ()

    runner.bind_evidence_context(0, "session", "cook")
    drain = runner.drain_observation_outcomes()

    assert [
        (
            drop.submission_sequence,
            drop.configuration_generation,
            drop.observation,
            drop.reason,
        )
        for drop in drain.terminal_drops
    ] == [
        (
            submission.submission_sequence,
            0,
            observation,
            "runner-no-observation-outcome",
        )
    ]
    assert runner._learning_core is None


def test_build_core_rejects_selected_mpc_missing_observation_failure_before_observation(
    monkeypatch,
):
    observation_calls = []

    class MissingObservationFailureMpcCore(_RecordingMpcCoreWithoutObservationFailure):
        def observe_frame(self, observation: FrameObservation) -> object:
            observation_calls.append(observation)
            raise RuntimeError("observation failure must never be reached")

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=MissingObservationFailureMpcCore),
    )
    logger = _RecordingLogger()
    settings = {
        "controller": {"selected": "mpc", "config": {"mpc": {}}},
        "globals": {"units": "C"},
        "cycle_data": {},
    }

    core, status = _build_core(
        settings,
        {"primary_setpoint": 100},
        logger=logger,
    )

    assert core is None
    assert status == "Inactive"
    assert observation_calls == []
    assert any("missing required MPC learning capability" in message for message in logger.exceptions)


def test_sync_runner_forwards_set_output():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    applied = AppliedOutput(0.4, OutputSource.CONTROLLER, 12.0, requested=0.4)
    runner.set_output(applied)
    assert core.applied == [applied]


def test_sync_runner_drains_the_exact_observation_outcome_once():
    outcome = {"role_generation": 0, "eligible": False}
    core = _CompleteRecordingMpcCore()
    core.observation_outcome = outcome
    runner = SyncControllerRunner(core)
    runner.bind_evidence_context(0, "session", "cook")
    observation = _frame(0)

    sequence = runner.observe_frame(observation)
    envelopes = runner.drain_observation_outcomes()

    assert [(item.submission_sequence, item.observation, item.outcome) for item in envelopes] == [
        (sequence.submission_sequence, observation, outcome)
    ]
    assert runner.drain_observation_outcomes().envelopes == ()


def test_fake_runner_bounds_outcomes_and_reports_exact_evictions():
    runner = FakeControllerRunner()
    runner.bind_evidence_context(0, "session", "cook")
    runner.observation_outcome = {"role_generation": 0, "eligible": False}
    for index in range(31):
        runner.observe_frame(_frame(index))

    drain = runner.drain_observation_outcomes()

    assert drain.dropped_count == 1
    assert drain.dropped_sequences == (1,)
    assert [envelope.submission_sequence for envelope in drain] == list(range(2, 32))


def test_fake_runner_copies_mutable_outcomes_before_generation_release():
    outcome = {
        "eligible": False,
        "rejection_reasons": ("original-rejection",),
        "forecast_origin_evidence": (),
    }
    runner = FakeControllerRunner()
    runner.observation_outcome = outcome
    runner.observe_frame(_frame(0))

    outcome["eligible"] = True
    outcome["rejection_reasons"] = ()
    runner.bind_evidence_context(0, "session", "cook")
    envelope = runner.drain_observation_outcomes().envelopes[0]

    assert envelope.outcome == {
        "eligible": False,
        "rejection_reasons": ("original-rejection",),
        "forecast_origin_evidence": (),
    }
    assert len(envelope.evidence) == 1
    summary = envelope.evidence[0].payload
    assert isinstance(summary, SessionSummaryEvidence)
    assert summary.accepted_observations == 0
    assert summary.rejection_reasons == ("original-rejection",)


def test_fake_runner_resets_only_delivered_eviction_counters():
    runner = FakeControllerRunner()
    runner.observation_outcome = {
        "eligible": False,
        "rejection_reasons": (),
        "forecast_origin_evidence": (),
    }
    for index in range(30):
        runner.observe_frame(_frame(index))
    runner.reconfigure({}, {})
    runner.bind_evidence_context(1, "new-session", "new-cook")
    runner.observe_frame(_frame(30))

    new_generation = runner.drain_observation_outcomes()

    assert [envelope.submission_sequence for envelope in new_generation.envelopes] == [31]
    assert new_generation.terminal_drops == ()
    assert new_generation.dropped_count == 0
    assert new_generation.dropped_sequences == ()

    runner.bind_evidence_context(0, "old-session", "old-cook")
    old_generation = runner.drain_observation_outcomes()

    assert [drop.submission_sequence for drop in old_generation.terminal_drops] == [1]
    assert old_generation.dropped_count == 1
    assert old_generation.dropped_sequences == (1,)


def test_sync_runner_reports_exact_outcome_evictions():
    core = _CompleteRecordingMpcCore()
    core.observation_outcome = {"role_generation": 0, "eligible": False}
    runner = SyncControllerRunner(core)
    runner.bind_evidence_context(0, "session", "cook")
    for index in range(31):
        runner.observe_frame(_frame(index))

    drain = runner.drain_observation_outcomes()

    assert drain.dropped_count == 1
    assert drain.dropped_sequences == (1,)
    assert [envelope.submission_sequence for envelope in drain] == list(range(2, 32))


def test_sync_runner_bounds_exact_eviction_metadata_to_unresolved_capacity():
    core = _CompleteRecordingMpcCore()
    core.observation_outcome = {"role_generation": 0, "eligible": False}
    runner = SyncControllerRunner(core)
    for index in range(91):
        runner.observe_frame(_frame(index))

    drain = runner.drain_observation_outcomes()

    assert drain.dropped_count == 0
    assert drain.dropped_sequences == ()
    assert drain.terminal_drops == ()

    runner.bind_evidence_context(0, "session", "cook")
    released = runner.drain_observation_outcomes()
    assert [drop.submission_sequence for drop in released.terminal_drops] == list(range(1, 62))
    assert released.dropped_count == 61
    assert released.dropped_sequences == tuple(range(2, 62))


def test_sync_runner_forwards_snapshot_and_restore():
    core = _RecordingCore()
    runner = SyncControllerRunner(core)
    assert runner.get_model_snapshot() == {"revision": 3, "K": 700.0}
    assert runner.restore_model({"revision": 9}) is True
    assert core.restored == {"revision": 9}


def test_sync_runner_restore_model_propagates_rejection():
    class _RejectingCore(_RecordingCore):
        def restore_model(self, snapshot):
            self.restored = snapshot
            return False

    core = _RejectingCore()
    assert SyncControllerRunner(core).restore_model({"revision": 1}) is False
    assert core.restored == {"revision": 1}


def test_controller_state_prefers_get_status():
    runner = SyncControllerRunner(_RecordingCore(status={"K": 700.0}))
    assert runner.controller_state() == {"K": 700.0}


def test_controller_state_treats_empty_status_dict_as_present():
    # {} is falsy but not None: get_status() answered, so it wins over the
    # dunder-dict fallback rather than being mistaken for "no answer".
    core = _RecordingCore(status={})
    core.p = 0.25
    assert SyncControllerRunner(core).controller_state() == {}


def test_controller_state_does_not_expose_core_internals_when_status_absent():
    core = _RecordingCore(status=None)
    core.p = 0.25
    assert SyncControllerRunner(core).controller_state() == {}


def test_controller_state_from_get_status_is_a_copy():
    # _RecordingCore.get_status() returns the same cached dict on every call,
    # so a runner that handed back that dict as-is would let this mutation
    # leak into the next read -- a fresh-dict-per-call get_status() could not
    # fail this test.
    core = _RecordingCore(status={"K": 700.0})
    runner = SyncControllerRunner(core)
    state = runner.controller_state()
    state["K"] = 999
    assert runner.controller_state() == {"K": 700.0}


def test_sync_reconfigure_installs_complete_core_before_closing_replaced_core(monkeypatch):
    import controller.runtime.runner as runner_module

    events = []

    class CloseCore(_RecordingCore):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def close(self):
            events.append((f"close-{self.name}", runner._core.name))

    old = CloseCore("old")
    new = CloseCore("new")
    process_owner = object()
    runner = SyncControllerRunner(old, grey_learning_process=process_owner)

    def build_core(
        settings,
        control,
        *,
        logger=None,
        model_persistence=None,
        trajectory_repository=None,
        fit_partition_digest=None,
        grey_learning_process=None,
    ):
        del settings, control
        assert logger is None
        assert model_persistence is None
        assert trajectory_repository is None
        assert fit_partition_digest is None
        assert grey_learning_process is process_owner
        return new, "Active"

    monkeypatch.setattr(runner_module, "_build_core", build_core)

    assert runner.reconfigure({"controller": {"selected": "mpc"}}, {}) == "Active"

    assert runner._core is new
    assert events == [("close-old", "new")]
    assert runner.configuration_revision() == 1
    runner.stop()
    assert events[-1] == ("close-new", "new")


def test_sync_stop_closes_final_core_exactly_once():
    class CloseCore(_RecordingCore):
        def __init__(self):
            super().__init__()
            self.closed = 0

        def close(self):
            self.closed += 1

    core = CloseCore()
    runner = SyncControllerRunner(core)
    runner.stop()
    runner.stop()
    assert core.closed == 1
