"""Pins the net-versus-NLP replay's lid window to one Hold's detector fires on.

`net_vs_nlp_replay.py` reproduces `hold.py`'s actuator shape in detail, but for
most of this plan it never opened the lid on the plant: `plant.step` was called
without `lid_open`, and cutting the fan *lowers* `h_amb`
(`grill_sim.py:112`), so the chamber trapped heat and warmed across a window
labelled "lid open". The replay's lid-window figures gate a shipping decision
(the plan's Task 14 Step 4), so a window production cannot reach is not a
cosmetic problem there.

A full replay is a 3 h NLP run at ~88 s per seed, far too slow for the suite.
These tests therefore split the two things that were wrong: the window
arithmetic is checked against the harness's own `_lid_windows`, and the thermal
excursion is checked by driving the real `GrillSim` through the schedule that
helper produces. Each has a negative control, because an assertion that 225 F
is above 191 F would pass with no lid model at all.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.net_vs_nlp_replay as replay_mod
from common.defaults import default_settings
from controller.grill_sim import GrillSim

LID_OPEN_AT = 2 * 3600
LID_OPEN_FOR = 120
SETPOINT_F = 225.0
U_MIN = replay_mod.CYCLE_DATA["u_min"]
THRESHOLD_PCT = default_settings()["cycle_data"]["LidOpenThreshold"]
# hold.py:241's condition, spelled the same way.
TRIGGER_F = SETPOINT_F * ((100 - THRESHOLD_PCT) / 100)
SEEDS = (0, 1, 2)


def test_the_actuator_pause_is_shorter_than_the_lid_is_open():
    """If these were equal the split would be vacuous and every assertion below
    would hold for the wrong reason."""
    assert replay_mod.LID_PAUSE_S < LID_OPEN_FOR


def test_the_actuators_resume_while_the_lid_is_still_open():
    """The interval the harness could not produce while one flag drove both."""
    at_resume = replay_mod._lid_windows(LID_OPEN_AT + replay_mod.LID_PAUSE_S, LID_OPEN_AT, LID_OPEN_FOR)

    assert at_resume == (True, False), (
        f"the lid must still be open once the actuator pause expires; got (lid, lid_paused)={at_resume}"
    )


def test_both_windows_open_together_and_the_lid_closes_last():
    assert replay_mod._lid_windows(LID_OPEN_AT, LID_OPEN_AT, LID_OPEN_FOR) == (True, True)
    assert replay_mod._lid_windows(LID_OPEN_AT - 1, LID_OPEN_AT, LID_OPEN_FOR) == (False, False)
    assert replay_mod._lid_windows(LID_OPEN_AT + LID_OPEN_FOR, LID_OPEN_AT, LID_OPEN_FOR) == (False, False)


def _coldest_reading_f(seed, h_lid=None):
    """Drive the plant through the replay's lid schedule and return the coldest
    chamber reading over the window and the recovery that follows it."""
    kwargs = {"seed": seed} if h_lid is None else {"seed": seed, "h_lid": h_lid}
    plant = GrillSim(**kwargs)
    set_c = (SETPOINT_F - 32.0) / 1.8

    def _hold_duty():
        return min(max(U_MIN + 0.02 * (set_c - plant.true_Tc), U_MIN), replay_mod.CYCLE_DATA["u_max"])

    for _ in range(LID_OPEN_AT):
        plant.step(auger_on=_hold_duty(), fan_frac=1.0)

    readings = []
    for t in range(LID_OPEN_AT, LID_OPEN_AT + LID_OPEN_FOR + 300):
        lid, lid_paused = replay_mod._lid_windows(t, LID_OPEN_AT, LID_OPEN_FOR)
        duty = U_MIN if lid_paused else _hold_duty()
        readings.append(plant.step(auger_on=duty, fan_frac=0.0 if lid_paused else 1.0, lid_open=lid))
    return min(readings) * 1.8 + 32.0


def test_the_lid_window_crosses_the_lid_open_threshold():
    mins = [_coldest_reading_f(seed) for seed in SEEDS]

    assert all(t < TRIGGER_F for t in mins), (
        f"the replay's lid window must produce an excursion the lid detector would "
        f"fire on (below {TRIGGER_F:.2f} F at a {SETPOINT_F:.0f} F setpoint); "
        f"coldest readings were {mins}"
    )


def test_a_lidless_plant_misses_the_threshold():
    """Negative control: the plant as the replay drove it for most of this plan,
    where an open lid leaks no heat."""
    mins = [_coldest_reading_f(seed, h_lid=0.0) for seed in SEEDS]

    assert all(t > TRIGGER_F for t in mins), (
        f"the no-heat-leak plant was expected to miss the {TRIGGER_F:.2f} F trigger, "
        f"so the assertion above measures the lid model; coldest readings were {mins}"
    )
    # And it barely moves the chamber at all, which is the defect itself.
    assert all(t > SETPOINT_F - 10.0 for t in mins), mins


@pytest.mark.slow
def test_the_real_replay_reports_a_lid_excursion():
    """Drives the harness itself rather than a reconstruction of its schedule.

    The fast tests above would all keep passing if `replay()` stopped passing
    `lid_open` to the plant, because they call the plant themselves. This one
    would not, which is the whole reason it is worth 90 seconds.
    """
    row = replay_mod.replay(
        seed=0,
        applied_q_split_expected=replay_mod._split_is_live(replay_mod.CYCLE_DATA),
    )

    assert row["lid_min_temp_f"] < TRIGGER_F, (
        f"the replay's own lid window must cross {TRIGGER_F:.2f} F; lid_min_temp_f was {row['lid_min_temp_f']:.2f}"
    )
