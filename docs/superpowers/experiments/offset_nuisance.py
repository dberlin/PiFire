#!/usr/bin/env python3

"""Is a per-cook nuisance parameter worth a free parameter?

A parameter-parsimony exploration found that freeing a constant disturbance `d`,
or freeing `T_amb`, cuts the per-cook ORACLE fit error on the MAK plant from
1.860 C to 0.597 C while barely moving the joint fit. The online refit fits one
cook at a time, so the oracle column is the relevant one, and the conclusion
drawn was "most inter-cook variation is an offset".

That conclusion rests on an IN-SAMPLE number, and docs/superpowers/experiments/
_promotion_signal.txt measured in-sample RMSE to carry almost no information
about whether a model is right -- Spearman -0.160 against truth error over the
in-scope population. This measures whether the offset buys anything the
controller can actually use.

WHICH ARM IS THE EXPLORATION'S MOVE. The exploration's `d` is a constant HEAT
offset inside the chamber power balance -- mpc_model.py's documented dynamics
puts it there, `dT_c/dt = (K_Q*heat_in - h_amb*(T_c - T_amb) - rad + d)/C_c` --
and not an offset on the predicted temperature. That is why its table reports
`full_d` and `full_Tamb` identical to three decimals rather than merely close: a
constant heat `d` and a shifted ambient are the SAME perturbation of that
balance, related by `d = h_amb * (T_amb shift)` up to the radiative term, which
at the shipped sigma=1.4e-09 is negligible. So ARM C BELOW IS THE FAITHFUL
REPRODUCTION of the exploration's move, and arm B is a different parameter: a
per-cook offset on the temperature itself, which is the cheapest thing an
implementer reaching for "absorb the level" would actually write. Both are
measured because both are live options; only arm C answers "does the
exploration's finding transfer".

WHAT IS MEASURED. Three arms, over the same record population
promotion_signal.py builds -- the two plants in controller/grill_sim.py across
eleven excitation profiles and every truncation length at or above the refit
floor, plus the real MAK cook and the flat cook tests/unit/mpc/test_mpc_refit.py
pins:

  A  shipped     free = (K_Q, C_c, theta), T_amb held at 20 C. The control,
                 which is update_mpc.fit_params called unmodified.
  B  offset      the same three, plus an additive offset d on the predicted
                 TEMPERATURE, in degrees C. Four free parameters. NOT the
                 exploration's d, which is a heat offset -- see above.
  C  free ambient  the same three, plus T_amb. Four free parameters. Equivalent
                 to the exploration's constant heat offset, as above.

Arms B and C reproduce fit_params exactly -- same starting point, same log-space
solve for the three scales, same trust-region method, same evaluation budget,
same finite guard and the same _DIVERGED residual for an unsimulable point --
and differ only by the fourth column. d is fitted in LINEAR degrees C with no
lower bound, because it is a signed level and not a positive scale; T_amb is
fitted in log space like every other positive parameter, which is also the space
its identifiability would be read in.

Each arm and record carries:

  * IN-SAMPLE RMSE with the nuisance kept. This is the oracle column the claim
    came from, and the number today's gate compares.
  * IN-SAMPLE RMSE with the nuisance discarded, so the level bias the offset was
    absorbing is visible as a quantity rather than inferred.
  * TRUTH ERROR WITH THE NUISANCE DISCARDED -- the fitted model's prediction of
    the plant's behaviour on two profiles no fit ever saw. This is the number
    that decides the question, and it is scored with the nuisance thrown away
    because that is how the model would be used: d has nowhere to live (there is
    no such field in Controller._MODEL_PARAM_KEYS) and a truth error that keeps
    it describes a controller that will never run. Arm C additionally carries a
    truth error with T_amb KEPT, because unlike d, T_amb IS in _MODEL_PARAM_KEYS
    and would genuinely cross into the config -- that asymmetry is the whole of
    the d-or-T_amb question and it is measured rather than argued.
  * HELD-OUT RMSE, nuisance discarded. The arm's own fitter is re-run on the
    first two thirds of the record and the result scored on the last third,
    warm: run through the whole record from its true start so the transport
    chain arrives charged, scored on the suffix only. Needs no plant, so it is
    the one generalisation measure the real MAK cook can carry.
  * s_min over that arm's free set, and whether the record clears
    model_promotion._IDENTIFIABILITY_FLOOR under it.
  * The fitted nuisance itself, so its size and its spread across cooks are
    visible rather than assumed.

WHAT THIS DELIBERATELY DOES NOT DO. It changes no decision path and edits
nothing under controller/. It does not score arm B or arm C by any metric the
nuisance is free to fit: in-sample RMSE is reported for both arms because it is
the claim under test, but every comparison the recommendation rests on is a
truth error or a held-out error taken AFTER the nuisance is discarded. It reads
_IDENTIFIABILITY_FLOOR to count records against it -- that constant shipped two
tasks ago and is not derived here -- and derives no threshold of its own. It
does not use MAKGrillSim as ground truth for the real MAK cook, for the reason
promotion_signal.py gives: that plant was identified FROM that cook, so the real
cook is reported with no truth column at all and its generalisation question is
answered by the held-out column instead.

THE POPULATION IS IN-SCOPE ONLY. controller/mpc.py refuses a refit below
mpc._REFIT_MIN_SAMPLES rows before the gate is reached, so the truncations
promotion_signal.py prints as out of scope are not fitted here at all. That is
the only thing dropped, it is dropped on the live controller's own rule, and
both the count and which lengths they are are printed below, derived from the
records rather than asserted.

A NOTE ON UNITS, because it decides how arms B and C compare. s_min under arm A
is degrees C RMS per e-fold of a log-parameter direction. ARM B ONLY: its fourth
column is dimensionless -- one degree of prediction per degree of offset -- so
after the 1/sqrt(N) normalisation its column norm is exactly 1, and therefore
s_min = min over unit x of ||Jx|| <= ||J e_d|| = 1.0 on every record that can
exist, not merely on the ones measured here. That bound is exact RELATIVE TO d
IN LINEAR DEGREES C: rescaling the parameter rescales the bound with it, so it
is a statement about this parameterisation and not a law about offsets. It does
NOT carry over to the exploration's heat offset, whose column is 1/C_c times a
step response rather than ones. Arm C's fourth column in log space is
dT/dlog(T_amb) ~ T_amb, about twenty times the same physical move, which would
flatter arm C purely by choice of units. Both are printed for arm C: the log one
because that is the space the shipped floor is expressed in, and a per-degree
one directly comparable to arm B's.

Usage:
  uv run python -m docs.superpowers.experiments.offset_nuisance

Set OFFSET_NUISANCE_WORKERS to change the pool size; it defaults to half the
cores, because this repo's suite carries wall-clock budget assertions that a
saturated machine breaks. No numba: the cost here is scipy least-squares calls
inside an existing simulator, which numba would not touch.
"""

