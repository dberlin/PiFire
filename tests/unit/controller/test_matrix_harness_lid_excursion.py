"""Pins the `lid_open_225` scenario to an excursion Hold's lid detector fires on.

`hold.py:238-242` arms the automatic detector only once the chamber has fallen
`LidOpenThreshold` percent below setpoint, so an excursion shallower than that
is not a lid event at all -- a scenario that stays above it is simulating a
state production cannot reach. These assertions read the trigger from settings
and require the scenario to cross it, on several seeds, driving the real
`GrillSim` through the real `run_scenario`. `pid_sp` stands in for the matrix's
controllers because the excursion is a property of the plant and the pause, not
of the controller, and it runs in a tenth of a second where MPC takes ninety.

Depth is pinned alongside width, because the two fail in opposite directions.
A plant that leaks no heat leaves the chamber near setpoint; a pause modelled as
lasting the whole lid window rather than `LidOpenPauseTime` digs the trough
*deeper* than required and would satisfy a depth-only assertion while
misrepresenting how long production surrenders control. Each has its own
negative control below.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix
from common.defaults import default_settings
from controller.grill_sim import GrillSim

SCENARIO = controller_matrix.SCENARIOS["lid_open_225"]
SETPOINT = SCENARIO.setpoints[0][1]
LID_WINDOW_S = SCENARIO.lid_open[0][1]
THRESHOLD_PCT = default_settings()["cycle_data"]["LidOpenThreshold"]
# hold.py:241's condition, spelled the same way.
TRIGGER_F = SETPOINT * ((100 - THRESHOLD_PCT) / 100)
# Every seed, because the recovery claim below is a per-seed pairing and the
# spread between seeds is what makes the paired form necessary: the whole
# excursion can land ~70 s earlier on one draw than another, so a three-seed
# sample silently excludes that regime. Twenty pid_sp runs cost under six
# seconds.
SEEDS = tuple(range(10))


def _lid_results(monkeypatch, h_lid=None, pause_s=None):
    if h_lid is not None:
        real_init = GrillSim.__init__

        def _fixed_h_lid(self, **kwargs):
            real_init(self, **{**kwargs, "h_lid": h_lid})

        monkeypatch.setattr(GrillSim, "__init__", _fixed_h_lid)
    cycle_config = {} if pause_s is None else {"LidOpenPauseTime": pause_s}
    return [controller_matrix.run_scenario("pid_sp", SCENARIO, seed, cycle_config=cycle_config) for seed in SEEDS]


def test_lid_open_scenario_crosses_the_lid_open_threshold(monkeypatch):
    mins = [r["lid_min_temp_f"] for r in _lid_results(monkeypatch)]

    assert all(t < TRIGGER_F for t in mins), (
        f"lid_open_225 must produce an excursion the lid detector would fire on "
        f"(below {TRIGGER_F:.2f} F at a {SETPOINT:.0f} F setpoint); coldest readings were {mins}"
    )


def test_the_chamber_returns_to_band_after_the_pause_expires(monkeypatch):
    """Width of the cold excursion after its trough.

    Probe/transport lag can briefly cross the band before the trough, so the
    metric starts recovery only after that coldest point. The lower bound is
    the pause itself, which control cannot outrun. The only upper bound is
    `is not None`: `_recovery_s` reports `None` when the chamber never comes
    back inside the band before the run ends, so the assertion says recovery
    happens at all and deliberately leaves the discrimination between pause
    models to the paired negative control below.
    """
    recoveries = [r["lid_recovery_s"] for r in _lid_results(monkeypatch)]

    pause_s = default_settings()["cycle_data"]["LidOpenPauseTime"]
    assert all(r is not None and r > pause_s for r in recoveries), (
        f"recovery must happen, and no sooner than the {pause_s} s pause; got {recoveries}"
    )


def test_a_lidless_plant_misses_the_threshold(monkeypatch):
    """Negative control for depth: the pre-fix plant, where an open lid leaks
    no heat."""
    # Taken before the lidless plant is patched in: _lid_results installs its
    # h_lid override on GrillSim for the rest of the test, so a leaking baseline
    # measured after it would silently be a second lidless run.
    leaking = [r["lid_min_temp_f"] for r in _lid_results(monkeypatch)]
    mins = [r["lid_min_temp_f"] for r in _lid_results(monkeypatch, h_lid=0.0)]

    assert all(t > TRIGGER_F for t in mins), (
        f"the no-heat-leak plant was expected to miss the {TRIGGER_F:.2f} F trigger, "
        f"so the assertion above measures the lid model; coldest readings were {mins}"
    )
    # And its trough is shallower than the leaking plant's, which is what makes
    # the depth attributable to the lid model rather than to the actuator pause.
    # Stated as a comparison rather than against a fixed margin: the pause alone
    # digs a real trough now that a framed scheduler stops the auger outright
    # instead of cycling it at a minimum, so any absolute bound would be
    # measuring the actuation rather than the lid.
    assert min(mins) > min(leaking), (
        f"a plant that leaks no heat must not fall as far as one that does; lidless {mins} against leaking {leaking}"
    )


def test_a_pause_lasting_the_whole_lid_window_recovers_more_slowly(monkeypatch):
    """Negative control for width: a pause held for as long as the lid is open,
    which is twice what `LidOpenPauseTime` grants it. The trough it digs still
    clears the detector trigger, so only recovery time catches it."""
    # Stated as a per-seed pairing rather than against a fixed bound. Seed to
    # seed the excursion shifts by tens of seconds, enough that the two models'
    # recovery times overlap when pooled across seeds -- a fixed bound would be
    # measuring which seeds were sampled. Within a seed the plant noise is
    # shared, so the difference is the pause model and nothing else.
    on_timer = [r["lid_recovery_s"] for r in _lid_results(monkeypatch)]
    results = _lid_results(monkeypatch, pause_s=LID_WINDOW_S)
    whole_window = [r["lid_recovery_s"] for r in results]

    assert all(r is not None for r in whole_window), (
        f"a whole-window pause must still recover within the run, or the comparison "
        f"below is vacuous; got {whole_window}"
    )
    assert all(slow > quick for quick, slow in zip(on_timer, whole_window, strict=True)), (
        f"a whole-window pause was expected to surrender control for longer on every seed, "
        f"so the assertion above measures the pause length; on the timer {on_timer} "
        f"against the whole window {whole_window}"
    )
    # The depth-only assertion cannot tell this model from the correct one.
    assert all(r["lid_min_temp_f"] < TRIGGER_F for r in results)


def test_recovery_starts_after_the_cold_excursion_trough():
    """A transient in-band reading before transport lag peaks is not recovery."""
    err_from_lid = np.asarray([0.0, -6.0, 0.0, -7.0, -12.0, -4.0])

    assert controller_matrix._recovery_s(err_from_lid) == 5
