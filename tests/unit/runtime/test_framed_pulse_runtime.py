from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from common.control_trace import ActuationMode, InhibitReason
from controller.applied_output import FrameFeedbackDisposition, OutputSource
from controller.runtime.framed_pulse import FramedPulseRuntime, FramedPulseSample, PulseControllerState
from controller.runtime.logic.pulse import PulseFrameResult, PulseReason, PulseResetReason
from controller.runtime.state import ControllerState
from grillplat.actuator_capabilities import AugerTiming


def _runtime(*, now: float = 0.0) -> tuple[FramedPulseRuntime, PulseControllerState]:
    controller = cast(PulseControllerState, ControllerState())
    runtime = FramedPulseRuntime()
    runtime.configure(
        ActuationMode.FRAMED_PULSE,
        controller=controller,
        timing=AugerTiming(pulse_s=2, frame_s=20),
        now=now,
        calibration_command_revision=4,
    )
    return runtime, controller


def _sample(
    *,
    temperature: float | None = 212.0,
    role_generation: int = 7,
) -> FramedPulseSample:
    return FramedPulseSample(
        temperature=temperature,
        setpoint=392.0,
        ambient_c=20.0,
        units="F",
        role_generation=role_generation,
    )


def _frame(
    *,
    start: float = 0.0,
    end: float = 20.0,
    delivered: float = 6.0,
    skipped: bool = False,
    reset_reason: PulseResetReason | None = None,
) -> PulseFrameResult:
    return PulseFrameResult(
        nominal_start_s=start,
        nominal_end_s=start + 20.0,
        ended_at_s=end,
        complete=not skipped and reset_reason is None,
        skipped=skipped,
        latched_request=0.3,
        credit_before_s=0.0,
        credit_after_s=0.0,
        scheduled_on_s=6,
        delivered_on_s=delivered,
        observed_transition_count=2,
        actual_start_on=False,
        actual_end_on=False,
        reset_reason=reset_reason,
    )


def _latch_controller_frame(runtime: FramedPulseRuntime, controller: PulseControllerState) -> None:
    controller.pulse_result_revision = 9
    controller.pulse_requested_duty = 0.3
    controller.pulse_combustion_load = 0.4
    controller.pulse_baseline_combustion_load = 0.25
    controller.pulse_calibration_probe_load = 0.15
    controller.pulse_maximum_duty = 0.5
    controller.pulse_requested_fan_duty = 50.0
    controller.fan_duty = 60.0
    controller.controls_fan = True
    controller.pulse_calibration_command_revision = 12
    controller.pulse_calibration_command_action = "start"
    controller.pulse_calibration_command_generation = 3
    runtime.advance(0.0, True, sample=_sample())


def test_scheduler_is_absent_until_framed_configuration() -> None:
    runtime = FramedPulseRuntime()

    assert runtime.scheduler is None
    assert runtime.frame_seconds == 0.0
    with pytest.raises(RuntimeError, match="pulse scheduler"):
        runtime.advance(0.0, False, sample=_sample())



def test_configure_initializes_scheduler_and_pulse_controller_state() -> None:
    runtime, controller = _runtime(now=5.0)

    assert runtime.scheduler is not None
    assert runtime.frame_seconds == 20.0
    assert runtime.actuation_mode is ActuationMode.FRAMED_PULSE
    assert controller.pulse_result_revision == -1
    assert controller.pulse_feedback_start_s == 5.0
    assert controller.calibration_command_revision == 4


def test_advance_latches_request_and_returns_immutable_transition_before_dispatch() -> None:
    runtime, controller = _runtime()
    controller.pulse_result_revision = 2
    controller.pulse_requested_duty = 0.9

    result = runtime.advance(2.0, False, sample=_sample())

    assert result.decision.reason is PulseReason.FRAME_STARTED
    assert result.decision.transition is not None
    assert result.decision.transition.command_on is True
    assert result.completions == ()
    assert result.feedback is not None
    assert result.feedback.applied.producing_result_revision == 2
    with pytest.raises(FrozenInstanceError):
        result.delivered_delta_s = 1.0  # type: ignore[misc]


