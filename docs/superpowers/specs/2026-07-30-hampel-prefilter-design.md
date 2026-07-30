# Hampel prefilter replaces the Kalman innovation gate

Supersedes the outlier-rejection half of
[2026-07-05-kalman-probe-filter-design.md](2026-07-05-kalman-probe-filter-design.md).
The constant-velocity estimator chosen there is unchanged and still correct.

## Problem

The 5-sigma innovation gate froze the output on genuine temperature jumps.

On a reject the filter kept the predicted covariance, on the theory that `P`
grows by `Q` each tick until the reading is admitted -- the code said "a
sustained real change is admitted within a sample or two". At the control
loop's actual cadence that is false. `Q00 = q*dt^4/4` is about 7.8e-7 at
dt=0.05 (`ctx.clock.sleep(0.05)`, controller/runtime/modes/base.py), so `P00`
crawls and `y^2/s` stays above the threshold for a long time.

Measured against the shipping filter at 50 ms:

| Stimulus | Time to follow |
|---|---|
| 50 F step | 384 consecutive rejects -- 19.2 s of frozen output |
| 100 F step | 33.2 s |
| sustained ramp >= 20 F/s | never within the 10 s test; ~186 F of error |
| probe pushed into hot food (exponential, tau <= 3 s) | ~36 s to settle |

The reachable causes are ordinary: a food probe moved between pieces of meat, a
probe reseated, an intermittent connector. The failure is also exactly the
hold-then-snap behavior the 2026-07-05 design set out to remove, reappearing
under a different trigger.

The gate's apparent strength -- immunity to arbitrarily long glitch bursts --
is the same property as the freeze. Deciding by distance from the estimate
cannot distinguish "long glitch" from "real jump" except by waiting.

## Decision

Reject outliers by comparing a reading to its **neighbours** instead of to the
estimate: a **Hampel filter** ahead of the estimator, and no gate.

A reading further than 3 robust sigmas (`1.4826 * MAD`) from the median of the
last 11 raw readings is replaced by that median. Window length sets both halves
of the trade in one number, in samples rather than as an emergent property of
the covariance:

- absorbs up to `(11 - 1) // 2` = **5** consecutive bad samples
- admits a change that persists for `(11 + 1) // 2` = **6** samples (0.3 s)

`mad_floor` (0.5 F / 0.28 C) is the smallest spread the test will assume, so a
sensor reading perfectly steady -- MAD of exactly zero -- does not then reject
every reading that follows.

## Results

From `docs/superpowers/experiments/probe_filter_compare.py`, 50 ms sampling,
2 F sensor noise. Hampel+EMA is included because it was the obvious
alternative; it is rejected -- an EMA has no rate state, so it trails a ramp by
`rate * tau` and gives up the low lag that motivated the Kalman.

| filter | hold sd | ramp lag | 50 F step | 5-glitch burst | cost |
|---|---|---|---|---|---|
| gated Kalman (before) | 0.35 F | -18 ms | 20.15 s | 0.6 F | 0.91 us |
| Hampel(11) + EMA | 0.36 F | 876 ms | 3.10 s | 0.7 F | 1.24 us |
| **Hampel(11) -> Kalman (now)** | **0.37 F** | **15 ms** | **2.00 s** | **0.7 F** | **1.82 us** |

Smoothing and lag are held; step admission improves about tenfold. At five
probes the cost is ~9 us per tick against a 50 ms budget.

## Accepted trade-offs

- **Bursts longer than 5 samples now pass.** The old gate absorbed them
  indefinitely, at the price above. A burst of 6+ consecutive readings that are
  all wrong and mutually consistent is a broken sensor, and holding a stale
  temperature is a worse answer to it than following the reading.
- **The first 11 samples after a start or reset are untested**, since the
  window is not yet full. Bounded, and the reset path already re-initializes
  from a single reading.
- **A fast rate change is trailed while the rate estimate spins up** -- a 20 F/s
  ramp peaks around 15 F behind before converging. The estimator follows and the
  error decays; it no longer stalls.

## Files

- `probes/kalman.py` -- Hampel prefilter, gate and its tuning removed.
- `probes/base.py` -- debug line reports `outlier=` rather than `gated=`.
- `tests/unit/probes/test_kalman.py` -- step admission, fast-ramp tracking, and
  a burst guard pinning the 5-sample tolerance being traded for.
- `docs/superpowers/experiments/probe_filter_compare.py` -- the harness, which
  keeps a copy of the pre-fix gated filter so the regression stays reproducible.
