"""MAKGrillSim must reproduce the cook it was identified from.

Replaying the logged firing-rate demand through the allocator and the Hold
auger cycle is the only check that says the parameters describe THAT grill
rather than merely being plausible numbers.
"""

import csv
import os

import numpy as np
import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.mpc_allocator import allocate

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv")

# The grill's physical auger ceiling during the logged cook.
U_MAX = 0.9
HOLD_CYCLE_S = 25.0
SETPOINT_C = (450 - 32) * 5 / 9


def _cook():
    with open(FIXTURE) as f:
        rows = list(csv.DictReader(f))
    t0 = float(rows[0]["time_s"])
    t = np.array([float(r["time_s"]) - t0 for r in rows])
    temp = np.array([float(r["temp_c"]) for r in rows])
    normalized_load = np.array([float(r["Q"]) / 100.0 for r in rows])
    return t, temp, normalized_load


def _replay(sim, t, normalized_load):
    """Drive `sim` for one second per step with normalized cook commands."""
    grid = np.arange(0.0, t[-1] + 1.0, 1.0)
    held = normalized_load[np.clip(np.searchsorted(t, grid, side="right") - 1, 0, len(normalized_load) - 1)]
    out = np.empty_like(grid)
    for i, now in enumerate(grid):
        out[i] = sim.true_Tc
        if i == len(grid) - 1:
            break
        allocation = allocate(
            held[i],
            u_max=U_MAX,
            fan_min_pct=40.0,
            fan_max_pct=100.0,
            enable_fan=False,
        )
        ratio = allocation.auger_duty
        # Hold runs the auger ON for ratio*cycle seconds of each cycle window.
        sim.step((now % HOLD_CYCLE_S) < ratio * HOLD_CYCLE_S, 1.0)
    return grid, out


def _first_crossing(grid, temps, level):
    for g, v in zip(grid, temps):
        if v >= level:
            return float(g)
    return None


@pytest.fixture
def replayed():
    t, temp, Q = _cook()
    sim = MAKGrillSim(seed=0, T0=float(temp[0]), fixed_fan=1.0)
    grid, out = _replay(sim, t, Q)
    return t, temp, grid, out


def test_the_replayed_trajectory_tracks_the_logged_one(replayed):
    t, temp, grid, out = replayed
    err = np.interp(t, grid, out) - temp
    assert float(np.sqrt(np.mean(err**2))) < 5.0
    assert float(np.max(np.abs(err))) < 15.0


def test_it_reaches_the_setpoint_when_the_real_grill_did(replayed):
    t, temp, grid, out = replayed
    measured = _first_crossing(t, temp, SETPOINT_C)
    simulated = _first_crossing(grid, out, SETPOINT_C)
    assert measured == pytest.approx(871.0, abs=1.0)  # pins the fixture itself
    assert simulated == pytest.approx(measured, abs=60.0)


def test_it_overshoots_to_the_temperature_the_real_grill_reached(replayed):
    _t, temp, _grid, out = replayed
    assert out.max() == pytest.approx(temp.max(), abs=5.0)
    # The overshoot is the point: ~70 F past a 450 F setpoint.
    assert out.max() - SETPOINT_C > 30.0


def test_the_base_plant_does_not_reproduce_this_cook():
    """The negative control. If stock GrillSim already matched, MAKGrillSim
    would be carrying no information."""
    t, temp, Q = _cook()
    base = GrillSim(seed=0, fixed_fan=1.0)
    base.T_f = base.T_c = base.T_meas = float(temp[0])
    grid, out = _replay(base, t, Q)
    err = np.interp(t, grid, out) - temp
    assert float(np.sqrt(np.mean(err**2))) > 20.0


def test_it_is_far_slower_than_the_base_plant():
    """The defect this grill exposed was braking distance, which is set by the
    chamber time constant."""
    assert MAKGrillSim().C_c > 5 * GrillSim().C_c
    assert len(MAKGrillSim().transit) > 4 * len(GrillSim().transit)


def test_constructor_arguments_still_reach_the_base_plant():
    sim = MAKGrillSim(seed=3, probe_tau=2.0, fixed_fan=0.5, T0=95.0)
    assert sim.probe_tau == 2.0
    assert sim.fixed_fan == 0.5
    assert sim.true_Tc == 95.0
    # An explicit override beats the MAK default rather than being ignored.
    assert len(MAKGrillSim(deadtime=7).transit) == 7