def test_completed_frame_uses_latched_maximum_duty_realized_duty_and_role_generation() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    runtime.advance(6.0, False, sample=_sample())
    controller.pulse_result_revision = 10
    controller.pulse_requested_duty = 0.9
    controller.pulse_maximum_duty = 1.0

    result = runtime.advance(20.0, False, sample=_sample(role_generation=8))

    assert len(result.completions) == 1
    completion = result.completions[0]
    assert completion.applied is not None
    assert completion.applied.ratio == pytest.approx(6.0 / 20.0)
    assert completion.applied.requested == 0.3
    assert completion.realized_combustion_load == pytest.approx((6.0 / 20.0) / 0.5)
    assert completion.observation is not None
    assert completion.observation.realized_auger_duty == pytest.approx(6.0 / 20.0)
    assert completion.observation.realized_q == pytest.approx((6.0 / 20.0) / 0.5)
    assert completion.observation.temp_c == pytest.approx(100.0)
    assert completion.observation.setpoint_c == pytest.approx(200.0)
    assert completion.observation.role_generation == 7


def test_completed_frame_preserves_result_and_calibration_identity() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    runtime.advance(6.0, False, sample=_sample())

    completion = runtime.advance(20.0, False, sample=_sample()).completions[0]

    assert completion.applied is not None
    assert completion.applied.producing_result_revision == 9
    assert completion.applied.producing_calibration_revision == 12
    assert completion.applied.producing_calibration_action == "start"
    assert completion.applied.producing_calibration_generation == 3
    assert completion.observation is not None
    assert completion.observation.result_revision == 9
    assert completion.observation.calibration_command_revision == 12
    assert completion.observation.calibration_command_action == "start"


def test_complete_frame_suppresses_duplicate_observation_identity() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    frame = _frame()

    first = runtime.complete_frame(frame, sample=_sample(), inhibit=InhibitReason.NONE)
    duplicate = runtime.complete_frame(frame, sample=_sample(), inhibit=InhibitReason.NONE)

    assert first.observation is not None
    assert first.frame_key == (0, 20_000)
    assert duplicate.observation is None
    assert duplicate.duplicate is True


def test_reset_keeps_running_interval_latch_when_current_request_changes() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    runtime.advance(6.0, False, sample=_sample())
    controller.pulse_result_revision = 10
    controller.pulse_requested_duty = 0.8
    controller.pulse_combustion_load = 0.9
    controller.pulse_baseline_combustion_load = 0.7

    result = runtime.reset(
        PulseResetReason.SAFETY,
        10.0,
        InhibitReason.SAFETY,
        actual_auger_on=False,
        sample=_sample(),
        terminal_feedback=True,
    )

    assert len(result.completions) == 1
    completion = result.completions[0]
    assert completion.result_revision == 9
    assert completion.requested_combustion_load == pytest.approx(0.4)
    assert completion.frame.latched_request == pytest.approx(0.3)
    assert completion.observation is not None
    assert completion.observation.result_revision == 9
    assert completion.observation.requested_q == pytest.approx(0.4)
    assert completion.observation.requested_auger_duty == pytest.approx(0.3)


def test_reset_at_frame_boundary_keeps_prior_credit_with_prior_identity() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    runtime.advance(6.0, False, sample=_sample())
    controller.pulse_result_revision = 10
    controller.pulse_requested_duty = 0.8
    controller.pulse_combustion_load = 0.9
    controller.pulse_baseline_combustion_load = 0.7

    result = runtime.reset(
        PulseResetReason.SAFETY,
        20.0,
        InhibitReason.SAFETY,
        actual_auger_on=False,
        sample=_sample(),
        terminal_feedback=True,
        cancellation_reason="stale_result",
    )

    assert len(result.completions) == 2
    completed, boundary_reset = result.completions
    assert completed.result_revision == 9
    assert completed.frame.delivered_on_s == pytest.approx(6.0)
    assert completed.requested_combustion_load == pytest.approx(0.4)
    assert completed.observation is not None
    assert completed.observation.requested_q == pytest.approx(0.4)
    assert completed.observation.requested_auger_duty == pytest.approx(0.3)
    assert boundary_reset.result_revision == 10
    assert boundary_reset.observation is None
    assert boundary_reset.frame.delivered_on_s == 0.0


