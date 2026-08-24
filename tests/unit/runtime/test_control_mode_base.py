"""Structural test for `ControlMode.run()`'s shared skeleton: a trivial
subclass records every hook invocation and we assert the ORDER matches the
template method's sense -> health -> safety -> act -> publish contract:
setup -> setup_safety -> [loop: check_safety -> on_tick -> status_fragment
(only when the 0.5s publish gate fires, AFTER the merged on_tick) ->
should_exit] -> teardown.

This complements (does not replace) the characterization oracle in
tests/characterization/, which is the real behavior-preservation gate.
"""

import pytest

import controller.runtime.modes.base as base_mode
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from controller.runtime.modes.base import ControlMode
from controller.runtime.modes.startup import StartupMode
from controller.runtime.state import WorkCycleState
from probes.thermocouple_health import (
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)
from probes.thermocouple_inference import (
    ThermocoupleInferencePolicy,
    fuse_thermocouple_health,
)
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.distance import FakeDistance
from tests.fakes.notifier import FakeNotifier
from tests.fakes.probes import FakeProbes
from tests.characterization.fixtures import base_settings, base_control, base_pellet_db


class _RecordingMode(ControlMode):
    name = "Recording"

    def __init__(self, ctx, state):
        super().__init__(ctx, state)
        self.calls = []
        self.teardown_ptemps = []

    def setup(self):
        self.calls.append("setup")

    def setup_safety(self, ptemp):
        self.calls.append("setup_safety")
        return "Active"

    def on_tick(self, now, ptemp, current_output_status):
        self.calls.append("on_tick")

    def check_safety(self, now, ptemp):
        self.calls.append("check_safety")
        return False

    def status_fragment(self):
        self.calls.append("status_fragment")
        return {}

    def should_exit(self, now, ptemp):
        self.calls.append("should_exit")
        # Bound the loop to exactly one iteration.
        return True

    def teardown(self, ptemp):
        self.calls.append("teardown")
        self.teardown_ptemps.append(ptemp)

    def _on_safety_event(self, event, now):
        self.calls.append(("safety_event", event))


def _make_ctx(*, temperatures=None, health_reports=None, inference_policy=None):
    settings = base_settings()
    if inference_policy is not None:
        settings["thermocouple_health"]["inference_policy"] = inference_policy
    control_data = base_control(mode="Recording")
    pellet_db = base_pellet_db()
    probes = FakeProbes().script(temperatures or [120])
    if health_reports is not None:
        probes.script_health(health_reports)
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    grill = FakeGrillPlatform(outputs=tuple(settings["platform"]["outputs"]))
    notifier = FakeNotifier()
    return ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
        store=store,
        notifications=notifier,
        clock=ManualClock(),
    )


def _healthy():
    return ThermocoupleHealthReport.healthy(now=0.0)


def _confirmed(fault):
    return ThermocoupleHealthReport.confirmed_hardware(
        faults=(fault,),
        now=0.0,
        status=fault.value,
    )

def _inferred(policy, *, primary=True):
    inferred = ThermocoupleHealthReport(
        state=ThermocoupleHealthState.CONFIRMED,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(
            ThermocoupleEvidence.JUNCTION_COLLAPSE,
            ThermocoupleEvidence.EXCITATION_RESPONSE,
        ),
        temperature_valid=False,
        observed_at=0.0,
    )
    return fuse_thermocouple_health(
        hardware=None,
        inferred=inferred,
        policy=ThermocoupleInferencePolicy(policy),
        is_primary=primary,
    )


def _suspected():
    return ThermocoupleHealthReport(
        state=ThermocoupleHealthState.SUSPECTED,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(ThermocoupleEvidence.JUNCTION_COLLAPSE,),
        observed_at=0.0,
    )


def _sensor(primary):
    return {
        "primary": {"Grill": primary},
        "food": {},
        "aux": {},
        "tr": {},
    }


def _positive_actuator_calls(calls):
    positive_names = {"igniter_on", "auger_on", "fan_on", "power_on", "pwm_fan_ramp"}
    return [call for call in calls if call[0] in positive_names]


