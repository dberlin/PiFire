"""Pins the net's training sampler to lid episodes production can actually reach.

`sample_mpc.py::_episode_span` is the *training* side of this plan's three lid
harnesses: whatever it teaches, the residual net believes. So an episode whose
lid opens without the chamber losing heat does not merely mis-measure, it trains
the policy on a regime that does not exist -- the actuators surrendered at a
chamber that never got cold, and never the state production actually hands the
controller back, which is a cold chamber at full authority.

Two failures make the episode unreachable, and they fail in opposite directions.
A plant that leaks no heat leaves the chamber near setpoint. A pause held for the
whole opening, rather than `LidOpenPauseTime`, digs the trough *deeper* than
production and would satisfy any depth assertion while misrepresenting how long
control was surrendered. Depth and recovery are therefore pinned together, each
against its own negative control, because either alone is satisfied by the
failure the other catches.

Everything below drives the real `_episode_span` -- the real MPC, the real
`GrillSim`, the real `_lid_windows`, the real `plant.step` call -- and reads the
sequence the plant was actually driven with off a recording subclass. Only the
draw that decides *whether* an episode gets a lid event is stubbed, so the
episode is deterministic; `test_the_drawn_lid_events_match_the_window_under_test`
keeps that stub honest against the real draw. Driving the harness end to end
this way is affordable, so nothing here needs to reconstruct the schedule
instead.
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.sample_mpc as sample_mpc
from common.defaults import default_settings
from controller.grill_sim import GrillSim

SETPOINT_C = 110.0
SETPOINT_F = SETPOINT_C * 1.8 + 32.0
THRESHOLD_PCT = default_settings()["cycle_data"]["LidOpenThreshold"]
# The sampler works in Celsius; `LidOpenThreshold` is a percentage of the
# setpoint in whatever units the install displays, so a Fahrenheit install needs
# the deeper fall (19.2 C, against 16.5 C read off the Celsius number). The
# stricter of the two is required here, so no assertion below can pass on the
# more forgiving convention.
TRIGGER_C = ((SETPOINT_F * (100 - THRESHOLD_PCT) / 100.0) - 32.0) / 1.8

EPISODE_MINUTES = 30
# The exploration dither generation actually runs at (`sample_mpc.main`'s
# default). It is a standard deviation on a 0-1 duty, so a value near 1 does not
# widen the visited region -- it replaces the controller, driving half the steps
# to a clipped 0 or 1 at random. An episode driven that way swings tens of
# degrees on its own, which is not a lid excursion and cannot be measured as one.
DITHER = 0.08
CYCLE_S = sample_mpc.CYCLE["HoldCycleTime"]
LID_START_STEP = 40
LID_WINDOW_STEPS = 5
LID_EVENT = (LID_START_STEP, LID_START_STEP + LID_WINDOW_STEPS)
# Seconds from the lid opening until the chamber is back inside the 5 F band the
# other two harnesses use. Set strictly between the modelled pause's recovery
# and the slower recovery a pause held for the whole opening produces, so it
# separates the two without being sensitive to run-to-run noise in either.
MAX_RECOVERY_S = 180
BAND_C = 5.0 / 1.8
SEEDS = (0, 1)


class _RecordingGrillSim(GrillSim):
    """The real plant, plus the commands the sampler drove it with.

    Physics is untouched, so the thermal result and the actuator sequence below
    are read off the same run.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.true_temps = []
        self.lid_opens = []
        self.fan_fracs = []

    def step(self, auger_on, fan_frac, lid_open=False):
        result = super().step(auger_on=auger_on, fan_frac=fan_frac, lid_open=lid_open)
        self.true_temps.append(self.true_Tc)
        self.lid_opens.append(bool(lid_open))
        self.fan_fracs.append(float(fan_frac))
        return result


def _drive_episode(seed, *, h_lid=None, pause_steps=None):
    """Run one real `_episode_span` over a fixed lid event and return its plant."""
    plants = []
    real_sim, real_draw, real_pause = sample_mpc.GrillSim, sample_mpc._draw_lid_events, sample_mpc.LID_PAUSE_STEPS

    def _recording(**kwargs):
        if h_lid is not None:
            kwargs["h_lid"] = h_lid
        plant = _RecordingGrillSim(**kwargs)
        plants.append(plant)
        return plant

    sample_mpc.GrillSim = _recording
    sample_mpc._draw_lid_events = lambda rng, nsteps: [LID_EVENT]
    if pause_steps is not None:
        sample_mpc.LID_PAUSE_STEPS = pause_steps
    try:
        # A single setpoint for the whole episode: the sampler's segment schedule
        # draws from [sp_lo, sp_hi], so collapsing the range removes the only
        # source of temperature variation that is not the lid.
        sample_mpc._episode_span((seed, EPISODE_MINUTES, DITHER, SETPOINT_C, SETPOINT_C, False))
    finally:
        sample_mpc.GrillSim, sample_mpc._draw_lid_events = real_sim, real_draw
        sample_mpc.LID_PAUSE_STEPS = real_pause
    return plants[-1]


