"""
A fresh look at why 325 F is not held on the realistic grill plant.

A9a measured, closed-loop over 3 h at 325 F, that the MPC holds the generic
`GrillSim` to 2.2 F of overshoot and 209 s of settling while on `MAKGrillSim`
-- the plant carrying a real MAK's identified parameters -- the best arm still
peaks 42.4 F high and never re-enters the 5 F band. That result was read as a
CONTROL failure. This asks the prior question nobody asked: with the duty
clamped to [u_min, u_max] and the fan where it actually sits, is 325 F a
temperature this grill can hold AT ALL?

WHAT IS MEASURED.

  R1 REACHABLE BAND. The steady-state chamber temperature of both plants at a
     fixed auger duty, over the fan range, from the plant's own energy balance
     -- heat in `u*H*eff` against `h_amb(fan)*(T_c-T_amb) + rad(T_c)`. The
     closed form is then VERIFIED against the shipped plant object itself: the
     simulator is placed at the predicted equilibrium and run an hour at the
     real 20 s pulse cycle, and its drift is reported. A closed form that is
     wrong shows up as drift.

  R2 ADMISSIBLE u_min. Inverted: for each common Hold setpoint, the largest
     u_min that still leaves the setpoint reachable, per plant and per fan.
     This is the number a config would have to respect, derived rather than
     picked.

  R3 BRAKING AND THE FAN. From a steady full-fire state the fuel is cut and the
     coast is measured at each fan setting, on both plants. The fan raises
     chamber loss (h_amb up to 1.6x) but also raises firepot->chamber transfer
     and the burn rate of fuel already in the pot, so its net worth as a brake
     is a measurement, not an inference.

  R4 RECOVERY ASYMMETRY. Placed 42.4 F above setpoint -- exactly A9a's observed
     MAK overshoot -- and firing at u_min, how long until the chamber falls
     back into the 5 F band? This is what an overshoot COSTS on this plant, and
     it is the number that decides whether prevention or recovery is worth
     engineering.

  R5 WHAT A SIMPLE CONTROLLER ACHIEVES. A deliberately crude alternative --
     feedforward duty plus proportional action on a LEAD-COMPENSATED error,
     `T + L*dT/dt`, which is the cheapest possible answer to a 100 s deadtime --
     run closed-loop on the same plants, same 3 h at 325 F, same seeds and the
     same metric code as A9a, over a FIXED (Kp, L) grid. Two feedforward arms:
     `ff` takes the steady duty from the plant's own equation (an oracle, so an
     upper bound on what any perfect steady-state model buys) and `pi` learns
     it with an integrator (implementable, no plant knowledge). Both are run at
     the harness's u_min=0.15 and at the shipped u_min=0.10, so the
     controller's contribution and the actuator's are separated.

  R6 HORIZON ARITHMETIC. The chamber time constant against the 600 s the
     controller plans over, and against the lead time R5 actually needed.

  R7 THE SHIPPED MPC AT A FEASIBLE OPERATING POINT. R1-R5 leave one inference
     open: that the MPC, not merely this study's crude controller, holds 325 F
     once the setpoint is inside the reachable band. That inference is measured
     here rather than assumed. The shipped MPC is run through A9a's own
     `controller_matrix.run_scenario`, on `MAKGrillSim`, over A9a's `steady_325`
     and A9a's seeds, at two `u_min` values -- and at ONE `HoldCycleTime`, so
     `u_min` is the single variable between the rows. (A9a moved both at once:
     it ran u_min=0.15 WITH HoldCycleTime=20, so its published numbers cannot
     attribute the difference and are not the comparison used here.)

WHAT IS DELIBERATELY NOT MEASURED.

  * R7 runs the UNCALIBRATED MPC only -- the shipped defaults, no refit, no
    adopted model. It is A9a's U3 arm, not its best F3 arm, because the F3 arm
    is the product of three successive cooks and a promotion gate. So R7
    answers "does the shipped controller reach and hold the setpoint once it is
    feasible", not "how well does a calibrated MPC track".
  * No parameter of the controller is changed and nothing under `controller/`
    is touched. Where a defect is found it is reported, not fixed.
  * Fan authority on `MAKGrillSim` is INHERITED, not fitted: the MAK
    identification run had the fan pinned at 100%, so R1's and R3's fan columns
    carry the base plant's fan-response shape. Every fan-dependent conclusion
    below is therefore structural, and the fan=1.0 column -- the one the A9a
    harness actually ran -- is the only one backed by the identification.
  * Lid events, setpoint steps and startup are out of scope; the scenario is
    A9a's `steady_325` exactly.

Usage:
    uv run python docs/superpowers/experiments/control_rethink.py \\
        > docs/superpowers/experiments/_control_rethink.txt
"""