@pytest.mark.parametrize("terminal_feedback", [False, True])
def test_reset_returns_interrupted_observation_with_optional_terminal_feedback(terminal_feedback: bool) -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    runtime.advance(6.0, False, sample=_sample())

    result = runtime.reset(
        PulseResetReason.SAFETY,
        10.0,
        InhibitReason.SAFETY,
        actual_auger_on=True,
        sample=_sample(),
        terminal_feedback=terminal_feedback,
        cancellation_reason="stale_result",
    )

    interrupted = result.completions[-1]
    assert interrupted.frame.reset_reason is PulseResetReason.SAFETY
    assert interrupted.observation is not None
    assert interrupted.observation.reset is True
    assert (interrupted.applied is not None) is terminal_feedback
    if interrupted.applied is not None:
        assert interrupted.applied.feedback_disposition is FrameFeedbackDisposition.DISCARDED
        assert interrupted.applied.producing_calibration_revision == 12
    assert runtime.scheduler is not None
    assert runtime.scheduler.advance(0.3, 11.0, False).reset_reason is PulseResetReason.SAFETY


@pytest.mark.parametrize(
    ("inhibit", "reset_reason", "stale", "expected_source", "lid", "manual", "safety"),
    [
        (InhibitReason.LID_OPEN, PulseResetReason.LID, False, "lid_open", True, False, False),
        (
            InhibitReason.MANUAL_OVERRIDE,
            PulseResetReason.MANUAL,
            False,
            "manual_override",
            False,
            True,
            False,
        ),
        (InhibitReason.SAFETY, PulseResetReason.SAFETY, False, "unknown", False, False, True),
        (InhibitReason.STALE_COMMAND, None, True, "controller", False, False, False),
    ],
)
def test_completed_frame_encodes_inhibit_source_and_disposition(
    inhibit: InhibitReason,
    reset_reason: PulseResetReason | None,
    stale: bool,
    expected_source: str,
    lid: bool,
    manual: bool,
    safety: bool,
) -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)
    controller.pulse_frame_stale_command = stale

    completion = runtime.complete_frame(
        _frame(reset_reason=reset_reason),
        sample=_sample(),
        inhibit=inhibit,
        terminal_feedback=reset_reason is not None,
        feedback_source=(
            OutputSource.LID_OPEN
            if lid
            else OutputSource.MANUAL_OVERRIDE
            if manual
            else OutputSource.CONTROLLER
        ),
    )

    assert completion.observation is not None
    assert completion.observation.output_source == expected_source
    assert completion.observation.lid_open is lid
    assert completion.observation.manual_override is manual
    assert completion.observation.safety_inhibited is safety
    assert completion.observation.stale is stale
    assert completion.observation.continuous is False
    if reset_reason is not None:
        assert completion.applied is not None
        assert completion.applied.feedback_disposition is FrameFeedbackDisposition.DISCARDED


def test_report_feedback_tracks_realized_duty_without_dispatching() -> None:
    runtime, controller = _runtime()
    _latch_controller_frame(runtime, controller)

    feedback = runtime.report_feedback(
        10.0,
        4.0,
        source=OutputSource.CONTROLLER,
        disposition=FrameFeedbackDisposition.PROGRESS,
    )

    assert feedback is not None
    assert feedback.applied.ratio == pytest.approx(0.4)
    assert feedback.applied.requested == 0.3
    assert feedback.realized_combustion_load == pytest.approx(0.8)
    assert controller.pulse_feedback_start_s == 10.0
    assert controller.pulse_feedback_delivered_on_s == 4.0


