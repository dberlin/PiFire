"""Structural test for `ControlMode.run()`'s shared skeleton: a trivial
subclass records every hook invocation and we assert the ORDER matches the
template method's sense -> safety -> act -> publish contract:
setup -> setup_safety -> [loop: check_safety -> on_tick -> status_fragment
(only when the 0.5s publish gate fires, AFTER the merged on_tick) ->
should_exit] -> teardown.

This complements (does not replace) the characterization oracle in
tests/characterization/, which is the real behavior-preservation gate.
"""

import pytest

from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import InMemoryStore
from controller.runtime.clock import ManualClock
from controller.runtime.state import WorkCycleState
from controller.runtime.modes.base import ControlMode
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

    def setup(self):
        self.calls.append("setup")

    def setup_safety(self, ptemp):
        self.calls.append("setup_safety")
        return "Active"

    def on_tick(self, now, ptemp, current_output_status):
        self.calls.append("on_tick")

    def check_safety(self, now, ptemp):
        self.calls.append("check_safety")

    def status_fragment(self):
        self.calls.append("status_fragment")
        return {}

    def should_exit(self, now, ptemp):
        self.calls.append("should_exit")
        # Bound the loop to exactly one iteration.
        return True

    def teardown(self, ptemp):
        self.calls.append("teardown")


def _make_ctx():
    settings = base_settings()
    control_data = base_control(mode="Recording")
    pellet_db = base_pellet_db()
    probes = FakeProbes().script([120])
    store = InMemoryStore(control=control_data, settings=settings, pellet_db=pellet_db)
    grill = FakeGrillPlatform(outputs=tuple(settings["platform"]["outputs"]))
    notifier = FakeNotifier()
    ctx = ControllerContext(
        devices=Devices(grill_platform=grill, probe_complex=probes, dist_device=FakeDistance()),
        store=store,
        notifications=notifier,
        clock=ManualClock(),
    )
    return ctx


def test_control_mode_hook_order_one_bounded_tick():
    ctx = _make_ctx()
    # ControlMode.run() reads ctx.clock.now() exactly once pre-loop (for
    # start_time/display_toggle_time/etc.) before entering `while status ==
    # 'Active':`. Advance the clock right after that first read so the loop's
    # first `now = ctx.clock.now()` is > 0.5s past display_toggle_time,
    # firing the status-publish gate (and status_fragment()) within this
    # single bounded iteration.
    real_now = ctx.clock.now
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        if calls["n"] == 1:
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
        return real_now() if clock_reads == 1 else real_now() + 0.6

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
    assert clock_reads_at_on_tick == [3]


def test_loop_identity_refresh_rotates_with_supplied_loop_time():
    mode = _make_mode()
    mode.control["cook_id"] = "old-session"
    refreshed = mode.control.copy()
    refreshed["cook_id"] = "new-session"
    rotations = []
    mode.on_cook_identity_rotated = lambda previous, current, now: rotations.append(
        (previous, current, now)
    )

    assert mode._refresh_cook_identity(refreshed, now=12.5) is refreshed
    assert rotations == [("old-session", "new-session", 12.5)]




def test_status_publishes_duty_fields():
    ctx = _make_ctx()
    real_now = ctx.clock.now
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        return real_now() if calls["n"] == 1 else real_now() + 0.6

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
        return real_now() if calls["n"] == 1 else real_now() + 0.6

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
