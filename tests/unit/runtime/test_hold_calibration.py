from dataclasses import replace

import pytest

from common.controller_model_state import CheckpointSaveOutcome
from common.control_trace import ResultStaleState, SafetyEventPayload, SafetyEventType, TraceEventKind
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


@pytest.mark.parametrize(
    ("configured", "units", "expected_c"),
    (
        (500.0, "F", 260.0),
        (260.0, "C", 260.0),
    ),
)
def test_safety_ceiling_uses_current_grill_limit_and_units_each_tick(
    hold_cycle,
    configured: float,
    units: str,
    expected_c: float,
) -> None:
    runner = FakeControllerRunner(period=1.0).script([_result()])
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["safety"]["maxtemp"] = configured
    hold.settings["globals"]["units"] = units
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    assert runner.safety_ceiling_c == pytest.approx(expected_c)


def test_invalid_safety_ceiling_fault_deduplicates_and_recovers(
    hold_cycle,
    monkeypatch,
) -> None:
    import controller.runtime.modes.hold as hold_module

    class Recorder:
        def __init__(self, *, warning):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            return None

        def close(self):
            return None

    recorder = Recorder(warning=lambda _message: None)
    monkeypatch.setattr(
        hold_module,
        "ControlTraceRecorder",
        lambda *, warning: recorder,
    )
    runner = FakeControllerRunner(period=1.0).script(
        [_result(index) for index in range(1, 9)]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "safety-ceiling"}
    trace = hold._control_trace
    context = hold._trace_session_context()
    assert trace is not None and context is not None
    assert trace.ensure_open(context, timestamp_ms=0) is not None
    hold.settings["globals"]["units"] = "K"

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.settings["globals"]["units"] = "C"
    hold.settings["safety"]["maxtemp"] = 260.0
    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())
    hold.settings["globals"]["units"] = "K"
    hold.on_tick(8.0, 200.0, hold.grill.get_output_status())
    hold.settings["globals"]["units"] = "C"
    hold.on_tick(10.0, 200.0, hold.grill.get_output_status())
    hold.settings["safety"]["maxtemp"] = float("nan")
    hold.on_tick(12.0, 200.0, hold.grill.get_output_status())
    hold.settings["safety"]["maxtemp"] = 260.0
    hold.on_tick(14.0, 200.0, hold.grill.get_output_status())
    hold.settings["safety"]["maxtemp"] = True
    hold.on_tick(16.0, 200.0, hold.grill.get_output_status())

    faults = [
        record.payload
        for record in recorder.records
        if isinstance(record.payload, SafetyEventPayload)
        and record.payload.detail.startswith(
            "cannot read the grill maximum temperature:"
        )
    ]
    assert len(faults) == 4
    assert "Celsius or Fahrenheit" in faults[0].detail
    assert "Celsius or Fahrenheit" in faults[1].detail
    assert "not finite" in faults[2].detail
    assert "not finite" in faults[3].detail
    assert runner.safety_ceiling_c == pytest.approx(260.0)


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
    hold.state.controller.cycle_start = -5.0

    for now in (1.0, 7.0, 13.0, 19.0, 27.0):
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


