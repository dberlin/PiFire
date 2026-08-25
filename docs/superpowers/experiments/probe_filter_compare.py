"""
Compare probe smoothing filters on synthetic traces at the real loop rate.

Produced the numbers behind docs/superpowers/specs/2026-07-30-hampel-prefilter-design.md.
Run from the repo root: `uv run python docs/superpowers/experiments/probe_filter_compare.py`

Candidates:
  - GatedKalman   : the pre-2026-07-30 shipping filter, kept here verbatim so the
                    regression it had stays reproducible after the fix landed
  - HampelEMA     : Hampel outlier rejection feeding a plain exponential average
  - TempKalman    : whatever probes/kalman.py currently is

The traces sample at 50 ms because that is the control loop's cadence
(`ctx.clock.sleep(0.05)` in controller/runtime/modes/base.py), and the gate's
behavior depends strongly on dt.
"""

import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from probes.kalman import TempKalman  # noqa: E402

DT = 0.05
RAMP = 1.5  # deg F/s, a typical pit startup ramp
NOISE = 2.0  # deg F, the sensor noise probes/kalman.py's R is tuned for


class GatedKalman:
    """The filter as it shipped before the Hampel prefilter replaced its gate.

    Kept for comparison only. The gate rejects readings more than 5 sigma from
    the prediction and keeps the predicted covariance, which at dt=0.05 grows so
    slowly (Q00 = q*dt^4/4 ~ 7.8e-7) that a real step stays rejected for tens of
    seconds.
    """

    def __init__(self, R=4.0, q=0.5, gate=5.0):
        self.R, self.q, self.gate2 = R, q, gate**2
        self.x, self.v = None, 0.0
        self.P = [[R, 0.0], [0.0, R]]
        self.last_time = None

    def update(self, z, now):
        if self.x is None:
            self.x, self.v, self.last_time = float(z), 0.0, now
            return self.x
        dt = min(max(now - self.last_time, 0.01), 1.0)
        self.last_time = now
        self.x += self.v * dt
        P = self.P
        p00 = P[0][0] + dt * (P[1][0] + P[0][1]) + dt * dt * P[1][1] + self.q * dt**4 / 4
        p01 = P[0][1] + dt * P[1][1] + self.q * dt**3 / 2
        p10 = P[1][0] + dt * P[1][1] + self.q * dt**3 / 2
        p11 = P[1][1] + self.q * dt * dt
        y = z - self.x
        s = p00 + self.R
        if (y * y) / s > self.gate2:
            self.P = [[p00, p01], [p10, p11]]
            return round(self.x, 1)
        k0, k1 = p00 / s, p10 / s
        self.x += k0 * y
        self.v += k1 * y
        self.P = [[(1 - k0) * p00, (1 - k0) * p01], [p10 - k1 * p00, p11 - k1 * p01]]
        return round(self.x, 1)


class HampelEMA:
    """Hampel rejection into an exponential moving average -- no rate state, so
    it trails any ramp by rate * tau no matter how the window is tuned."""

    def __init__(self, k=11, nsigma=3.0, alpha=0.055, mad_floor=0.5):
        self.k, self.nsigma, self.alpha, self.mad_floor = k, nsigma, alpha, mad_floor
        self.win, self.y = [], None

    def update(self, z, now=None):
        self.win.append(float(z))
        if len(self.win) > self.k:
            self.win.pop(0)
        x = float(z)
        if len(self.win) >= self.k:
            med = statistics.median(self.win)
            mad = statistics.median([abs(w - med) for w in self.win])
            if abs(x - med) > self.nsigma * max(1.4826 * mad, self.mad_floor):
                x = med
        self.y = x if self.y is None else self.y + self.alpha * (x - self.y)
        return round(self.y, 1)


CANDIDATES = [
    ("gated Kalman (pre-fix)", GatedKalman),
    ("Hampel(11) + EMA a=0.055", HampelEMA),
    ("Hampel(11) -> Kalman (now)", lambda: TempKalman("F")),
]


def drive(filt, readings):
    t, out = 0.0, []
    for z in readings:
        t += DT
        v = filt.update(z, now=t)
        out.append(np.nan if v is None else v)
    return np.array(out)


def noisy(truth, seed):
    return truth + np.random.default_rng(seed).normal(0, NOISE, len(truth))


def main():
    rule = "-" * 78

    print("\n1. Noise and lag: 30 s hold, 120 s ramp at 1.5 F/s, 60 s hold\n" + rule)
    truth = np.concatenate(
        [
            np.full(int(30 / DT), 70.0),
            70.0 + RAMP * np.arange(int(120 / DT)) * DT,
            np.full(int(60 / DT), 70.0 + RAMP * 120),
        ]
    )
    z = noisy(truth, 1)
    a, b = int(30 / DT), int(150 / DT)
    print(f"{'filter':<30}{'hold sd':>10}{'ramp lag':>12}{'overshoot':>12}")
    for name, make in CANDIDATES:
        e = drive(make(), z) - truth
        hold_sd = float(np.nanstd(e[b + int(10 / DT) :]))
        lag = -float(np.nanmean(e[a + int(10 / DT) : b - int(5 / DT)])) / RAMP * 1000
        over = float(np.nanmax(e[b : b + int(20 / DT)]))
        print(f"{name:<30}{hold_sd:>9.2f}F{lag:>10.0f}ms{over:>11.2f}F")
    print(f"{'(raw readings)':<30}{float(np.std(z[b:] - truth[b:])):>9.2f}F")

    print("\n2. Sustained-step admission: seconds until within 2 F of the new level\n" + rule)
    print(f"{'filter':<30}" + "".join(f"{s:>11} F" for s in (10, 20, 50, 100)))
    for name, make in CANDIDATES:
        row = f"{name:<30}"
        for step in (10, 20, 50, 100):
            tr = np.concatenate([np.full(int(30 / DT), 250.0), np.full(int(60 / DT), 250.0 + step)])
            o = drive(make(), noisy(tr, 2))
            hit = next((j for j in range(int(30 / DT), len(o)) if abs(o[j] - (250 + step)) < 2.0), None)
            row += f"{(hit - int(30 / DT)) * DT:>12.2f}s" if hit else f"{'never':>13}"
        print(row)

    print("\n3. Consecutive-glitch tolerance: worst output error, glitch = 850 F\n" + rule)
    print(f"{'filter':<30}" + "".join(f"{b:>9}" for b in (1, 3, 5, 6, 8)))
    for name, make in CANDIDATES:
        row = f"{name:<30}"
        for blen in (1, 3, 5, 6, 8):
            tr = np.full(int(70 / DT), 250.0)
            zz = noisy(tr, 3)
            s0 = int(35 / DT)
            zz[s0 : s0 + blen] = 850.0
            o = drive(make(), zz)
            row += f"{float(np.nanmax(np.abs(o[s0 : s0 + 60] - 250.0))):>8.1f}F"
        print(row)

    print("\n4. Cost per update\n" + rule)
    for name, make in CANDIDATES:
        f, t0, now = make(), time.perf_counter(), 0.0
        for i in range(30000):
            now += DT
            f.update(250.0 + (i % 5) * 0.2, now=now)
        print(f"{name:<30}{(time.perf_counter() - t0) / 30000 * 1e6:>8.2f} us")


if __name__ == "__main__":
    main()