import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

from controller.grill_sim import DT, GrillSim, MAKGrillSim  # noqa: E402

# ---------------------------------------------------------------------------
# Fixed run list. Nothing below is adaptive: no sweep extends itself, no loop
# runs until a threshold is met.
# ---------------------------------------------------------------------------

BUDGET_MIN = 60
SETPOINT_F = 325.0
DURATION_S = 10800  # 3 h, A9a's steady_325
SEEDS = (0, 1, 2)
FANS = (0.0, 0.25, 0.5, 0.75, 1.0)

# The A9a harness's cycle_data (docs/superpowers/experiments/controller_matrix.py:53).
HARNESS_CYCLE = {"HoldCycleTime": 20, "u_min": 0.15, "u_max": 0.9}
# The SHIPPED cycle_data (common/defaults.py). These differ, and the difference
# turns out to matter more than any controller parameter in this study.
SHIPPED_U_MIN = 0.10

R5_KP = (0.0005, 0.001, 0.002, 0.004)  # duty per F of error
R5_LEAD = (0.0, 100.0, 200.0, 400.0, 800.0)  # s of derivative lead
R5_TI = (900.0, 3600.0)  # s, integral time for the `pi` arm
R5_UMIN = (0.15, 0.10)

# R7: both rows at the SHIPPED cycle time, so u_min is the only variable.
R7_UMIN = (0.15, 0.10)
R7_CYCLE_S = 25
R7_WORKERS = 4  # another agent may be running the test suite; do not saturate

# R8: HoldCycleTime is not fixed -- it defaults to 25 s and may be raised. The
# user's stated ceiling is 60 s, and the range they report holding is 180-600 F.
R8_CYCLES = (20, 25, 30, 40, 60)
R8_SETPOINTS = (180.0, 200.0, 225.0, 325.0, 450.0, 520.0, 600.0)

PLANTS = {"GrillSim": GrillSim, "MAKGrillSim": MAKGrillSim}

# A9a's published MAK/GrillSim medians, quoted for comparison only.
A9A = {
    ("GrillSim", "MPC best (F3 cook 3)"): (98.10, 2.2, 327.2, "209", 32610),
    ("MAKGrillSim", "MPC best (F3 cook 3)"): (5.25, 42.4, 367.4, "never", 403478),
    ("MAKGrillSim", "MPC uncalibrated (U3)"): (0.21, 82.7, 407.7, "never", 646557),
}


def f_to_c(f):
    return (f - 32.0) * 5.0 / 9.0


def c_to_f(c):
    return c * 9.0 / 5.0 + 32.0


# ---------------------------------------------------------------------------
# R1 -- the plant's own steady-state energy balance.
#
# Transcribed from GrillSim.step: at equilibrium the chamber neither gains nor
# loses, so h_fc*(T_f-T_c) == h_amb*(T_c-T_amb) + rad, and the firepot balance
# makes that same transfer equal to `heat`. Averaged over an auger cycle the
# fuel burned equals the fuel fed, so mean burn == duty * feed_rate.
# ---------------------------------------------------------------------------


def _heat_in(plant, duty, fan):
    """Mean heat release at steady state, from the plant's own combustion terms."""
    burn = plant.feed_rate * duty  # steady state: burn rate == feed rate
    avail_air = 0.45 + 0.85 * fan
    needed_air = burn * 0.9 + 1e-6
    eff = min(max(avail_air / needed_air, 0.45), 1.0)
    return burn * plant.H * eff


def _loss(plant, T_c, fan):
    h_amb = plant.h_amb0 * (0.8 + 0.5 * fan)
    rad = plant.sigma * ((T_c + 273.15) ** 4 - (plant.T_amb + 273.15) ** 4)
    return h_amb * (T_c - plant.T_amb) + rad


