"""Guards the two `GrillSim.step` parameters that were widened for the matrix
harness, so neither one perturbs the plant for the callers that do not use it.

`auger_on` is a boolean-compatible fractional duty: `float(auger_on)` must
behave identically to the boolean `auger_on` every existing caller passes. The
GrillSim matrix harness (`docs/superpowers/experiments/controller_matrix.py`)
needs `step()` to accept the auger's exact fractional on-time within a window
instead of only a boolean sample of it. Every other caller -- this directory's
`test_mpc_closed_loop.py`/`test_mpc_net_loop.py`, `sample_mpc.py`, the
net-policy training sampler -- passes a Python bool.

`lid_open` is a keyword every existing caller omits. `GrillSim` is the plant of
record for 20+ experiments and two committed matrix artifacts, so the default
`lid_open=False` must reproduce the pre-existing trajectory exactly, not merely
closely: a drift of a few ULP compounds over the 3.5 h runs those artifacts
measure. The positive control below drives the same plant with `lid_open=True`
and shows the parameter does reach the dynamics, so the no-op assertion is not
passing on an argument the plant ignores.

Each test drives the real (current) `GrillSim.step` twice from the same seed
with the same on/off pattern and compares the temperature trajectories.
"""

from controller.grill_sim import GrillSim

FAN_FRAC = 0.6
# An uneven on/off pattern (not all-on or all-off), long enough to exercise
# the deadtime transit line, fuel accumulation, and the wind-gust RNG draws.
_BLOCK = [True, True, True] + [False] * 14 + [True, True]
BOOL_SEQUENCE = (_BLOCK * 20)[:300]


def test_float_auger_on_matches_boolean_auger_on():
    seed = 7
    float_sequence = [float(on) for on in BOOL_SEQUENCE]

    bool_plant = GrillSim(seed=seed)
    float_plant = GrillSim(seed=seed)

    bool_trace = [bool_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in BOOL_SEQUENCE]
    float_trace = [float_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in float_sequence]

    assert bool_trace == float_trace


def test_lid_open_false_matches_omitting_lid_open():
    seed = 7

    omitted_plant = GrillSim(seed=seed)
    explicit_plant = GrillSim(seed=seed)

    omitted_trace = [omitted_plant.step(auger_on=on, fan_frac=FAN_FRAC) for on in BOOL_SEQUENCE]
    explicit_trace = [explicit_plant.step(auger_on=on, fan_frac=FAN_FRAC, lid_open=False) for on in BOOL_SEQUENCE]

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