import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from scipy.optimize import least_squares

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from controller.mpc import _REFIT_INIT, _REFIT_MIN_SAMPLES  # noqa: E402
from controller.mpc_model import simulate_grey_box  # noqa: E402
from controller.model_promotion import _IDENTIFIABILITY_FLOOR  # noqa: E402
from controller.update_mpc import _DIVERGED, _FREE, _LOWER_BOUND, _MAX_NFEV, _SIM_KEYS  # noqa: E402
from tools.experiments import promotion_signal as ps  # noqa: E402

#: The three arms, in the order every table prints them.
ARMS = ("A_shipped", "B_offset", "C_free_amb")

#: Where a record is split for the held-out measurement. promotion_signal.py's
#: value, so the two experiments' held-out columns are the same measurement.
SPLIT_FRAC = ps.SPLIT_FRAC

#: The ambient the shipped fitter holds, and the value arm C starts from and arm
#: A never leaves.
T_AMB = ps.T_AMB


# ------------------------------------------------------------------- fitting
def _sim_arm(params, t, Q, T0):
    """The arm's trajectory: the grey box, plus this arm's nuisance if it has one.

    `params` carries T_amb (arm C moves it, the others hold it) and may carry
    `d`, which is added to every sample. Returns None where the model cannot be
    simulated, in either of the two shapes update_mpc.fit_params describes -- a
    raised OverflowError out of the chamber's float arithmetic and a quiet NaN
    out of numpy.
    """
    p = {k: float(params[k]) for k in _SIM_KEYS}
    p["n_delay"] = int(round(float(params["n_delay"])))
    try:
        y = simulate_grey_box(t, Q, T_amb=float(params["T_amb"]), T0=float(T0), **p)
    except OverflowError:
        return None
    if not np.all(np.isfinite(y)):
        return None
    return y + float(params.get("d", 0.0))


def _rmse(params, t, Q, T0, target, sl=slice(None)):
    """RMSE of `params` over `sl`, or inf where it cannot be simulated."""
    y = _sim_arm(params, t, Q, T0)
    if y is None:
        return float("inf")
    return float(np.sqrt(np.mean((y[sl] - target) ** 2)))


def discard_nuisance(params):
    """The model as the controller would actually carry it forward.

    d is dropped: there is no such field in Controller._MODEL_PARAM_KEYS, so a
    per-cook offset has nowhere to be stored and is a nuisance by construction.
    T_amb is returned to the value the shipped fitter holds it at, so arm C is
    scored on the same footing -- what its three DYNAMIC parameters learned,
    once the level freedom that helped them fit is taken away again.
    """
    out = dict(params)
    out.pop("d", None)
    out["T_amb"] = T_AMB
    return out


