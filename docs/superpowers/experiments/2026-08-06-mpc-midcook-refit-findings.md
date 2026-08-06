# Mid-cook refit: what it is worth, and what it costs

**Question.** MPC learns its grill in one batch at teardown, so cook 1 on an
unfamiliar grill is steered entirely by the shipped defaults. On a real MAK
those defaults are roughly ten times too fast (`C_c` 320 against a measured
3115.9), and an MPC plans its braking distance against exactly that number, so
it drives through setpoint: 12.7 / 22.8 / 33.9 °F of overshoot at 225 / 350 /
450. Cook 2, holding the model cook 1 produced, lands inside 1.6 °F. The
question is whether cook 1 can be made to help itself.

**Answer.** Yes, and with the code that already ships. The fitter behind the
teardown refit, run on the history buffer the controller already keeps, produces
a usable model 10–12 minutes into a first cook — before the overshoot it would
prevent. Adopting it through the existing `restore_model` is the whole
mechanism. No new identifier, no parameter mapping, no estimator surgery.

The one thing that must be built rather than reused is the trigger. Adopting at
the first moment the existing gate says yes is measurably harmful at 225 °F,
where the accepted model is still 62 % wrong; mid-cook adoption needs a
stronger bar than the teardown decision it borrows, because it steers the rest
of the cook rather than the next one.

Harnesses: `mpc_online_window.py` (when the model becomes learnable) and
`mpc_midcook_adopt.py` (whether adopting it helps). Plant `MAKGrillSim`,
2 seeds, medians. Every number below is cook 1 from shipped defaults.

## The window opens before the damage

`refit_from_cook` was run against successive prefixes of a real cook, from a
fresh controller carrying the shipped defaults each time — the decision an
online scheme would actually be making at that minute.

| setpoint | cook-1 overshoot | first accept | reaches setpoint | peaks | lead |
|---|---|---|---|---|---|
| 225 °F | 12.7 °F | 10 min | 8.0 min | 10.3 min | ~0 min |
| 350 °F | 22.8 °F | 12 min | 12.2 min | 15.2 min | 3 min |
| 450 °F | 33.9 °F | 12 min | 16.4 min | 20.6 min | 8.6 min |

Before 10 minutes the gate refuses on `_REFIT_MIN_SAMPLES` (120). At 10 minutes
on the hotter setpoints it refuses on the identifiability floor (0.459 against
0.5), which clears by 12. The lead time grows with the setpoint exactly as the
overshoot does: the method has most warning where the problem is worst.

The first accepted fit puts `C_c` at ~2400 against a truth of 3115.9, from a
shipped 320 — about 85 % of the model error removed on the first try. It
converges to −10 % by 45 minutes and stops improving there.

## Adopting it works, and the ramp is when to do it

450 °F, the worst case:

| arm | overshoot | in-band | tail in-band | settle |
|---|---|---|---|---|
| baseline (ships now) | 34.1 °F | 50.8 % | 87.9 % | 6956 s |
| adopt @12 min | **5.6 °F** | 89.6 % | 99.3 % | 8331 s |
| adopt @20 min | 34.1 °F | 86.6 % | 100 % | 1714 s |
| adopt @30 min | 34.1 °F | 82.7 % | 100 % | 2216 s |

Two separate benefits, with different deadlines:

- **Overshoot** is only recoverable while the chamber is still climbing.
  Adopting at 20 or 30 minutes leaves it at 34.1 °F, identical to baseline,
  because the peak is at 20.6.
- **Holding** improves whenever the model arrives. Even the latest adoption
  takes in-band from 50.8 % to 82.7 % and settling from 6956 s to 2216 s.

At 350 °F, adopting at 12 minutes takes overshoot 22.7 → 18.0 °F, in-band
56.3 % → 91.6 %, settle 6501 s → 1107 s. At 225 °F the peak is at 10.3 minutes,
before the gate will look at all, so there is no overshoot to recover; adopting
at 20 minutes still takes in-band 69.4 % → 89.2 %.

## An accepted model can still be too wrong to adopt

The gate asks whether a candidate beats the incumbent on the same data. Early
in a cook a candidate can clear that bar and still be badly wrong, because the
incumbent it beat is wrong by a factor of ten.

At 225 °F the fit accepted at 10 minutes carries `C_c` 1180 — 62 % low — and
adopting it is the worst outcome measured anywhere in this work:

| 225 °F | overshoot | in-band | tail in-band | settle |
|---|---|---|---|---|
| baseline | 12.9 °F | 69.4 % | 100 % | 5033 s |
| adopt @10 min (`C_c` −62 %) | 14.7 °F | **23.0 %** | **39.8 %** | 11397 s |
| adopt @12 min (`C_c` −46 %) | 13.4 °F | 71.5 % | 96.4 % | 8363 s |
| adopt @20 min (`C_c` −17 %) | 12.9 °F | 88.4 % | 100 % | 1522 s |

So "the earliest moment the gate says yes" is the wrong trigger. The teardown
gate is calibrated for a decision judged against a whole finished cook; a
mid-cook adoption steers everything that follows it, and needs a stronger bar
than the one that merely beats a known-bad incumbent.

