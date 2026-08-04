#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Promotion Signal: what separates a fit worth promoting from one that is not
*****************************************

 controller/mpc.py refits the grey box at the end of a cook and asks
 controller/model_promotion.evaluate whether to put the result on a live grill.
 The only evidence evaluate weighs about fit QUALITY is an RMSE ratio, and both
 RMSEs are measured on the record the candidate was fitted to. The candidate saw
 that record; the incumbent did not. This measures what that costs, and what
 signal would not have that defect.

 WHAT IS MEASURED. Records come from the two plants in controller/grill_sim.py,
 logged at the shipped control period, over eleven excitation profiles and eight
 truncation lengths, plus the real MAK cook and the flat cook that
 tests/unit/mpc/test_mpc_refit.py pins. Every record is fitted with the SHIPPED
 fitter -- update_mpc.fit_params from mpc._REFIT_INIT at the shipped sigma and
 n_delay -- and then, against each of two incumbents (the shipped defaults, and
 a model already calibrated to that plant):

   * IN-SAMPLE RMSE, candidate and incumbent, on the record itself. This is the
     signal the gate uses today, computed the way mpc.py computes it.
   * HELD-OUT RMSE. The record is split; the fitter is re-run on the prefix
     alone and the resulting model is scored on the suffix it never saw. Two
     scorings, because they differ and the difference matters to whoever builds
     the gate: `cold` restarts the simulation at the suffix's first sample the
     way update_mpc.fit_quality does, which leaves the transport-delay chain
     empty at a point where the real chain is charged; `warm` runs the model
     through the prefix first so the chain arrives charged, and scores only the
     suffix.
   * TRUTH ERROR. For the simulated plants the answer is known, so the fitted
     model is scored against the PLANT's behaviour on two profiles no fit ever
     saw (a full-fire-then-cut probe and a four-level step sequence), plus the
     dead time and the coast the model predicts against the plant's own. Truth
     error is scored against BEHAVIOUR, not against parameters: the plant is two
     lumps and the model is one, so their parameters are not comparable.
   * INFORMATIVENESS. Singular values of the residual Jacobian in log-parameter
     space, normalised per sample -- s_min is the degrees C RMS the prediction
     moves when the worst-determined direction of (K_Q, C_c, theta) is moved by
     one e-fold. Taken at the fitted point AND at each incumbent's parameters,
     because a runaway candidate can make the Jacobian at its own point look
     healthy and the incumbent's cannot be moved by the fit under test.
   * SPLIT CONSISTENCY. The record is fitted twice more, on the prefix and on
     the suffix separately, and the two models are compared by BEHAVIOUR over a
     fixed probe input -- degrees C RMS between their predictions, and the ratio
     of the braking distances they imply. A record that determines the model
     gives two fits that agree about what the grill will do next; one that does
     not gives two fits that agree about the record and nothing else. This needs
     no plant, so a gate could compute it on a live grill.
   * The cheap candidates alongside those: record length over the fitted
     effective time constant, the spread and level count of Q, the temperature
     span, and the Jacobian's reciprocal condition number.

 WHAT "SHOULD PROMOTE" MEANS HERE, and why it is not a judgement call. A
 promotion is justified exactly when the candidate predicts the plant's UNSEEN
 behaviour better than the incumbent does. So the ground-truth label is
 truth_rmse(candidate) < truth_rmse(incumbent) -- no threshold, no constant, no
 free parameter. Every gate below is scored against that label as a confusion
 matrix.

 WHAT THIS DELIBERATELY DOES NOT DO. It changes no decision path: it calls
 model_promotion.evaluate read-only, to report what the gate does today, and it
 imports no threshold it then derives -- the recommended thresholds are read off
 the tables this prints and off nothing else. It does not score a fit by
 comparing parameters to the plant's. It does not use MAKGrillSim as ground
 truth for the real MAK cook: that plant was identified FROM that cook, so any
 agreement would be the identification talking, and the real cook is therefore
 reported without a truth column at all.

 The two validation profiles are not among the eleven fitted ones and are never
 fitted, so no fit has seen a step at their levels or a cut at their time.

 Usage:
   uv run python -m docs.superpowers.experiments.promotion_signal

 Set PROMOTION_SIGNAL_WORKERS to change the pool size; it defaults to half the
 cores, because this repo's suite carries wall-clock budget assertions that a
 saturated machine breaks.