def fit_arm(arm, t, y, Q):
    """Fit one arm to a record. update_mpc.fit_params with an optional 4th column.

    Arm A delegates to the shipped fitter itself rather than to a reproduction
    of it, so the control cannot drift from what ships. Arms B and C reproduce
    it: same `_REFIT_INIT` starting point, same log-space solve over `_FREE`,
    same `trf`, same `_MAX_NFEV`, same `_DIVERGED` residual at an unsimulable
    point, same convergence rule -- scipy reporting a criterion met AND the
    result being simulable.
    """
    if arm == "A_shipped":
        out = ps.shipped_fit(t, y, Q)
        out["d"] = 0.0
        return out

    y = np.asarray(y, dtype=float)
    held = {k: float(dict(_REFIT_INIT, sigma=ps.SIGMA)[k]) for k in ("h_amb", "sigma")}
    lo = math.log(_LOWER_BOUND)
    x0 = [math.log(float(_REFIT_INIT[k])) for k in _FREE]
    if arm == "B_offset":
        # A signed level in degrees C, not a positive scale, so it is fitted
        # linearly and unbounded. Starting at zero is the shipped model: the
        # solve is asked what the offset buys over having none.
        x0.append(0.0)
        bounds = ([lo] * len(_FREE) + [-np.inf], [np.inf] * (len(_FREE) + 1))
    elif arm == "C_free_amb":
        x0.append(math.log(T_AMB))
        bounds = ([lo] * (len(_FREE) + 1), [np.inf] * (len(_FREE) + 1))
    else:
        raise ValueError(arm)
    x0 = np.array(x0, dtype=float)

    def unpack(z):
        params = dict(held, n_delay=ps.N_DELAY, T_amb=T_AMB, d=0.0)
        params.update(zip(_FREE, (math.exp(v) for v in z[: len(_FREE)])))
        if arm == "B_offset":
            params["d"] = float(z[-1])
        else:
            params["T_amb"] = math.exp(float(z[-1]))
        return params

    def residual(z):
        sim = _sim_arm(unpack(z), t, Q, float(y[0]))
        if sim is None:
            return np.full_like(y, _DIVERGED)
        return sim - y

    res = least_squares(residual, x0, method="trf", bounds=bounds, max_nfev=_MAX_NFEV)
    out = unpack(res.x)
    out["converged"] = bool(res.status > 0) and _sim_arm(out, t, Q, float(y[0])) is not None
    out["nfev"] = int(res.nfev)
    return out


def arm_svals(arm, t, Q, fitted, T0):
    """Singular values of d(prediction)/d(parameter) over the arm's free set, per sample.

    The first three columns are promotion_signal.log_svals' columns -- degrees C
    RMS per e-fold of log K_Q, log C_c, log theta -- taken at THIS arm's fitted
    point, which is not arm A's. The fourth column is the arm's nuisance.

    Returns (s_min in the arm's own units, s_min with the nuisance column in
    per-degree units). For arms A and B the two are the same number: A has no
    fourth column, and B's is already per-degree -- d moves the prediction one
    degree per degree, so that column is exactly ones and its norm is exactly 1,
    which is why arm B's s_min cannot exceed 1.0 on any record whatsoever. They
    differ for arm C, where dT/dlog(T_amb) is about T_amb times dT/dT_amb: the
    log reading is the one comparable to the shipped floor's units and the
    per-degree one is the one comparable to arm B.
    """
    h = 1e-3
    cols = []
    for key in _FREE:
        base = float(fitted[key])
        if not (base > 0.0 and math.isfinite(base)):
            return None, None
        up = _sim_arm(dict(fitted, **{key: base * math.exp(h)}), t, Q, T0)
        dn = _sim_arm(dict(fitted, **{key: base * math.exp(-h)}), t, Q, T0)
        if up is None or dn is None:
            return None, None
        cols.append((up - dn) / (2.0 * h))

    def smallest(extra):
        J = np.column_stack(cols + ([] if extra is None else [extra])) / math.sqrt(len(t))
        return float(np.linalg.svd(J, compute_uv=False)[-1])

    if arm == "A_shipped":
        s = smallest(None)
        return s, s
    if arm == "B_offset":
        s = smallest(np.ones(len(t)))
        return s, s
    base = float(fitted["T_amb"])
    if not (base > 0.0 and math.isfinite(base)):
        return None, None
    up = _sim_arm(dict(fitted, T_amb=base * math.exp(h)), t, Q, T0)
    dn = _sim_arm(dict(fitted, T_amb=base * math.exp(-h)), t, Q, T0)
    if up is None or dn is None:
        return None, None
    d_dlog = (up - dn) / (2.0 * h)
    return smallest(d_dlog), smallest(d_dlog / base)