def test_excitation_uses_actual_auger_igniter_union_without_double_counting():
    mode = _make_mode()
    mode.name = "Hold"
    mode.control["primary_setpoint"] = 212.0
    grill = mode.grill
    clock = mode.ctx.clock

    mode._read_probes_with_excitation()
    mode.state.cycle.ratio = 100.0
    clock.advance(2.0)
    mode._read_probes_with_excitation()
    grill.auger_on()
    clock.advance(3.0)
    mode._read_probes_with_excitation()
    grill.igniter_on()
    clock.advance(4.0)
    mode._read_probes_with_excitation()
    grill.auger_off()
    clock.advance(5.0)
    mode._read_probes_with_excitation()
    grill.igniter_off()
    clock.advance(6.0)
    mode._read_probes_with_excitation()

    contexts = [call["excitation"] for call in mode.probe_complex.read_calls]
    assert [context.delivered_heat_on_s for context in contexts] == [
        0.0,
        0.0,
        3.0,
        4.0,
        5.0,
        0.0,
    ]


def test_excitation_clock_regression_contributes_no_negative_heat():
    mode = _make_mode()
    mode.name = "Hold"
    mode.grill.auger_on()

    mode._read_probes_with_excitation()
    mode.ctx.clock.advance(5.0)
    mode._read_probes_with_excitation()
    mode.ctx.clock.advance(-10.0)
    mode._read_probes_with_excitation()

    contexts = [call["excitation"] for call in mode.probe_complex.read_calls]
    assert [context.delivered_heat_on_s for context in contexts] == [0.0, 5.0, 0.0]


@pytest.mark.parametrize(
    ("mode_name", "active_cook"),
    [
        ("Startup", True),
        ("Reignite", True),
        ("Smoke", True),
        ("Hold", True),
        ("Manual", True),
        ("Recipe", True),
        ("Monitor", False),
        ("Prime", False),
        ("Shutdown", False),
        ("Stop", False),
        ("Error", False),
    ],
)
def test_excitation_context_uses_active_mode_set_and_celsius_setpoint(mode_name, active_cook):
    mode = _make_mode()
    mode.name = mode_name
    mode.settings["globals"]["units"] = "F"
    mode.control["primary_setpoint"] = 212.0

    mode._read_probes_with_excitation()

    call = mode.probe_complex.read_calls[-1]
    assert call["now"] == 0.0
    assert call["excitation"].active_cook is active_cook
    assert call["excitation"].primary_setpoint_c == pytest.approx(100.0)


def test_run_passes_excitation_context_at_preflight_post_setup_and_tick():
    ctx = _make_ctx(temperatures=[225.0, 225.0, 225.0])
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert len(ctx.devices.probe_complex.read_calls) == 3
    assert all(call["excitation"] is not None for call in ctx.devices.probe_complex.read_calls)
    assert [call["now"] for call in ctx.devices.probe_complex.read_calls] == [0.0, 0.0, 0.0]


def test_confirmed_primary_fault_preflight_skips_mode_setup_and_positive_actuation(monkeypatch):
    monitor_events = []

    class RecordingMonitor:
        def __init__(self, *args, **kwargs):
            pass

        def start_monitor(self):
            monitor_events.append("start")

        def stop_monitor(self):
            monitor_events.append("stop")

    monkeypatch.setattr(base_mode, "Process_Monitor", RecordingMonitor)
    ctx = _make_ctx(
        temperatures=[None],
        health_reports=[{"Grill": _confirmed(ThermocoupleFault.OPEN)}],
    )
    ctx.devices.probe_complex.consume_thermocouple_health_transitions = lambda: ()
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert "setup" not in mode.calls
    assert "teardown" not in mode.calls
    assert "on_tick" not in mode.calls
    assert ctx.store.read_all_metrics() == []
    assert ctx.notifications.sent == []
    assert not _positive_actuator_calls(ctx.devices.grill_platform.calls)
    assert [name for name, _args in ctx.devices.grill_platform.calls] == [
        "igniter_off",
        "auger_off",
        "fan_off",
        "power_off",
    ]
    assert monitor_events == ["start", "stop"]


