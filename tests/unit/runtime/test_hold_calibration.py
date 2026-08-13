from dataclasses import replace

import pytest

from common.controller_model_state import CheckpointSaveOutcome
from common.control_trace import ResultStaleState, TraceEventKind
from common.model_evidence import EvidenceKind
from controller.model_learning.calibration import CalibrationDecision, CalibrationProgress
from controller.mpc_allocator import allocate
from controller.runtime.model_persistence import EvidenceSubmission
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import ControllerUpdateResult
from controller.runtime.framed_pulse import FramedPulseRuntime
from controller.applied_output import FrameFeedbackDisposition, OutputSource

from tests.fakes.runner import FakeControllerRunner
def _runtime(mode) -> FramedPulseRuntime:
    runtime = mode._framed_pulse
    assert isinstance(runtime, FramedPulseRuntime)
    return runtime

def _trace(mode):
    trace = mode._control_trace
    assert trace is not None
    return trace



def _advance_runtime(mode, now, actual_auger_on, *, ptemp=None, apply_transition=True):
    result = _runtime(mode).advance(
        now,
        actual_auger_on,
        sample=mode._framed_sample(ptemp),
        prior_output_source=_trace(mode).applied_state.output_source,
    )
    transition = result.decision.transition
    if apply_transition and transition is not None:
        if transition.command_on:
            mode.grill.auger_on()
        else:
            mode.grill.auger_off()
    mode._dispatch_framed_result(result, record_terminal_trace=False)
    return result.decision




def _result(
    revision=1,
    *,
    baseline=0.3,
    probe=0.0,
    command_revision=1,
    command_action="start",
    stage: str | None = None,
    completed_stages: tuple[str, ...] = (),
    active: bool | None = None,
):
    baseline_allocation = allocate(baseline, u_max=0.9, fan_min_pct=0.0, fan_max_pct=100.0, enable_fan=False)
    allocation = allocate(baseline + probe, u_max=0.9, fan_min_pct=0.0, fan_max_pct=100.0, enable_fan=False)
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan=None,
        input_temperature=200.0,
        allocation=allocation,
        baseline_allocation=baseline_allocation,
        calibration=CalibrationDecision(
            probe != 0.0 if active is None else active,
            probe,
            stage if stage is not None else "low" if probe else None,
            CalibrationProgress(),
            command_revision=command_revision,
            command_action=command_action,
            completed_stages=completed_stages,
        ),
        revision=revision,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


class _OrderedCalibrationRunner(FakeControllerRunner):
    def __init__(self, events):
        super().__init__(period=1.0)
        self.events = events
        self.script([_result()])

    def set_safety_ceiling_c(self, ceiling_c):
        self.events.append("safety-ceiling")
        super().set_safety_ceiling_c(ceiling_c)

    def request_calibration(self, command):
        self.events.append("calibration-command")
        super().request_calibration(command)

    def submit(self, temp):
        self.events.append("temperature-submit")
        super().submit(temp)

    def latest(self):
        self.events.append("result")
        return super().latest()


def test_safety_ceiling_and_calibration_command_precede_result_consumption(hold_cycle):
    events = []
    runner = _OrderedCalibrationRunner(events)
    hold = hold_cycle(runner, controller="mpc")
    hold.control["mpc_calibration"] = {
        "action": "start",
        "revision": 1,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    assert events == ["safety-ceiling", "calibration-command", "temperature-submit", "result"]


def test_hold_consumes_latest_calibration_revision_once_across_reconfiguration(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(), _result(2), _result(3)])
    hold = hold_cycle(runner, controller="mpc")
    hold.control["mpc_calibration"] = {
        "action": "start",
        "revision": 1,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())

    assert [command.command_revision for command in runner.calibration_requests] == [1]