## Asking repeatedly recovers from a bad early adoption

Probing on a cadence — every minute until the first acceptance, then every 30 —
and adopting whenever the gate accepts:

| | overshoot | in-band | tail in-band | settle | adopts | fit CPU |
|---|---|---|---|---|---|---|
| 225 baseline | 12.9 °F | 69.4 % | 100 % | 5033 s | — | — |
| 225 periodic | 14.7 °F | 78.9 % | 100 % | 2718 s | 2 | 39.7 s |
| 350 baseline | 22.7 °F | 56.3 % | 96.9 % | 6501 s | — | — |
| 350 periodic | **12.7 °F** | 88.3 % | 99.9 % | 7159 s | 3 | 37.5 s |
| 450 baseline | 34.1 °F | 50.8 % | 87.9 % | 6956 s | — | — |
| 450 periodic | 11.9 °F | 80.0 % | 98.6 % | 8150 s | 3 | 36.4 s |

Repeating is self-correcting: at 225 it makes the harmful 10-minute adoption
and then recovers, ending at 100 % tail in-band and settling in half
baseline's time, where the single 10-minute adoption never recovers at all.
It also gives the best 350 °F overshoot of anything tested.

Neither policy dominates. A single well-timed adoption is better at 450
(5.6 °F against 11.9); repetition is better at 350 (12.7 against 18.0) and is
the only one that survives its own mistakes. What the pair shows is that the
trigger should be a quality bar rather than a clock, with repetition as the
safety net behind it.

## Three things the measurement contradicts

**Deferring promotion until the grill is quiet is backwards.** The entire
overshoot benefit is earned by adopting *during* the ramp. A rule that waits
for quiescence adopts after the peak and collects only the holding half.

**Do not carry the estimator state across the rebuild.** Carrying it whole was
the worst arm tested — overshoot fell to 7.8 °F but tail in-band collapsed to
37.9 %, a controller too timid to hold. The disturbance state `d` was the only
place a tenfold chamber error could be absorbed, so it holds a large fictitious
load; carried onto parameters that no longer need it, the same error is counted
twice and the controller under-fires for the rest of the cook. Zeroing `d`
fixes it — and a plain cold restart of the estimator does just as well
(overshoot 5.6 °F, tail in-band 99.3 %), because the filter reconverges in
seconds against a 5-second measurement cadence.

That last point is what makes this cheap: the existing `restore_model` already
rebuilds and already drops the state estimate. It is correct as written.

**The promotion gate is load-bearing, not a nicety.** The negative control —
rebuild at the same moment with the *same* parameters — came out **worse than
baseline** (51.2 °F against 34.1). Adoption flips the controller into
identified mode, which switches on the equilibrium feed-forward and the
learned-residual objective. That is a large win with a good model and a large
loss with a bad one. Nothing may be adopted that has not beaten the incumbent
on the same data.

## Cost

A fit re-simulates the whole history once per least-squares evaluation, so its
cost grows with the cook: 0.2–0.4 s at 12 minutes, ~1 s at 45, ~2.5 s at 90,
measured on the development machine and not on the Pi that is the nominal
target. Scale, do not quote.

This rules out a flat cadence. Asking every 5 minutes for a whole 3.5-hour cook
spent 91 s of CPU across 42 fits to make 3 adoptions, and its probes at 10/15/20
minutes straddled the 12-minute opening — 17.6 °F of overshoot where a
well-timed single adoption got 5.6.

The cost curve and the value curve point the same way: early fits are cheap and
decisive, late ones expensive and redundant. Probing every minute until the
first acceptance and every 30 after it still spent 36–40 s of CPU per cook,
and essentially all of it went to the late probes — six fits over a history
grown to 2500 rows, re-deciding a model that stopped improving at 45 minutes.
Bounding what a late fit reads, or stopping once the parameters settle, is
where that cost goes; neither is measured here.

The work cannot run on the control path in any case —
`ThreadedControllerRunner` already owns the core off the Hold loop, and a
multi-second fit belongs off both.

## What this does not establish

- One plant. `MAKGrillSim` is a real grill, but a slow one, and the shipped
  defaults are wrong for it in a specific direction. A grill whose true `C_c`
  is near the shipped 320 has nothing to gain here and everything to lose from
  a bad promotion; the gate is the only thing standing between those two cases,
  and it has not been tested against the second.
- Steady setpoints only. No lid opening, no setpoint change, no reignite.
- The 225 °F case gains nothing on overshoot, because its peak precedes the
  sample floor. Whether that floor can safely come down is unmeasured — and
  the 10-minute result above says lowering it without a stronger quality bar
  would make things worse, not better.
- The stronger bar itself is unbuilt and unmeasured. What the data shows is
  that one is needed and roughly where it sits (the harmful adoption was 62 %
  off, the benign ones 17–46 %); it does not show which statistic separates
  them. `identifiability` already exists and is the obvious candidate, but it
  passed at 10 minutes, so the floor would have to move rather than the metric.