def steady_T_c(plant, duty, fan):
    """Chamber temperature (C) at which the plant's losses balance its heat in."""
    Q = _heat_in(plant, duty, fan)
    lo, hi = plant.T_amb, 5000.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _loss(plant, mid, fan) < Q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def required_duty(plant, T_c, fan):
    """Duty whose mean heat release exactly balances losses at T_c."""
    need = _loss(plant, T_c, fan)
    # eff == 1.0 wherever avail_air exceeds needed_air, which holds for every
    # duty considered here; assert rather than assume.
    duty = need / plant.H
    assert (0.45 + 0.85 * fan) >= 0.9 * duty * plant.feed_rate, "efficiency clipped; invert numerically"
    return duty


# ---------------------------------------------------------------------------
# Plant driving -- the auger pulse the harness uses, replicated.
# ---------------------------------------------------------------------------


def _auger_frac(t, duty, cycle_s):
    """Fraction of this 1 s tick the auger runs, for a duty-cycled auger."""
    phase = t % cycle_s
    on = duty * cycle_s
    if phase + DT <= on:
        return 1.0
    if phase >= on:
        return 0.0
    return (on - phase) / DT


def run_fixed_duty(plant_cls, duty, fan, seconds, *, T0_c, seed=0, cycle_s=20):
    """Hold a fixed duty and fan; return (T_c at end, mean dT/dt over last 600 s)."""
    plant = plant_cls(seed=seed)
    plant.T_c = plant.T_f = plant.T_meas = float(T0_c)
    tail_start = None
    for t in range(seconds):
        plant.step(auger_on=_auger_frac(t, duty, cycle_s), fan_frac=fan)
        if t == seconds - 600:
            tail_start = plant.T_c
    drift = (plant.T_c - tail_start) / 600.0 if tail_start is not None else float("nan")
    return plant.T_c, drift


def run_coast(plant_cls, fan_hot, fan_coast, *, seed=0, charge_s=7200, coast_s=3600, cycle_s=20):
    """Charge at u_max, cut the fuel, and measure the coast."""
    plant = plant_cls(seed=seed)
    for t in range(charge_s):
        plant.step(auger_on=_auger_frac(t, HARNESS_CYCLE["u_max"], cycle_s), fan_frac=fan_hot)
    T_cut = plant.T_c
    peak, t_peak = T_cut, 0
    for t in range(coast_s):
        plant.step(auger_on=0.0, fan_frac=fan_coast)
        if plant.T_c > peak:
            peak, t_peak = plant.T_c, t + 1
    return T_cut, peak, t_peak, plant.T_c


def run_recovery(plant_cls, duty, fan, *, start_c, target_c, band_c, seed=0, limit_s=14400, cycle_s=20):
    """From an overshoot, fire at `duty` and time the return into the band."""
    plant = plant_cls(seed=seed)
    plant.T_c = plant.T_f = plant.T_meas = float(start_c)
    for t in range(limit_s):
        plant.step(auger_on=_auger_frac(t, duty, cycle_s), fan_frac=fan)
        if plant.T_c <= target_c + band_c:
            return t + 1
    return None


# ---------------------------------------------------------------------------
# R5 -- the crude alternative controller, and A9a's metric code.
# ---------------------------------------------------------------------------


def metrics(temps_f, setpoint_f):
    """A9a's definitions: % within 5 F, peak, overshoot, settle instant, IAE."""
    within = sum(1 for x in temps_f if abs(x - setpoint_f) <= 5.0)
    peak = max(temps_f)
    settle_from = None
    rise = None
    for t, x in enumerate(temps_f):
        if rise is None and x >= setpoint_f - 5.0:
            rise = t
        if abs(x - setpoint_f) <= 5.0:
            if settle_from is None:
                settle_from = t
        else:
            settle_from = None
    iae = sum(abs(x - setpoint_f) for x in temps_f)
    return {
        "pct": 100.0 * within / len(temps_f),
        "over": peak - setpoint_f,
        "peak": peak,
        "rise": rise,
        "settle": settle_from,
        "iae": iae,
    }