def test_observed_inferred_primary_remains_numeric_notifies_once_and_runs_tick():
    report = {"Grill": _inferred("observe")}
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, 225.0],
        health_reports=[report, report, report],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Recording"
    assert mode.calls.count("on_tick") == 1
    assert mode.calls.count("setup_safety") == 1
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]


def test_enforced_inferred_primary_preflight_stops_before_setup_and_numeric_guards(monkeypatch):
    evaluated = []
    monkeypatch.setattr(
        base_mode,
        "evaluate_phase",
        lambda *_args: evaluated.append(_args) or False,
    )
    ctx = _make_ctx(
        temperatures=[None],
        health_reports=[{"Grill": _inferred("enforce")}],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert "setup" not in mode.calls
    assert "setup_safety" not in mode.calls
    assert "on_tick" not in mode.calls
    assert evaluated == []


def test_enforced_inferred_primary_after_setup_stops_before_setup_safety(monkeypatch):
    evaluated = []
    monkeypatch.setattr(
        base_mode,
        "evaluate_phase",
        lambda *_args: evaluated.append(_args) or False,
    )
    ctx = _make_ctx(
        temperatures=[225.0, None],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _inferred("enforce")},
        ],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert "setup" in mode.calls
    assert "setup_safety" not in mode.calls
    assert "on_tick" not in mode.calls
    assert evaluated == []


def test_enforced_inferred_primary_tick_stops_before_guards_manual_and_on_tick(monkeypatch):
    evaluated = []

    def reject_none(_mode, _ctx, phase, _now, ptemp):
        assert ptemp is not None
        evaluated.append(phase)
        return False

    monkeypatch.setattr(base_mode, "evaluate_phase", reject_none)
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, None],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _healthy()},
            {"Grill": _inferred("enforce")},
        ],
    )
    control = ctx.store.read_control()
    control["manual"]["change"] = "auger"
    control["manual"]["output"] = True
    ctx.store.write_control_snapshot(control, origin="test")
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert mode.calls.count("on_tick") == 0
    assert evaluated == ["pre_loop"]
    assert not _positive_actuator_calls(ctx.devices.grill_platform.calls)


@pytest.mark.parametrize("policy", ("off", "observe", "enforce"))
def test_hardware_primary_stops_under_every_inference_policy(policy):
    ctx = _make_ctx(
        temperatures=[None],
        health_reports=[{"Grill": _confirmed(ThermocoupleFault.OPEN)}],
        inference_policy=policy,
    )

    _RecordingMode(ctx, WorkCycleState()).run()

    assert ctx.store.read_control()["mode"] == "Error"


def test_suspected_primary_stays_numeric_without_notification_or_stop():
    report = {"Grill": _suspected()}
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, 225.0],
        health_reports=[report, report, report],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Recording"
    assert mode.calls.count("on_tick") == 1
    assert ctx.notifications.sent == []


def test_observe_to_enforce_stops_existing_confirmation_without_duplicate_notification():
    observed = {"Grill": _inferred("observe")}
    enforced = {"Grill": _inferred("enforce")}
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, None],
        health_reports=[observed, observed, enforced],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]


def test_mode_reentry_does_not_repeat_consumed_confirmation_notification():
    observed = {"Grill": _inferred("observe")}
    ctx = _make_ctx(
        temperatures=[225.0] * 6,
        health_reports=[observed] * 6,
    )

    _RecordingMode(ctx, WorkCycleState()).run()
    _RecordingMode(ctx, WorkCycleState()).run()

    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]


def test_confirmed_primary_fault_after_setup_skips_none_safety_paths(monkeypatch):
    evaluated = []

    def record_evaluate_phase(mode, ctx, phase, now, ptemp):
        evaluated.append((phase, ptemp))
        return False

    monkeypatch.setattr(base_mode, "evaluate_phase", record_evaluate_phase)
    ctx = _make_ctx(
        temperatures=[225.0, None],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _confirmed(ThermocoupleFault.OPEN)},
        ],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert "setup" in mode.calls
    assert "setup_safety" not in mode.calls
    assert "on_tick" not in mode.calls
    assert evaluated == []
    assert ("safety_event", "thermocouple_fault") in mode.calls
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]