*****************************************
"""

import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from controller import model_promotion as promo  # noqa: E402
from controller.grill_sim import DT, GrillSim, MAKGrillSim  # noqa: E402
from controller.mpc import _DEFAULTS, _REFIT_INIT, _REFIT_MIN_SAMPLES, Controller  # noqa: E402
from controller.mpc_model import simulate_grey_box  # noqa: E402
from controller.update_mpc import _FREE, _SIM_KEYS, fit_params, fit_quality  # noqa: E402

T_AMB = 20.0
SIGMA = float(_DEFAULTS["sigma"])
N_DELAY = int(_DEFAULTS["n_delay"])
#: The cadence a cook is actually logged at, so a synthetic record and the real
#: one carry the same number of samples per second of grill.
LOG_PERIOD_S = float(_DEFAULTS["control_period"])
LOG_STRIDE = int(round(LOG_PERIOD_S / DT))
#: The horizon and step evaluate() is asked about: the shipped pair, so its
#: braking-distance demand is the one a real grill would be given.
N_HORIZON, T_STEP = int(_DEFAULTS["n_horizon"]), float(_DEFAULTS["t_step"])

MODEL_KEYS = Controller._MODEL_PARAM_KEYS
SHIPPED = {k: float(_DEFAULTS[k]) for k in MODEL_KEYS}

#: Where a record is split for the held-out measurement. Two thirds fitted, one
#: third scored: enough record left to fit from, and a third of a cook is long
#: against the ~110 s dead time the suffix has to exercise.
SPLIT_FRAC = 2.0 / 3.0

#: Truncation lengths in seconds. 600 s is where a log at the shipped cadence
#: first reaches mpc._REFIT_MIN_SAMPLES, so the two shorter ones are below the
#: floor the controller already enforces. They are marked rather than dropped:
#: what they show is how much of the inversion that floor already covers.
LENGTHS_S = (300, 450, 600, 900, 1200, 1800, 2400, 3600)

RECORD_S = 3600
WARMUP_S = 6000

#: The reference record each plant's "already calibrated" incumbent is fitted
#: from: the richest profile at full length.
REFERENCE = ("prbs_wide", RECORD_S)


# ------------------------------------------------------------------ profiles
def _pad(duty):
    """One extra sample, so a record of RECORD_S seconds ends AT t=RECORD_S.

    Without it the last sample sits one step short and every truncation length
    is a step shorter than its label.
    """
    return np.concatenate([duty, duty[-1:]])


def _segments(pairs):
    return _pad(np.concatenate([np.full(int(n), float(v)) for v, n in pairs]))


def _prbs(lo, hi, seed, total=RECORD_S):
    rng = np.random.default_rng(seed)
    segs = []
    while sum(len(s) for s in segs) < total:
        segs.append(np.full(int(rng.integers(60, 180)), float(rng.uniform(lo, hi))))
    return _pad(np.concatenate(segs)[:total])


_PROFILE_CACHE = {}


def profiles():
    """name -> (duty per second, warm-up duty, warm-up seconds).

    A warm-up is run and discarded, so a record that starts at the plant's own
    steady state is a real plant record and not a synthetic constant. That is
    what `steady_hold` is: the uninformative cook, produced by the plant rather
    than asserted. `step_small` is the same start with one five-point step in
    it -- the barely-informative case between the two.
    """
    if not _PROFILE_CACHE:
        _PROFILE_CACHE.update(
            {
                "ramp_coast": (_segments([(1.0, 1500), (0.0, 2100)]), 0.0, 0),
                "steps_up": (_segments([(0.30, 1200), (0.60, 1200), (1.00, 1200)]), 0.0, 0),
                "steps_down": (_segments([(1.00, 1200), (0.50, 1200), (0.20, 1200)]), 0.0, 0),
                "pulse": (_segments([(1.0, 300), (0.0, 600)] * 4), 0.0, 0),
                "hold_high": (_segments([(0.70, RECORD_S)]), 0.0, 0),
                "hold_low": (_segments([(0.25, RECORD_S)]), 0.0, 0),
                "prbs_wide": (_prbs(0.0, 1.0, 11), 0.0, 0),
                "prbs_narrow": (_prbs(0.35, 0.55, 12), 0.0, 0),
                "prbs_tiny": (_prbs(0.44, 0.46, 13), 0.0, 0),
                "step_small": (_segments([(0.50, 1800), (0.55, 1800)]), 0.50, WARMUP_S),
                "steady_hold": (_segments([(0.50, RECORD_S)]), 0.50, WARMUP_S),
            }
        )
    return _PROFILE_CACHE


#: Profiles the truth error is scored on. Neither is fitted anywhere, and
#: neither shares a level schedule or a switch time with a fitted one.
VALIDATION = {
    "cq_probe": _segments([(1.0, 900), (0.0, 900)]),
    "val_steps": _segments([(0.20, 900), (0.80, 900), (0.45, 900), (0.90, 900)]),
}
#: The second the fuel is cut in cq_probe, which is where its coast starts.
CQ_CUT = 900.0

#: A fixed input schedule two models are compared ON. It is an input, not data:
#: nothing about any plant or any record enters it, so comparing two fits over
#: it measures how differently they BEHAVE where the controller cares -- through
#: a full-fire ramp and the coast after a cut -- rather than how differently
#: their parameters read, which across this model's scaling directions is not
#: the same question.
PROBE_T = np.arange(0.0, 1800.0 + LOG_PERIOD_S, LOG_PERIOD_S)
PROBE_Q = np.where(PROBE_T < CQ_CUT, 100.0, 0.0)


def _plant(name, seed=0):
    if name == "mak":
        return MAKGrillSim(seed=seed, fixed_fan=1.0, T0=20.0)
    s = GrillSim(seed=seed, fixed_fan=1.0)
    s.T_f = s.T_c = s.T_meas = 20.0
    return s


def _drive(name, duty, warm_duty, warm_s, seed=0):
    """Run the plant through `duty` after a discarded warm-up. (t, true, meas)."""
    s = _plant(name, seed=seed)
    for _ in range(int(warm_s / DT)):
        s.step(auger_on=float(warm_duty), fan_frac=1.0)
    n = len(duty)
    true = np.empty(n)
    meas = np.empty(n)
    for i in range(n):
        s.step(auger_on=float(duty[i]), fan_frac=1.0)
        true[i] = s.true_Tc
        meas[i] = s.measured()
    return np.arange(n, dtype=float) * DT, true, meas


def plant_record(plant, profile, seed=0):
    duty, warm_duty, warm_s = profiles()[profile]
    t, true, meas = _drive(plant, duty, warm_duty, warm_s, seed=seed)
    k = slice(None, None, LOG_STRIDE)
    return dict(plant=plant, profile=profile, t=t[k], y=meas[k], true=true[k], Q=(duty * 100.0)[k])


def flat_synthetic(sigma_c, seed=0):
    """The uninformative cook tests/unit/mpc/test_mpc_refit.py pins.

    Reproduced exactly at sigma_c=0.05: 400 rows at the 5 s control period,
    constant Q, constant temperature plus a little sensor noise. It has no plant
    behind it, so it carries no truth error and is excluded from the confusion
    matrices; what it is here for is its informativeness statistics, which are
    what a gate would have to read. The 0.15 arm carries controller/grill_sim's
    own sensor noise instead, so the conclusion cannot be an artefact of the
    unusually quiet noise the test happens to use.
    """
    rng = np.random.default_rng(seed)
    n = 400
    t = np.arange(n, dtype=float) * LOG_PERIOD_S
    y = 100.0 + rng.normal(0.0, sigma_c, size=n)
    return dict(plant=None, profile=f"flat_synth_{sigma_c:g}", t=t, y=y, true=None, Q=np.full(n, 50.0))


def real_cook():
    import pandas as pd

    df = pd.read_csv(os.path.join(REPO, "tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv"))
    t = df["time_s"].values.astype(float)
    return dict(
        plant=None,
        profile="real_mak_cook",
        t=t - t[0],
        y=df["temp_c"].values.astype(float),
        true=None,
        Q=df["Q"].values.astype(float),
    )


# ------------------------------------------------------------------- fitting
def _sim(params, t, Q, T0):
    p = {k: float(params[k]) for k in _SIM_KEYS}
    p["n_delay"] = int(round(float(params["n_delay"])))
    return simulate_grey_box(t, Q, T_amb=T_AMB, T0=float(T0), **p)


def _safe_rmse(params, t, Q, T0, target, sl=slice(None)):
    """RMSE of the model over `sl`, or inf where it cannot be simulated."""
    try:
        y = _sim(params, t, Q, T0)
    except OverflowError:
        return float("inf")
    if not np.all(np.isfinite(y)):
        return float("inf")
    return float(np.sqrt(np.mean((y[sl] - target) ** 2)))


def shipped_fit(t, y, Q):
    """A refit exactly as controller/mpc.py performs one."""
    return fit_params(t, y, Q, T_amb=T_AMB, init=dict(_REFIT_INIT), sigma=SIGMA, n_delay=N_DELAY)


def log_svals(t, Q, fitted, T0):
    """Singular values of d(residual)/d(log p) over `_FREE`, per sample.

    The residual is (model - log), so its Jacobian is the model's, and the model
    does not depend on the measured temperatures at all beyond the T0 it starts
    from. This is therefore a property of the record's INPUTS and of the fitted
    point -- not of how well the fit turned out -- which is what makes it usable
    as an informativeness statistic rather than a restatement of the RMSE.

    Central differences at one part in a thousand of a log, matching the space
    the shipped solve works in. A singular value is degrees C RMS per e-fold of
    the corresponding orthonormal direction in (log K_Q, log C_c, log theta), so
    the smallest one answers: how far can this record's best-fitting parameters
    be moved, in the direction it constrains least, before the prediction moves
    at all?
    """
    h = 1e-3
    cols = []
    for key in _FREE:
        base = float(fitted[key])
        if not (base > 0.0 and math.isfinite(base)):
            return None
        try:
            y_up = _sim(dict(fitted, **{key: base * math.exp(h)}), t, Q, T0)
            y_dn = _sim(dict(fitted, **{key: base * math.exp(-h)}), t, Q, T0)
        except OverflowError:
            return None
        if not (np.all(np.isfinite(y_up)) and np.all(np.isfinite(y_dn))):
            return None
        cols.append((y_up - y_dn) / (2.0 * h))
    J = np.column_stack(cols) / math.sqrt(len(t))
    return np.linalg.svd(J, compute_uv=False)


def model_disagreement(a, b):
    """Degrees C RMS between two models' predictions over the fixed probe.

    Two fits of the same grill that a record DETERMINES agree about what the
    grill will do next. Two fits it does not determine agree about the record
    and about nothing else. This is that difference, measured where the
    controller spends its accuracy -- and it needs no plant, so a gate could
    compute it on a live grill from the same record it just fitted.
    """
    try:
        ya, yb = _sim(a, PROBE_T, PROBE_Q, 20.0), _sim(b, PROBE_T, PROBE_Q, 20.0)
    except OverflowError:
        return float("inf")
    if not (np.all(np.isfinite(ya)) and np.all(np.isfinite(yb))):
        return float("inf")
    return float(np.sqrt(np.mean((ya - yb) ** 2)))


def _ratio(a, b):
    """The larger of two positive quantities over the smaller; inf if either fails."""
    if not (math.isfinite(a) and math.isfinite(b) and a > 0 and b > 0):
        return float("inf")
    return max(a, b) / min(a, b)


def dead_and_coast(t, y):
    """Dead time and coast on the cq_probe profile, for one trajectory."""
    rise = np.nonzero(y - y[0] >= 1.0)[0]
    dead = float(t[rise[0]]) if len(rise) else float("inf")
    cut = int(np.searchsorted(t, CQ_CUT))
    coast = float(np.max(y[cut:]) - y[cut])
    return dead, coast


# -------------------------------------------------------------- truth errors
_VAL_CACHE = {}


def validation_runs(plant):
    """The plant's own behaviour on the two unseen profiles. Cached per process."""
    if plant not in _VAL_CACHE:
        out = {}
        for name, duty in VALIDATION.items():
            t, true, _ = _drive(plant, duty, 0.0, 0)
            out[name] = (t, duty * 100.0, true)
        _VAL_CACHE[plant] = out
    return _VAL_CACHE[plant]


def truth_error(params, plant):
    """(pooled RMSE against the plant, dead-time error, coast error).

    RMSE is pooled over both validation profiles with equal weight per profile,
    so a long one does not outvote a short one. The dead-time and coast errors
    are the model's reading minus the plant's on cq_probe, signed: NEGATIVE
    means the model believes the grill stops sooner than it does, which is the
    shape that brakes late.
    """
    per = []
    dead_err = coast_err = float("nan")
    for name, (t, Q, true) in validation_runs(plant).items():
        try:
            y = _sim(params, t, Q, float(true[0]))
        except OverflowError:
            return float("inf"), float("nan"), float("nan")
        if not np.all(np.isfinite(y)):
            return float("inf"), float("nan"), float("nan")
        per.append(float(np.sqrt(np.mean((y - true) ** 2))))
        if name == "cq_probe":
            md, mc = dead_and_coast(t, y)
            pd_, pc_ = dead_and_coast(t, true)
            dead_err, coast_err = md - pd_, mc - pc_
    return float(np.sqrt(np.mean(np.square(per)))), dead_err, coast_err