def run_simple(plant_cls, *, arm, Kp, lead_s, Ti, u_min, fan, seed, setpoint_f=SETPOINT_F, duration_s=DURATION_S):
    """
    Feedforward (or integral) duty plus proportional action on a lead-compensated
    error. Control period is the auger cycle; the slope is a two-point difference
    over one lead window of the LAGGED, NOISY probe, so no state of the plant is
    read that a real controller could not read.
    """
    plant = plant_cls(seed=seed)
    cycle_s = HARNESS_CYCLE["HoldCycleTime"]
    u_max = HARNESS_CYCLE["u_max"]
    sp_c = f_to_c(setpoint_f)
    u_ff = required_duty(plant, sp_c, fan) if arm == "ff" else u_min
    integ = 0.0
    hist = []  # (t, measured C)
    duty = u_max
    temps_f = []
    for t in range(duration_s):
        meas_f = c_to_f(plant.measured())
        if t % cycle_s == 0:
            hist.append((t, f_to_c(meas_f)))
            hist[:] = hist[-16:]
            slope = 0.0
            if lead_s > 0 and len(hist) >= 2:
                # slope over the shortest span that covers the lead window
                base = hist[0]
                for h in hist:
                    if t - h[0] <= lead_s:
                        base = h
                        break
                if t > base[0]:
                    slope = (hist[-1][1] - base[1]) / (t - base[0])
            err_c = sp_c - (hist[-1][1] + lead_s * slope)
            err_f = err_c * 9.0 / 5.0
            if arm == "pi":
                integ += err_f * cycle_s / Ti * Kp
                integ = min(max(integ, u_min - u_max), u_max)  # anti-windup
                duty = u_ff + integ + Kp * err_f
            else:
                duty = u_ff + Kp * err_f
            duty = min(max(duty, u_min), u_max)
        plant.step(auger_on=_auger_frac(t, duty, cycle_s), fan_frac=fan)
        temps_f.append(meas_f)
    return metrics(temps_f, setpoint_f)


# ---------------------------------------------------------------------------
# R7 -- the shipped MPC, through the matrix harness's explicit cycle seam.
# ---------------------------------------------------------------------------


def _mpc_run(arg):
    import contextlib
    import io

    seed, cycle_data = arg
    import tools.experiments.controller_matrix as cm

    # mpc.py prints an uncalibrated-model banner on construction; it belongs in
    # the record but not in the middle of a table.
    with contextlib.redirect_stdout(io.StringIO()):
        return cm.run_scenario("mpc", cm.SCENARIOS["steady_325"], seed, plant="MAKGrillSim", cycle_config=cycle_data)


def run_mpc_batch(u_min, cycle_s, seeds=SEEDS):
    from multiprocessing import Pool

    cycle = {"HoldCycleTime": cycle_s, "u_min": u_min, "u_max": HARNESS_CYCLE["u_max"]}
    with Pool(min(R7_WORKERS, len(seeds))) as pool:
        return pool.map(_mpc_run, [(seed, cycle) for seed in seeds])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def w(line=""):
    print(line)