@pytest.fixture(scope="module")
def episodes():
    """Every configuration this file needs, each driven once."""
    return {
        "nominal": [_drive_episode(seed) for seed in SEEDS],
        "lidless": [_drive_episode(seed, h_lid=0.0) for seed in SEEDS],
        "conflated": [_drive_episode(seed, pause_steps=LID_WINDOW_STEPS) for seed in SEEDS],
    }


def _coldest_c(plant):
    opened = LID_START_STEP * CYCLE_S
    watched = opened + (LID_WINDOW_STEPS * CYCLE_S) + MAX_RECOVERY_S
    return float(np.min(plant.true_temps[opened:watched]))


def _recovery_s(plant):
    """Seconds from lid opening until the chamber recovers from the cold-side
    excursion into the setpoint band. None if the episode ends first."""
    temps = np.asarray(plant.true_temps[LID_START_STEP * CYCLE_S :])
    below = np.flatnonzero(temps < SETPOINT_C - BAND_C)
    if not len(below):
        return 0
    back = np.flatnonzero(np.abs(temps[below[0] :] - SETPOINT_C) <= BAND_C)
    return int(below[0] + back[0]) if len(back) else None


# ----- the split itself ------------------------------------------------------


def test_the_actuator_pause_is_the_shortest_whole_cycle_covering_lidopenpausetime():
    """The rounding this file's grid forces. Hold arms the pause as a timer but
    only re-decides the ratio on a cycle boundary, so the pause survives every
    boundary it is still latched at and ends at the first one after it expires.
    Rounding down would return control a whole cycle before production does."""
    held_s = sample_mpc.LID_PAUSE_STEPS * CYCLE_S

    assert sample_mpc.LID_PAUSE_S <= held_s < sample_mpc.LID_PAUSE_S + CYCLE_S, (
        f"{sample_mpc.LID_PAUSE_STEPS} steps holds the actuators {held_s} s, which is not the "
        f"shortest whole cycle covering a {sample_mpc.LID_PAUSE_S} s pause on a {CYCLE_S} s grid"
    )


def test_the_actuator_pause_is_shorter_than_the_lid_is_open():
    """If these were equal the split would be vacuous and every assertion in
    this file would hold for the wrong reason."""
    assert sample_mpc.LID_PAUSE_STEPS < LID_WINDOW_STEPS


def test_the_actuators_resume_while_the_lid_is_still_open():
    """The interval that does not exist while one flag drives both windows."""
    at_resume = sample_mpc._lid_windows(LID_START_STEP + sample_mpc.LID_PAUSE_STEPS, [LID_EVENT])

    assert at_resume == (True, False), (
        f"the lid must still be open once the actuator pause expires; got (lid, lid_paused)={at_resume}"
    )


def test_both_windows_open_together_and_the_lid_closes_last():
    assert sample_mpc._lid_windows(LID_START_STEP, [LID_EVENT]) == (True, True)
    assert sample_mpc._lid_windows(LID_START_STEP - 1, [LID_EVENT]) == (False, False)
    assert sample_mpc._lid_windows(LID_START_STEP + LID_WINDOW_STEPS, [LID_EVENT]) == (False, False)


def test_the_drawn_lid_events_match_the_window_under_test():
    """Keeps the stubbed draw representative: the real one produces openings of
    the length the assertions below are calibrated on, and long enough that the
    actuators always resume before the lid shuts."""
    rng = np.random.default_rng(0)
    drawn = [e for _ in range(200) for e in sample_mpc._draw_lid_events(rng, 288)]

    assert drawn, "the draw never produced a lid event"
    lengths = {hi - lo for lo, hi in drawn}
    assert lengths <= {2, 3, 4, 5, 6}, lengths
    assert LID_WINDOW_STEPS in lengths


# ----- the excursion, driven through the real sampler ------------------------