# ------------------------------------------------------------ one measurement
def measure(rec, incumbents):
    """Everything measured about one record. Runs in a worker process.

    `incumbents` is name -> parameter dict; each is scored on this record the
    same three ways the candidate is, so a gate built on any of the three
    signals can be replayed against any of them.
    """
    t, y, Q = rec["t"], rec["y"], rec["Q"]
    n = len(t)
    out = dict(plant=rec["plant"], profile=rec["profile"], length_s=rec["length_s"], n=n, dur_s=float(t[-1] - t[0]))

    fitted = shipped_fit(t, y, Q)
    out["converged"] = bool(fitted["converged"])
    out["nfev"] = int(fitted["nfev"])
    out["fit"] = {k: float(fitted[k]) for k in MODEL_KEYS}
    out["insample"] = {"cand": fit_quality(t, y, Q, fitted, T_amb=T_AMB)[0]}

    # --- held out: the fitter re-run on the prefix, scored on the suffix ----
    k = max(2, int(round(n * SPLIT_FRAC)))
    pre = fit_params(t[:k], y[:k], Q[:k], T_amb=T_AMB, init=dict(_REFIT_INIT), sigma=SIGMA, n_delay=N_DELAY)
    out["pre_converged"] = bool(pre["converged"])
    out["pre_fit"] = {key: float(pre[key]) for key in MODEL_KEYS}
    ts, ys, Qs = t[k:], y[k:], Q[k:]
    out["heldout_n"] = len(ts)
    scorable = len(ts) >= 2
    out["cold"] = {}
    out["warm"] = {}
    if scorable:
        # cold: restarted at the suffix's first sample, the way fit_quality
        # scores anything -- so the transport chain starts empty at a point
        # where the plant's is charged.
        out["cold"]["cand"] = fit_quality(ts, ys, Qs, pre, T_amb=T_AMB)[0]
        # warm: run through the whole record from its true start, scored on the
        # suffix only, so the chain arrives in the state the record put it in.
        out["warm"]["cand"] = _safe_rmse(pre, t, Q, float(y[0]), ys, slice(k, None))

    # --- split consistency: the same record fitted twice, on disjoint halves -
    if len(ts) >= 3:
        suf = fit_params(ts, ys, Qs, T_amb=T_AMB, init=dict(_REFIT_INIT), sigma=SIGMA, n_delay=N_DELAY)
        out["suf_converged"] = bool(suf["converged"])
        out["suf_fit"] = {key: float(suf[key]) for key in MODEL_KEYS}
        out["split_disagree"] = model_disagreement(out["pre_fit"], out["suf_fit"])
        out["split_brake_ratio"] = _ratio(
            promo.longest_braking_distance(out["pre_fit"]), promo.longest_braking_distance(out["suf_fit"])
        )
        out["split_tau_ratio"] = _ratio(
            promo.effective_tau(out["pre_fit"], promo.T_HAZARD_C), promo.effective_tau(out["suf_fit"], promo.T_HAZARD_C)
        )
    else:
        out["suf_converged"] = False
        out["suf_fit"] = dict(out["pre_fit"])
        out["split_disagree"] = out["split_brake_ratio"] = out["split_tau_ratio"] = float("inf")

    for name, params in incumbents.items():
        out["insample"][name] = fit_quality(t, y, Q, params, T_amb=T_AMB)[0]
        if scorable:
            out["cold"][name] = fit_quality(ts, ys, Qs, params, T_amb=T_AMB)[0]
            out["warm"][name] = _safe_rmse(params, t, Q, float(y[0]), ys, slice(k, None))
        # The same informativeness question asked at the model already believed
        # rather than at the fitted point. A runaway candidate can make the
        # Jacobian at ITS parameters look healthy; the incumbent's cannot be
        # moved by the fit under test.
        sv_i = log_svals(t, Q, params, float(y[0]))
        out[f"s_min_{name}"] = float(sv_i[-1]) if sv_i is not None else float("nan")

    # --- informativeness ---------------------------------------------------
    sv = log_svals(t, Q, fitted, float(y[0]))
    out["s_min"] = float(sv[-1]) if sv is not None else float("nan")
    out["s_max"] = float(sv[0]) if sv is not None else float("nan")
    out["cond"] = float(sv[0] / sv[-1]) if (sv is not None and sv[-1] > 0) else float("inf")
    out["inv_cond"] = float(sv[-1] / sv[0]) if (sv is not None and sv[0] > 0) else 0.0
    #: The quantity the horizon is sized from, read off the candidate itself.
    #: Needs no plant and no record, so a gate can compare it to the incumbent's.
    out["brake_s"] = float(promo.longest_braking_distance(out["fit"]))
    out["q_std"] = float(np.std(Q))
    out["q_range"] = float(np.max(Q) - np.min(Q))
    out["q_levels"] = float(len(np.unique(np.round(Q, 1))))
    out["temp_span"] = float(np.max(y) - np.min(y))
    tau = promo.effective_tau(fitted, float(np.median(y)))
    out["tau_s"] = float(tau)
    out["len_over_tau"] = float(out["dur_s"] / tau) if tau > 0 and math.isfinite(tau) else float("inf")

    # --- truth -------------------------------------------------------------
    if rec["plant"] is not None:
        out["truth_cand"], out["dead_err"], out["coast_err"] = truth_error(fitted, rec["plant"])
        out["truth_pre"] = truth_error(pre, rec["plant"])[0]
    else:
        out["truth_cand"] = out["truth_pre"] = float("nan")
        out["dead_err"] = out["coast_err"] = float("nan")
    return out


def truncations(rec):
    """The record cut to each length that fits inside it, plus its own length."""
    dur = float(rec["t"][-1] - rec["t"][0])
    seen = []
    for L in sorted(LENGTHS_S, reverse=True):
        if L > dur + 1e-9:
            continue
        m = int(np.searchsorted(rec["t"], rec["t"][0] + L, side="right"))
        if m < 3:
            continue
        cut = dict(rec)
        for key in ("t", "y", "true", "Q"):
            cut[key] = None if rec[key] is None else rec[key][:m]
        cut["length_s"] = int(L)
        seen.append(int(L))
        yield cut
    if int(round(dur)) not in seen:
        cut = dict(rec)
        cut["length_s"] = int(round(dur))
        yield cut


def _job(args):
    return measure(*args)