def main():
    t0 = time.time()
    w("Control rethink -- is 325 F reachable on the realistic plant?")
    w("=" * 78)
    w()
    w("BUDGET AND RUN LIST (fixed before anything ran)")
    w(f"  budget      {BUDGET_MIN} minutes wall clock")
    w("  R1  reachable band: closed form + drift check on the shipped plant object")
    w("  R2  largest admissible u_min per setpoint, per plant, per fan")
    w("  R3  coast after fuel cut, per coast fan, vs the shipped brake estimator")
    w("  R4  time to fall back into the 5 F band from A9a's observed +42.4 F")
    w(
        f"  R5  crude FF/PI + lead controller, {len(R5_KP)}x{len(R5_LEAD)} grid, "
        f"{len(R5_TI)} Ti, u_min in {R5_UMIN}, seeds {SEEDS}"
    )
    w("  R6  horizon arithmetic")
    w(f"  R7  the SHIPPED uncalibrated MPC on MAKGrillSim, u_min in {R7_UMIN} at a")
    w(f"      fixed HoldCycleTime={R7_CYCLE_S}, seeds {SEEDS}, {R7_WORKERS} workers")
    w(f"  R8  reachable band vs HoldCycleTime in {R8_CYCLES}, and validation of the")
    w(f"      plant against the {R8_SETPOINTS[0]:.0f}-{R8_SETPOINTS[-1]:.0f} F range the user reports holding")
    w("  NOT run: a CALIBRATED MPC (R7 is A9a's U3 arm, not its F3 arm), lid")
    w("  events, setpoint steps, startup.")
    w()
    w("THE TWO u_min VALUES IN PLAY. common/defaults.py ships u_min=0.10 with a")
    w("25 s HoldCycleTime. The A9a harness (controller_matrix.py:53) runs")
    w("u_min=0.15 with a 20 s cycle. Every A9a number was measured at 0.15.")
    w()

    # ---------------- R1 ----------------
    w("R1 -- REACHABLE BAND")
    w("-" * 78)
    w("Steady chamber temperature at a fixed auger duty. `closed` is the energy")
    w("balance; `sim` places the shipped plant object at `closed`, runs it an hour")
    w("at the real 20 s pulse cycle and reports where it ended and how fast it was")
    w("still moving. A wrong closed form shows up as drift.")
    w()
    w(f"{'plant':<12}{'duty':>6}{'fan':>6}{'closed F':>10}{'sim F':>9}{'drift F/h':>11}")
    duty_rows = (SHIPPED_U_MIN, HARNESS_CYCLE["u_min"], 0.20, 0.30)
    for name, cls in PLANTS.items():
        probe = cls(seed=0)
        for duty in duty_rows:
            for fan in (0.0, 0.5, 1.0):
                T = steady_T_c(probe, duty, fan)
                T_end, drift = run_fixed_duty(cls, duty, fan, 3600, T0_c=T, seed=0)
                w(
                    f"{name:<12}{duty:>6.2f}{fan:>6.2f}{c_to_f(T):>10.1f}"
                    f"{c_to_f(T_end):>9.1f}{drift * 3600 * 9 / 5:>11.2f}"
                )
    w()
    w(f"Duty required to hold {SETPOINT_F:.0f} F, from the same balance:")
    w(f"{'plant':<12}" + "".join(f"{'fan ' + format(f, '.2f'):>11}" for f in FANS))
    for name, cls in PLANTS.items():
        probe = cls(seed=0)
        row = "".join(f"{required_duty(probe, f_to_c(SETPOINT_F), f):>11.4f}" for f in FANS)
        w(f"{name:<12}{row}")
    w()
    for name, cls in PLANTS.items():
        probe = cls(seed=0)
        for u_min, label in ((HARNESS_CYCLE["u_min"], "harness"), (SHIPPED_U_MIN, "shipped")):
            floors = [c_to_f(steady_T_c(probe, u_min, f)) for f in FANS]
            ok = [f for f, fl in zip(FANS, floors) if fl < SETPOINT_F]
            verdict = f"reachable at fan >= {min(ok):.2f}" if ok else "UNREACHABLE at every fan"
            w(
                f"  {name:<12} u_min={u_min:.2f} ({label}): floor "
                f"{min(floors):.1f}..{max(floors):.1f} F  ->  {SETPOINT_F:.0f} F {verdict}"
            )
    w()

    # ---------------- R2 ----------------
    w("R2 -- LARGEST ADMISSIBLE u_min")
    w("-" * 78)
    w("The duty that exactly balances losses at the setpoint IS the largest u_min")
    w("that leaves it holdable. Below the setpoint the grill can only wait; it has")
    w("no cooling actuator.")
    w()
    header = f"{'setpoint F':>11}" + "".join(f"{p + ' f' + format(f, '.1f'):>17}" for p in PLANTS for f in (0.0, 1.0))
    w(header)
    for sp in (200.0, 225.0, 250.0, 275.0, 300.0, 325.0, 350.0, 400.0, 450.0):
        cells = ""
        for name, cls in PLANTS.items():
            probe = cls(seed=0)
            for fan in (0.0, 1.0):
                cells += f"{required_duty(probe, f_to_c(sp), fan):>17.4f}"
        w(f"{sp:>11.0f}{cells}")
    w()
    w(
        f"Read the MAKGrillSim columns against the two u_min in play, {HARNESS_CYCLE['u_min']:.2f} and {SHIPPED_U_MIN:.2f}."
    )
    w()

    # ---------------- R3 ----------------
    w("R3 -- BRAKING, AND WHAT THE FAN IS WORTH AS A BRAKE")
    w("-" * 78)
    w("Charged 2 h at u_max with the fan at 1.0, then fuel cut. `rise F` is how")
    w("much further the chamber climbs after the cut; `t_peak s` is how long that")
    w("takes; `T+1h F` is where it has fallen to an hour later.")
    w()
    w(f"{'plant':<12}{'coast fan':>10}{'T_cut F':>9}{'rise F':>8}{'t_peak s':>10}{'T+1h F':>9}")
    coast = {}
    for name, cls in PLANTS.items():
        for fan_coast in (0.0, 0.5, 1.0):
            T_cut, peak, t_peak, T_end = run_coast(cls, 1.0, fan_coast, seed=0)
            coast[(name, fan_coast)] = (peak - T_cut, t_peak)
            w(
                f"{name:<12}{fan_coast:>10.2f}{c_to_f(T_cut):>9.1f}"
                f"{(peak - T_cut) * 9 / 5:>8.1f}{t_peak:>10d}{c_to_f(T_end):>9.1f}"
            )
    w()
    w()

    # ---------------- R4 ----------------
    w("R4 -- WHAT AN OVERSHOOT COSTS")
    w("-" * 78)
    w(f"Placed at {SETPOINT_F:.0f}+42.4 F (A9a's observed MAK overshoot) and firing at")
    w("u_min, time to fall back inside the 5 F band. `never` means not within 4 h.")
    w()
    w(f"{'plant':<12}{'u_min':>7}{'fan':>6}{'recovery s':>12}{'as min':>9}")
    for name, cls in PLANTS.items():
        for u_min in R5_UMIN:
            for fan in (0.0, 1.0):
                r = run_recovery(
                    cls,
                    u_min,
                    fan,
                    start_c=f_to_c(SETPOINT_F + 42.4),
                    target_c=f_to_c(SETPOINT_F),
                    band_c=5.0 * 5 / 9,
                    seed=0,
                )
                shown = f"{r}" if r is not None else "never"
                mins = f"{r / 60:.1f}" if r is not None else "-"
                w(f"{name:<12}{u_min:>7.2f}{fan:>6.2f}{shown:>12}{mins:>9}")
    w()

    # ---------------- R5 ----------------
    w("R5 -- WHAT A CRUDE CONTROLLER ACHIEVES")
    w("-" * 78)
    w("Same plants, same 3 h at 325 F, same seeds, same metric definitions as A9a.")
    w("`ff` gets its steady duty from the plant's own equation (an ORACLE -- an")
    w("upper bound on a perfect steady-state model, not an implementable arm);")
    w("`pi` learns the same duty with an integrator and knows nothing. Fan is")
    w("pinned at 1.0, exactly as the A9a harness pinned it. Medians over seeds.")
    w()
    w("`rise s` is the first crossing of setpoint-5 F: a controller cannot buy low")
    w("overshoot by simply refusing to heat without it showing here.")
    w()

    def _agg(runs):
        out = {}
        for k in ("pct", "over", "peak", "iae"):
            out[k] = statistics.median([r[k] for r in runs])
        for k in ("rise", "settle"):
            vals = [r[k] for r in runs if r[k] is not None]
            out[k] = statistics.median(vals) if len(vals) > len(runs) // 2 else None
        return out

    w(
        f"{'plant':<12}{'arm':>4}{'u_min':>7}{'Kp':>8}{'lead':>6}{'Ti':>7}"
        f"{'%<5F':>8}{'over F':>8}{'peak F':>8}{'rise s':>8}{'settle':>8}{'IAE':>10}"
    )
    best = {}
    for name, cls in PLANTS.items():
        for u_min in R5_UMIN:
            for arm in ("ff", "pi"):
                tis = R5_TI if arm == "pi" else (0.0,)
                for Kp in R5_KP:
                    for lead in R5_LEAD:
                        for Ti in tis:
                            m = _agg(
                                [
                                    run_simple(cls, arm=arm, Kp=Kp, lead_s=lead, Ti=Ti, u_min=u_min, fan=1.0, seed=s)
                                    for s in SEEDS
                                ]
                            )
                            key = (name, u_min, arm)
                            if key not in best or m["iae"] < best[key][0]["iae"]:
                                best[key] = (m, Kp, lead, Ti)
    for name in PLANTS:
        for u_min in R5_UMIN:
            for arm in ("ff", "pi"):
                m, Kp, lead, Ti = best[(name, u_min, arm)]
                settle = "never" if m["settle"] is None else f"{m['settle']:.0f}"
                rise = "never" if m["rise"] is None else f"{m['rise']:.0f}"
                ti = f"{Ti:.0f}" if arm == "pi" else "-"
                w(
                    f"{name:<12}{arm:>4}{u_min:>7.2f}{Kp:>8.4f}{lead:>6.0f}{ti:>7}"
                    f"{m['pct']:>8.2f}{m['over']:>8.1f}{m['peak']:>8.1f}{rise:>8}{settle:>8}{m['iae']:>10.0f}"
                )
    w()
    w("Best row per (plant, u_min, arm) by IAE, out of the fixed grid above. A9a's")
    w("published MPC medians, for the same scenario and metric code:")
    w()
    w(f"{'plant':<12}{'arm':<24}{'%<5F':>8}{'over F':>8}{'peak F':>8}{'settle':>8}{'IAE':>10}")
    for (plant, arm), (pct, over, peak, settle, iae) in A9A.items():
        w(f"{plant:<12}{arm:<24}{pct:>8.2f}{over:>8.1f}{peak:>8.1f}{settle:>8}{iae:>10d}")
    w("(A9a ran u_min=0.15. Compare it to the u_min=0.15 rows, not the 0.10 rows.)")
    w()

    # ---------------- R6 ----------------
    w("R6 -- HORIZON ARITHMETIC")
    w("-" * 78)
    w(f"{'plant':<12}{'fan':>6}{'tau s':>9}{'tau min':>9}{'600s/tau':>10}")
    for name, cls in PLANTS.items():
        probe = cls(seed=0)
        for fan in (0.0, 1.0):
            h_amb = probe.h_amb0 * (0.8 + 0.5 * fan)
            tau = probe.C_c / h_amb
            w(f"{name:<12}{fan:>6.2f}{tau:>9.0f}{tau / 60:>9.1f}{600 / tau:>10.3f}")
    w()
    w("The best lead time R5 found is in the `lead` column above; read it against")
    w("the plant deadtime (GrillSim 20 s, MAKGrillSim 100 s) and against 600 s.")
    w()

    # ---------------- R7 ----------------
    w("R7 -- THE SHIPPED MPC AT A FEASIBLE OPERATING POINT")
    w("-" * 78)
    w("A9a's own harness, A9a's scenario and seeds, MAKGrillSim, the UNCALIBRATED")
    w("shipped MPC. HoldCycleTime is held at the shipped 25 s in BOTH rows, so")
    w("u_min is the only variable between them. Medians over seeds.")
    w()
    w(
        f"{'u_min':>7}{'floor F':>9}{'%<5F':>8}{'over F':>8}{'peak F':>8}"
        f"{'settle':>8}{'final F':>9}{'duty':>8}{'IAE':>10}"
    )
    probe = MAKGrillSim(seed=0)
    for u_min in R7_UMIN:
        rows = run_mpc_batch(u_min, R7_CYCLE_S)
        med = {}
        for k in ("pct_within_5f", "overshoot_f", "final_temp_f", "mean_duty", "iae"):
            med[k] = statistics.median([r[k] for r in rows])
        settles = [r["settle_s"] for r in rows if r["settle_s"] is not None]
        settle = f"{statistics.median(settles):.0f}" if len(settles) > len(rows) // 2 else "never"
        floor = c_to_f(steady_T_c(probe, u_min, 1.0))
        w(
            f"{u_min:>7.2f}{floor:>9.1f}{med['pct_within_5f']:>8.2f}{med['overshoot_f']:>8.1f}"
            f"{SETPOINT_F + med['overshoot_f']:>8.1f}{settle:>8}{med['final_temp_f']:>9.1f}"
            f"{med['mean_duty']:>8.4f}{med['iae']:>10.0f}"
        )
    w()
    w("`floor F` is R1's minimum-firing-rate equilibrium at fan 1.0, repeated here")
    w("so the row can be read against what the actuator allows. `final F` is where")
    w("the 3 h run ended; on an infeasible row it cannot approach the setpoint.")
    w("The uncalibrated-model banner mpc.py prints on construction is suppressed")
    w("inside the workers so it does not land in the table; it fires on every run.")
    w()

    # ---------------- R8 ----------------
    w("R8 -- DUTY RESOLUTION: THE REACHABLE BAND AGAINST HoldCycleTime")
    w("-" * 78)
    w("A duty-cycled auger cannot ask for an arbitrarily small duty. The harness")
    w("asserts `cycle_time*ratio >= 1 and cycle_time*(1-ratio) >= 1`")
    w("(controller_matrix.py:169), so the smallest duty it can express is")
    w("1/HoldCycleTime. THAT BOUND IS A HARNESS ARTEFACT, NOT A PHYSICAL ONE:")
    w("`_auger_toggle_tick`'s own docstring says production evaluates the toggle")
    w("at ~20 Hz onto an auger that integrates fuel continuously, so the shipped")
    w("resolution is far finer than 1 s. The real floor is whatever the auger")
    w("mechanism and pellet delivery impose, and NOBODY HAS MEASURED IT. The")
    w("table below is therefore the band this SIMULATION can express, and it is")
    w("an upper bound on the restriction a real grill suffers.")
    w()
    w("The binding minimum duty is max(u_min setting, 1/HoldCycleTime); the rows")
    w("below set u_min to the resolution floor, so they isolate resolution.")
    w()
    w(f"{'plant':<12}{'cycle s':>8}{'min duty':>10}{'min F f0':>10}{'min F f1':>10}{'max F f0':>10}{'max F f1':>10}")
    for name, cls in PLANTS.items():
        probe = cls(seed=0)
        for cyc in R8_CYCLES:
            u_res = 1.0 / cyc
            w(
                f"{name:<12}{cyc:>8d}{u_res:>10.4f}"
                f"{c_to_f(steady_T_c(probe, u_res, 0.0)):>10.1f}"
                f"{c_to_f(steady_T_c(probe, u_res, 1.0)):>10.1f}"
                f"{c_to_f(steady_T_c(probe, HARNESS_CYCLE['u_max'], 0.0)):>10.1f}"
                f"{c_to_f(steady_T_c(probe, HARNESS_CYCLE['u_max'], 1.0)):>10.1f}"
            )
    w()
    w("Duty MAKGrillSim needs across the range the user reports this grill holds:")
    w(f"{'setpoint F':>11}{'fan 0.0':>10}{'fan 1.0':>10}   smallest cycle s that expresses it")
    probe = MAKGrillSim(seed=0)
    for sp in R8_SETPOINTS:
        u0 = required_duty(probe, f_to_c(sp), 0.0)
        u1 = required_duty(probe, f_to_c(sp), 1.0)
        # The setpoint is holdable when SOME fan setting needs at least the
        # resolution floor; more fan raises the duty needed, so fan 1.0 is the
        # easiest case at the bottom of the range.
        ok = [c for c in R8_CYCLES if 1.0 / c <= max(u0, u1) <= HARNESS_CYCLE["u_max"]]
        note = f"{min(ok)} s" if ok else f"none <= {max(R8_CYCLES)} s"
        w(f"{sp:>11.0f}{u0:>10.4f}{u1:>10.4f}   {note}")
    w()
    w("More fan RAISES the duty a low setpoint needs, so at the bottom of the")
    w("range the fan is what makes a setpoint expressible. That is the opposite")
    w("of a brake: it is operating range.")
    w()
    w("VALIDATION AGAINST A KNOWN REAL DATUM. The user reports this physical")
    w(f"grill is controllable from {R8_SETPOINTS[0]:.0f} F to {R8_SETPOINTS[-1]:.0f} F, and the logged cook")
    w("reached 520 F. A plant model that cannot express that range would be")
    w("miscalibrated, the way H=140 was before it was corrected to 420.")
    lo, hi = R8_SETPOINTS[0], R8_SETPOINTS[-1]
    u_lo = max(required_duty(probe, f_to_c(lo), 0.0), required_duty(probe, f_to_c(lo), 1.0))
    u_hi = max(required_duty(probe, f_to_c(hi), 0.0), required_duty(probe, f_to_c(hi), 1.0))
    lo_ok = [c for c in R8_CYCLES if 1.0 / c <= u_lo]
    w(
        f"  {lo:.0f} F needs duty {u_lo:.4f} (fan 1.0) -- expressible from "
        f"{min(lo_ok) if lo_ok else '>' + str(max(R8_CYCLES))} s cycle upward"
    )
    w(f"  {hi:.0f} F needs duty {u_hi:.4f} (fan 1.0) -- against u_max {HARNESS_CYCLE['u_max']:.2f}")
    verdict = "REPRODUCES" if lo_ok and u_hi <= HARNESS_CYCLE["u_max"] else "CANNOT REPRODUCE"
    w(f"  verdict: MAKGrillSim {verdict} the user's stated {lo:.0f}-{hi:.0f} F range.")
    w()
    w("WALL CLOCK")
    w("-" * 78)
    w(f"  total {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