def test_startup_post_setup_fault_persists_last_valid_primary_temperature():
    fault_sample = _sensor(None)
    ctx = _make_ctx(
        temperatures=[225.0, fault_sample],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _confirmed(ThermocoupleFault.OPEN)},
        ],
    )
    control = ctx.store.read_control()
    control["mode"] = "Startup"
    ctx.store.write_control_snapshot(control, origin="test")

    StartupMode(ctx, WorkCycleState()).run()

    saved_control = ctx.store.read_control()
    safety = saved_control["safety"]
    assert isinstance(safety, dict)
    after_start_temp = safety["afterstarttemp"]
    assert after_start_temp == 225.0
    assert isinstance(after_start_temp, (int, float))
    assert fault_sample["primary"]["Grill"] is None


def test_confirmed_primary_fault_on_tick_breaks_before_numeric_guards_and_actuation(monkeypatch):
    evaluated = []

    def reject_none(mode, ctx, phase, now, ptemp):
        assert ptemp is not None
        evaluated.append((phase, ptemp))
        return False

    monkeypatch.setattr(base_mode, "evaluate_phase", reject_none)
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, None],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _healthy()},
            {"Grill": _confirmed(ThermocoupleFault.SHORT)},
        ],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert mode.calls.count("on_tick") == 0
    assert evaluated == [("pre_loop", 225.0)]
    assert ("safety_event", "thermocouple_fault") in mode.calls
    assert ctx.notifications.sent == ["Thermocouple_Fault_Primary"]
    assert not _positive_actuator_calls(ctx.devices.grill_platform.calls)
    assert mode.teardown_ptemps == [225.0]
    assert isinstance(ctx.store, InMemoryStore)
    assert ctx.store.read_current()["P"]["Grill"] is None


def test_tick_health_fence_blocks_pending_positive_manual_override():
    ctx = _make_ctx(
        temperatures=[225.0, 225.0, None],
        health_reports=[
            {"Grill": _healthy()},
            {"Grill": _healthy()},
            {"Grill": _confirmed(ThermocoupleFault.SHORT)},
        ],
    )
    control = ctx.store.read_control()
    manual = control["manual"]
    assert isinstance(manual, dict)
    manual["change"] = "auger"
    manual["output"] = True
    ctx.store.write_control_snapshot(control, origin="test")
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.name = "Manual"

    mode.run()

    assert ctx.store.read_control()["mode"] == "Error"
    assert mode.calls.count("on_tick") == 0
    assert not _positive_actuator_calls(ctx.devices.grill_platform.calls)


@pytest.mark.parametrize(
    ("group", "label"),
    [
        pytest.param("food", "Food", id="food"),
        pytest.param("aux", "Aux", id="aux"),
    ],
)
def test_inferred_secondary_fault_continues_and_repeated_sample_notifies_once(group, label):
    sensor = _sensor(225.0)
    sensor[group][label] = None
    report = {"Grill": _healthy(), label: _inferred("observe", primary=False)}
    ctx = _make_ctx(
        temperatures=[sensor, sensor, sensor],
        health_reports=[report, report, report],
    )
    mode = _RecordingMode(ctx, WorkCycleState())

    mode.run()

    assert ctx.store.read_control()["mode"] == "Recording"
    assert mode.calls.count("on_tick") == 1
    assert ctx.notifications.sent == ["Thermocouple_Fault_Secondary"]


def test_control_mode_hook_order_one_bounded_tick():
    ctx = _make_ctx()
    # Preflight and post-setup probe reads each receive their own monotonic
    # timestamp before the existing start-time read. Advance only for the
    # loop timestamp so the status publish gate fires in this bounded tick.
    real_now = ctx.clock.now
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        if calls["n"] <= 3:
            return real_now()
        return real_now() + 0.6

    ctx.clock.now = _now

    mode = _RecordingMode(ctx, WorkCycleState())
    mode.run()

    # sense -> safety -> act -> publish: check_safety now runs BEFORE the merged
    # on_tick, and the status-publish gate (status_fragment) runs AFTER it.
    assert mode.calls == [
        "setup",
        "setup_safety",
        "check_safety",
        "on_tick",
        "status_fragment",
        "should_exit",
        "teardown",
    ]


