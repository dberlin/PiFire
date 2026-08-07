from dataclasses import replace

import pytest

from common.control_trace import (
    ActuationMode,
    ControllerType,
    InhibitReason,
    OutputSource,
    ResultStaleState,
    SafetyEventType,
    TraceEventKind,
)
from controller.applied_output import FrameFeedbackDisposition
from controller.runtime.logic.pulse import PulseResetReason
from controller.runtime.runner import ControllerUpdateResult
from controller.runtime.logic.pulse import PulseFrameResult

from tests.fakes.runner import FakeControllerRunner


def _output(revision: int, duty: float, *, fan_duty: float | None = None) -> ControllerUpdateResult:
    return ControllerUpdateResult(
        cycle_ratio=duty,
        fan=None if fan_duty is None else {"duty": fan_duty},
        input_temperature=200.0,
        revision=revision,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


def _status(hold):
    return hold.grill.get_output_status()


@pytest.mark.parametrize(
    ("controller", "controller_type"),
    [
        ("pid", ControllerType.PID),
        ("pid_sp", ControllerType.PID_SP),
        ("mpc", ControllerType.MPC),
    ],
)
def test_every_production_controller_builds_one_pulse_scheduler_and_starts_off(hold_cycle, controller, controller_type):
    hold = hold_cycle(FakeControllerRunner(controller_type=controller_type), controller=controller)

    hold.setup()

    assert hold._pulse_scheduler is not None
    assert hold.grill.get_output_status()["auger"] is False
    assert hold.state.cycle.cycle_time == 0.0


def test_low_duty_accumulates_to_one_quantum(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.05)])
    hold = hold_cycle(runner, controller="mpc", cycle_data_extra={"u_min": 0.9, "HoldCycleTime": 99})

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_requested_duty == 0.05
    assert hold.grill.get_output_status()["auger"] is False
    hold.on_tick(22.0, 200.0, _status(hold))
    assert hold.grill.get_output_status()["auger"] is True


def test_result_is_adopted_once_and_latched_at_next_frame(hold_cycle):
    first = _output(1, 0.1)
    replacement = _output(2, 0.9)
    runner = FakeControllerRunner(period=1.0).script([first, first, replacement])
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_result_revision == 2
    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.grill.get_output_status()["auger"] is True


def test_stale_result_continues_last_command_and_measured_feedback(hold_cycle):
    result = _output(1, 0.1)
    runner = FakeControllerRunner(period=1.0).script([result, result, result])
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    runner.applied.clear()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    hold.on_tick(24.0, 200.0, _status(hold))
    hold.on_tick(26.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_result_revision == 1
    assert hold.state.controller.pulse_requested_duty == 0.1
    assert runner.applied[-1].ratio == 1.0
    assert runner.applied[-1].requested == 0.1


def test_stale_command_inhibits_non_solve_ticks_until_a_fresh_result_arrives(hold_cycle):
    fresh = _output(1, 0.9)
    stale = replace(fresh, stale_state=ResultStaleState.STALE)
    recovered = _output(2, 0.9)
    runner = FakeControllerRunner(period=1.0).script([fresh, stale, recovered])
    hold = hold_cycle(runner, controller="mpc")

    hold.state.metrics = {"id": "stale-recovery-no-catchup", "augerontime": 0.0}
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))
    delivery_at_reset = hold.state.controller.pulse_feedback_delivered_on_s

    hold.on_tick(4.5, 200.0, _status(hold))

    assert hold.grill.get_output_status()["auger"] is False
    assert hold.state.controller.pulse_feedback_delivered_on_s == delivery_at_reset
    hold.on_tick(6.0, 200.0, _status(hold))
    assert hold.state.controller.pulse_stale_command is False
    assert hold.grill.get_output_status()["auger"] is True
    hold.on_tick(8.0, 200.0, _status(hold))
    hold.on_tick(26.0, 200.0, _status(hold))

    assert hold.state.metrics["augerontime"] == 18.0


def test_reconfiguration_replaces_scheduler_and_discards_prior_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="pid")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    original_scheduler = hold._pulse_scheduler
    hold.control["controller_update"] = True

    hold.on_tick(24.0, 200.0, _status(hold))

    assert hold._pulse_scheduler is not original_scheduler
    assert hold._pulse_scheduler.advance(0.1, 42.0, False).credit_s < 2.0


def test_reconfiguration_uses_post_reset_auger_state_for_the_replacement_scheduler(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "reconfigure-observed-state", "augerontime": 0.0}
    hold.on_tick(2.0, 200.0, _status(hold))
    original_scheduler = hold._pulse_scheduler
    captured_before_reset = _status(hold)
    hold.control["controller_update"] = True
    runner.applied.clear()

    hold.on_tick(4.0, 200.0, captured_before_reset)

    assert hold._pulse_scheduler is not original_scheduler
    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.metrics["augerontime"] == 0.0
    assert runner.applied[0].ratio == 0.0
    assert runner.applied[0].source is OutputSource.SEED