def test_hold_cancels_active_probe_without_reserving_an_operator_revision(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.lid.open_detected = True
    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    assert runner.calibration_cancellations == ["lid_open"]
    assert runner.calibration_requests == []


def test_hold_records_baseline_and_probe_on_framed_observation(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    assert runner.observations[0].baseline_q == 0.3
    assert runner.observations[0].probe_q == 0.1
    assert runner.observations[0].requested_q == 0.4


def test_active_zero_probe_dwell_does_not_claim_completed_probe_evidence(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(active=True, stage="low")])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    observation = runner.observations[0]
    assert observation.calibration_status == "active"
    assert observation.probe_q == 0.0
    hold.state.metrics = {"id": "zero-probe-evidence"}
    trace = hold._control_trace
    context = hold._trace_session_context()
    assert trace is not None and context is not None
    assert trace.ensure_open(context, timestamp_ms=22_000) is not None
    batches = []
    class _NoEvidencePersistence:
        evidence_blocked = False
        failed = False

        def submit_evidence_batch(self, records):
            batches.append(tuple(records))
            return EvidenceSubmission(accepted=True)

        def submit_checkpoint(self, name, snapshot):
            return True

        def flush_and_stop(self):
            return True

    class _SilentLogger:
        def info(self, message):
            return None

        def warning(self, message):
            return None

        def error(self, message):
            return None

    runtime = HoldLearningRuntime(
        runner=None,
        model_store=None,
        persistence=_NoEvidencePersistence(),
        trace=trace,
        controller_name="mpc",
        logger=_SilentLogger(),
        initial_generation=0,
    )
    runtime.submit_completed_observation((0, 20_000), observation)
    assert batches == []


def test_default_five_second_polls_terminalize_only_the_twenty_second_frame(hold_cycle):
    runner = FakeControllerRunner(period=5.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.controller.cycle_start = -6.0

    for now in (0.0, 6.0, 12.0, 18.0, 26.0):
        hold.on_tick(now, 200.0, hold.grill.get_output_status())

    terminal = [item for item in runner.applied if item.feedback_disposition.value != "progress"]
    routine = [item for item in runner.applied if item.feedback_disposition.value == "progress"]
    assert len(routine) >= 3
    assert [item.feedback_disposition.value for item in terminal] == ["complete"]
    assert len(runner.observations) == 1


def test_hold_stamps_latched_probe_frame_before_lid_reset(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.result_revision == 1
    assert cancelled.calibration_command_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "lid_open"
    assert cancelled.cancellation_command_action == "safety-cancel"
    assert cancelled.cancellation_command_revision == 0


def test_boundary_cancellation_preserves_completed_frame_and_marks_reset_partial(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(23.0, 200.0, hold.grill.get_output_status())

    completed, cancelled = runner.observations[-2:]
    assert completed.result_revision == 1
    assert completed.calibration_status == "active"
    assert completed.calibration_cancellation_reason is None
    assert cancelled.result_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "lid_open"
    assert cancelled.cancellation_command_action == "safety-cancel"
    terminal = [item for item in runner.applied if item.feedback_disposition is not FrameFeedbackDisposition.PROGRESS]
    assert [(item.timestamp, item.feedback_disposition) for item in terminal] == [
        (22.0, FrameFeedbackDisposition.COMPLETE),
        (23.0, FrameFeedbackDisposition.DISCARDED),
    ]
    assert terminal[0].source is OutputSource.CONTROLLER
    assert terminal[0].ratio == completed.realized_auger_duty
    assert terminal[1].ratio == cancelled.realized_auger_duty
    assert (
        terminal[0].producing_result_revision,
        terminal[0].producing_calibration_revision,
        terminal[0].producing_calibration_action,
    ) == (
        completed.result_revision,
        completed.calibration_command_revision,
        completed.calibration_command_action,
    )


def test_multiboundary_cancellation_reports_exact_old_frame_then_skipped_gap_and_partial(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    traces = []
    assert hold._control_trace is not None
    hold._control_trace.record = lambda kind, payload, _at: traces.append((kind, payload)) or True
    _advance_runtime(hold, 10.0, True, ptemp=200.0)
    hold.state.lid.open_detected = True
    hold.on_tick(63.0, 200.0, hold.grill.get_output_status())

    terminal = [item for item in runner.applied if item.feedback_disposition is not FrameFeedbackDisposition.PROGRESS]
    assert [(item.timestamp, item.feedback_disposition) for item in terminal] == [
        (22.0, FrameFeedbackDisposition.COMPLETE),
        (42.0, FrameFeedbackDisposition.DISCARDED),
        (62.0, FrameFeedbackDisposition.DISCARDED),
        (63.0, FrameFeedbackDisposition.DISCARDED),
    ]
    assert terminal[0].ratio == 12.0 / 20.0
    assert terminal[1].ratio == terminal[2].ratio == 1.0
    assert terminal[3].ratio == 1.0

    applied = [payload for kind, payload in traces if kind is TraceEventKind.APPLIED_OUTPUT]
    assert [(item.interval_start_ms, item.interval_end_ms, item.realized_auger_duty) for item in applied] == [
        (2_000, 22_000, 12.0 / 20.0),
        (22_000, 42_000, 1.0),
        (42_000, 62_000, 1.0),
        (62_000, 63_000, 1.0),
    ]


def test_multiframe_catchup_pairs_each_exact_feedback_with_its_observation(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    _advance_runtime(hold, 10.0, True, ptemp=200.0)
    _advance_runtime(hold, 63.0, True, ptemp=201.0)

    assert [
        (applied.timestamp, observation.frame_end_s, applied.ratio) for applied, observation in runner.frame_completions
    ] == [
        (22.0, 22.0, 12.0 / 20.0),
        (42.0, 42.0, 1.0),
        (62.0, 62.0, 1.0),
    ]


def test_hold_stamps_latched_probe_frame_before_reconfigure_reset(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.result_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "reset"
    assert cancelled.cancellation_command_action == "safety-cancel"
    assert cancelled.cancellation_command_revision == 0


def test_hold_does_not_carry_cancelled_frame_status_into_later_baseline(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1), _result(2)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(24.0, 200.0, hold.grill.get_output_status())

    cancelled, baseline = runner.observations[-2:]
    assert cancelled.calibration_status == "cancelled"
    assert baseline.result_revision == 2
    assert baseline.probe_q == 0.0
    assert baseline.calibration_status == "inactive"
    assert baseline.calibration_cancellation_reason is None
    assert baseline.cancellation_command_action == "none"


@pytest.mark.parametrize(
    ("intervention", "expected_reason"),
    (
        ("lid-opening", "lid_open"),
        ("manual-takeover", "manual_override"),
        ("stale-result", "stale_result"),
        ("scheduler-reset", "reset"),
    ),
)
def test_runtime_intervention_cancels_probe_to_exact_grey_box_baseline(
    hold_cycle,
    intervention: str,
    expected_reason: str,
) -> None:
    active = _result(probe=0.1)
    following = _result(2, probe=0.1)
    if intervention == "stale-result":
        following = replace(
            following,
            stale_state=ResultStaleState.STALE,
            result_age_seconds=1.0,
        )
    inactive = _result(3)
    runner = FakeControllerRunner(period=1.0).script([active, following, inactive])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    if intervention == "lid-opening":
        hold.state.lid.open_detected = True
    elif intervention == "manual-takeover":
        hold.state.manual_override["auger"] = 10.0
    elif intervention == "scheduler-reset":
        hold.control["controller_update"] = True

    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == expected_reason
    assert cancelled.probe_q == pytest.approx(0.1)
    assert runner.calibration_cancellations == [expected_reason]
    assert following.baseline_allocation is not None
    assert hold.state.controller.pulse_requested_duty == pytest.approx(following.baseline_allocation.auger_duty)

    if intervention == "scheduler-reset":
        hold.on_tick(24.0, 200.0, hold.grill.get_output_status())
        baseline = runner.observations[-1]
        assert baseline.result_revision == following.revision
        assert baseline.probe_q == 0.0
        assert baseline.calibration_status == "inactive"
        assert baseline.calibration_cancellation_reason is None
        assert baseline.cancellation_command_revision == 0
        assert baseline.cancellation_command_action == "none"
        assert baseline.combined_allocation == following.baseline_allocation
        assert runner.calibration_cancellations == ["reset"]


def test_scheduler_reset_does_not_notify_inactive_calibration_owner(hold_cycle) -> None:
    runner = FakeControllerRunner(period=1.0).script([_result(), _result(2)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    assert runner.calibration_cancellations == []


def test_cancelled_frame_persists_matching_raw_and_compact_evidence_once(hold_cycle, monkeypatch):
    import controller.runtime.modes.hold as hold_module

    class Recorder:
        def __init__(self, *, warning):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            pass

        def close(self):
            pass

    class Store:
        def load(self, _name):
            return None

        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = Recorder(warning=lambda _message: None)
    persisted = []
    workers = []
    real_worker = hold_module.ModelPersistenceWorker

    class CapturingWorker(real_worker):
        def __init__(self, store, logger):
            super().__init__(store, logger, append_evidence=persisted.extend)
            workers.append(self)

    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", CapturingWorker)
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    runner.observation_outcome = {
        "role_generation": 0,
        "eligible": False,
        "rejection_reasons": ("reset",),
        "input_variance": 0.0,
        "input_levels": 0,
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
        "effective_updates": 0,
        "model_digest": None,
    }
    hold = hold_cycle(runner, model_store=Store(), controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "cook-calibration"}

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(23.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(25.0, 200.0, hold.grill.get_output_status())
    assert workers[0].flush_and_stop(timeout=1.0)

    raw = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.MODEL_OBSERVATION]
    compact = [record.payload for record in persisted if record.kind is EvidenceKind.CALIBRATION_SUMMARY]
    assert [(item.result_revision, item.calibration_status, item.calibration_cancellation_reason) for item in raw] == [
        (1, "active", None),
        (1, "cancelled", "lid_open"),
    ]
    assert [(item.result_revision, item.status, item.cancellation_reason) for item in compact] == [
        (1, "active", None),
        (1, "cancelled", "lid_open"),
    ]
    assert all(item.command_revision == 1 and item.command_action == "start" for item in compact)
    assert compact[1].cancellation_command_revision == 0
    assert compact[1].cancellation_command_action == "safety-cancel"
    assert compact[1].probe_count == 0
    assert compact[0].stage == "low"


def test_hold_persists_measured_completed_stages_on_coast_evidence(hold_cycle, monkeypatch):
    import controller.runtime.modes.hold as hold_module

    class Recorder:
        def __init__(self, *, warning):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            pass

        def close(self):
            pass

    class Store:
        def load(self, _name):
            return None

        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.SAVED

    persisted = []
    workers = []
    real_worker = hold_module.ModelPersistenceWorker

    class CapturingWorker(real_worker):
        def __init__(self, store, logger):
            super().__init__(store, logger, append_evidence=persisted.extend)
            workers.append(self)

    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: Recorder(warning=warning))
    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", CapturingWorker)
    runner = FakeControllerRunner(period=1.0).script(
        [
            _result(
                stage="coast",
                completed_stages=("low", "middle", "high"),
                active=True,
            ),
        ]
    )
    runner.observation_outcome = {
        "role_generation": 0,
        "eligible": False,
        "rejection_reasons": ("insufficient-history",),
        "input_variance": 0.0,
        "input_levels": 0,
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
        "effective_updates": 0,
        "model_digest": None,
    }
    hold = hold_cycle(runner, model_store=Store(), controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "cook-calibration"}

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(23.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(25.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(26.0, 200.0, hold.grill.get_output_status())
    learning = hold._hold_learning
    assert learning is not None
    learning.reconcile_outcomes(26.0)
    assert runner.observations
    assert runner.observations[-1].calibration_stage == "coast"
    assert runner.observations[-1].calibration_command_revision == 1
    assert workers[0].flush_and_stop(timeout=1.0)

    coast = [
        record.payload
        for record in persisted
        if record.kind is EvidenceKind.CALIBRATION_SUMMARY and record.payload.stage == "coast"
    ]
    assert len(coast) == 1
    assert coast[0].completed_stages == ("low", "middle", "high")


def test_a_command_that_cannot_be_built_is_rejected_once_and_named(hold_cycle, caplog):
    """It used to be retried every tick, forever: the revision was never
    consumed, so calibration silently never started and the only record was a
    trace event no operator reads."""
    import logging

    runner = FakeControllerRunner(period=1.0).script([_result(), _result(2), _result(3)])
    hold = hold_cycle(runner, controller="mpc")
    hold.control["mpc_calibration"] = {
        "action": "start",
        "revision": 1,
        # ambient_c missing entirely -- CalibrationCommand cannot be built.
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    hold.setup()

    with caplog.at_level(logging.ERROR, logger="control"):
        hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
        hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
        hold.on_tick(6.0, 200.0, hold.grill.get_output_status())

    assert runner.calibration_requests == []
    # Rejected once, not once per tick.
    assert len([r for r in caplog.records if "calibration command" in r.getMessage()]) == 1
    assert "ambient_c" in caplog.text