def test_preloop_identity_refresh_does_not_shift_mode_timer_origin():
    ctx = _make_ctx()
    real_now = ctx.clock.now
    clock_reads = 0

    def _now():
        nonlocal clock_reads
        clock_reads += 1
        return real_now() if clock_reads <= 3 else real_now() + 0.6

    ctx.clock.now = _now
    mode = _RecordingMode(ctx, WorkCycleState())
    clock_reads_at_on_tick = []
    on_tick = mode.on_tick

    def record_on_tick(now, ptemp, current_output_status):
        clock_reads_at_on_tick.append(clock_reads)
        return on_tick(now, ptemp, current_output_status)

    mode.on_tick = record_on_tick

    mode.run()

    assert mode.state.timers.start_time == 0.0
    assert "status_fragment" in mode.calls
    assert clock_reads_at_on_tick == [5]


def test_loop_identity_refresh_rotates_with_supplied_loop_time():
    mode = _make_mode()
    mode.control["cook_id"] = "old-session"
    refreshed = mode.control.copy()
    refreshed["cook_id"] = "new-session"
    rotations = []
    mode.on_cook_identity_rotated = lambda previous, current, now: rotations.append((previous, current, now))

    assert mode._refresh_cook_identity(refreshed, now=12.5) is refreshed
    assert rotations == [("old-session", "new-session", 12.5)]


def test_status_publishes_duty_fields():
    ctx = _make_ctx()
    real_now = ctx.clock.now
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        return real_now() if calls["n"] <= 3 else real_now() + 0.6

    ctx.clock.now = _now
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.run()
    status = ctx.store.read_status()
    assert "cycle_ratio" in status
    assert "fan_duty" in status
    # Default state: no auger ratio set, DC fan disabled, fan output off.
    assert status["cycle_ratio"] == 0.0
    assert status["fan_duty"] == 0


def _make_mode():
    """Build a ControlMode the way run() does before entering the loop, so
    _apply_manual_overrides can be exercised directly without running the
    full work-cycle loop."""
    ctx = _make_ctx()
    mode = _RecordingMode(ctx, WorkCycleState())
    mode.settings = ctx.store.read_settings()
    mode.control = ctx.store.read_control()
    mode.state.manual_override = {"igniter": 0, "auger": 0, "fan": 0, "power": 0, "pwm": 0}
    return mode


@pytest.mark.parametrize("actuator", ["auger", "fan", "igniter", "power"])
def test_on_manual_output_is_called_with_the_change_and_output(actuator):
    """The hook fires while control['manual'] still names the actuator, for
    each of the four boolean actuators -- each has its own branch and its own
    call site, so this must be parametrized rather than covering "auger" alone."""
    mode = _make_mode()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = actuator
    control["manual"]["output"] = True
    mode.settings["safety"]["allow_manual_changes"] = True

    mode._apply_manual_overrides(
        control,
        now=100.0,
        current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100},
    )

    assert seen == [(actuator, True)]
    # and the reset still happened afterwards
    assert control["manual"]["change"] is False


def test_on_manual_output_is_not_called_when_no_change_is_pending():
    mode = _make_mode()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = False

    mode._apply_manual_overrides(
        control,
        now=100.0,
        current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100},
    )

    assert seen == []


def test_on_manual_output_fires_even_when_no_physical_toggle_is_needed():
    """The hook marks override application, not a change in actuator state:
    it must still fire when the requested output already matches the current
    one (no fan_on()/fan_off() call happens)."""
    mode = _make_mode()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = "auger"
    control["manual"]["output"] = True
    mode.settings["safety"]["allow_manual_changes"] = True

    mode._apply_manual_overrides(
        control,
        now=100.0,
        # auger already on and matches the requested output: no actuation call
        current_output_status={"auger": True, "fan": False, "igniter": False, "power": False, "pwm": 100},
    )

    assert seen == [("auger", True)]