# ------------------------------------------------------------ one measurement
def measure(rec):
    """Every arm's numbers for one record. Runs in a worker process."""
    t, y, Q = rec["t"], rec["y"], rec["Q"]
    n = len(t)
    out = dict(plant=rec["plant"], profile=rec["profile"], length_s=rec["length_s"], n=n)
    k = max(2, int(round(n * SPLIT_FRAC)))
    ts, ys = t[k:], y[k:]
    scorable = len(ts) >= 2

    for arm in ARMS:
        fitted = fit_arm(arm, t, y, Q)
        bare = discard_nuisance(fitted)
        row = {
            "converged": bool(fitted["converged"]),
            "nfev": int(fitted["nfev"]),
            "fit": {key: float(bare[key]) for key in ps.MODEL_KEYS},
            # The nuisance as fitted. For arm C it is reported as the ambient
            # itself and as the level shift it amounts to, so it is on the same
            # axis as arm B's d.
            "d": float(fitted.get("d", 0.0)),
            "T_amb": float(fitted["T_amb"]),
            "nuis": float(fitted.get("d", 0.0)) if arm == "B_offset" else float(fitted["T_amb"]) - T_AMB,
            # The oracle column: what the claim under test improved.
            "insample": _rmse(fitted, t, Q, float(y[0]), y),
            # The same record, scored by the model that will actually be kept.
            "insample_bare": _rmse(bare, t, Q, float(y[0]), y),
        }
        s_own, s_perdeg = arm_svals(arm, t, Q, fitted, float(y[0]))
        row["s_min"] = float("nan") if s_own is None else s_own
        row["s_min_perdeg"] = float("nan") if s_perdeg is None else s_perdeg

        # Truth, with the nuisance discarded. The number that decides the task.
        if rec["plant"] is not None:
            row["truth"], row["dead_err"], row["coast_err"] = ps.truth_error(bare, rec["plant"])
            # Arm C only: T_amb is in _MODEL_PARAM_KEYS, so unlike d it could
            # genuinely be carried forward. Reported so the choice between the
            # two nuisances is decided by a number rather than by taste.
            row["truth_kept"] = ps.truth_error(fitted, rec["plant"])[0] if arm == "C_free_amb" else float("nan")
        else:
            row["truth"] = row["truth_kept"] = float("nan")
            row["dead_err"] = row["coast_err"] = float("nan")

        # Held out, nuisance discarded: the arm's fitter on the prefix, scored
        # on the suffix it never saw. Plant-free, so the real cook has it too.
        if scorable:
            pre = fit_arm(arm, t[:k], y[:k], Q[:k])
            row["pre_nuis"] = float(pre.get("d", 0.0)) if arm == "B_offset" else float(pre["T_amb"]) - T_AMB
            row["ho_warm"] = _rmse(discard_nuisance(pre), t, Q, float(y[0]), ys, slice(k, None))
        else:
            row["pre_nuis"] = float("nan")
            row["ho_warm"] = float("inf")
        out[arm] = row
    return out


def _job(rec):
    return measure(rec)


# ------------------------------------------------------------------ analysis
def finite(vals):
    return [v for v in vals if v is not None and math.isfinite(v)]


def median(vals):
    vals = sorted(finite(vals))
    if not vals:
        return float("nan")
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else 0.5 * (vals[m - 1] + vals[m])


def q(vals, frac):
    vals = sorted(finite(vals))
    if not vals:
        return float("nan")
    return vals[min(len(vals) - 1, max(0, int(round(frac * (len(vals) - 1)))))]


def fmt(v, w=8, p=3):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return f"{'--':>{w}s}"
    if isinstance(v, float) and math.isinf(v):
        return f"{'inf':>{w}s}"
    return f"{v:>{w}.{p}f}"


def paired(rows, arm, base, key, base_key=None):
    """(n compared, wins for `arm`, ties, losses, median delta, worst regression).

    `base_key` differs from `key` only for arm C's kept-T_amb column, which has
    no counterpart on arm A and is therefore compared against arm A's ordinary
    truth error -- the model arm A would actually have produced.
    """
    deltas = []
    for r in rows:
        a, b = r[arm][key], r[base][base_key or key]
        if math.isfinite(a) and math.isfinite(b):
            deltas.append(a - b)
    if not deltas:
        return 0, 0, 0, 0, float("nan"), float("nan")
    wins = sum(1 for d in deltas if d < -1e-9)
    ties = sum(1 for d in deltas if abs(d) <= 1e-9)
    return len(deltas), wins, ties, len(deltas) - wins - ties, median(deltas), max(deltas)


