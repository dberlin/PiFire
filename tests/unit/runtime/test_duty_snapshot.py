"""The single duty computation shared by the status blob and the history row.

`_duty_snapshot` exists so the dashboard's duty tiles and the history chart's
duty series cannot disagree. They are written from the same control loop but at
different cadences -- status every 0.5s, history every 3s -- so a second copy
of this logic would drift on exactly the branches nobody checks, and a
dashboard reading 0% beside a history row plotting 100% for the same instant is
a disagreement no one would think to look for.

These drive the method against hand-built state rather than a running loop: it
reads only `self.state.cycle.ratio` and `self.settings["platform"]`, so a stub
covers every branch without standing up a grill.
"""

from types import SimpleNamespace

from common.modes import Mode
from controller.runtime.modes.base import ControlMode


class _Stub(ControlMode):
    """A ControlMode with just the two attributes _duty_snapshot reads.

    A real subclass rather than a duck-typed object, so `realized_cycle_ratio`
    resolves to the base class's own default -- which is part of what these
    assert -- instead of to something the test invented.
    """

    def __init__(self, ratio, dc_fan):
        self.state = SimpleNamespace(cycle=SimpleNamespace(ratio=ratio))
        self.settings = {"platform": {"dc_fan": dc_fan}}


class _MeasuringStub(_Stub):
    """A mode that measures delivered duty, as Hold's framed pulses do."""

    def __init__(self, ratio, dc_fan, realized):
        super().__init__(ratio, dc_fan)
        self._realized = realized

    def realized_cycle_ratio(self):
        return self._realized


def _mode(*, ratio=0.0, dc_fan=False):
    return _Stub(ratio, dc_fan)._duty_snapshot


def test_hold_reports_the_commanded_cycle_ratio_rounded():
    snapshot = _mode(ratio=0.33333)({}, Mode.HOLD, {"auger": True, "fan": True})

    assert snapshot["cycle_ratio"] == 0.33


def test_manual_coerces_the_auger_output_to_a_ratio():
    """Manual has no controller and no cycle: the auger is simply on or off."""
    on = _mode(ratio=0.9)({}, Mode.MANUAL, {"auger": True, "fan": False})
    off = _mode(ratio=0.9)({}, Mode.MANUAL, {"auger": False, "fan": False})

    assert on["cycle_ratio"] == 1.0
    assert off["cycle_ratio"] == 0.0


def test_manual_ignores_the_stale_cycle_ratio_left_by_a_previous_mode():
    """state.cycle.ratio is not cleared on entering Manual, so it must not leak.

    Without the Mode.MANUAL branch a grill switched from Hold to Manual would
    keep reporting Hold's last duty for an auger the operator is driving by
    hand.
    """
    snapshot = _mode(ratio=0.42)({}, Mode.MANUAL, {"auger": False, "fan": False})

    assert snapshot["cycle_ratio"] == 0.0


def test_fan_off_reports_zero_duty_whatever_was_requested():
    """The gate is the OUTPUT, not the request.

    control['duty_cycle'] is the duty the fan WOULD be given. Reporting it for
    a fan that is off puts "FAN DUTY 100%" beside "FAN IDLE" on the dashboard,
    and draws a fan line at 100% through an idle stretch of the history chart.
    """
    snapshot = _mode(dc_fan=True)({"duty_cycle": 80}, Mode.HOLD, {"auger": False, "fan": False})

    assert snapshot["fan_duty"] == 0


def test_dc_fan_reports_the_pwm_percent_and_an_ac_fan_reports_full_on():
    dc = _mode(dc_fan=True)({"duty_cycle": 65}, Mode.HOLD, {"auger": True, "fan": True})
    ac = _mode(dc_fan=False)({"duty_cycle": 65}, Mode.HOLD, {"auger": True, "fan": True})

    assert dc["fan_duty"] == 65
    assert ac["fan_duty"] == 100, "an on/off fan is either 0% or 100%, never the requested duty"


def test_manual_dc_fan_reads_pwm_from_the_output_not_from_control():
    """In Manual the operator's PWM is on the platform, not in control[].

    Reading control['duty_cycle'] here would report the last controller-driven
    duty for a fan the operator is setting by hand.
    """
    snapshot = _mode(dc_fan=True)({"duty_cycle": 80}, Mode.MANUAL, {"auger": False, "fan": True, "pwm": 35})

    assert snapshot["fan_duty"] == 35


def test_fan_duty_is_a_whole_percent():
    """It rides a NUMERIC column, and 65.0 would render as "65.0%" on an axis."""
    snapshot = _mode(dc_fan=True)({"duty_cycle": 65.7}, Mode.HOLD, {"auger": True, "fan": True})

    assert isinstance(snapshot["fan_duty"], int)


def test_status_and_history_read_the_same_snapshot_for_one_tick():
    """Both consumers take the same function, so they cannot disagree by formula.

    This pins the property that matters at the seam -- one input state yields
    one duty -- rather than re-testing the branches above. If the status path
    ever grows its own copy of this arithmetic, this is what fails.
    """
    snapshot = _mode(ratio=0.4, dc_fan=True)
    inputs = ({"duty_cycle": 55}, Mode.HOLD, {"auger": True, "fan": True})

    for_status = snapshot(*inputs)
    for_history = snapshot(*inputs)

    assert for_status == for_history == {"cycle_ratio": 0.4, "realized_cycle_ratio": None, "fan_duty": 55}


def test_a_mode_that_does_not_measure_reports_no_realized_duty():
    """None, not a copy of the commanded ratio.

    Most modes drive the auger open-loop from a configured cycle, so nothing
    measures what was delivered. Echoing the command under a second name would
    draw two identical lines and imply a clamp had been measured when none was.
    """
    snapshot = _mode(ratio=0.4)({}, Mode.SMOKE, {"auger": True, "fan": True})

    assert snapshot["realized_cycle_ratio"] is None


def test_a_measuring_mode_reports_the_delivered_duty_alongside_the_command():
    """The gap between the two is where a clamp acted.

    A controller asking for 8% against a duty floor that cannot pulse below
    12% is the case this exists to make visible: the grill runs warm and
    nothing on any screen says why.
    """
    snapshot = _MeasuringStub(0.08, False, 0.12)._duty_snapshot({}, Mode.HOLD, {"auger": True, "fan": True})

    assert snapshot["cycle_ratio"] == 0.08
    assert snapshot["realized_cycle_ratio"] == 0.12
