"""Guards `controller/grill_sim.py`'s `fed = self.feed_rate * float(auger_on)`
line as a no-op for every existing boolean caller.

The GrillSim matrix harness (`docs/superpowers/experiments/controller_matrix.py`)
now needs `GrillSim.step()` to accept a fractional duty (the auger's exact
on-time within a 1 s window) instead of only a boolean sample of it. Every
other caller -- this directory's `test_mpc_closed_loop.py`/`test_mpc_net_loop.py`,
`sample_mpc.py`, the net-policy training sampler -- passes a Python bool, and
`float(auger_on)` is algebraically identical to the old `if auger_on else 0.0`
branch for `True`/`False`. This test proves that by running the same seed and
the same True/False sequence through both implementations and comparing the
resulting temperature trajectories exactly.
"""

import numpy as np

from controller.grill_sim import DT, GrillSim


def _legacy_step(self, auger_on, fan_frac):
    """Frozen copy of `GrillSim.step` as it existed before the `float(auger_on)`
    change -- kept only so this test has something to compare the current
    implementation against; not meant to track future changes to `step()`."""
    if self.fixed_fan is not None:
        fan_frac = self.fixed_fan
    fan = float(np.clip(fan_frac, 0.0, 1.0))
    eff_fan = fan if self.fan_is_lever else 0.65

    fed = self.feed_rate if auger_on else 0.0
    released = self.transit.popleft()
    self.transit.append(fed)
    self.fuel += released

    burn = self.k_burn * self.fuel * (0.5 + 0.6 * eff_fan)
    burn = min(burn, self.fuel)
    avail_air = 0.45 + 0.85 * eff_fan
    needed_air = burn * 0.9 + 1e-6
    eff = float(np.clip(avail_air / needed_air, 0.45, 1.0))
    self.afr = avail_air / needed_air
    noise = 1.0 + self.rng.normal(0, 0.05)
    heat = burn * self.H * eff * max(noise, 0.0)

    h_fc = self.h_fc0 * (0.6 + 0.7 * eff_fan)
    h_amb = self.h_amb0 * (0.8 + 0.5 * eff_fan) * self._wind()
    rad = self.sigma * ((self.T_c + 273.15) ** 4 - (self.T_amb + 273.15) ** 4)

    dT_f = (heat - h_fc * (self.T_f - self.T_c)) / self.C_f
    dT_c = (h_fc * (self.T_f - self.T_c) - h_amb * (self.T_c - self.T_amb) - rad) / self.C_c
    self.T_f += dT_f * DT
    self.T_c += dT_c * DT
    self.fuel = max(0.0, self.fuel - burn * DT)
    self.T_meas += (self.T_c - self.T_meas) * DT / self.probe_tau
    self.t += DT
    return self.true_Tc


def test_float_auger_on_matches_legacy_boolean_branch():
    seed = 7
    fan_frac = 0.6
    # An uneven on/off pattern (not all-on or all-off), long enough to exercise
    # the deadtime transit line, fuel accumulation, and the wind-gust RNG draws.
    block = [True, True, True] + [False] * 14 + [True, True]
    sequence = (block * 20)[:300]

    fixed = GrillSim(seed=seed)
    legacy = GrillSim(seed=seed)

    fixed_trace = [fixed.step(auger_on=on, fan_frac=fan_frac) for on in sequence]
    legacy_trace = [_legacy_step(legacy, on, fan_frac) for on in sequence]

    assert fixed_trace == legacy_trace