def main():
    started = time.time()
    workers = int(os.environ.get("OFFSET_NUISANCE_WORKERS", max(1, (os.cpu_count() or 2) // 2)))

    def say(s=""):
        print(s)

    say("=" * 108)
    say("OFFSET NUISANCE -- does a fourth free parameter buy a better model, or only a better fit?")
    say("=" * 108)
    say(f"shipped fitter : _REFIT_INIT={dict(_REFIT_INIT)} sigma={ps.SIGMA:g} n_delay={ps.N_DELAY} free={list(_FREE)}")
    say("arms           : A_shipped (3 free)  B_offset (+d on TEMPERATURE, linear C)  C_free_amb (+T_amb, log)")
    say(
        "                 the parsimony exploration's `d` is a constant HEAT offset in the power balance,"
        "\n                 so ARM C reproduces its move (d = h_amb * ambient shift, up to a negligible"
        "\n                 radiative term); ARM B is the different, cheaper thing an implementer would write."
    )
    say(f"held-out split : {SPLIT_FRAC:.4f} of the record, scored warm on the suffix")
    say(
        f"floor read     : model_promotion._IDENTIFIABILITY_FLOOR = {_IDENTIFIABILITY_FLOOR:g} (imported, not derived here)"
    )

    # ---------------------------------------------------------- the population
    cuts = []
    for plant in ("mak", "generic"):
        for profile in ps.profiles():
            cuts.extend(ps.truncations(ps.plant_record(plant, profile)))
    for rec in [ps.real_cook(), ps.flat_synthetic(0.05), ps.flat_synthetic(0.15)]:
        cuts.extend(ps.truncations(rec))
    cuts, collapsed = ps.deduplicate(cuts)
    jobs = [c for c in cuts if len(c["t"]) >= _REFIT_MIN_SAMPLES]
    dropped = len(cuts) - len(jobs)
    say()
    say(
        f"--- population: {len(cuts)} distinct records ({len(collapsed)} collapsed as byte-identical), "
        f"{dropped} dropped below the {_REFIT_MIN_SAMPLES}-sample refit floor, {len(jobs)} fitted ---"
    )
    dropped_lengths = sorted({c["length_s"] for c in cuts if len(c["t"]) < _REFIT_MIN_SAMPLES})
    kept_lengths = sorted({c["length_s"] for c in jobs})
    say(
        f"    Dropped truncation lengths, read off the records rather than assumed: {dropped_lengths} s."
        f"\n    Kept: {kept_lengths} s. controller/mpc.py refuses a refit shorter than {_REFIT_MIN_SAMPLES}"
        "\n    samples before the gate is reached, so the dropped ones produce no verdict to be right about."
        "\n    Nothing else is dropped and no arm is measured on a smaller population than another."
    )
    say(f"--- fitting {len(jobs)} records x {len(ARMS)} arms x 2 (full + prefix) on {workers} workers ---")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_job, jobs, chunksize=1))
    say(f"    done at t+{time.time() - started:.0f}s")

    sim_rows = [r for r in rows if r["plant"] is not None]
    real_rows = sorted([r for r in rows if r["profile"] == "real_mak_cook"], key=lambda r: r["length_s"])
    say(
        f"    {len(sim_rows)} carry a truth error (a plant behind them); the {len(rows) - len(sim_rows)} that do not"
        "\n    are the real MAK cook and the flat synthetic cooks, which are read on held-out RMSE only."
    )

    # ------------------------------------------------------------ self-checks
    say()
    say("--- harness self-checks ------------------------------------------------------------------")
    rc = real_rows[-1]
    recorded = dict(C_c=3591.95, theta=111.32, K_Q=9.9208)
    a = rc["A_shipped"]["fit"]
    say(
        "1. arm A on the real cook reproduces tests/unit/mpc/test_model_promotion.REAL_MAK_FIT: "
        + " ".join(f"{k}={a[k]:.5g}(rec {v:g})" for k, v in recorded.items())
    )
    say(f"   worst relative disagreement {max(abs(a[k] / v - 1.0) for k, v in recorded.items()):.2e}")
    say(
        "   -- arm A is update_mpc.fit_params called unmodified, so this pins the control to the shipped"
        "\n      fitter. If it drifts, nothing below is about the code that ships."
    )
    # The offset is separable: at any fixed dynamics the d minimising the sum of
    # squares is exactly minus the mean residual, which makes
    # rmse(discarded)^2 - rmse(kept)^2 identically d^2. If the 4-parameter solve
    # reached its own minimum in d, that identity holds to solver precision.
    # This checks the solve rather than assuming it, on every record at once.
    worst = (0.0, "")
    for r in rows:
        b = r["B_offset"]
        if not (b["converged"] and math.isfinite(b["insample"]) and math.isfinite(b["insample_bare"])):
            continue
        gap = b["insample_bare"] ** 2 - b["insample"] ** 2
        err = abs(math.sqrt(max(gap, 0.0)) - abs(b["d"]))
        if err > worst[0]:
            worst = (err, f"{r['plant']}/{r['profile']}/{r['length_s']}s")
    say(
        f"2. arm B's solve reaches the separable optimum in d on every converged record: worst departure"
        f"\n   from sqrt(rmse_discarded^2 - rmse_kept^2) == |d| is {worst[0]:.2e} C, at {worst[1] or 'none'}"
    )
    say(
        "   -- d enters the model linearly, so that identity is exact at the optimum. A departure would"
        "\n      mean the 4-parameter solve stopped short of its own best d and arm B was understated,"
        "\n      which would bias this experiment TOWARDS the answer it is being asked to challenge."
    )
    nc = {arm: sum(1 for r in rows if not r[arm]["converged"]) for arm in ARMS}
    say("3. solves that did not converge, by arm: " + "  ".join(f"{k}={v}" for k, v in nc.items()))
    say(
        f"4. arm C's T_amb ran to a bound on {sum(1 for r in rows if r['C_free_amb']['T_amb'] < 1.0)} records"
        f" (fitted below 1 C) and above 40 C on {sum(1 for r in rows if r['C_free_amb']['T_amb'] > 40.0)}."
        "\n   -- reported because a fitted ambient outside roughly 0-40 C is not an ambient; it is the solve"
        "\n      using T_amb as a free level and running out of record before anything stops it."
    )

    # =======================================================================
    say()
    say("=" * 108)
    say("SECTION 1 -- does the nuisance improve TRUTH error, or only in-sample error?")
    say("=" * 108)
    say("Population: the simulated records only, because only they have a plant to be right about.")
    say("'in-sample kept' is the oracle column the claim came from. 'truth' is the fitted model's")
    say("prediction of behaviour it never saw, scored WITH THE NUISANCE DISCARDED -- the model as the")
    say("controller would carry it. Every value is degrees C.")
    say()
    say(f"  {'arm':12s} {'in-sample kept':>26s} {'in-sample discarded':>26s} {'TRUTH (discarded)':>26s}")
    say(f"  {'':12s} {'median   p90    max':>26s} {'median   p90    max':>26s} {'median   p90    max':>26s}")
    for arm in ARMS:
        cells = []
        for key in ("insample", "insample_bare", "truth"):
            v = [r[arm][key] for r in sim_rows]
            cells.append(f"{fmt(median(v), 7)} {fmt(q(v, 0.9), 7)} {fmt(q(v, 1.0), 9)}")
        say(f"  {arm:12s} " + "  ".join(f"{c:>25s}" for c in cells))
    say()
    say("The same question as a PAIRED comparison against arm A, record by record. A win is the arm")
    say("beating arm A on that record; 'median delta' is the arm minus arm A, so negative is better;")
    say("'worst regression' is the largest amount by which the arm is worse than arm A on any record.")
    say()
    say(
        f"  {'arm':12s} {'metric':22s} {'n':>4s} {'win':>5s} {'tie':>5s} {'loss':>5s} {'median delta':>14s} {'worst regression':>18s}"
    )
    for arm in ARMS[1:]:
        for key, label in (
            ("insample", "in-sample kept"),
            ("insample_bare", "in-sample discarded"),
            ("truth", "TRUTH (discarded)"),
            ("ho_warm", "held-out (discarded)"),
        ):
            n, w, t_, ls, md, wr = paired(sim_rows, arm, "A_shipped", key)
            say(f"  {arm:12s} {label:22s} {n:>4d} {w:>5d} {t_:>5d} {ls:>5d} {fmt(md, 14, 4)} {fmt(wr, 18, 4)}")
    say()
    say()
    say("The worst-regressing records behind that table, named, because a median that improves while the")
    say("tail blows out is the shape a gate has to survive rather than the shape an average describes.")
    say(f"  {'arm':12s} {'record':34s} {'arm A truth':>12s} {'arm truth':>12s} {'delta':>10s}")
    for arm in ARMS[1:]:
        worst_rows = sorted(
            (r for r in sim_rows if math.isfinite(r[arm]["truth"]) and math.isfinite(r["A_shipped"]["truth"])),
            key=lambda r: r[arm]["truth"] - r["A_shipped"]["truth"],
            reverse=True,
        )[:3]
        for r in worst_rows:
            name = f"{r['plant']}/{r['profile']}/{r['length_s']}s"
            say(
                f"  {arm:12s} {name:34s} {fmt(r['A_shipped']['truth'], 12, 3)} {fmt(r[arm]['truth'], 12, 3)} "
                f"{fmt(r[arm]['truth'] - r['A_shipped']['truth'], 10, 3)}"
            )
    say()
    say("And the correlation between what the nuisance buys IN-SAMPLE and what it buys in TRUTH.")
    say("If the in-sample improvement predicted the truth improvement this would be strongly positive;")
    say("A12a measured the analogous correlation for the level of in-sample RMSE at -0.160.")
    for arm in ARMS[1:]:
        pairs = [
            (r[arm]["insample"] - r["A_shipped"]["insample"], r[arm]["truth"] - r["A_shipped"]["truth"])
            for r in sim_rows
            if all(
                math.isfinite(x)
                for x in (r[arm]["insample"], r["A_shipped"]["insample"], r[arm]["truth"], r["A_shipped"]["truth"])
            )
        ]
        rho, n_rho = ps.spearman([p[0] for p in pairs], [p[1] for p in pairs])
        say(f"  {arm:12s} Spearman(in-sample gain, truth gain) = {fmt(rho, 7, 3)}  over n={n_rho}")
    say()
    say("Arm C only: the same truth error with T_amb KEPT rather than discarded. d has nowhere to be")
    say("stored -- there is no such field in Controller._MODEL_PARAM_KEYS -- but T_amb is one of those")
    say("keys, so for arm C alone 'carry it forward' is an option the code already supports.")
    n, w, t_, ls, md, wr = paired(sim_rows, "C_free_amb", "A_shipped", "truth_kept", base_key="truth")
    say(
        f"  C_free_amb  truth with T_amb kept  n={n} win={w} tie={t_} loss={ls} median delta={fmt(md, 10, 4)} worst regression={fmt(wr, 10, 4)}"
    )
    v = [r["C_free_amb"]["truth_kept"] for r in sim_rows]
    say(f"              median {fmt(median(v), 7)}  p90 {fmt(q(v, 0.9), 7)}  max {fmt(q(v, 1.0), 9)}")

    # =======================================================================
    say()
    say("=" * 108)
    say("SECTION 2 -- what the fourth parameter costs in identifiability")
    say("=" * 108)
    say("s_min is the smallest singular value of d(prediction)/d(parameter) over the arm's free set,")
    say("per sample. Adding a column can only lower it: the minimum is taken over a larger space.")
    say("ARM B ONLY: its fourth column is exactly ones, so after the 1/sqrt(N) normalisation its norm is")
    say("exactly 1, hence s_min <= 1.0 on every record that can exist -- an identity, not a measurement.")
    say("That bound is exact relative to d in LINEAR degrees C; rescaling the parameter rescales the")
    say("bound, so it describes this parameterisation and is NOT a law about offsets in general. In")
    say("particular it does not cover the exploration's heat offset, whose column is 1/C_c times a step")
    say("response rather than ones. Arm C's log column is about T_amb=20 times the same physical move,")
    say("so its per-degree reading is also printed and is the one comparable to arm B.")
    say()
    say(f"  {'arm':14s} {'median':>9s} {'p10':>9s} {'min':>9s} {'below 0.50':>12s} {'of':>5s} {'newly refused':>15s}")
    base_pass = {id(r): r["A_shipped"]["s_min"] >= _IDENTIFIABILITY_FLOOR for r in rows}
    for arm in ARMS:
        for key, label in (
            [("s_min", arm)] if arm != "C_free_amb" else [("s_min", arm + " (log)"), ("s_min_perdeg", arm + " (per C)")]
        ):
            v = [r[arm][key] for r in rows]
            below = sum(1 for x in v if not (math.isfinite(x) and x >= _IDENTIFIABILITY_FLOOR))
            newly = sum(
                1
                for r in rows
                if base_pass[id(r)] and not (math.isfinite(r[arm][key]) and r[arm][key] >= _IDENTIFIABILITY_FLOOR)
            )
            say(
                f"  {label:14s} {fmt(median(v), 9, 4)} {fmt(q(v, 0.1), 9, 4)} {fmt(q(v, 0.0), 9, 4)} "
                f"{below:>12d} {len(v):>5d} {newly:>15d}"
            )
    say()
    say("'below 0.50' counts records the floor would refuse under that arm; 'newly refused' counts those")
    say("that clear it today under arm A and would stop clearing it. Population is every fitted record,")
    say("including the flat synthetic ones the floor exists to refuse -- they are already below it under")
    say("arm A, so they cannot be newly refused and do not inflate that column.")
    say()
    say("THE REAL COOK, which is the record the whole learning feature exists to learn from:")
    say()
    say(f"  {'length':>8s} {'n':>5s} " + " ".join(f"{a:>13s}" for a in ("A_shipped", "B_offset", "C log", "C per C")))
    for r in real_rows:
        vals = [
            r["A_shipped"]["s_min"],
            r["B_offset"]["s_min"],
            r["C_free_amb"]["s_min"],
            r["C_free_amb"]["s_min_perdeg"],
        ]
        cells = " ".join(f"{fmt(x, 8, 4)}{'  ' if x >= _IDENTIFIABILITY_FLOOR else ' X'}   " for x in vals)
        say(f"  {str(r['length_s']) + 's':>8s} {r['n']:>5d} " + cells)
    say(
        f"  (X marks a value below the {_IDENTIFIABILITY_FLOOR:g} floor -- the record would be refused, i.e. no model is learned)"
    )
    say()
    say("Could the floor simply be RE-DERIVED under a four-parameter arm? That is the obvious rescue, so")
    say("it is measured rather than dismissed. A12 set 0.50 between the records that determine nothing and")
    say("the records that do, and the room it had is the ratio between the two classes. UNINFORM here is")
    say("A12's own structural class, constant-free: the flat synthetic cooks and each plant's steady_hold,")
    say("which contain exactly one transient-free operating point by construction. The real cook is the")
    say("record the feature exists to learn from, so any floor must sit below it and above that class.")
    say()
    say(f"  {'arm':18s} {'max over UNINFORM':>18s} {'min over real cook':>19s} {'room (ratio)':>14s}")
    uninform = [r for r in rows if str(r["profile"]).startswith("flat_synth") or r["profile"] == "steady_hold"]
    for arm, key, label in (
        ("A_shipped", "s_min", "A_shipped"),
        ("B_offset", "s_min", "B_offset"),
        ("C_free_amb", "s_min", "C_free_amb (log)"),
        ("C_free_amb", "s_min_perdeg", "C_free_amb (per C)"),
    ):
        hi = max(finite([r[arm][key] for r in uninform]) or [float("nan")])
        lo_real = min(finite([r[arm][key] for r in real_rows]) or [float("nan")])
        room = (lo_real / hi) if hi > 0 else float("inf")
        say(f"  {label:18s} {fmt(hi, 18, 4)} {fmt(lo_real, 19, 4)} {fmt(room, 14, 2)}")
    say()
    say(f"UNINFORM population n={len(uninform)}; real cook n={len(real_rows)}. A ratio at or below 1.0 means no")
    say("floor exists that admits the real cook and refuses the cooks that determine nothing -- the two")
    say("classes have crossed, and re-deriving the constant cannot recover what the fourth parameter cost.")

    # =======================================================================
    say()
    say("=" * 108)
    say("SECTION 3 -- how big is the nuisance, and is it stable?")
    say("=" * 108)
    say("A stable offset across cooks of the same grill would be a real bias -- worth surfacing to the")
    say("operator as a probe calibration issue rather than silently absorbing. One that swings from cook")
    say("to cook is absorbing whatever that record happened to contain. 'full vs prefix' is the same")
    say("record's offset fitted on all of it and on its first two thirds: a real bias does not move.")
    say()
    say(
        f"  {'arm':12s} {'plant':9s} {'n':>4s} {'median':>9s} {'p10':>9s} {'p90':>9s} {'min':>9s} {'max':>9s} {'|full-prefix| med':>18s}"
    )
    for arm in ARMS[1:]:
        for plant in ("mak", "generic", "real_mak_cook"):
            sel = [r for r in rows if (r["plant"] == plant or r["profile"] == plant)]
            if not sel:
                continue
            v = [r[arm]["nuis"] for r in sel]
            drift = [abs(r[arm]["nuis"] - r[arm]["pre_nuis"]) for r in sel]
            say(
                f"  {arm:12s} {plant:9s} {len(v):>4d} {fmt(median(v), 9, 3)} {fmt(q(v, 0.1), 9, 3)} "
                f"{fmt(q(v, 0.9), 9, 3)} {fmt(q(v, 0.0), 9, 3)} {fmt(q(v, 1.0), 9, 3)} {fmt(median(drift), 18, 3)}"
            )
    say()
    say("Degrees C in every column. Arm C's is reported as (fitted T_amb - 20 C), the level shift it")
    say("amounts to, so it sits on the same axis as arm B's d.")
    say()
    say("The real cook's nuisance at each length, which is the one an operator would ever see:")
    for r in real_rows:
        say(
            f"  {str(r['length_s']) + 's':>7s}  d={r['B_offset']['nuis']:+8.3f} C (prefix {r['B_offset']['pre_nuis']:+8.3f})"
            f"   T_amb={r['C_free_amb']['T_amb']:8.3f} C, i.e. {r['C_free_amb']['nuis']:+8.3f} C"
            f" (prefix {r['C_free_amb']['pre_nuis']:+8.3f})"
        )

    # =======================================================================
    say()
    say("=" * 108)
    say("SECTION 4 -- does it help the REAL cook?")
    say("=" * 108)
    say("MAKGrillSim was identified FROM this cook, so it cannot serve as truth for it and no truth")
    say("column exists here -- promotion_signal.py declines the same comparison for the same reason.")
    say("What can be measured is held-out: the arm's fitter run on the first two thirds and scored on")
    say("the last third, with the nuisance discarded. The offset is free to fit the prefix and cannot")
    say("touch the suffix, so this is not a metric the nuisance can game.")
    say()
    say(f"  {'length':>8s} {'n':>5s} {'in-sample kept':>34s} {'held-out, nuisance discarded':>40s}")
    say(f"  {'':8s} {'':5s} " + " ".join(f"{a:>11s}" for a in ARMS) + "   " + " ".join(f"{a:>12s}" for a in ARMS))
    for r in real_rows:
        ins = " ".join(fmt(r[a]["insample"], 11, 4) for a in ARMS)
        ho = " ".join(fmt(r[a]["ho_warm"], 12, 4) for a in ARMS)
        say(f"  {str(r['length_s']) + 's':>8s} {r['n']:>5d} {ins}   {ho}")
    say()
    say("The same two columns over every record that has no plant behind it, and over all records:")
    say()
    say(f"  {'population':28s} {'arm':12s} {'in-sample kept med':>20s} {'held-out med':>14s} {'held-out p90':>14s}")
    for label, sel in (("all fitted records", rows), ("simulated only", sim_rows), ("real MAK cook", real_rows)):
        for arm in ARMS:
            say(
                f"  {label:28s} {arm:12s} {fmt(median([r[arm]['insample'] for r in sel]), 20, 4)} "
                f"{fmt(median([r[arm]['ho_warm'] for r in sel]), 14, 4)} {fmt(q([r[arm]['ho_warm'] for r in sel], 0.9), 14, 4)}"
            )
    say()
    say(f"total elapsed {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