def test_safety_manual_lid_and_teardown_reset_credit(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))
    hold._last_now = 23.0

    hold._on_manual_output("auger", True)
    hold._on_safety_event("stop", 24.0)
    assert hold.grill.get_output_status()["auger"] is False

    hold.ctx.clock.advance(24.0)
    hold.teardown(200.0)
    assert runner.stops == 1


@pytest.mark.parametrize("event", ["stop", "error", "temperature_guard"])
def test_guard_events_reset_without_restoring_credit(hold_cycle, event):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(22.0, 200.0, _status(hold))

    hold._on_safety_event(event, 23.0)
    decision = hold._pulse_scheduler.advance(0.9, 24.0, False)

    assert decision.reset_reason is not None
    assert decision.credit_s < 2.0
    assert hold.grill.get_output_status()["auger"] is False


def test_lid_inhibit_discards_credit_and_preempts_auger(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True

    hold.on_tick(2.0, 100.0, _status(hold))

    assert hold.state.lid.open_detected is True
    assert hold.grill.get_output_status()["auger"] is False
    assert hold._pulse_scheduler.advance(0.9, 3.0, False).reset_reason is not None


def test_deferred_mpc_to_pid_swap_accounts_old_delivery_and_seeds_post_reset_output(hold_cycle, monkeypatch):
    class DeferredRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self.pending = True
            return "Active"

        def complete_swap(self):
            self._controller_type = ControllerType.PID
            self._commands_fan = False
            self._configuration_revision += 1

    runner = DeferredRunner(period=1.0, commands_fan=True, controller_type=ControllerType.MPC).script([_output(1, 0.5)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.store._settings["controller"]["selected"] = "pid"
    hold.state.metrics = {"id": "deferred-generation-accounting", "augerontime": 0.0}
    hold.control["controller_update"] = True
    status = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    hold.on_tick(2.0, 200.0, status)
    hold._advance_framed_pulse(2.0, True)

    assert hold.grill.get_output_status()["auger"] is True
    configure_scheduler = hold._configure_pulse_scheduler

    def configure_with_live_ratio():
        configure_scheduler()
        hold.state.cycle.ratio = 0.5

    monkeypatch.setattr(hold, "_configure_pulse_scheduler", configure_with_live_ratio)
    runner.applied.clear()

    runner.complete_swap()
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold._pulse_scheduler is not None
    assert hold.state.cycle.cycle_time == 0.0
    assert hold._controller_name == "pid"
    assert hold.grill.get_output_status()["auger"] is True
    assert hold.state.metrics["augerontime"] == 2.0
    seed = runner.applied[0]
    assert seed.ratio == 0.0
    assert seed.source is OutputSource.SEED


def test_missed_frames_are_recorded_as_skipped_without_catchup(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    frames = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: frames.append((kind, payload)) or True)
    hold.setup()
    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(62.0, 200.0, _status(hold))

    skipped = [payload for kind, payload in frames if kind is TraceEventKind.ACTUATION_FRAME and payload.skipped]
    assert skipped and all(payload.scheduled_on_seconds == 0.0 for payload in skipped)
    terminal_feedback = [item for item in runner.applied if item.feedback_disposition is not FrameFeedbackDisposition.PROGRESS]
    assert terminal_feedback[-1].feedback_disposition is FrameFeedbackDisposition.DISCARDED


def test_auger_and_fan_adopt_together_from_one_result_revision(hold_cycle):
    first = _output(1, 0.1, fan_duty=25.0)
    replacement = _output(2, 0.9, fan_duty=75.0)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script([first, replacement])
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["platform"]["dc_fan"] = True
    hold.control["pwm_control"] = True
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.state.controller.pulse_requested_fan_duty == 75.0
    assert hold.state.controller.fan_duty == 75.0


@pytest.mark.parametrize("actual_on", [False, True])
def test_reset_accounts_observed_output_before_safety_or_manual_preemption(hold_cycle, monkeypatch, actual_on):
    runner = FakeControllerRunner(period=1.0).script([_output(1, 0.1)])
    hold = hold_cycle(runner, controller="mpc")
    records = []
    monkeypatch.setattr(hold, "_trace_record", lambda kind, payload, ts: records.append((kind, payload)) or True)
    hold.setup()
    if actual_on:
        hold.grill.auger_on()

    hold.on_tick(2.0, 200.0, _status(hold))
    hold._on_safety_event("stop", 4.0)
    hold._last_now = 4.0
    hold._on_manual_output("auger", actual_on)

    frames = [payload for kind, payload in records if kind is TraceEventKind.ACTUATION_FRAME]
    assert frames
    assert all(
        payload.actual_end_active is payload.actual_start_active ^ bool(payload.transition_count % 2)
        for payload in frames
    )


def test_reset_keeps_cumulative_delivery_baselines_for_feedback_and_metrics(hold_cycle):
    runner = FakeControllerRunner(period=1.0)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "cook-reset-accounting", "augerontime": 0.0}
    hold.state.controller.pulse_requested_duty = 0.1
    runner.applied.clear()

    hold._advance_framed_pulse(20.0, False)
    hold._advance_framed_pulse(22.0, True)
    before_reset = hold._advance_framed_pulse(24.0, False)
    assert before_reset.delivered_on_s == 2.0
    hold._reset_framed_pulse(PulseResetReason.SAFETY, 24.0, InhibitReason.SAFETY)

    hold._record_pulse_delivery(12.0)
    hold._report_framed_feedback(44.0, 12.0)

    assert hold.state.metrics["augerontime"] == 12.0
    assert runner.applied[-1].ratio == 0.5


def test_stale_result_preempts_hardware_and_discards_scheduler_credit(hold_cycle):
    fresh = _output(1, 0.9)
    stale = replace(fresh, stale_state=ResultStaleState.STALE)
    runner = FakeControllerRunner(period=1.0).script([fresh, stale])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, _status(hold))
    assert hold.grill.get_output_status()["auger"] is True
    hold.on_tick(4.0, 200.0, _status(hold))

    assert hold.grill.get_output_status()["auger"] is False
    assert hold._pulse_scheduler.advance(0.9, 6.0, False).reset_reason is not None


def test_completed_frame_feedback_uses_the_completed_frame_request_bound_and_revision(hold_cycle):
    runner = FakeControllerRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    controller = hold.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.1
    controller.pulse_maximum_duty = 0.5
    hold._advance_framed_pulse(0.0, False)
    hold._advance_framed_pulse(0.0, True)
    hold._advance_framed_pulse(2.0, True)
    hold._advance_framed_pulse(2.0, False)
    controller.pulse_result_revision = 2
    controller.pulse_requested_duty = 0.9
    controller.pulse_maximum_duty = 1.0
    runner.applied.clear()

    controller.trace_prior_output_source = OutputSource.CONTROLLER
    hold._advance_framed_pulse(20.0, False)

    assert runner.applied[-1].requested == 0.1
    assert hold.state.controller.trace_prior_combustion_load == 0.2
    assert hold.state.controller.trace_interval_result_revision == 1


def test_teardown_reports_final_observed_pulse_delivery_before_reset(hold_cycle):
    runner = FakeControllerRunner()
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    controller = hold.state.controller
    controller.pulse_result_revision = 1
    controller.pulse_requested_duty = 0.1
    hold._advance_framed_pulse(0.0, False)
    runner.applied.clear()
    hold.ctx.clock.advance(2.0)
    hold._advance_framed_pulse(0.0, True)

    hold.teardown(200.0)

    assert any(applied.requested == 0.1 and applied.ratio == 1.0 for applied in runner.applied)


class _ObservationStatusRunner(FakeControllerRunner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.observation_status = {}

    def controller_state(self):
        return {"fake": True, **self.observation_status}


def _completed_frame(
    *,
    start=0.0,
    end=20.0,
    delivered=6.0,
    skipped=False,
    reset_reason=None,
):
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


def _configure_frame_observation(mode, *, revision=1, u_max=0.5, load=0.3):
    controller = mode.state.controller
    controller.pulse_result_revision = revision
    controller.pulse_frame_result_revision = revision
    controller.pulse_requested_duty = 0.0 if load is None else load
    controller.pulse_combustion_load = load
    controller.pulse_maximum_duty = u_max
    controller.pulse_requested_fan_duty = 50.0
    controller.pulse_frame_requested_auger_duty = 0.0 if load is None else load
    controller.pulse_frame_combustion_load = load
    controller.pulse_frame_baseline_combustion_load = 0.0 if load is None else load
    controller.pulse_frame_calibration_probe_load = 0.0
    controller.pulse_frame_calibration_stage = None
    controller.pulse_frame_maximum_duty = u_max
    controller.pulse_frame_applied_fan_duty = 60.0
    controller.pulse_frame_stale_command = False
    controller.controls_fan = True


def test_framed_completed_observations_are_exactly_aligned_and_deduplicated(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "completed-frame-observations"}
    _configure_frame_observation(mode)

    mode._advance_framed_pulse(0.0, True, ptemp=212.0)
    mode._advance_framed_pulse(6.0, False, ptemp=212.0)
    mode._advance_framed_pulse(20.0, False, ptemp=212.0)
    mode._advance_framed_pulse(20.0, True, ptemp=392.0)
    mode._advance_framed_pulse(26.0, False, ptemp=392.0)
    mode._advance_framed_pulse(40.0, False, ptemp=392.0)

    assert len(runner.observations) == 2
    first, second = runner.observations
    assert (first.frame_start_s, first.frame_end_s, first.delivered_on_s) == (0.0, 20.0, 6.0)
    assert first.realized_q == pytest.approx((6.0 / 20.0) / 0.5)
    assert first.temp_c == pytest.approx(100.0)
    assert (second.frame_start_s, second.frame_end_s, second.delivered_on_s) == (20.0, 40.0, 6.0)
    assert second.temp_c == pytest.approx(200.0)


def test_framed_observation_latches_role_generation_at_frame_start(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    runner.observation_status = {"adaptation": {"role_generation": 7}}
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "latched-role-generation"}
    _configure_frame_observation(mode)

    mode._advance_framed_pulse(0.0, True, ptemp=212.0)
    mode._advance_framed_pulse(6.0, False, ptemp=212.0)
    runner.observation_status = {"adaptation": {"role_generation": 8}}
    mode._advance_framed_pulse(20.0, False, ptemp=212.0)
    mode._advance_framed_pulse(20.0, True, ptemp=392.0)
    mode._advance_framed_pulse(26.0, False, ptemp=392.0)
    mode._advance_framed_pulse(40.0, False, ptemp=392.0)

    assert [(item.result_revision, item.role_generation) for item in runner.observations] == [(1, 7), (1, 8)]


@pytest.mark.parametrize(
    ("case", "inhibit", "skipped", "reset_reason", "expected_source"),
    [
        ("lid", InhibitReason.LID_OPEN, False, PulseResetReason.LID, "lid_open"),
        ("manual", InhibitReason.MANUAL_OVERRIDE, False, PulseResetReason.MANUAL, "manual_override"),
        ("safety", InhibitReason.SAFETY, False, PulseResetReason.SAFETY, "unknown"),
        ("stale", InhibitReason.STALE_COMMAND, False, None, "controller"),
        ("skipped", InhibitReason.NONE, True, None, "controller"),
        ("reset", InhibitReason.NONE, False, PulseResetReason.MODE_CHANGE, "unknown"),
        ("unknown", InhibitReason.NONE, False, None, "unknown"),
    ],
)
def test_ineligible_completed_frames_are_delivered_with_explicit_provenance(
    hold_cycle, case, inhibit, skipped, reset_reason, expected_source
):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode._trace_session_id = f"session-{case}"
    runner.bind_evidence_context(0, mode._trace_session_id, None)
    mode.state.metrics = {"id": f"ineligible-{case}"}
    _configure_frame_observation(mode, load=None if case == "unknown" else 0.3)
    records = []
    mode._trace_record = lambda kind, payload, timestamp: records.append((kind, payload, timestamp)) or True
    runner.observation_outcome = {
        "role_generation": 0,
        "eligible": False,
        "rejection_reasons": ("ineligible_frame",),
        "input_variance": 0.0,
        "input_levels": 0,
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
        "effective_updates": 0,
        "model_digest": "a" * 64,
    }

    mode._observe_completed_pulse_frame(
        _completed_frame(skipped=skipped, reset_reason=reset_reason),
        ptemp=212.0,
        inhibit=inhibit,
    )

    assert len(runner.observations) == 1
    mode._reconcile_model_observation_outcomes(now=20.0)
    observation = runner.observations[0]
    assert observation.output_source == expected_source
    assert observation.continuous is False
    assert observation.lid_open is (case == "lid")
    assert observation.manual_override is (case == "manual")
    assert observation.safety_inhibited is (case == "safety")
    assert observation.stale is (case == "stale")
    assert observation.skipped is (case == "skipped")
    assert observation.reset is (case in {"lid", "manual", "safety", "reset"})
    assert records and records[-1][0] is TraceEventKind.MODEL_OBSERVATION
    assert records[-1][1].eligible is False


def test_seed_and_zero_duration_frames_do_not_reach_the_runner(hold_cycle):
    runner = _ObservationStatusRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "seed-zero-observations"}
    _configure_frame_observation(mode, revision=0)

    mode._observe_completed_pulse_frame(_completed_frame(), ptemp=212.0, inhibit=InhibitReason.NONE)
    _configure_frame_observation(mode)
    mode._observe_completed_pulse_frame(
        _completed_frame(end=0.0, delivered=0.0), ptemp=212.0, inhibit=InhibitReason.NONE
    )

    assert runner.observations == []
