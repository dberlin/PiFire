"""End-to-end Hold control-trace contracts using the normal fake runtime seam."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.control_trace import ControllerBranch, ControllerType, PidSpUpdatePayload, TraceEventKind
from common.datastore_accessors import read_control_trace_session
from controller.applied_output import OutputSource
from controller.base import MpcFailureState, MpcTraceDiagnostics, PidSpTraceDiagnostics, PidTraceDiagnostics
from controller.mpc_allocator import allocate
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.runner import ControllerUpdateResult, SyncControllerRunner
from tests.fakes.runner import FakeControllerRunner


class _Recorder:
    def __init__(self, *, warning):
        self.records = []
        self.flushes = []
        self.closed = 0

    def record(self, record):
        self.records.append(record)

    def flush_due(self, now_ms):
        self.flushes.append(now_ms)

    def close(self):
        self.closed += 1


def _pid_result(revision=1):
    diagnostics = PidTraceDiagnostics(
        observed_dt_seconds=2.0,
        error=5.0,
        proportional_term=0.2,
        integral_term=0.1,
        derivative_term=0.0,
        integral_accumulator=0.1,
        integral_clamped=False,
        derivative_input=1.0,
        derivative_state=0.5,
        proportional_band=100.0,
        kp=1.0,
        ki=0.1,
        kd=0.0,
        center=225.0,
        previous_temperature=219.0,
        previous_update_time=1.0,
        raw_output=0.3,
        final_output=0.3,
    )
    return ControllerUpdateResult(
        cycle_ratio=0.3,
        fan=None,
        input_temperature=100.0,
        diagnostics=diagnostics,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _pid_sp_result(revision=1):
    diagnostics = PidSpTraceDiagnostics(
        observed_dt_seconds=2.0,
        error=5.0,
        proportional_term=0.2,
        integral_term=0.1,
        derivative_term=0.0,
        integral_accumulator=0.1,
        integral_clamped=False,
        derivative_input=1.0,
        derivative_state=0.5,
        proportional_band=100.0,
        kp=1.0,
        ki=0.1,
        kd=0.0,
        center=225.0,
        previous_temperature=219.0,
        previous_update_time=1.0,
        raw_output=0.3,
        final_output=0.3,
        measured_rate=-0.4,
        predicted_temperature=221.5,
        predicted_error=3.5,
        tau_seconds=12.0,
        theta_seconds=4.0,
        stable_window_seconds=15.0,
        center_factor=0.75,
        new_target_before=True,
        new_target_after=False,
        target_change_temperature=218.0,
        target_change_time=0.5,
        branch=ControllerBranch.OVERSHOOT,
    )
    return ControllerUpdateResult(
        cycle_ratio=0.3,
        fan=None,
        input_temperature=100.0,
        diagnostics=diagnostics,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _mpc_result(
    revision=1,
    *,
    consecutive_policy_failures=0,
    raw_policy_firing_load=40.0,
    applied_combustion_load=40.0,
    requested_auger_duty=None,
):
    diagnostics = MpcTraceDiagnostics(
        state_names=("temperature",),
        state_values=(220.0,),
        disturbance_estimate=0.0,
        model_revision=1,
        model_provenance="configured",
        raw_policy_firing_load=raw_policy_firing_load,
        equilibrium_feed_forward=35.0,
        residual_move=5.0,
        bounded_firing_load=40.0,
        applied_combustion_load=applied_combustion_load,
        policy_kind="net",
        failure_state=MpcFailureState.SUCCESS,
        consecutive_policy_failures=consecutive_policy_failures,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
    )
    allocation = allocate(
        40.0,
        Q_min=5.0,
        Q_max=100.0,
        u_min=0.1,
        u_max=0.9,
        fan_min_pct=40.0,
        fan_max_pct=100.0,
        enable_fan=True,
    )
    if requested_auger_duty is not None:
        allocation = replace(allocation, auger_duty=requested_auger_duty)
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan={"duty": allocation.fan_duty or 0.0},
        input_temperature=100.0,
        diagnostics=diagnostics,
        allocation=allocation,
        revision=revision,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.1,
        solve_duration_seconds=1.1 - 1.0,
        completed_wall_time=1.1,
    )


def _install_recorder(monkeypatch):
    import controller.runtime.modes.hold as hold_module

    recorder = _Recorder(warning=lambda _message: None)
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    return recorder


def test_pid_hold_records_session_update_applied_output_and_flushes_in_cook(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0).script([_pid_result()])
    mode = hold_cycle(runner, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-pid"}
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    assert [record.event_kind for record in recorder.records] == [
        TraceEventKind.SESSION,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.APPLIED_OUTPUT,
    ]
    assert all(record.cook_id == "cook-pid" for record in recorder.records)
    update = recorder.records[1]
    applied = recorder.records[2]
    assert update.controller is ControllerType.PID
    assert update.payload.result_revision == applied.payload.result_revision == 1
    assert update.payload.control_period_seconds == 1.0
    assert applied.payload.realized_auger_duty != runner.applied[-1].ratio
    assert recorder.flushes


def test_mpc_hold_records_update_allocation_and_fixed_cycle_feedback_once_per_revision(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result()
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script([result, result])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 220.0, output)

    assert [record.event_kind for record in recorder.records] == [
        TraceEventKind.SESSION,
        TraceEventKind.CONTROL_UPDATE,
        TraceEventKind.ALLOCATION,
        TraceEventKind.APPLIED_OUTPUT,
        TraceEventKind.ACTUATION_FRAME,
        TraceEventKind.APPLIED_OUTPUT,
    ]
    update, allocation = recorder.records[1:3]
    assert update.payload.actuation_mode.value == "fixed_cycle"
    assert update.payload.result_revision == allocation.payload.result_revision == 1
    assert update.payload.control_period_seconds == 1.0
    assert result.allocation is not None
    assert allocation.payload.requested_auger_duty == result.allocation.auger_duty


def test_mpc_trace_marks_the_first_success_after_staleness_recovered(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script(
        [_mpc_result(1, consecutive_policy_failures=1), _mpc_result(2)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-recovery"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 220.0, output)
    updates = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]

    assert updates[0].stale is True
    assert updates[1].stale is False
    assert updates[1].recovered is True


def test_hold_records_branch_local_safety_and_closes_recorder_once(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0).script([_pid_result()])
    mode = hold_cycle(runner, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-safety"}
    mode._ensure_trace_session(1.0)

    mode._on_manual_output("auger", True)
    mode._on_manual_release("auger", 2.0)
    mode._on_safety_event("temperature_guard", 3.0)
    mode.teardown(220.0)
    mode.teardown(220.0)
    assert [
        record.payload.event.value for record in recorder.records if record.event_kind is TraceEventKind.SAFETY_EVENT
    ] == [
        "manual_takeover",
        "manual_release",
        "temperature_guard",
    ]
    assert recorder.closed == 1


def test_mpc_trace_preserves_a_zero_raw_policy_load(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script([_mpc_result(raw_policy_firing_load=0.0)])
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-raw-zero"}
    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})
    (update,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]

    assert update.raw_output == 0.0


def test_hold_emits_one_incomplete_terminal_interval(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0).script([_pid_result()])
    mode = hold_cycle(runner, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-terminal"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.ctx.clock.advance(3.0)
    mode.teardown(220.0)
    mode.teardown(220.0)
    terminal = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and not record.payload.sample_complete
    ]

    assert len(terminal) == 1
    assert terminal[0].realized_combustion_load is None


def test_hold_flushes_typed_rows_to_sqlite_before_teardown(hold_cycle, monkeypatch, ds):
    import controller.runtime.modes.hold as hold_module

    recorder = ControlTraceRecorder(
        monotonic_clock=lambda: 0,
        wall_clock=lambda: 40 * 24 * 60 * 60 * 1_000,
    )
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    monkeypatch.setattr(hold_module.time, "monotonic_ns", lambda: 5_000_000_000)
    runner = FakeControllerRunner(period=1.0).script([_pid_result()])
    mode = hold_cycle(runner, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-persisted"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    try:
        mode.on_tick(2.0, 220.0, output)
        assert mode._trace_session_id is not None
        records = read_control_trace_session(mode._trace_session_id)

        assert [record.event_kind for record in records] == [
            TraceEventKind.SESSION,
            TraceEventKind.CONTROL_UPDATE,
            TraceEventKind.APPLIED_OUTPUT,
        ]
        assert records[0].cook_id == "cook-persisted"
        assert records[1].payload.result_revision == records[2].payload.result_revision == 1
    finally:
        mode.ctx.clock.advance(2.0)
        mode.teardown(220.0)


def test_pid_sp_completed_update_records_exact_typed_fields_and_branch(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(FakeControllerRunner(period=1.0).script([_pid_sp_result()]), controller="pid_sp")
    mode.setup()
    mode.state.metrics = {"id": "cook-pid-sp"}

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    (record,) = [record for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE]
    payload = record.payload
    assert isinstance(payload, PidSpUpdatePayload)
    assert (
        payload.measured_rate,
        payload.predicted_temperature,
        payload.predicted_error,
        payload.tau_seconds,
        payload.theta_seconds,
        payload.stable_window_seconds,
        payload.center_factor,
        payload.new_target_before,
        payload.new_target_after,
        payload.target_change_temperature,
        payload.target_change_ms,
        payload.branch,
    ) == (-0.4, 221.5, 3.5, 12.0, 4.0, 15.0, 0.75, True, False, 218.0, 500, ControllerBranch.OVERSHOOT)


def test_reconfigure_finishes_the_old_pid_session_before_opening_coherent_mpc_session(hold_cycle, monkeypatch):
    class _ReconfiguringRunner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            self._commands_fan = True
            return "Active"

    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = _ReconfiguringRunner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore())
    mode.settings["platform"]["dc_fan"] = True
    mode.control["pwm_control"] = True
    mode.ctx.store._settings["platform"]["dc_fan"] = True
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.ctx.store._settings["controller"]["config"]["mpc"]["trace_marker"] = "new-mpc-session"
    mode.ctx.store._control["pwm_control"] = True
    mode.setup()
    mode.state.metrics = {"id": "cook-reconfigure"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)
    old_session_id = mode._trace_session_id

    mode.control["controller_update"] = True
    mode.on_tick(4.0, 220.0, output)

    sessions = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    reconfigure = next(
        record
        for record in recorder.records
        if record.event_kind is TraceEventKind.SAFETY_EVENT and record.payload.event.value == "controller_reconfigure"
    )
    new_session = sessions[-1]
    assert len(sessions) == 2
    assert old_session_id is not None
    assert reconfigure.session_id == old_session_id
    assert new_session.session_id != old_session_id
    assert new_session.controller is ControllerType.MPC
    assert new_session.payload.fan_authority is True
    assert dict((setting.key, setting.value) for setting in new_session.payload.controller_config)["trace_marker"] == (
        "new-mpc-session"
    )
    old_incomplete = [
        record
        for record in recorder.records
        if record.session_id == old_session_id
        and record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and not record.payload.sample_complete
    ]
    assert len(old_incomplete) == 1
    assert old_incomplete[0].payload.result_revision == 1
    new_session_events = [record for record in recorder.records if record.session_id == new_session.session_id]
    assert [record.event_kind for record in new_session_events[:2]] == [
        TraceEventKind.SESSION,
        TraceEventKind.MODEL_EVENT,
    ]
    assert new_session_events[0].payload.model_revision == 8
    assert new_session_events[0].payload.model_provenance == "restore_submitted"
    assert new_session_events[1].payload.event.value == "restore"
    assert not any(
        record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.result_revision == 0
        for record in new_session_events
    )
    assert (
        next(
            record
            for record in recorder.records
            if record.session_id == new_session.session_id and record.event_kind is TraceEventKind.CONTROL_UPDATE
        ).controller
        is ControllerType.MPC
    )


def test_fixed_cycle_frame_uses_new_bounded_ratio_for_its_scheduled_times(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(FakeControllerRunner(period=1.0), cycle_data_extra={"HoldCycleTime": 20.0}, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-cycle"}
    mode._ensure_trace_session(2.0)
    mode.state.cycle.ratio = 0.9

    mode._trace_start_frame(2.0, raw_duty=0.9, bounded_duty=0.25, revision=3, active=False)
    mode._trace_finish_frame(5.0)

    (frame,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.ACTUATION_FRAME]
    assert (frame.bounded_duty, frame.scheduled_on_seconds, frame.scheduled_off_seconds) == (0.25, 5.0, 15.0)


def test_manual_auger_takeover_finishes_the_active_frame_with_actual_delivery(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0)
    mode = hold_cycle(runner, controller="pid")
    mode.setup()
    mode.state.metrics = {"id": "cook-manual"}
    mode._ensure_trace_session(2.0)
    mode._trace_start_frame(2.0, raw_duty=0.5, bounded_duty=0.5, revision=4, active=True)
    mode._last_now = 5.0

    mode._on_manual_output("auger", True)

    (frame,) = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.ACTUATION_FRAME]
    assert (frame.actual_on_seconds, frame.transition_count, frame.inhibit_reason.value, frame.output_active) == (
        3.0,
        1,
        "manual_override",
        True,
    )
    assert runner.applied[-1].ratio == 1.0


def test_mpc_zero_raw_load_and_zero_requested_auger_duty_remain_zero(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    result = _mpc_result(raw_policy_firing_load=0.0, requested_auger_duty=0.0)
    mode = hold_cycle(FakeControllerRunner(period=1.0, commands_fan=True).script([result]), controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc-zero"}

    mode.on_tick(2.0, 220.0, {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100})

    update = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.CONTROL_UPDATE)
    allocation = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.ALLOCATION)
    applied = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT)
    assert (update.raw_output, allocation.requested_auger_duty, applied.realized_auger_duty) == (0.0, 0.0, 0.1)


@pytest.mark.parametrize(
    ("accepted", "expected_event"),
    [(False, "reject"), (True, "adopt")],
    ids=["rejected-refit", "accepted-refit"],
)
def test_refit_records_refit_then_its_verdict(hold_cycle, monkeypatch, accepted, expected_event):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True)
    runner.snapshot = {"revision": 7}
    runner.refit_verdict = SimpleNamespace(accepted=accepted)
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.settings["controller"]["config"]["mpc"]["enable_identification"] = True
    mode.state.metrics = {"id": f"cook-refit-{accepted}"}
    mode._ensure_trace_session(1.0)

    mode.teardown(220.0)

    model_events = [
        record.payload.event.value for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVENT
    ]
    assert model_events == ["refit", expected_event]


def test_mpc_applied_load_is_the_prior_interval_consumed_by_the_next_result(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script(
        [_mpc_result(1, applied_combustion_load=0.0), _mpc_result(2, applied_combustion_load=17.5)]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc-feedback"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 220.0, output)

    applied = [record.payload for record in recorder.records if record.event_kind is TraceEventKind.APPLIED_OUTPUT]
    assert applied[-1].result_revision == 2
    assert applied[-1].realized_combustion_load == 17.5


def test_mpc_lid_interval_records_the_next_completed_feedback_load(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script(
        [
            _mpc_result(1, applied_combustion_load=0.0),
            _mpc_result(2, applied_combustion_load=11.0),
            _mpc_result(3, applied_combustion_load=23.0),
        ]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc-lid-feedback"}
    mode.state.target_temp_achieved = True
    mode.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.on_tick(4.0, 180.0, output)
    mode.on_tick(6.0, 180.0, output)

    lid_feedback = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT and record.payload.output_source is OutputSource.LID_OPEN
    ]
    assert [(payload.result_revision, payload.realized_combustion_load) for payload in lid_feedback] == [(3, 23.0)]


def test_mpc_manual_interval_records_the_next_completed_feedback_load(hold_cycle, monkeypatch):
    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True).script(
        [
            _mpc_result(1, applied_combustion_load=0.0),
            _mpc_result(2, applied_combustion_load=8.0),
            _mpc_result(3, applied_combustion_load=14.0),
        ]
    )
    mode = hold_cycle(runner, controller="mpc")
    mode.setup()
    mode.state.metrics = {"id": "cook-mpc-manual-feedback"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}

    mode.on_tick(2.0, 220.0, output)
    mode.state.manual_override["auger"] = 5.0
    mode._last_now = 3.0
    mode._on_manual_output("auger", True)
    mode.on_tick(4.0, 220.0, output)
    mode.on_tick(6.0, 220.0, output)

    manual_feedback = [
        record.payload
        for record in recorder.records
        if record.event_kind is TraceEventKind.APPLIED_OUTPUT
        and record.payload.output_source is OutputSource.MANUAL_OVERRIDE
    ]
    assert [(payload.result_revision, payload.realized_combustion_load) for payload in manual_feedback] == [
        (2, 8.0),
        (3, 14.0),
    ]


def test_initial_async_restore_session_uses_queued_snapshot_not_old_published_snapshot(hold_cycle, monkeypatch):
    class _ModelStore:
        def load(self, controller):
            return {"revision": 8} if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, wants_async=True)
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-async-restore"}

    mode._ensure_trace_session(1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (8, "restore_submitted")
    assert runner.snapshot == {"revision": 1}
    assert [
        record.payload.event.value for record in recorder.records if record.event_kind is TraceEventKind.MODEL_EVENT
    ] == ["restore"]


def _run_first_loop_safety_trace(hold_cycle, monkeypatch, *, control_mode=None, guard_temperature=None):
    import controller.runtime.modes.base as base_module

    class _Monitor:
        def start_monitor(self):
            pass

        def heartbeat(self):
            pass

        def stop_monitor(self):
            pass

    recorder = _install_recorder(monkeypatch)
    monkeypatch.setattr(base_module, "Process_Monitor", lambda *args, **kwargs: _Monitor())
    mode = hold_cycle(FakeControllerRunner(period=1.0), controller="pid")
    if control_mode is not None:
        mode.ctx.store._control["mode"] = control_mode
        mode.ctx.store._control["updated"] = True
    if guard_temperature is not None:
        mode.ctx.devices.probe_complex.script([225.0, guard_temperature])

    mode.run()

    return [
        record
        for record in recorder.records
        if record.event_kind in (TraceEventKind.SESSION, TraceEventKind.SAFETY_EVENT)
    ]


def test_first_loop_stop_persists_session_then_typed_safety_through_fake_device_run(hold_cycle, monkeypatch):
    records = _run_first_loop_safety_trace(hold_cycle, monkeypatch, control_mode="Stop")

    assert [record.event_kind for record in records] == [TraceEventKind.SESSION, TraceEventKind.SAFETY_EVENT]
    assert records[0].session_id == records[1].session_id
    assert records[1].payload.event.value == "stop"


def test_first_loop_error_persists_session_then_typed_safety_through_fake_device_run(hold_cycle, monkeypatch):
    records = _run_first_loop_safety_trace(hold_cycle, monkeypatch, control_mode="Error")

    assert [record.event_kind for record in records] == [TraceEventKind.SESSION, TraceEventKind.SAFETY_EVENT]
    assert records[0].session_id == records[1].session_id
    assert records[1].payload.event.value == "error"


def test_first_loop_universal_temperature_guard_persists_typed_safety_through_fake_device_run(hold_cycle, monkeypatch):
    records = _run_first_loop_safety_trace(hold_cycle, monkeypatch, guard_temperature=1_000.0)

    assert [record.event_kind for record in records] == [TraceEventKind.SESSION, TraceEventKind.SAFETY_EVENT]
    assert records[0].session_id == records[1].session_id
    assert records[1].payload.event.value == "temperature_guard"


@pytest.mark.parametrize(
    ("stored_snapshot", "restore_accepted"),
    [
        (None, True),
        ({"revision": 8}, False),
        ({"revision": "invalid"}, True),
    ],
    ids=["no-stored-model", "rejected-restore", "invalid-stored-model"],
)
def test_async_reconfigure_does_not_leak_the_old_published_model_into_new_session(
    hold_cycle, monkeypatch, stored_snapshot, restore_accepted
):
    class _Runner(FakeControllerRunner):
        def reconfigure(self, settings, control, logger=None):
            return "Active"

        def restore_model(self, snapshot):
            self.restored.append(snapshot)
            return restore_accepted

    class _ModelStore:
        def load(self, controller):
            return stored_snapshot if controller == "mpc" else None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = _Runner(period=1.0, wants_async=True).script([_pid_result(), _mpc_result(2)])
    runner.snapshot = {"revision": 1}
    mode = hold_cycle(runner, controller="pid", model_store=_ModelStore())
    mode.ctx.store._settings["controller"]["selected"] = "mpc"
    mode.setup()
    mode.state.metrics = {"id": f"cook-no-leak-{restore_accepted}"}
    output = {"auger": False, "fan": False, "igniter": False, "power": True, "pwm": 100}
    mode.on_tick(2.0, 220.0, output)

    mode.control["controller_update"] = True
    mode.on_tick(4.0, 220.0, output)

    (new_session,) = [
        record
        for record in recorder.records
        if record.event_kind is TraceEventKind.SESSION and record.controller is ControllerType.MPC
    ]
    assert (new_session.payload.model_revision, new_session.payload.model_provenance) == (None, None)


def test_sync_runner_with_async_preferring_core_records_completed_restore(hold_cycle, monkeypatch):
    class _Core:
        def __init__(self):
            self.snapshot = {"revision": 1}

        def set_target(self, setpoint):
            pass

        def get_control_period(self):
            return 1.0

        def commands_fan(self):
            return False

        def wants_async(self):
            return True

        def set_output(self, applied):
            pass

        def get_model_snapshot(self):
            return self.snapshot

        def restore_model(self, snapshot):
            self.snapshot = snapshot
            return True

        def get_status(self):
            return None

    class _ModelStore:
        def load(self, controller):
            return {"revision": 8}

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    mode = hold_cycle(SyncControllerRunner(_Core()), controller="mpc", model_store=_ModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-sync-restore"}

    mode._ensure_trace_session(1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (8, "restored")


def test_initial_session_uses_the_current_published_model_without_restore(hold_cycle, monkeypatch):
    class _NoModelStore:
        def load(self, controller):
            return None

        def save(self, controller, snapshot):
            return True

    recorder = _install_recorder(monkeypatch)
    runner = FakeControllerRunner(period=1.0, commands_fan=True, wants_async=True)
    runner.snapshot = {"revision": 5}
    mode = hold_cycle(runner, controller="mpc", model_store=_NoModelStore())
    mode.setup()
    mode.state.metrics = {"id": "cook-published-model"}

    mode._ensure_trace_session(1.0)

    (session,) = [record for record in recorder.records if record.event_kind is TraceEventKind.SESSION]
    assert (session.payload.model_revision, session.payload.model_provenance) == (5, "persisted")
