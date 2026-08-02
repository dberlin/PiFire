"""Guards `GrillSim.step`'s `auger_on` parameter as a boolean-compatible
fractional duty: `float(auger_on)` must behave identically to the boolean
`auger_on` every existing caller passes.

The GrillSim matrix harness (`docs/superpowers/experiments/controller_matrix.py`)
needs `step()` to accept the auger's exact fractional on-time within a window
instead of only a boolean sample of it. Every other caller -- this directory's
`test_mpc_closed_loop.py`/`test_mpc_net_loop.py`, `sample_mpc.py`, the
net-policy training sampler -- passes a Python bool. This test drives the
real (current) `GrillSim.step` twice with the same seed and the same on/off
pattern, once as `True`/`False` and once as the equivalent `1.0`/`0.0`, and
asserts the two runs produce identical temperature trajectories.
"""

from controller.grill_sim import GrillSim


def test_float_auger_on_matches_boolean_auger_on():
    seed = 7
    fan_frac = 0.6
    # An uneven on/off pattern (not all-on or all-off), long enough to exercise
    # the deadtime transit line, fuel accumulation, and the wind-gust RNG draws.
    block = [True, True, True] + [False] * 14 + [True, True]
    bool_sequence = (block * 20)[:300]
    float_sequence = [float(on) for on in bool_sequence]

    bool_plant = GrillSim(seed=seed)
    float_plant = GrillSim(seed=seed)

    bool_trace = [bool_plant.step(auger_on=on, fan_frac=fan_frac) for on in bool_sequence]
    float_trace = [float_plant.step(auger_on=on, fan_frac=fan_frac) for on in float_sequence]

    assert bool_trace == float_trace