# ------------------------------------------------------------------ analysis
def spearman(a, b):
    """Rank correlation, computed here so a reader can check it.

    Ties take their average rank, which is what makes this Spearman rather than
    a correlation of argsorts.
    """
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    ok = np.isfinite(a) & np.isfinite(b)
    a, b = a[ok], b[ok]
    if len(a) < 3:
        return float("nan"), len(a)

    def rank(v):
        order = np.argsort(v, kind="stable")
        r = np.empty(len(v), dtype=float)
        r[order] = np.arange(len(v), dtype=float)
        for val in np.unique(v):
            m = v == val
            if m.sum() > 1:
                r[m] = r[m].mean()
        return r

    ra, rb = rank(a), rank(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan"), len(a)
    return float(np.corrcoef(ra, rb)[0, 1]), len(a)


def gate_verdict(row, incumbent, cand_rmse, inc_rmse):
    """model_promotion.evaluate on one row, with the fit-quality signal swapped.

    Called read-only. The candidate model, the incumbent model, the parameter
    bounds and the braking-distance demand are exactly what ships; the only
    thing varied is which pair of numbers is handed in as the two RMSEs. The
    candidate model is always the FULL-record fit even when a held-out signal is
    used, because that is the model a gate would adopt -- the prefix fit exists
    only to produce a number about a record, not to be installed. mpc.py's own
    non-convergence veto sits in front of evaluate on the real path, so it is
    applied here too.
    """
    if not row["converged"]:
        return False, "solve did not converge"
    if not (math.isfinite(cand_rmse) and math.isfinite(inc_rmse)):
        return False, "a required RMSE is not finite"
    v = promo.evaluate(
        row["fit"],
        incumbent,
        candidate_rmse=cand_rmse,
        incumbent_rmse=inc_rmse,
        n_horizon=N_HORIZON,
        t_step=T_STEP,
    )
    return bool(v.accepted), v.reason


def fmt(v, w=8, p=3):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return f"{'--':>{w}}"
    return f"{v:>{w}.{p}f}"


SIGNALS = (("in-sample (today)", "insample"), ("held-out cold", "cold"), ("held-out warm", "warm"))


def main():
    started = time.time()
    workers = int(os.environ.get("PROMOTION_SIGNAL_WORKERS", max(1, (os.cpu_count() or 2) // 2)))

    def say(s=""):
        print(s)

    say("=" * 104)
    say("PROMOTION SIGNAL -- what separates a fit worth promoting from one that is not")
    say("=" * 104)
    say(f"shipped fitter : _REFIT_INIT={dict(_REFIT_INIT)} sigma={SIGMA:g} n_delay={N_DELAY} free={list(_FREE)}")
    say(
        f"log cadence    : {LOG_PERIOD_S:g}s;  refit floor {_REFIT_MIN_SAMPLES} samples "
        f"(= {_REFIT_MIN_SAMPLES * LOG_PERIOD_S:.0f}s at that cadence)"
    )
    say(
        f"held-out split : {SPLIT_FRAC:.4f} of the record;  evaluate() asked at n_horizon={N_HORIZON} t_step={T_STEP:g}"
    )
    say("shipped incumb.: " + " ".join(f"{k}={SHIPPED[k]:g}" for k in MODEL_KEYS))

    # ---------------------------------------------------------- self-checks
    say()
    say("--- harness self-checks ---------------------------------------------------------------")
    rc = real_cook()
    rc_fit = shipped_fit(rc["t"], rc["y"], rc["Q"])
    #: tests/unit/mpc/test_model_promotion.py REAL_MAK_FIT, transcribed. If this
    #: harness cannot reproduce the repo's own recorded fit of this record, it
    #: is not measuring the shipped fitter and nothing below means anything.
    recorded = dict(C_c=3591.95, theta=111.32, K_Q=9.9208)
    say(
        "real-cook fit vs test_model_promotion.REAL_MAK_FIT: "
        + " ".join(f"{k}={rc_fit[k]:.5g} (recorded {v:g})" for k, v in recorded.items())
    )
    say(f"  worst relative disagreement {max(abs(rc_fit[k] / v - 1.0) for k, v in recorded.items()):.2e}")
    for plant in ("mak", "generic"):
        runs = validation_runs(plant)
        t, _, true = runs["cq_probe"]
        pd_, pc_ = dead_and_coast(t, true)
        st, _, strue = runs["val_steps"]
        say(
            f"plant {plant:8s} cq_probe truth: dead={pd_:.0f}s coast={pc_:.1f}C peak={true.max():.0f}C | "
            f"val_steps truth: span={strue.max() - strue.min():.0f}C peak={strue.max():.0f}C"
        )

    # -------------------------------- phase 1: the already-calibrated incumbent
    say()
    say("--- phase 1: the second incumbent, each plant's own full-length prbs_wide fit --------")
    calibrated = {}
    for plant in ("mak", "generic"):
        rec = plant_record(plant, REFERENCE[0])
        f_ = shipped_fit(rec["t"], rec["y"], rec["Q"])
        calibrated[plant] = {k: float(f_[k]) for k in MODEL_KEYS}
        tr, de, ce = truth_error(calibrated[plant], plant)
        say(
            f"  {plant:8s} calibrated : "
            + " ".join(f"{k}={calibrated[plant][k]:.5g}" for k in ("C_c", "K_Q", "theta"))
            + f"   truth={tr:.3f}C dead_err={de:+.0f}s coast_err={ce:+.2f}C"
        )
    for plant in ("mak", "generic"):
        tr, de, ce = truth_error(SHIPPED, plant)
        say(f"  {plant:8s} shipped    : truth={tr:.3f}C dead_err={de:+.0f}s coast_err={ce:+.2f}C")
    inc_truth = {p: truth_error(SHIPPED, p)[0] for p in ("mak", "generic")}
    inc_coast = {p: truth_error(SHIPPED, p)[2] for p in ("mak", "generic")}
    cal_truth = {p: truth_error(calibrated[p], p)[0] for p in ("mak", "generic")}

    # ------------------------------------------------------- phase 2: the sweep
    jobs = []
    for plant in ("mak", "generic"):
        for profile in profiles():
            for cut in truncations(plant_record(plant, profile)):
                jobs.append((cut, {"shipped": SHIPPED, "calibrated": calibrated[plant]}))
    for rec in [rc, flat_synthetic(0.05), flat_synthetic(0.15)]:
        for cut in truncations(rec):
            jobs.append((cut, {"shipped": SHIPPED}))

    say()
    say(f"--- phase 2: fitting {len(jobs)} records ({2 * len(jobs)} shipped-fitter solves) on {workers} workers ---")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(_job, jobs, chunksize=1))
    say(f"    done at t+{time.time() - started:.0f}s")

    sim_rows = [r for r in rows if r["plant"] is not None]
    real_rows = [r for r in rows if r["profile"] == "real_mak_cook"]
    flat_rows = [r for r in rows if str(r["profile"]).startswith("flat_synth")]
    incumbents = {"shipped": lambda r: SHIPPED, "calibrated": lambda r: calibrated[r["plant"]]}
    inc_truth_of = {"shipped": inc_truth, "calibrated": cal_truth}

    def klass(r):
        """UNINFORM / INFORM / other, on grounds independent of every statistic tested.

        UNINFORM is structural: the flat synthetic cooks and the plant's own
        steady_hold contain exactly one transient-free operating point by
        construction. INFORM is behavioural and constant-free: the fit predicts
        the plant's unseen behaviour better than the shipped incumbent does AND
        does not make the coast reading worse -- i.e. a record this gate ought
        to let through. Everything else is shown but does not draw the line.
        """
        if str(r["profile"]).startswith("flat_synth") or r["profile"] == "steady_hold":
            return "UNINFORM"
        if r["plant"] is None:
            return "real"
        better = r["truth_cand"] < inc_truth[r["plant"]]
        safe = math.isfinite(r["coast_err"]) and r["coast_err"] >= inc_coast[r["plant"]]
        return "INFORM" if (better and safe) else "other"

    # ---------------------------------------------------- per-row appendix
    say()
    say("=" * 104)
    say("APPENDIX A -- every record measured")
    say("=" * 104)
    say("RMSEs in C. cand/inc columns are candidate and the SHIPPED incumbent. ho_cold/ho_warm are")
    say("the PREFIX fit scored on the suffix it never saw (cold = restarted there as fit_quality does,")
    say("warm = run through the prefix first). truth = pooled RMSE on the two unseen validation")
    say("profiles; d_err/c_err = model minus plant dead time and coast on cq_probe, so a NEGATIVE")
    say("c_err is a model that believes the grill stops sooner than it does. s_min = C RMS per e-fold")
    say("of the worst-determined direction of (log K_Q, log C_c, log theta).")
    hdr = (
        f"{'plant':8s} {'profile':15s} {'len_s':>6s} {'n':>4s} {'cv':>3s} "
        f"{'insamp_c':>8s} {'insamp_i':>8s} {'ho_cold_c':>9s} {'ho_cold_i':>9s} "
        f"{'ho_warm_c':>9s} {'ho_warm_i':>9s} {'truth_c':>8s} {'truth_pr':>8s} "
        f"{'d_err':>6s} {'c_err':>7s} {'s_min':>9s} {'cond':>9s} {'L/tau':>6s} {'q_std':>6s} {'Tspan':>6s}"
    )
    say(hdr)
    say("-" * len(hdr))
    for r in sorted(rows, key=lambda r: (str(r["plant"]), r["profile"], -r["length_s"])):
        say(
            f"{str(r['plant']):8s} {r['profile']:15s} {r['length_s']:>6d} {r['n']:>4d} "
            f"{('y' if r['converged'] else 'N'):>3s} "
            f"{fmt(r['insample']['cand'])} {fmt(r['insample']['shipped'])} "
            f"{fmt(r['cold'].get('cand'), 9)} {fmt(r['cold'].get('shipped'), 9)} "
            f"{fmt(r['warm'].get('cand'), 9)} {fmt(r['warm'].get('shipped'), 9)} "
            f"{fmt(r['truth_cand'])} {fmt(r['truth_pre'])} "
            f"{fmt(r['dead_err'], 6, 0)} {fmt(r['coast_err'], 7, 2)} "
            f"{fmt(r['s_min'], 9, 5)} {fmt(r['cond'], 9, 1)} {fmt(r['len_over_tau'], 6, 2)} "
            f"{fmt(r['q_std'], 6, 1)} {fmt(r['temp_span'], 6, 1)}"
        )

    say()
    say("=" * 104)
    say("APPENDIX B -- the informativeness statistics of every record")
    say("=" * 104)
    say("s_min@fit / s_min@ship = the smallest per-sample singular value of d(model)/d(log p) at")
    say("the fitted point and at the shipped incumbent's parameters. split_* compare the prefix fit")
    say("with a separate fit of the suffix: disagree is C RMS between their predictions over the")
    say("fixed probe, brake and tau are the ratios of the braking distance and the effective tau at")
    say("the hazard temperature they imply. pre_C_c/suf_C_c are the two fits' capacitances.")
    hdr = (
        f"{'plant':8s} {'profile':15s} {'len_s':>6s} {'s_min@fit':>11s} {'s_min@ship':>11s} {'inv_cond':>9s} "
        f"{'split_dis':>10s} {'split_brk':>10s} {'split_tau':>10s} {'pre_C_c':>10s} {'suf_C_c':>10s} "
        f"{'L/tau':>6s} {'Tspan':>7s} {'truth_c':>8s}"
    )
    say(hdr)
    say("-" * len(hdr))
    for r in sorted(rows, key=lambda r: (str(r["plant"]), r["profile"], -r["length_s"])):
        say(
            f"{str(r['plant']):8s} {r['profile']:15s} {r['length_s']:>6d} "
            f"{fmt(r['s_min'], 11, 6)} {fmt(r.get('s_min_shipped'), 11, 6)} {fmt(r['inv_cond'], 9, 5)} "
            f"{fmt(r['split_disagree'], 10, 3)} {fmt(r['split_brake_ratio'], 10, 3)} {fmt(r['split_tau_ratio'], 10, 3)} "
            f"{fmt(r['pre_fit']['C_c'], 10, 1)} {fmt(r['suf_fit']['C_c'], 10, 1)} "
            f"{fmt(r['len_over_tau'], 6, 2)} {fmt(r['temp_span'], 7, 1)} {fmt(r['truth_cand'], 8, 2)}"
        )

    # -------------------------------------- Q1/Q2: does in-sample still invert
    say()
    say("=" * 104)
    say("SECTION 1 -- does in-sample RMSE still invert, and does a held-out RMSE rank correctly?")
    say("=" * 104)
    say("Spearman rank correlation of each fit-quality signal against TRUTH error over the simulated")
    say("records. A signal a gate can trust ranks a fit the way truth does: +1 is perfect, 0 says")
    say("nothing, negative means the signal is inverted -- it calls the worse fit the better one.")
    say("Each signal is scored against the truth error of the fit it actually describes: in-sample")
    say("against the full-record fit, both held-out columns against the prefix fit.")
    say()
    bands = [
        ("all lengths", lambda r: True),
        (
            f"<{int(_REFIT_MIN_SAMPLES * LOG_PERIOD_S)}s (below the refit floor)",
            lambda r: r["length_s"] < _REFIT_MIN_SAMPLES * LOG_PERIOD_S,
        ),
        ("600-1200s (the real cook's band)", lambda r: 600 <= r["length_s"] <= 1200),
        ("1800-3600s", lambda r: r["length_s"] > 1200),
    ]
    say("The last three columns are not fit-quality signals at all -- they are the informativeness")
    say("statistics, correlated against the same truth error. A negative s_min correlation is the")
    say("right sign: more information in the record, less error in the model it yields.")
    say()
    say(
        f"{'band':40s} {'n':>4s} {'in-sample':>10s} {'ho_cold':>10s} {'ho_warm':>10s} | "
        f"{'s_min':>8s} {'temp_span':>10s} {'split_dis':>10s}"
    )
    say("-" * 106)
    for label, pred in bands:
        sub = [r for r in sim_rows if pred(r)]
        truth = [r["truth_cand"] for r in sub]
        rho_in, n_in = spearman([r["insample"]["cand"] for r in sub], truth)
        rho_hc, _ = spearman([r["cold"].get("cand", float("nan")) for r in sub], [r["truth_pre"] for r in sub])
        rho_hw, _ = spearman([r["warm"].get("cand", float("nan")) for r in sub], [r["truth_pre"] for r in sub])
        rho_s, _ = spearman([r["s_min"] for r in sub], truth)
        rho_t, _ = spearman([r["temp_span"] for r in sub], truth)
        rho_d, _ = spearman([r["split_disagree"] for r in sub], truth)
        say(
            f"{label:40s} {n_in:>4d} {rho_in:>10.3f} {rho_hc:>10.3f} {rho_hw:>10.3f} | "
            f"{rho_s:>8.3f} {rho_t:>10.3f} {rho_d:>10.3f}"
        )

    say()
    say("The inversion laid out per profile: in-sample RMSE / truth error against record length.")
    say("An inversion is in-sample falling while truth rises.")
    for plant in ("mak", "generic"):
        say()
        say(f"  === {plant} ===  (each cell insample/truth, C)")
        say(f"  {'profile':15s} " + " ".join(f"{str(L) + 's':>15s}" for L in sorted(LENGTHS_S)))
        for profile in profiles():
            cells = []
            for L in sorted(LENGTHS_S):
                m = [r for r in sim_rows if r["plant"] == plant and r["profile"] == profile and r["length_s"] == L]
                cells.append(f"{m[0]['insample']['cand']:6.2f}/{m[0]['truth_cand']:8.2f}" if m else "--")
            say(f"  {profile:15s} " + " ".join(f"{c:>15s}" for c in cells))

    # ------------------------------------------------- confusion matrices
    say()
    say("=" * 104)
    say("SECTION 2 -- what each signal actually decides")
    say("=" * 104)
    say("Label: a promotion is JUSTIFIED iff the candidate predicts the plant's unseen behaviour")
    say("better than the incumbent does, truth_rmse(candidate) < truth_rmse(incumbent). No threshold")
    say("and no constant enters that label. Everything below is model_promotion.evaluate itself,")
    say("with only the pair of RMSEs handed to it varied.")
    say()
    say(
        f"{'incumbent':11s} {'signal':20s} {'n':>4s} {'TP':>4s} {'FP':>4s} {'TN':>4s} {'FN':>4s} {'wrong':>7s} {'worst FP c_err':>15s} {'worst FP truth':>15s}"
    )
    say("-" * 104)
    for inc_name, inc_of in incumbents.items():
        for sig_label, sig_key in SIGNALS:
            tp = fp = tn = fn = 0
            worst_c = 0.0
            worst_t = 0.0
            for r in sim_rows:
                if inc_name == "calibrated" and r["profile"] == REFERENCE[0] and r["length_s"] == REFERENCE[1]:
                    continue  # the incumbent IS this row's fit; nothing to decide
                justified = r["truth_cand"] < inc_truth_of[inc_name][r["plant"]]
                accepted, _ = gate_verdict(
                    r, inc_of(r), r[sig_key].get("cand", float("nan")), r[sig_key].get(inc_name, float("nan"))
                )
                if accepted and justified:
                    tp += 1
                elif accepted and not justified:
                    fp += 1
                    if math.isfinite(r["coast_err"]):
                        worst_c = min(worst_c, r["coast_err"])
                    worst_t = max(worst_t, r["truth_cand"] - inc_truth_of[inc_name][r["plant"]])
                elif not accepted and justified:
                    fn += 1
                else:
                    tn += 1
            n = tp + fp + tn + fn
            say(
                f"{inc_name:11s} {sig_label:20s} {n:>4d} {tp:>4d} {fp:>4d} {tn:>4d} {fn:>4d} "
                f"{(fp + fn) / n:>7.1%} {worst_c:>+15.2f} {worst_t:>+15.2f}"
            )
    say()
    say("A false positive is a model put on a grill that predicts it WORSE than what it replaced.")
    say("'worst FP c_err' is the most under-predicted coast among those false positives, in C -- the")
    say("size of the braking error the gate let through. 'worst FP truth' is how much worse than the")
    say("incumbent the worst accepted model was, in C of pooled prediction error.")

    # ------------------------------------------- Q3: the uninformative record
    say()
    say("=" * 104)
    say("SECTION 3 -- what separates an uninformative record from an informative one")
    say("=" * 104)
    say("UNINFORM (structural): the flat synthetic cooks and the plant's own steady_hold -- one")
    say("transient-free operating point by construction. INFORM (behavioural, constant-free): the")
    say("fit beats the shipped incumbent's truth error and does not worsen its coast reading, i.e.")
    say("a record the gate ought to let through. 'other' records are shown but do not draw the line.")
    say()
    counts = {}
    for r in rows:
        counts[klass(r)] = counts.get(klass(r), 0) + 1
    say("class sizes: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    say()
    stats = ("s_min", "cond", "len_over_tau", "q_std", "q_range", "q_levels", "temp_span", "insample_cand")

    def stat_of(r, st):
        return r["insample"]["cand"] if st == "insample_cand" else r[st]

    say(
        f"{'statistic':16s} {'max over UNINFORM':>18s} {'min over INFORM':>16s} {'separates?':>11s} {'margin (ratio)':>15s}"
    )
    say("-" * 82)
    for st in stats:
        un = [stat_of(r, st) for r in rows if klass(r) == "UNINFORM" and math.isfinite(stat_of(r, st))]
        inf_ = [stat_of(r, st) for r in rows if klass(r) == "INFORM" and math.isfinite(stat_of(r, st))]
        if not un or not inf_:
            continue
        hi_un, lo_in = max(un), min(inf_)
        ratio = (lo_in / hi_un) if hi_un > 0 else float("inf")
        say(f"{st:16s} {hi_un:>18.5g} {lo_in:>16.5g} {('YES' if hi_un < lo_in else 'no'):>11s} {ratio:>15.4g}")
    say()
    say("For in-sample RMSE the columns read the other way round on purpose: a LOW in-sample RMSE is")
    say("what the gate rewards, so 'separates? no' with the UNINFORM column BELOW the INFORM one")
    say("means the signal is not merely useless there but inverted.")
    say()
    say("The records around the boundary, sorted by s_min (everything with s_min < 1, plus every")
    say("UNINFORM and INFORM row):")
    say(
        f"{'class':9s} {'plant':8s} {'profile':15s} {'len_s':>6s} {'s_min':>10s} {'cond':>9s} {'L/tau':>6s} {'insamp':>7s} {'truth':>8s} {'c_err':>7s} {'theta':>7s}"
    )
    say("-" * 104)
    for r in sorted(rows, key=lambda r: r["s_min"] if math.isfinite(r["s_min"]) else 1e18):
        k = klass(r)
        if k in ("other", "real") and not (math.isfinite(r["s_min"]) and r["s_min"] < 1.0):
            continue
        say(
            f"{k:9s} {str(r['plant']):8s} {r['profile']:15s} {r['length_s']:>6d} "
            f"{fmt(r['s_min'], 10, 6)} {fmt(r['cond'], 9, 1)} {fmt(r['len_over_tau'], 6, 2)} "
            f"{fmt(r['insample']['cand'], 7, 2)} {fmt(r['truth_cand'], 8, 2)} {fmt(r['coast_err'], 7, 2)} "
            f"{fmt(r['fit']['theta'], 7, 1)}"
        )

    # -------------------------------------------------- Q4: the real MAK cook
    say()
    say("=" * 104)
    say("SECTION 4 -- where the real MAK cook falls")
    say("=" * 104)
    say("No truth column: there is no plant behind this record. MAKGrillSim was identified FROM this")
    say("cook, so scoring a fit of it against that plant would be the identification talking.")
    say()
    say(
        f"{'len_s':>6s} {'n':>4s} {'cv':>3s} {'insamp_c':>9s} {'insamp_i':>9s} {'ho_cold_c':>9s} {'ho_cold_i':>9s} "
        f"{'ho_warm_c':>9s} {'ho_warm_i':>9s} {'s_min':>10s} {'cond':>9s} {'L/tau':>6s} {'C_c':>9s} {'K_Q':>8s} {'theta':>7s}"
    )
    say("-" * 128)
    for r in sorted(real_rows, key=lambda r: -r["length_s"]):
        f_ = r["fit"]
        say(
            f"{r['length_s']:>6d} {r['n']:>4d} {('y' if r['converged'] else 'N'):>3s} "
            f"{fmt(r['insample']['cand'], 9)} {fmt(r['insample']['shipped'], 9)} "
            f"{fmt(r['cold'].get('cand'), 9)} {fmt(r['cold'].get('shipped'), 9)} "
            f"{fmt(r['warm'].get('cand'), 9)} {fmt(r['warm'].get('shipped'), 9)} "
            f"{fmt(r['s_min'], 10, 6)} {fmt(r['cond'], 9, 1)} {fmt(r['len_over_tau'], 6, 2)} "
            f"{fmt(f_['C_c'], 9, 1)} {fmt(f_['K_Q'], 8, 3)} {fmt(f_['theta'], 7, 1)}"
        )
    say()
    say("what the gate says about the full cook, today and with each signal swapped in:")
    full = max(real_rows, key=lambda r: r["length_s"])
    for sig_label, sig_key in SIGNALS:
        cand, inc = full[sig_key].get("cand", float("nan")), full[sig_key].get("shipped", float("nan"))
        acc, reason = gate_verdict(full, SHIPPED, cand, inc)
        say(f"  {sig_label:20s} cand={cand:.4f} inc={inc:.4f} -> {'ACCEPT' if acc else 'refuse'}: {reason}")
    say()
    say("prefix fit vs full fit on the real cook (what the split costs it):")
    say("  full  : " + " ".join(f"{k}={full['fit'][k]:.5g}" for k in ("C_c", "K_Q", "theta")))
    say("  prefix: " + " ".join(f"{k}={full['pre_fit'][k]:.5g}" for k in ("C_c", "K_Q", "theta")))

    # ------------------------------------------------------------ flat cooks
    say()
    say("=" * 104)
    say("SECTION 5 -- the uninformative cooks in detail")
    say("=" * 104)
    say("flat_synth_0.05 at 1995s is the record tests/unit/mpc/test_mpc_refit.py pins verbatim.")
    say("steady_hold is the plant's own version: warmed to steady state at 50% duty, then logged, so")
    say("it carries a truth error the synthetic one cannot.")
    say()
    say(
        f"{'plant':8s} {'profile':15s} {'len_s':>6s} {'insamp':>8s} {'s_min':>11s} {'cond':>10s} {'theta':>8s} "
        f"{'C_c':>9s} {'truth':>8s} {'c_err':>7s} {'gate today':>11s}"
    )
    say("-" * 112)
    for r in sorted(
        flat_rows + [x for x in rows if x["profile"] == "steady_hold"],
        key=lambda r: (str(r["plant"]), r["profile"], -r["length_s"]),
    ):
        acc, _ = gate_verdict(r, SHIPPED, r["insample"]["cand"], r["insample"]["shipped"])
        say(
            f"{str(r['plant']):8s} {r['profile']:15s} {r['length_s']:>6d} "
            f"{fmt(r['insample']['cand'], 8, 4)} {fmt(r['s_min'], 11, 7)} {fmt(r['cond'], 10, 1)} "
            f"{fmt(r['fit']['theta'], 8, 2)} {fmt(r['fit']['C_c'], 9, 1)} {fmt(r['truth_cand'])} "
            f"{fmt(r['coast_err'], 7, 2)} {('ACCEPT' if acc else 'refuse'):>11s}"
        )

    # ---------------------------------------------------------- the boundary
    say()
    say("=" * 104)
    say("SECTION 6 -- the numbers a threshold can be read off")
    say("=" * 104)
    for st in ("s_min", "cond", "len_over_tau"):
        un = [(stat_of(r, st), r) for r in rows if klass(r) == "UNINFORM" and math.isfinite(stat_of(r, st))]
        inf_ = [(stat_of(r, st), r) for r in rows if klass(r) == "INFORM" and math.isfinite(stat_of(r, st))]
        if not un or not inf_:
            continue
        hi, lo = max(un, key=lambda x: x[0]), min(inf_, key=lambda x: x[0])
        say()
        say(f"{st}:")
        say(f"  highest UNINFORM : {hi[0]:<14.6g} {hi[1]['plant']}/{hi[1]['profile']}/{hi[1]['length_s']}s")
        say(f"  lowest  INFORM   : {lo[0]:<14.6g} {lo[1]['plant']}/{lo[1]['profile']}/{lo[1]['length_s']}s")
        if hi[0] > 0 and lo[0] > hi[0]:
            say(f"  gap              : {lo[0] / hi[0]:.4g}x    geometric midpoint {math.sqrt(hi[0] * lo[0]):.6g}")
        else:
            say("  the two overlap -- no threshold on this statistic separates them")
        say(
            "  real MAK cook    : "
            + ", ".join(
                f"{stat_of(r, st):.5g}@{r['length_s']}s" for r in sorted(real_rows, key=lambda r: r["length_s"])
            )
        )
    say()
    say("Held-out margin: the ratio candidate/incumbent on the given signal, over records where")
    say("promotion is justified against that incumbent versus where it is not. A gate that decides")
    say("on this ratio alone needs the two populations to separate.")
    say()
    say(
        f"{'incumbent':11s} {'signal':14s} {'justified: max ratio':>21s} {'unjustified: min ratio':>23s} {'separates?':>11s}"
    )
    say("-" * 84)
    for inc_name in incumbents:
        for sig_label, sig_key in (("held-out cold", "cold"), ("held-out warm", "warm"), ("in-sample", "insample")):
            j, u = [], []
            for r in sim_rows:
                if inc_name == "calibrated" and r["profile"] == REFERENCE[0] and r["length_s"] == REFERENCE[1]:
                    continue
                c, i = r[sig_key].get("cand"), r[sig_key].get(inc_name)
                if c is None or i is None or not (math.isfinite(c) and math.isfinite(i)) or i <= 0:
                    continue
                (j if r["truth_cand"] < inc_truth_of[inc_name][r["plant"]] else u).append(c / i)
            if not j or not u:
                continue
            say(
                f"{inc_name:11s} {sig_label:14s} {max(j):>21.4g} {min(u):>23.4g} {('YES' if max(j) < min(u) else 'no'):>11s}"
            )

    # -------------------------------------------------------- threshold sweep
    say()
    say("=" * 104)
    say("SECTION 7 -- an informativeness test in front of the gate, swept over its threshold")
    say("=" * 104)
    say("Each rule is model_promotion.evaluate exactly as it ships, on the named fit-quality")
    say("signal, AND a demand that one statistic clear a threshold. Every threshold offered is a")
    say("value some record in this run actually took, so each row of the sweep is traceable to a")
    say("row of Appendix A. Two error counts are kept:")
    say("  wrong  = accepted when unjustified, or refused when justified (as in SECTION 2)")
    say("  DANGER = accepted AND the adopted model reads a SHORTER coast than the one it replaced.")
    say("           That is the brakes-late shape, and it is the count a safety gate must zero.")
    say()
    say("DANGER is counted on the MAK plant only, and the generic count is carried beside it for")
    say("inspection. The generic plant's own coast at the probe's reference is 0.1 C against MAK's")
    say("25.1 C, so 'shorter coast' there is a comparison between two roundings; MAK is also the")
    say("grill that actually overshot. A count that let 0.4 C of generic noise outvote 25 C of MAK")
    say("would be measuring the wrong plant.")
    say()

    #: (label, per-row accessor, "higher passes"). Reciprocal-conditioning and
    #: the split statistics are included in their natural direction rather than
    #: inverted, so a threshold reads as the quantity a gate would name.
    STATS = (
        ("s_min@fit", lambda r, inc: r["s_min"], True),
        ("s_min@incumbent", lambda r, inc: r.get(f"s_min_{inc}", float("nan")), True),
        ("inv_cond", lambda r, inc: r["inv_cond"], True),
        ("temp_span", lambda r, inc: r["temp_span"], True),
        ("len_over_tau", lambda r, inc: r["len_over_tau"], True),
        ("q_std", lambda r, inc: r["q_std"], True),
        ("split_disagree", lambda r, inc: r["split_disagree"], False),
        ("split_brake_ratio", lambda r, inc: r["split_brake_ratio"], False),
        ("split_tau_ratio", lambda r, inc: r["split_tau_ratio"], False),
        ("brake_vs_inc", lambda r, inc: brake_vs_inc(r, inc), True),
        ("(none)", lambda r, inc: 1.0, True),
    )

    #: The braking distance each incumbent implies, so a candidate's can be
    #: compared to it directly. This is the quantity the horizon is sized from
    #: and the one a brakes-late model gets wrong, and both sides of the
    #: comparison are read off models -- no plant, no record, so a live gate can
    #: compute it.
    inc_brake_of = {
        name: {p: promo.longest_braking_distance(of({"plant": p})) for p in ("mak", "generic")}
        for name, of in incumbents.items()
    }
    # The real cook and the flat cooks have no plant. Only the shipped incumbent
    # is defined for them, and it does not depend on one.
    inc_brake_of["shipped"][None] = promo.longest_braking_distance(SHIPPED)

    def brake_vs_inc(r, inc):
        """Candidate braking distance over the incumbent's. Below 1 is a shortening."""
        b = inc_brake_of[inc].get(r["plant"])
        if b is None or not (math.isfinite(b) and b > 0) or not math.isfinite(r["brake_s"]):
            return float("nan")
        return r["brake_s"] / b

    def population(inc_name):
        return [
            r
            for r in sim_rows
            if not (inc_name == "calibrated" and r["profile"] == REFERENCE[0] and r["length_s"] == REFERENCE[1])
        ]

    #: The incumbent's own coast reading, so "shorter than what it replaced" is
    #: a comparison against a fixed number rather than a re-simulation.
    inc_coast_of = {
        name: {p: truth_error(of({"plant": p}), p)[2] for p in ("mak", "generic")} for name, of in incumbents.items()
    }

    #: Per (incumbent, signal, row): what evaluate() says and what accepting
    #: would mean. Computed once; the threshold sweep below only filters it, so
    #: sweeping a statistic costs no further model evaluations.
    base = {}
    for inc_name, inc_of in incumbents.items():
        for _lbl, sig_key in SIGNALS:
            recs = []
            for r in population(inc_name):
                ok, _ = gate_verdict(
                    r, inc_of(r), r[sig_key].get("cand", float("nan")), r[sig_key].get(inc_name, float("nan"))
                )
                inc_c = inc_coast_of[inc_name][r["plant"]]
                shorter = math.isfinite(r["coast_err"]) and math.isfinite(inc_c) and r["coast_err"] < inc_c
                recs.append(
                    dict(
                        row=r,
                        ok=ok,
                        justified=r["truth_cand"] < inc_truth_of[inc_name][r["plant"]],
                        danger=shorter and r["plant"] == "mak",
                        danger_gen=shorter and r["plant"] != "mak",
                        c_excess=(r["coast_err"] - inc_c)
                        if math.isfinite(r["coast_err"]) and math.isfinite(inc_c)
                        else 0.0,
                        t_excess=r["truth_cand"] - inc_truth_of[inc_name][r["plant"]],
                    )
                )
            base[(inc_name, sig_key)] = recs

    def score_pred(inc_name, sig_key, passes_fn):
        """(n, accepted, TP, FP, FN, danger_mak, danger_generic, worst danger c_err, worst FP truth)."""
        acc = tp = fp = fn = danger = dgen = 0
        worst_c = 0.0
        worst_t = 0.0
        recs = base[(inc_name, sig_key)]
        for e in recs:
            accepted = e["ok"] and passes_fn(e["row"], inc_name)
            acc += accepted
            if accepted and e["justified"]:
                tp += 1
            elif accepted:
                fp += 1
                worst_t = max(worst_t, e["t_excess"])
            elif e["justified"]:
                fn += 1
            if accepted and e["danger"]:
                danger += 1
                worst_c = min(worst_c, e["c_excess"])
            if accepted and e["danger_gen"]:
                dgen += 1
        return len(recs), acc, tp, fp, fn, danger, dgen, worst_c, worst_t

    def score(inc_name, sig_key, stat_get, higher_passes, thr):
        """(n, accepted, wrong, danger, worst danger c_err, worst FP truth excess)."""

        def passes(r, inc):
            v = stat_get(r, inc)
            return bool(math.isfinite(v) and ((v >= thr) if higher_passes else (v <= thr)))

        n, acc, _tp, fp, fn, danger, _dg, worst_c, worst_t = score_pred(inc_name, sig_key, passes)
        return n, acc, fp + fn, danger, worst_c, worst_t

    for inc_name in incumbents:
        say()
        say(f"### incumbent = {inc_name} " + "-" * (104 - 20 - len(inc_name)))
        say(
            f"{'signal':14s} {'statistic':18s} {'best threshold':>15s} {'n':>4s} {'acc':>4s} {'wrong':>6s} "
            f"{'DANGER':>7s} {'worst c_err':>12s} | {'zero-DANGER thr':>16s} {'wrong there':>12s} {'real cook':>11s}"
        )
        say("-" * 122)
        for sig_label, sig_key in SIGNALS:
            for stat_label, stat_get, higher in STATS:
                vals = sorted(
                    {stat_get(r, inc_name) for r in population(inc_name) if math.isfinite(stat_get(r, inc_name))}
                )
                if not vals:
                    continue
                # Candidate thresholds: every observed value, plus one past each
                # end so "let everything through" and "let nothing through" are
                # both in the sweep.
                cands = [-math.inf] + vals + [math.inf] if higher else [math.inf] + vals + [-math.inf]
                scored = [(thr, score(inc_name, sig_key, stat_get, higher, thr)) for thr in cands]
                best = min(scored, key=lambda x: (x[1][2], x[1][3]))
                zero = [(thr, s) for thr, s in scored if s[3] == 0]
                if higher:
                    zd = min(zero, key=lambda x: x[0]) if zero else None
                else:
                    zd = max(zero, key=lambda x: x[0]) if zero else None
                rc_val = stat_get(full, "shipped") if stat_label != "(none)" else 1.0
                rc = "--"
                if zd is not None and math.isfinite(rc_val):
                    rc = "PASS" if ((rc_val >= zd[0]) if higher else (rc_val <= zd[0])) else "refused"
                    rc = f"{rc_val:.4g} {rc}"
                n, acc, wrong, dang, wc, _wt = best[1]
                say(
                    f"{sig_label:14s} {stat_label:18s} {best[0]:>15.5g} {n:>4d} {acc:>4d} {wrong:>6d} "
                    f"{dang:>7d} {wc:>+12.2f} | "
                    + (
                        f"{zd[0]:>16.5g} {zd[1][2]:>12d} {rc:>11s}"
                        if zd
                        else f"{'none exists':>16s} {'--':>12s} {rc:>11s}"
                    )
                )
        say("'best threshold' minimises wrong decisions, then DANGER. 'zero-DANGER thr' is the loosest")
        say("threshold at which no accepted model reads a shorter coast than the one it replaced, and")
        say("'wrong there' is what that costs in total wrong decisions. 'real cook' is the full 1240 s")
        say("MAK cook's value of the statistic and whether it clears the zero-DANGER threshold.")

    say()
    say("Where the real MAK cook sits on every statistic, at each truncation:")
    say(f"{'len_s':>6s} " + " ".join(f"{lbl:>18s}" for lbl, _, _ in STATS[:-1]))
    say("-" * 104)
    for r in sorted(real_rows, key=lambda r: -r["length_s"]):
        say(f"{r['length_s']:>6d} " + " ".join(f"{fmt(g(r, 'shipped'), 18, 5)}" for _, g, _ in STATS[:-1]))
    say()
    say("and on the flat cooks, for the same comparison:")
    say(f"{'plant':8s} {'profile':15s} {'len_s':>6s} " + " ".join(f"{lbl:>18s}" for lbl, _, _ in STATS[:-1]))
    say("-" * 122)
    for r in sorted(
        flat_rows + [x for x in rows if x["profile"] == "steady_hold"],
        key=lambda r: (str(r["plant"]), r["profile"], -r["length_s"]),
    ):
        say(
            f"{str(r['plant']):8s} {r['profile']:15s} {r['length_s']:>6d} "
            + " ".join(f"{fmt(g(r, 'shipped'), 18, 5)}" for _, g, _ in STATS[:-1])
        )

    # ------------------------------------------------------- compound rules
    say()
    say("=" * 104)
    say("SECTION 8 -- rules built from more than one test")
    say("=" * 104)
    say("No single statistic in SECTION 7 both keeps the justified promotions and zeroes DANGER, so")
    say("the conjunctions are measured here. Every threshold quoted is a quantile of the values this")
    say("run observed, printed with the quantile it came from, so it is traceable to Appendix B.")
    say()

    def quantile(vals, q):
        v = sorted(x for x in vals if math.isfinite(x))
        return v[min(len(v) - 1, max(0, int(round(q * (len(v) - 1)))))] if v else float("nan")

    all_smin = [r["s_min"] for r in sim_rows]
    all_brk = [r["split_brake_ratio"] for r in sim_rows]
    smin_grid = [(q, quantile(all_smin, q)) for q in (0.0, 0.25, 0.5, 0.6, 0.75, 0.9)]
    brk_grid = [(q, quantile(all_brk, q)) for q in (1.0, 0.9, 0.75, 0.5, 0.25, 0.1)]
    say(
        "s_min@fit thresholds (quantiles of the 176 simulated records): "
        + ", ".join(f"q{q:.2f}={v:.4g}" for q, v in smin_grid)
    )
    say(
        "split_brake_ratio thresholds (same):                          "
        + ", ".join(f"q{q:.2f}={v:.4g}" for q, v in brk_grid)
    )
    say()
    for inc_name in incumbents:
        for brake_gate in (False, True):
            say(
                f"### incumbent={inc_name}  signal=held-out warm  "
                + (
                    "plus brake_vs_inc >= 1.0 (candidate may not shorten the braking distance)"
                    if brake_gate
                    else "no braking-direction test"
                )
            )
            say(f"  cells are TP/FP/DANGER over n={len(base[(inc_name, 'warm')])}")
            say(f"  {'s_min >=':>12s} " + " ".join(f"{'brk<=' + f'{v:.3g}':>16s}" for _q, v in brk_grid))
            for _qs, ts in smin_grid:
                cells = []
                for _qb, tb in brk_grid:

                    def passes(r, inc, ts=ts, tb=tb, bg=brake_gate):
                        if not (math.isfinite(r["s_min"]) and r["s_min"] >= ts):
                            return False
                        if not (math.isfinite(r["split_brake_ratio"]) and r["split_brake_ratio"] <= tb):
                            return False
                        if bg:
                            b = brake_vs_inc(r, inc)
                            if not (math.isfinite(b) and b >= 1.0):
                                return False
                        return True

                    _n, _a, tp, fp, _fn, dg, _dgen, _wc, _wt = score_pred(inc_name, "warm", passes)
                    cells.append(f"{tp}/{fp}/{dg}")
                say(f"  {ts:>12.4g} " + " ".join(f"{c:>16s}" for c in cells))
            say()

    say("Named rules, side by side. 'today' is the shipped gate; every other row is the shipped")
    say("evaluate() with the stated additions. T and B are the q0.60 and q0.25 grid values above.")
    say()
    T_S = quantile(all_smin, 0.60)
    B_S = quantile(all_brk, 0.25)

    def _always(r, inc):
        return True

    def _smin(r, inc):
        return math.isfinite(r["s_min"]) and r["s_min"] >= T_S

    def _brk(r, inc):
        return math.isfinite(r["split_brake_ratio"]) and r["split_brake_ratio"] <= B_S

    def _dir(r, inc):
        b = brake_vs_inc(r, inc)
        return math.isfinite(b) and b >= 1.0

    RULES = (
        ("today (in-sample)", "insample", _always),
        ("held-out warm", "warm", _always),
        ("held-out warm + dir", "warm", _dir),
        (f"held-out warm + s_min>={T_S:.3g}", "warm", _smin),
        (f"held-out warm + split_brk<={B_S:.3g}", "warm", _brk),
        ("held-out warm + s_min + split_brk", "warm", lambda r, i: _smin(r, i) and _brk(r, i)),
        ("held-out warm + all three", "warm", lambda r, i: _smin(r, i) and _brk(r, i) and _dir(r, i)),
        ("in-sample + all three", "insample", lambda r, i: _smin(r, i) and _brk(r, i) and _dir(r, i)),
        ("never promote", "warm", lambda r, i: False),
    )
    say(
        f"{'incumbent':11s} {'rule':36s} {'n':>4s} {'acc':>4s} {'TP':>4s} {'FP':>4s} {'FN':>4s} "
        f"{'DANGER':>7s} {'d_gen':>6s} {'worst c_err':>12s} {'worst FP truth':>15s}"
    )
    say("-" * 116)
    for inc_name in incumbents:
        for label, sig_key, pred in RULES:
            n, acc, tp, fp, fn, dg, dgen, wc, wt = score_pred(inc_name, sig_key, pred)
            say(
                f"{inc_name:11s} {label:36s} {n:>4d} {acc:>4d} {tp:>4d} {fp:>4d} {fn:>4d} "
                f"{dg:>7d} {dgen:>6d} {wc:>+12.2f} {wt:>+15.2f}"
            )
    say()
    say("And what each rule would do with the real MAK cook (full 1240 s, shipped incumbent):")
    say(
        f"  s_min={full['s_min']:.4g} (needs >= {T_S:.4g})   split_brake_ratio={full['split_brake_ratio']:.4g} (needs <= {B_S:.4g})"
    )
    say(f"  split_disagree={full['split_disagree']:.4g} C between its own two halves' fits over the fixed probe")
    say(f"  brake_vs_inc={brake_vs_inc(full, 'shipped'):.4g} (needs >= 1.0)")
    for label, sig_key, pred in RULES:
        cand, inc = full[sig_key].get("cand", float("nan")), full[sig_key].get("shipped", float("nan"))
        ok, _ = gate_verdict(full, SHIPPED, cand, inc)
        say(f"  {label:36s} -> {'ACCEPT' if (ok and pred(full, 'shipped')) else 'refuse'}")

    # ------------------------------------------------- the operating point
    say()
    say("=" * 104)
    say("SECTION 9 -- the operating point, and the three bounds it has to clear")
    say("=" * 104)
    b_uninform = max(r["s_min"] for r in rows if klass(r) == "UNINFORM" and math.isfinite(r["s_min"]))
    b_safe = {}
    for inc_name in incumbents:
        for sig_label, sig_key in SIGNALS:
            vals = sorted({r["s_min"] for r in population(inc_name) if math.isfinite(r["s_min"])})
            hit = None
            for thr in vals + [math.inf]:

                def passes(r, inc, thr=thr):
                    return math.isfinite(r["s_min"]) and r["s_min"] >= thr

                if score_pred(inc_name, sig_key, passes)[5] == 0:
                    hit = thr
                    break
            b_safe[(inc_name, sig_key)] = hit
    say("A floor on s_min@fit has to clear all of:")
    say(f"  (1) every uninformative record: max s_min over the UNINFORM class = {b_uninform:.6g}")
    for (inc_name, sig_key), v in b_safe.items():
        say(f"  (2) zero DANGER, incumbent={inc_name:10s} signal={sig_key:9s}: s_min >= {v:.6g}")
    floor = max([b_uninform] + [v for v in b_safe.values() if v is not None and math.isfinite(v)])
    say(f"  --> the binding bound is {floor:.6g}; rounding up gives the value carried below.")
    REC = round(floor + 0.005, 2)
    say(
        f"  --> RECOMMENDED FLOOR s_min >= {REC:.2f} C RMS per e-fold ({REC / b_uninform:.2f}x the worst uninformative record)"
    )
    say()
    say(f"What that floor does, against every incumbent and every fit-quality signal:")
    say(
        f"{'incumbent':11s} {'signal':20s} {'floor':>6s} {'n':>4s} {'acc':>4s} {'TP':>4s} {'FP':>4s} {'FN':>4s} "
        f"{'DANGER':>7s} {'d_gen':>6s} {'worst c_err':>12s} {'worst FP truth':>15s}"
    )
    say("-" * 112)
    for inc_name in incumbents:
        for sig_label, sig_key in SIGNALS:
            for thr, tag in ((-math.inf, "off"), (REC, f"{REC:.2f}")):

                def passes(r, inc, thr=thr):
                    return math.isfinite(r["s_min"]) and r["s_min"] >= thr

                n, acc, tp, fp, fn, dg, dgen, wc, wt = score_pred(inc_name, sig_key, passes)
                say(
                    f"{inc_name:11s} {sig_label:20s} {tag:>6s} {n:>4d} {acc:>4d} {tp:>4d} {fp:>4d} {fn:>4d} "
                    f"{dg:>7d} {dgen:>6d} {wc:>+12.2f} {wt:>+15.2f}"
                )
    say()
    say("Every record the floor refuses that today's gate accepts, and every record it admits, by class:")
    for name, pred in (("UNINFORM", lambda r: klass(r) == "UNINFORM"), ("INFORM", lambda r: klass(r) == "INFORM")):
        vals = [r["s_min"] for r in rows if pred(r) and math.isfinite(r["s_min"])]
        passed = [v for v in vals if v >= REC]
        say(f"  {name:9s}: {len(passed)} of {len(vals)} clear the floor  (max {max(vals):.5g}, min {min(vals):.5g})")
    say(
        "  real MAK cook: "
        + ", ".join(
            f"{r['length_s']}s={r['s_min']:.4g}{'' if r['s_min'] >= REC else ' (refused)'}"
            for r in sorted(real_rows, key=lambda r: r["length_s"])
        )
    )
    say()
    say("For scale: controller/grill_sim.py's sensor noise is 0.15 C per sample and the real cook's")
    say(f"fixture resolves to 0.001 C, so a floor of {REC:.2f} C RMS per e-fold asks that the worst-determined")
    say("parameter direction move the prediction by more than a sensor's worth of degrees before the")
    say("record is allowed to speak about it.")

    say()
    say(f"total elapsed {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
