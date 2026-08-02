"""Pins the `lid_open_225` scenario to an excursion Hold's lid detector fires on.

The matrix harness once modelled a lid-open pause purely on the actuator side.
`GrillSim` scales both `h_fc` and `h_amb` with the fan, so cutting the fan
*trapped* heat: the chamber warmed 224.9 -> 227.3 F across the "lid open" event.
The scenario simulated a state production cannot reach, and every conclusion
drawn from it was a conclusion about a grill whose lid never opened.

`hold.py:238-242` arms the automatic detector only once the chamber has fallen
`LidOpenThreshold` percent below setpoint, so an excursion shallower than that
is not a lid event at all. These assertions read the trigger from settings and
require the scenario to cross it, on several seeds, driving the real `GrillSim`
through the real `run_scenario`. `pid_sp` stands in for the matrix's controllers
because the excursion is a property of the plant and the pause, not of the
controller, and it runs in a tenth of a second where MPC takes ninety.

The negative control restores the pre-fix plant (`h_lid = 0`, i.e. an open lid
with no heat leak) and shows it misses the trigger by a wide margin -- so the
assertion is measuring the lid model rather than agreeing with itself.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import docs.superpowers.experiments.controller_matrix as controller_matrix
from common.defaults import default_settings
from controller.grill_sim import GrillSim

SCENARIO = controller_matrix.SCENARIOS["lid_open_225"]
SETPOINT = SCENARIO.setpoints[0][1]
THRESHOLD_PCT = default_settings()["cycle_data"]["LidOpenThreshold"]
# hold.py:241's condition, spelled the same way.
TRIGGER_F = SETPOINT * ((100 - THRESHOLD_PCT) / 100)
SEEDS = (0, 1, 2)


def _lid_min_temps(monkeypatch, h_lid=None):
    if h_lid is not None:
        real_init = GrillSim.__init__

        def _fixed_h_lid(self, **kwargs):
            real_init(self, **{**kwargs, "h_lid": h_lid})

        monkeypatch.setattr(GrillSim, "__init__", _fixed_h_lid)
    return [controller_matrix.run_scenario("pid_sp", SCENARIO, seed)["lid_min_temp_f"] for seed in SEEDS]


def test_lid_open_scenario_crosses_the_lid_open_threshold(monkeypatch):
    mins = _lid_min_temps(monkeypatch)

    assert all(t < TRIGGER_F for t in mins), (
        f"lid_open_225 must produce an excursion the lid detector would fire on "
        f"(below {TRIGGER_F:.2f} F at a {SETPOINT:.0f} F setpoint); coldest readings were {mins}"
    )


def test_a_lidless_plant_misses_the_threshold(monkeypatch):
    """Negative control: the pre-fix plant, where an open lid leaks no heat."""
    mins = _lid_min_temps(monkeypatch, h_lid=0.0)

    assert all(t > TRIGGER_F for t in mins), (
        f"the no-heat-leak plant was expected to miss the {TRIGGER_F:.2f} F trigger, "
        f"so the assertion above measures the lid model; coldest readings were {mins}"
    )
    # And it barely moves the chamber at all, which is the defect itself.
    assert all(t > SETPOINT - 10.0 for t in mins), mins