def test_the_sampler_teaches_an_excursion_the_lid_detector_would_fire_on(episodes):
    mins = [_coldest_c(p) for p in episodes["nominal"]]

    assert all(t < TRIGGER_C for t in mins), (
        f"a sampled lid event must cool the chamber past the detector trigger "
        f"({TRIGGER_C:.2f} C at a {SETPOINT_C:.0f} C setpoint); coldest were {mins}"
    )


def test_a_lidless_plant_misses_the_threshold(episodes):
    """Negative control for depth, on the one parameter that carries the lid's
    heat loss: `h_lid=0.0` removes the `h_amb += self.h_lid` term and nothing
    else, so both arms still take the same `plant.step(..., lid_open=lid)` call
    path. It is conservative -- the fan is still cut for the pause, and fan-off
    *lowers* `h_amb`, so this arm loses marginally more heat than the pre-fix
    sampler did and the trigger it has to miss is that much harder to miss."""
    mins = [_coldest_c(p) for p in episodes["lidless"]]

    assert all(t > TRIGGER_C for t in mins), (
        f"the no-heat-leak plant was expected to miss the {TRIGGER_C:.2f} C trigger, "
        f"so the assertion above measures the lid model; coldest were {mins}"
    )
    # And it barely moves the chamber at all, which is the defect itself.
    assert all(t > SETPOINT_C - 15.0 for t in mins), mins


def test_the_chamber_recovers_on_the_pause_timer_not_the_lid_window(episodes):
    """Width of the same excursion. The chamber cannot re-enter the band while
    the auger is pinned, so recovery outlasts the pause; it comes back well
    inside `MAX_RECOVERY_S` because Hold hands control back on the timer rather
    than when the lid shuts."""
    recoveries = [_recovery_s(p) for p in episodes["nominal"]]

    assert all(r is not None and sample_mpc.LID_PAUSE_S < r < MAX_RECOVERY_S for r in recoveries), (
        f"recovery must fall between the {sample_mpc.LID_PAUSE_S} s pause and {MAX_RECOVERY_S} s; got {recoveries}"
    )


def test_a_pause_lasting_the_whole_lid_window_recovers_too_slowly(episodes):
    """Negative control for width: the actuators following the physical window
    instead of `LidOpenPauseTime`, which is the conflation the split exists to
    prevent. The trough it digs is *deeper*, so it clears the detector trigger
    and only the recovery bound catches it."""
    recoveries = [_recovery_s(p) for p in episodes["conflated"]]

    assert all(r is not None and r >= MAX_RECOVERY_S for r in recoveries), (
        f"a whole-window pause was expected to blow through the {MAX_RECOVERY_S} s recovery bound, "
        f"so the assertion above measures the pause length; got {recoveries}"
    )
    # The depth assertion cannot tell this model from the correct one.
    assert all(_coldest_c(p) < TRIGGER_C for p in episodes["conflated"])


# ----- what the sampler actually drove the plant with ------------------------


def test_the_sampler_drives_the_two_windows_at_their_own_lengths(episodes):
    """Read off the plant rather than reconstructed, so a `_episode_span` that
    stopped opening the lid -- or started cutting the fan for the whole opening
    -- fails here regardless of what the thermal bounds admit."""
    plant = episodes["nominal"][0]
    opened, shut = LID_START_STEP * CYCLE_S, (LID_START_STEP + LID_WINDOW_STEPS) * CYCLE_S
    resumed = (LID_START_STEP + sample_mpc.LID_PAUSE_STEPS) * CYCLE_S

    assert all(plant.lid_opens[opened:shut]), "the chamber must stand open for the whole physical window"
    assert not any(plant.lid_opens[:opened]), "the chamber must be shut before the lid opens"
    assert not any(plant.lid_opens[shut:]), "the chamber must be shut once the lid closes"
    assert not any(plant.fan_fracs[opened:resumed]), "the fan must be off for the actuator pause"
    assert all(plant.fan_fracs[resumed:shut]), (
        "the fan must be running again while the lid is still open -- the third phase the split creates"
    )
    # Pinned to the nominal LidOpenPauseTime directly, independent of how the
    # grid rounds it to steps -- the shortest-whole-cycle test above pins the
    # rounding, this pins the actuators to what that rounding is supposed to cover.
    assert not any(plant.fan_fracs[opened : opened + int(sample_mpc.LID_PAUSE_S)]), (
        "the actuators must stay surrendered for the whole LidOpenPauseTime"
    )
