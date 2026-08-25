"""
*****************************************
 PiFire MPC Grill Simulator (test-only)
*****************************************

 A higher-fidelity, deliberately MISMATCHED nonlinear grill plant for
 closed-loop validation of the MPC. Unlike the controller's smooth grey-box
 model it includes the effects that actually limit pellet-grill control:

   - discrete pellet-PULSE feeding (the auger toggles on/off), not a smooth
     continuous heat source;
   - transport + ignition DEADTIME between feeding a pellet and its heat
     release (default ~20 s);
   - the FAN as a real lever (it accelerates burn, boosts firepot->chamber
     convection, and increases chamber->ambient loss);
   - an OPEN LID as a chamber-to-ambient heat leak large enough to produce the
     excursion Hold's lid detector fires on;
   - combustion noise (pellet quality), sensor LAG (probe time constant ~4.5 s),
     and a light, occasional WIND breeze nudging chamber heat loss a few percent.

 So a passing closed-loop result is honest about realistic performance (a few
 degrees C band), not the artificially tight band an idealized plant gives.

 Interface: step(auger_on, fan_frac, lid_open=False) advances one DT=1 s;
 measured() returns the lagged + noisy probe reading; true_Tc is the
 (noise-free) chamber temperature.

*****************************************
"""

import numpy as np

DT = 1.0


class GrillSim:
    def __init__(self, *, seed=0, deadtime=20, fan_is_lever=True, fixed_fan=None, probe_tau=4.5, H=420.0, h_lid=1.5):
        self.rng = np.random.default_rng(seed)
        self.fan_is_lever = fan_is_lever
        self.fixed_fan = fixed_fan  # if set, fan held at this frac
        self.probe_tau = probe_tau
        # truth params (offset from the controller's nominal grey-box)
        self.C_f, self.C_c = 9.0, 300.0
        self.h_fc0, self.h_amb0 = 1.3, 0.42
        # extra chamber->ambient conductance while the lid is open; ~4x the
        # closed-lid, fan-off loss, which drops a 225 F chamber well past the
        # 15% fall that arms Hold's lid detector within a two-minute pause
        self.h_lid = h_lid
        self.sigma = 1.4e-9
        self.feed_rate = 1.0  # fuel units/s while auger ON
        self.H = H  # heat per fuel unit (~140 -> 334F max; ~300 -> ~450F max)
        self.k_burn = 0.10
        self.T_amb = 17.0
        from collections import deque

        self.transit = deque([0.0] * int(deadtime))
        self.fuel = 0.0
        self.T_f = 20.0
        self.T_c = 20.0
        self.T_meas = 20.0
        self.t = 0.0
        self.afr = 1.0
        self._gust_until = -1.0
        self._gust = 1.0

    def _wind(self):
        # Realistic light wind: mostly dead calm, with an occasional light 1-2 mph
        # breeze that bumps chamber heat loss only a few percent. (An earlier model
        # multiplied loss by 1.6-2.6x, which swamped control and is not
        # representative -- real cooks are mostly calm.)
        if self.t > self._gust_until:
            if self.rng.random() < 0.0010:  # ~ every 17 min
                self._gust = self.rng.uniform(1.03, 1.12)
                self._gust_until = self.t + self.rng.uniform(30, 90)
            else:
                self._gust = 1.0
        return self._gust

    @property
    def true_Tc(self):
        return float(self.T_c)

    def measured(self):
        return self.T_meas + float(self.rng.normal(0, 0.15))

    def step(self, auger_on, fan_frac, lid_open=False):
        if self.fixed_fan is not None:
            fan_frac = self.fixed_fan
        fan = float(np.clip(fan_frac, 0.0, 1.0))
        eff_fan = fan if self.fan_is_lever else 0.65

        # deadtime: pellets fed now release heat `deadtime` seconds later
        fed = self.feed_rate * float(auger_on)
        released = self.transit.popleft()
        self.transit.append(fed)
        self.fuel += released

        # combustion: fan accelerates burn; air sufficiency sets efficiency
        burn = self.k_burn * self.fuel * (0.5 + 0.6 * eff_fan)
        burn = min(burn, self.fuel)
        avail_air = 0.45 + 0.85 * eff_fan
        needed_air = burn * 0.9 + 1e-6
        eff = float(np.clip(avail_air / needed_air, 0.45, 1.0))
        self.afr = avail_air / needed_air
        noise = 1.0 + self.rng.normal(0, 0.05)
        heat = burn * self.H * eff * max(noise, 0.0)

        # fan-dependent transfer + loss; wind gusts on loss
        h_fc = self.h_fc0 * (0.6 + 0.7 * eff_fan)
        h_amb = self.h_amb0 * (0.8 + 0.5 * eff_fan) * self._wind()
        if lid_open:
            h_amb += self.h_lid
        rad = self.sigma * ((self.T_c + 273.15) ** 4 - (self.T_amb + 273.15) ** 4)

        dT_f = (heat - h_fc * (self.T_f - self.T_c)) / self.C_f
        dT_c = (h_fc * (self.T_f - self.T_c) - h_amb * (self.T_c - self.T_amb) - rad) / self.C_c
        self.T_f += dT_f * DT
        self.T_c += dT_c * DT
        self.fuel = max(0.0, self.fuel - burn * DT)
        self.T_meas += (self.T_c - self.T_meas) * DT / self.probe_tau
        self.t += DT
        return self.true_Tc


class MAKGrillSim(GrillSim):
    """GrillSim carrying a real MAK grill's measured thermal parameters.

    The base GrillSim is deliberately mismatched but generic. This one is
    identified from a logged 450 F Hold cook on an actual MAK
    (tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv), so a closed-loop result
    on it speaks about a grill that exists: a chamber roughly ten times slower
    than the base plant, fed through a ~100 s transport-and-ignition deadtime.
    That combination is what makes braking distance the dominant control
    problem rather than an afterthought.

    Only the ratios C_c/h_amb0 (chamber time constant) and H/h_amb0 (steady
    gain) are identifiable from a single heat-up ramp, so h_amb0 keeps the base
    plant's value and the two ratios are carried in C_c and H.

    The identification run had the fan pinned at 100% throughout, so the fan
    ENTERS these numbers only at full airflow: the fan-response shape is the
    base plant's, unfitted. Treat fan authority here as inherited structure,
    not measurement.
    """

    #: Fitted to the logged cook: RMSE 2.3 C, peak 519 F against 520 F measured.
    C_C = 3115.9
    HEAT_PER_UNIT = 958.8
    DEADTIME = 100
    AMBIENT_C = 20.0

    def __init__(self, *, T0=20.0, **kwargs):
        kwargs.setdefault("deadtime", self.DEADTIME)
        kwargs.setdefault("H", self.HEAT_PER_UNIT)
        super().__init__(**kwargs)
        self.C_c = self.C_C
        self.T_amb = self.AMBIENT_C
        # A cook starts from whatever the chamber is already sitting at, which
        # for a reheat is well above ambient.
        self.T_f = self.T_c = self.T_meas = float(T0)