def test_runtime_error_and_edge_branches() -> None:
    runtime = FramedPulseRuntime()
    with pytest.raises(RuntimeError):
        _ = runtime.actuation_mode
    with pytest.raises(RuntimeError):
        runtime.latch(0)
    with pytest.raises(ValueError):
        runtime.configure(
            cast(ActuationMode, "continuous"),
            controller=cast(PulseControllerState, ControllerState()),
            timing=AugerTiming(pulse_s=2, frame_s=20),
            now=0.0,
        )

    runtime, controller = _runtime()
    assert runtime.actuation_mode is ActuationMode.FRAMED_PULSE
    runtime.latch(4)
    assert runtime.observation_sequence == 0
    assert runtime.report_feedback(0.0, 0.0, source=OutputSource.CONTROLLER) is None

    controller.pulse_feedback_start_s = None
    assert runtime.report_feedback(12.0, 0.0, source=OutputSource.CONTROLLER) is None
    assert runtime.report_feedback(12.0, 0.0, source=OutputSource.CONTROLLER) is None


def test_reset_and_completion_edge_branches() -> None:
    runtime, controller = _runtime()
    no_frame = runtime.reset(
        PulseResetReason.SAFETY,
        0.0,
        InhibitReason.SAFETY,
        actual_auger_on=False,
        sample=_sample(),
        terminal_feedback=False,
        report_feedback=True,
    )
    assert len(no_frame.completions) == 1

    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.3
    runtime.advance(0.0, False, sample=_sample())
    runtime.advance(20.0, False, sample=_sample())
    completed = runtime.reset(
        PulseResetReason.SAFETY,
        20.0,
        InhibitReason.SAFETY,
        actual_auger_on=False,
        sample=_sample(),
        terminal_feedback=False,
        report_feedback=True,
    )
    assert completed.completions

    missing_frame = replace(_frame(), nominal_start_s=40.0, nominal_end_s=60.0, ended_at_s=60.0)
    missing = runtime.complete_frame(
        missing_frame,
        sample=replace(_sample(), temperature=None),
        inhibit=InhibitReason.NONE,
    )
    duplicate_gap = runtime.complete_frame(
        missing_frame,
        sample=replace(_sample(), temperature=None),
        inhibit=InhibitReason.NONE,
    )
    assert missing.missing_observation_reason == "missing-temperature"
    assert missing.duplicate is False
    assert duplicate_gap.duplicate is True
    controller.pulse_result_revision = -1
    runtime.latch(0)
    missing_revision = runtime.complete_frame(
        replace(_frame(), nominal_start_s=20.0, nominal_end_s=40.0, ended_at_s=40.0),
        sample=_sample(),
        inhibit=InhibitReason.NONE,
    )
    assert missing_revision.observation is None
    assert missing_revision.missing_observation_reason == "missing-result-revision"


def test_reset_cancels_active_calibration_and_missing_revision_gap_is_deduplicated() -> None:
    runtime, controller = _runtime()
    controller.pulse_result_revision = 2
    controller.pulse_calibration_status = "active"
    controller.pulse_calibration_probe_load = 0.1
    runtime.latch(0)

    runtime.reset(
        PulseResetReason.SAFETY,
        10.0,
        InhibitReason.SAFETY,
        actual_auger_on=False,
        terminal_feedback=False,
        sample=_sample(),
        cancellation_reason="safety",
        cancellation_command_revision=9,
        cancellation_command_action="safety-cancel",
    )

    assert controller.pulse_frame_calibration_cancellation_reason == "safety"

    controller.pulse_result_revision = -1
    controller.pulse_combustion_load = None
    runtime.latch(0)
    gap_frame = replace(_frame(), nominal_start_s=80.0, nominal_end_s=100.0, ended_at_s=100.0)
    missing = runtime.complete_frame(
        gap_frame,
        sample=_sample(),
        inhibit=InhibitReason.NONE,
    )
    duplicate = runtime.complete_frame(
        gap_frame,
        sample=_sample(),
        inhibit=InhibitReason.NONE,
    )
    assert missing.missing_observation_reason == "missing-result-revision"
    assert missing.duplicate is False
    assert duplicate.duplicate is True