def test_on_manual_output_is_not_called_when_a_pwm_request_is_rejected():
    """A pwm change is only "present" in control['manual'] until its own gate
    (dc_fan enabled, fan currently on, speed actually differing) decides
    whether it is applied. With dc_fan disabled the request is never applied,
    so the hook must not fire even though control['manual']['change'] == 'pwm'."""
    mode = _make_mode()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = "pwm"
    control["manual"]["pwm"] = 50
    mode.settings["safety"]["allow_manual_changes"] = True
    mode.settings["platform"]["dc_fan"] = False

    mode._apply_manual_overrides(
        control,
        now=100.0,
        current_output_status={"auger": False, "fan": True, "igniter": False, "power": False, "pwm": 100},
    )

    assert seen == []


def test_on_manual_output_reports_the_resolved_speed_for_an_accepted_pwm_change():
    """An accepted pwm change (dc_fan enabled, fan on, speed differing from
    current) is the fifth call site, and its `output` argument is the
    resolved duty-cycle speed -- not control['manual']['output'], which pwm
    requests never populate. Reverting the argument to control['manual']['output']
    (False here) must fail this."""
    mode = _make_mode()
    seen = []
    mode._on_manual_output = lambda name, output: seen.append((name, output))
    control = mode.control
    control["manual"]["change"] = "pwm"
    control["manual"]["pwm"] = 50
    control["manual"]["output"] = False
    mode.settings["safety"]["allow_manual_changes"] = True
    mode.settings["platform"]["dc_fan"] = True

    mode._apply_manual_overrides(
        control,
        now=100.0,
        current_output_status={"auger": False, "fan": True, "igniter": False, "power": False, "pwm": 100},
    )

    assert seen == [("pwm", 50)]
    assert control["manual"]["pwm"] == 100  # reset after being applied


def test_on_manual_output_default_is_a_no_op():
    assert _make_mode()._on_manual_output("auger", True) is None


def test_last_now_defaults_to_zero_before_any_tick():
    assert _make_mode()._last_now == 0.0


def test_apply_manual_overrides_refreshes_last_now_unconditionally():
    """`_last_now` is the loop-consistent `now` a mode's `_on_manual_output`
    override reads (the hook has no `now` parameter of its own -- see its
    docstring). It must be refreshed even when no manual change is pending:
    `run()` samples `now` once per iteration and passes that same value to
    `_apply_manual_overrides` and `on_tick`, so gating the refresh behind a
    pending change would let it go stale on every no-op tick."""
    mode = _make_mode()
    control = mode.control
    control["manual"]["change"] = False

    mode._apply_manual_overrides(
        control,
        now=123.0,
        current_output_status={"auger": False, "fan": False, "igniter": False, "power": False, "pwm": 100},
    )

    assert mode._last_now == 123.0


def _status_with_dc_fan(*, fan_on: bool, duty: int):
    settings = base_settings()
    settings["platform"]["dc_fan"] = True
    control_data = base_control(mode="Recording")
    control_data["duty_cycle"] = duty
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=base_pellet_db())
    grill = FakeGrillPlatform(dc_fan=True, outputs=tuple(settings["platform"]["outputs"]))
    if fan_on:
        grill.fan_on(duty)
    ctx = ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=FakeProbes().script([120]), dist_device=FakeDistance()),
        store=store,
        notifications=FakeNotifier(),
        clock=ManualClock(),
    )
    real_now = ctx.clock.now
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        return real_now() if calls["n"] <= 3 else real_now() + 0.6

    ctx.clock.now = _now
    _RecordingMode(ctx, WorkCycleState()).run()
    return ctx.store.read_status()


def test_a_dc_fan_that_is_not_running_reports_no_duty():
    """control['duty_cycle'] is the duty the fan WOULD be given. Reporting it
    while the fan is off puts "FAN IDLE" next to "FAN DUTY 100%" on the
    dashboard. The AC branch and the Manual branch both gate on the output."""
    assert _status_with_dc_fan(fan_on=False, duty=100)["fan_duty"] == 0


def test_a_running_dc_fan_still_reports_its_commanded_duty():
    assert _status_with_dc_fan(fan_on=True, duty=100)["fan_duty"] == 100