def test_manual_callback_then_in_flight_result_uses_one_cancellation_path(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=1.0).script(
        [_result(probe=0.1), _result(2, probe=0.1)]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    hold.state.manual_override["auger"] = 30.0

    hold._last_now = 3.0
    hold._last_ptemp = 200.0
    hold._on_manual_output("auger", True)
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = [
        observation
        for observation in runner.observations
        if observation.calibration_status == "cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].calibration_cancellation_reason == "manual_override"
    assert runner.calibration_cancellations == ["manual_override"]


def test_stale_calibration_cancellation_resets_framed_pulse_once(
    hold_cycle,
    monkeypatch,
) -> None:
    stale = replace(
        _result(2, probe=0.1),
        stale_state=ResultStaleState.STALE,
        result_age_seconds=1.0,
    )
    runner = FakeControllerRunner(period=1.0).script(
        [_result(probe=0.1), stale]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    runtime = _runtime(hold)
    resets = []
    reset = runtime.reset

    def record_reset(*args, **kwargs):
        resets.append(args[0])
        return reset(*args, **kwargs)

    monkeypatch.setattr(runtime, "reset", record_reset)

    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    assert resets == [resets[0]]
    assert runner.calibration_cancellations == ["stale_result"]


@pytest.mark.parametrize(
    ("intervention", "expected_reason"),
    (
        ("lid-toggle", "lid_open"),
        ("safety-event", "safety"),
    ),
)
def test_callbacks_cancel_active_frame_once_before_in_flight_result(
    hold_cycle,
    intervention: str,
    expected_reason: str,
) -> None:
    runner = FakeControllerRunner(period=1.0).script(
        [_result(probe=0.1), _result(2, probe=0.1)]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    if intervention == "lid-toggle":
        hold.control["lid_open_toggle"] = True
        hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    else:
        hold._on_safety_event("temperature_guard", 3.0)
        hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = [
        observation
        for observation in runner.observations
        if observation.calibration_status == "cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].calibration_cancellation_reason == expected_reason
    assert runner.calibration_cancellations == [expected_reason]


def test_repeated_stale_active_result_is_handled_without_repeat_or_restart(
    hold_cycle,
    monkeypatch,
) -> None:
    stale = replace(
        _result(2, probe=0.1),
        stale_state=ResultStaleState.STALE,
        result_age_seconds=1.0,
    )
    runner = FakeControllerRunner(period=1.0).script(
        [_result(probe=0.1), stale, stale]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    runtime = _runtime(hold)
    resets = 0
    advances = 0
    reset = runtime.reset
    advance = runtime.advance

    def record_reset(*args, **kwargs):
        nonlocal resets
        resets += 1
        return reset(*args, **kwargs)

    def record_advance(*args, **kwargs):
        nonlocal advances
        advances += 1
        return advance(*args, **kwargs)

    monkeypatch.setattr(runtime, "reset", record_reset)
    monkeypatch.setattr(runtime, "advance", record_advance)

    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    assert resets == 1
    assert hold.state.controller.pulse_frame_calibration_status == "cancelled"
    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())

    baseline = stale.baseline_allocation
    assert baseline is not None
    assert resets == 1
    assert advances == 0
    assert runner.calibration_cancellations == ["stale_result"]
    assert hold.state.controller.pulse_requested_duty == pytest.approx(
        baseline.auger_duty
    )


@pytest.mark.parametrize("action", ("pause", "stop", "reset-progress"))
def test_operator_cancellation_is_admitted_before_command_aware_latest(
    hold_cycle,
    action: str,
) -> None:
    class _CommandAwareRunner(FakeControllerRunner):
        def __init__(self) -> None:
            super().__init__(period=1.0)
            self.active = _result(probe=0.1)
            assert self.active.calibration is not None
            self.after_command = replace(
                self.active,
                calibration=replace(
                    self.active.calibration,
                    active=False,
                    probe_q=0.0,
                    command_revision=2,
                    command_action=action,
                ),
            )
            self.command_received = False

        def request_calibration(self, command):
            super().request_calibration(command)
            self.command_received = True

        def latest(self):
            return self.after_command if self.command_received else self.active

    runner = _CommandAwareRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["mpc_calibration"] = {
        "action": action,
        "revision": 2,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }

    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == f"operator_{action}"
    assert cancelled.cancellation_command_revision == 2
    assert cancelled.cancellation_command_action == action
    assert [command.command_revision for command in runner.calibration_requests] == [2]

def test_cancelled_old_identity_does_not_strip_distinct_active_result(
    hold_cycle,
    monkeypatch,
) -> None:
    old = _result(probe=0.1)
    distinct = _result(
        2,
        probe=0.1,
        command_revision=2,
        command_action="resume",
        stage="middle",
    )
    assert distinct.calibration is not None
    distinct = replace(
        distinct,
        calibration=replace(distinct.calibration, command_generation=3),
    )
    runner = FakeControllerRunner(period=1.0).script([old, distinct])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold._last_now = 3.0
    hold._on_manual_output("auger", True)
    hold.state.manual_override["auger"] = 0.0
    runtime = _runtime(hold)
    advances = 0
    advance = runtime.advance

    def record_advance(*args, **kwargs):
        nonlocal advances
        advances += 1
        return advance(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", record_advance)
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    assert hold.state.controller.pulse_calibration_status == "active"
    assert hold.state.controller.pulse_calibration_probe_load == pytest.approx(0.1)
    assert hold.state.controller.pulse_calibration_command_revision == 2
    assert hold.state.controller.pulse_calibration_command_action == "resume"
    assert hold.state.controller.pulse_calibration_command_generation == 3
    assert advances == 1


def test_manual_callback_adopts_same_revision_inactive_baseline_without_restart(
    hold_cycle,
    monkeypatch,
) -> None:
    class _SameRevisionManualRunner(FakeControllerRunner):
        def __init__(self) -> None:
            super().__init__(period=1.0)
            self.active = _result(probe=0.1)
            assert self.active.calibration is not None
            assert self.active.baseline_allocation is not None
            self.inactive = replace(
                self.active,
                cycle_ratio=self.active.baseline_allocation.auger_duty,
                allocation=self.active.baseline_allocation,
                calibration=replace(
                    self.active.calibration,
                    active=False,
                    probe_q=0.0,
                ),
            )

        def latest(self):
            return self.inactive if self.calibration_cancellations else self.active

    runner = _SameRevisionManualRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.controller.cycle_start = -1.0
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    runtime = _runtime(hold)
    advances = 0
    advance = runtime.advance

    def record_advance(*args, **kwargs):
        nonlocal advances
        advances += 1
        return advance(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", record_advance)
    hold._last_now = 3.0
    hold._on_manual_output("auger", True)
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    baseline = runner.inactive.baseline_allocation
    assert baseline is not None
    assert runner.calibration_cancellations == ["manual_override"]
    assert hold.state.controller.pulse_requested_duty == pytest.approx(
        baseline.auger_duty
    )
    assert hold.state.controller.pulse_calibration_status == "inactive"
    assert hold.state.controller.pulse_calibration_probe_load == 0.0
    assert advances == 1
    assert hold.state.controller.pulse_frame_calibration_status == "inactive"
    assert hold.state.controller.pulse_frame_calibration_probe_load == 0.0


def test_operator_cancellation_inside_control_period_waits_for_post_command_result(
    hold_cycle,
    monkeypatch,
) -> None:
    class _SameRevisionCommandRunner(FakeControllerRunner):
        def __init__(self) -> None:
            super().__init__(period=10.0)
            self.active = _result(probe=0.1)
            assert self.active.calibration is not None
            assert self.active.baseline_allocation is not None
            self.after_command = replace(
                self.active,
                cycle_ratio=self.active.baseline_allocation.auger_duty,
                allocation=self.active.baseline_allocation,
                calibration=replace(
                    self.active.calibration,
                    active=False,
                    probe_q=0.0,
                    command_revision=2,
                    command_action="pause",
                ),
            )
            self.command_received = False

        def request_calibration(self, command):
            super().request_calibration(command)
            self.command_received = True

        def latest(self):
            return self.after_command if self.command_received else self.active

    runner = _SameRevisionCommandRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.controller.cycle_start = -11.0
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    runtime = _runtime(hold)
    runner.calibration_cancellations.clear()
    advances = 0
    advance = runtime.advance
    assert hold.state.controller.pulse_frame_calibration_status == "active"
    assert hold.state.controller.pulse_frame_calibration_probe_load == pytest.approx(
        0.1
    )

    def record_advance(*args, **kwargs):
        nonlocal advances
        advances += 1
        return advance(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", record_advance)
    hold.control["mpc_calibration"] = {
        "action": "pause",
        "revision": 2,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }

    hold.on_tick(3.0, 200.0, hold.grill.get_output_status())
    assert advances == 0
    assert runner.calibration_cancellations == []
    assert hold.state.controller.pulse_frame_calibration_status == "cancelled"

    hold.state.controller.cycle_start = 2.0
    hold.on_tick(14.0, 200.0, hold.grill.get_output_status())

    baseline = runner.after_command.baseline_allocation
    assert baseline is not None
    assert hold.state.controller.pulse_requested_duty == pytest.approx(
        baseline.auger_duty
    )
    assert hold.state.controller.pulse_calibration_status == "inactive"
    assert hold.state.controller.pulse_calibration_probe_load == 0.0
    assert advances == 1
    assert hold.state.controller.pulse_frame_calibration_status == "inactive"
    assert hold.state.controller.pulse_frame_calibration_probe_load == 0.0
    assert len(
        [
            observation
            for observation in runner.observations
            if observation.calibration_status == "cancelled"
        ]
    ) == 1
    assert [command.command_revision for command in runner.calibration_requests] == [2]



@pytest.mark.parametrize("action", ("pause", "stop", "reset-progress"))
def test_newer_operator_cancellation_keeps_exact_command_identity(
    hold_cycle,
    action: str,
) -> None:
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["mpc_calibration"] = {
        "action": action,
        "revision": 2,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }



    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == f"operator_{action}"
    assert cancelled.cancellation_command_revision == 2
    assert cancelled.cancellation_command_action == action
    assert runner.calibration_cancellations == []


def test_manual_release_records_one_release_trace_when_cancelling_probe(
    hold_cycle,
    monkeypatch,
) -> None:
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    trace = _trace(hold)
    releases = []
    record_safety = trace.record_safety

    def capture_release(context):
        if context.event is SafetyEventType.MANUAL_RELEASE:
            releases.append(context)
        return record_safety(context)

    monkeypatch.setattr(trace, "record_safety", capture_release)

    hold._on_manual_release("auger", 3.0)

    assert len(releases) == 1


@pytest.mark.parametrize("active", (False, True), ids=("inactive", "zero-probe"))
def test_manual_release_still_records_once_without_active_probe(
    hold_cycle,
    monkeypatch,
    active: bool,
) -> None:
    runner = FakeControllerRunner(period=1.0).script([_result(active=active)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    trace = _trace(hold)
    releases = []
    record_safety = trace.record_safety

    def capture_release(context):
        if context.event is SafetyEventType.MANUAL_RELEASE:
            releases.append(context)
        return record_safety(context)

    monkeypatch.setattr(trace, "record_safety", capture_release)

    hold._on_manual_release("auger", 3.0)

    assert len(releases) == 1
    assert runner.calibration_cancellations == []


def test_manual_release_cancels_an_active_probe_once(hold_cycle) -> None:
    runner = FakeControllerRunner(period=1.0).script(
        [_result(probe=0.1), _result(2, probe=0.1)]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    hold._on_manual_release("auger", 3.0)
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = [
        observation
        for observation in runner.observations
        if observation.calibration_status == "cancelled"
    ]
    assert len(cancelled) == 1
    assert cancelled[0].calibration_cancellation_reason == "manual_override"
    assert runner.calibration_cancellations == ["manual_override"]


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


def test_current_stale_probe_result_does_not_claim_prior_interval_evidence(
    hold_cycle,
    monkeypatch,
) -> None:
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

    monkeypatch.setattr(
        hold_module,
        "ControlTraceRecorder",
        lambda *, warning: recorder,
    )
    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", CapturingWorker)
    current = _result(
        2,
        probe=0.1,
        command_revision=7,
        command_action="resume",
        stage="middle",
        completed_stages=("low",),
    )
    assert current.calibration is not None
    current = replace(
        current,
        stale_state=ResultStaleState.STALE,
        result_age_seconds=1.0,
        calibration=replace(current.calibration, command_generation=3),
    )
    class EvidenceRunner(FakeControllerRunner):
        observation_outcome: object

    runner = EvidenceRunner(period=1.0).script([_result(), current])
    runner.observation_outcome = {
        "role_generation": 0,
        "eligible": False,
        "rejection_reasons": ("stale_result",),
        "input_variance": 0.0,
        "input_levels": 0,
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
        "effective_updates": 0,
        "model_digest": None,
    }
    hold = hold_cycle(runner, model_store=Store(), controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "current-stale-calibration"}

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(25.0, 200.0, hold.grill.get_output_status())
    assert workers[0].flush_and_stop(timeout=1.0)

    current_observations = [
        observation
        for observation in runner.observations
        if observation.result_revision == 2
    ]
    assert current_observations == []
    baseline = current.baseline_allocation
    assert baseline is not None
    controller = hold.state.controller
    assert controller.pulse_frame_result_revision == 1
    assert controller.pulse_requested_duty == pytest.approx(baseline.auger_duty)
    assert controller.pulse_combustion_load == pytest.approx(
        baseline.normalized_combustion_load
    )
    assert controller.pulse_baseline_combustion_load == pytest.approx(
        baseline.normalized_combustion_load
    )
    assert controller.pulse_calibration_command_revision == 7
    assert controller.pulse_calibration_command_action == "resume"
    assert controller.pulse_calibration_command_generation == 3
    assert controller.pulse_calibration_cancellation_reason == "stale_result"
    assert controller.pulse_calibration_probe_load == 0.0
    assert runner.calibration_cancellations == ["stale_result"]

    raw = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION
        and record.payload.result_revision == 2
    ]
    compact = [
        record.payload
        for record in persisted
        if record.kind is EvidenceKind.CALIBRATION_SUMMARY
        and record.payload.result_revision == 2
    ]
    safety = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.SAFETY_EVENT
        and isinstance(record.payload, SafetyEventPayload)
        and record.payload.result_revision == 2
        and "stale_result" in record.payload.detail
    ]
    assert raw == []
    assert compact == []
    assert len(safety) == 1
    assert safety[0].event is SafetyEventType.SCHEDULER_RESET

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
