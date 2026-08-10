"""Guards the two `GrillSim.step` parameters that were widened for the matrix
harness, so neither one perturbs the plant for the callers that do not use it.

`auger_on` is a boolean-compatible fractional duty: `float(auger_on)` must
behave identically to the boolean `auger_on` every existing caller passes. The
GrillSim matrix harness (`docs/superpowers/experiments/controller_matrix.py`)
needs `step()` to accept the auger's exact fractional on-time within a window
instead of only a boolean sample of it. The production controller and remaining
closed-loop fixtures pass a Python bool.

`lid_open` is a keyword every existing caller omits. `GrillSim` is the plant of
record for 20+ experiments and two committed matrix artifacts, so the default
`lid_open=False` must reproduce the pre-existing trajectory exactly, not merely
closely: a drift of a few ULP compounds over the 3.5 h runs those artifacts
measure. Comparing `step(a, f)` against `step(a, f, lid_open=False)` cannot show
that -- in Python those are one call taking one branch, so any perturbation of
the closed-lid dynamics moves both sides equally. `CLOSED_LID_FINAL_C` is
therefore an absolute pin, the value the plant produced before `lid_open`
existed. The positive control below drives the same plant with `lid_open=True`
and shows the parameter does reach the dynamics.

Each test drives the real (current) `GrillSim.step` twice from the same seed
with the same on/off pattern and compares the temperature trajectories.
"""

from controller.grill_sim import GrillSim

FAN_FRAC = 0.6
# An uneven on/off pattern (not all-on or all-off), long enough to exercise
# the deadtime transit line, fuel accumulation, and the wind-gust RNG draws.
_BLOCK = [True, True, True] + [False] * 14 + [True, True]
BOOL_SEQUENCE = (_BLOCK * 20)[:300]
# Final chamber reading of BOOL_SEQUENCE at seed 7, measured on the plant as it
# stood before the lid model was added.
CLOSED_LID_FINAL_C = 90.48906753109672


def test_float_auger_on_matches_boolean_auger_on():
    seed = 7
    float_sequence = [float(on) for on in BOOL_SEQUENCE]

    bool_plant = GrillSim(seed=seed)
    float_plant = GrillSim(seed=seed)

    bool_trace = [bool_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in BOOL_SEQUENCE]
    float_trace = [float_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in float_sequence]

    assert bool_trace == float_trace


def test_closed_lid_trajectory_matches_the_plant_before_the_lid_model():
    seed = 7

    omitted_plant = GrillSim(seed=seed)
    explicit_plant = GrillSim(seed=seed)

    omitted_trace = [omitted_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in BOOL_SEQUENCE]
    explicit_trace = [explicit_plant.step(auger_on=on, fan_frac=FAN_FRAC, lid_open=False) for on in BOOL_SEQUENCE]

    assert omitted_trace[-1] == CLOSED_LID_FINAL_C, (
        f"the closed-lid trajectory has moved off the pre-lid-model plant: {omitted_trace[-1]!r}"
    )
    assert omitted_trace == explicit_trace


def test_lid_open_true_cools_the_chamber():
    """Positive control for the no-op guard above: `lid_open` is wired into
    the dynamics, so `lid_open=False` matching is a property of the default
    rather than of a dead parameter."""
    seed = 7

    closed_plant = GrillSim(seed=seed)
    open_plant = GrillSim(seed=seed)

    closed_trace = [closed_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in BOOL_SEQUENCE]
    open_trace = [open_plant.step(auger_on=on, fan_frac=FAN_FRAC, lid_open=True) for on in BOOL_SEQUENCE]

    assert open_trace[-1] < closed_trace[-1]
