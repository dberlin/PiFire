#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

Fits the grey-box thermal parameters to one typed SQLite MPC control-trace
session. Fitting runs through controller.mpc_model's shared forward simulator,
so the parameters produced describe the same dynamics the controller plans
against -- radiative loss and transport deadtime included.

The selected session must contain uninterrupted, completed MPC control updates
and same-revision applied combustion loads. Capture the cook with the fan under
the controller's command: a trace taken with the fan pinned at one duty only
describes the grill at that duty.

Usage: python -m controller.update_mpc (--cook COOK_ID | --session SESSION_ID)
       [--database PATH] [--t-amb 20] [--json]
*****************************************
"""

import argparse
import json
import os
import math
import sqlite3
import sys

import numpy as np
from scipy.optimize import least_squares

from common.control_trace import (
    ActuationMode,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    FixedCycleFramePayload,
    FramedPulseFramePayload,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    RecorderGapPayload,
    SessionPayload,
    TRACE_SCHEMA_VERSION,
)
from common.datastore_accessors import read_control_trace_cook, read_control_trace_session
from controller.applied_output import OutputSource

from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau, steady_state_at_full_fire
from controller.mpc_model import simulate_grey_box

# Keys the controller reads back out of a fitted result.
CONFIG_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

# Fitted free parameters. h_amb and sigma are held at their init values.
#
# WHY ONE OF THEM MUST BE HELD. The dynamics are invariant under scaling the
# chamber capacitance, both loss coefficients and the input gain --
# (C_c, h_amb, sigma, K_Q) -- by one common factor, because the state equation
# is homogeneous in them, so the trajectory of the one measured state is
# bit-identical. What a log determines is the ratios among them, which is what
# the controller plans against: the effective time constant
# C_c/(h_amb + 4*sigma*(T+273.15)**3) is one of them, and is unchanged by which
# parameter is held. See docs/superpowers/experiments/sigma_identifiability.py.
#
# WHY h_amb AND sigma ARE BOTH HELD, not just one. Holding one of the four
# fixes that scaling, and what a log leaves undetermined after that is the
# SPLIT of the chamber's loss between its linear and radiative parts: h_amb and
# sigma trade against each other with C_c following, at essentially no residual
# cost. Leaving either free lets the solve run away along that trade -- with
# h_amb free the real MAK cook lands at C_c 2.6e7 and h_amb 7.4e3, an order of
# magnitude past model_promotion.PROMOTION_BOUNDS, so evaluate() refuses the
# model however well it describes the log; with sigma free it goes the other
# way, to an all-radiative model at sigma 5e-3 and C_c 3e8. Holding both keeps
# every fit inside the bounds. The price is that the radiative share is fixed
# rather than fitted, so a grill whose share differs is described by a model
# carrying the right C_c/h_amb and the wrong split. That model mismatch is
# measured in tests/unit/mpc/test_model_promotion.py.
#
# WHAT THE THREE FREE ONES ARE. They are exactly the directions a cook
# determines. K_Q/C_c, the steady input gain, is the best-determined quantity
# in the model -- reproducible to 0.5% across nine cooks including ones where
# the raw parameters ran away by 800x. C_c against the held conductances is the
# effective time constant, recovered to within 2% of truth at every ambient-loss
# level from 0.25x to 4x nominal. theta is the only parameter sharply
# identifiable on every record measured including the real 1240 s cook, and the
# largest single lever on both dead time and coast.
_FREE = ("K_Q", "C_c", "theta")

_SIM_KEYS = ("C_c", "h_amb", "K_Q", "sigma", "theta", "n_delay")

# Parameters a caller supplies a starting value for. `_FREE` selects which of
# these the solve moves; the rest are held at the value they came in with.
_FIT_KEYS = ("C_c", "h_amb", "K_Q", "sigma", "theta")

# Strictly positive: theta divides the lag time constant, and every other free
# parameter is a capacitance or a conductance. The solve works in log space
# (see `fit_params`), so this is expressed as a floor on the logarithm and the
# positivity itself is structural rather than a constraint the solver enforces.
_LOWER_BOUND = 1e-9

# Evaluations the solve is allowed. Not enough to converge on every log -- see
# `fit_params`, which reports whether it did rather than presenting an
# exhausted solve as a finished one.
_MAX_NFEV = 2000

# The per-sample residual reported for a parameter set the model cannot be
# simulated at. 1e4 degrees C is far outside anything a cook contains, so the
# solve treats such a point as very bad, which is what it is; what matters is
# that it is a NUMBER, because a NaN residual is not comparable to anything and
# a trust region cannot step away from what it cannot compare.
_DIVERGED = 1e4


class TraceSelectionError(ValueError):
    """The selected control trace cannot provide an unambiguous fit input."""


def load_trace_samples(
    *,
    cook_id: str | None = None,
    session_id: str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load one complete, uninterrupted MPC session as fitting arrays.

    Hold labels each applied interval with the command that produced it. The
    returned temperature/load pairs therefore use each update except the final
    one and its own same-revision complete applied output. An optional revision
    zero SEED interval is ignored because it predates the first accepted command.
    """
    if (cook_id is None) == (session_id is None):
        raise TraceSelectionError("select exactly one of cook_id or session_id")

    try:
        records = (
            read_control_trace_cook(cook_id, database_path=database_path)
            if cook_id is not None
            else read_control_trace_session(session_id, database_path=database_path)
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise TraceSelectionError(f"could not read selected control trace: {exc}") from exc

    if not records:
        raise TraceSelectionError("selected control trace contains no records")
    sessions = {record.session_id for record in records}
    if len(sessions) != 1:
        raise TraceSelectionError("selected cook contains more than one control session; select one session_id")
    if any(record.schema_version != TRACE_SCHEMA_VERSION for record in records):
        raise TraceSelectionError("selected control trace has an incompatible schema version")
    if any(record.controller is not ControllerType.MPC for record in records):
        raise TraceSelectionError("selected control trace mixes controller types")
    if any(isinstance(record.payload, RecorderGapPayload) for record in records):
        raise TraceSelectionError("selected control trace contains a recorder gap")

    session_records = [record for record in records if isinstance(record.payload, SessionPayload)]
    if len(session_records) != 1 or session_records[0].payload.controller is not ControllerType.MPC:
        raise TraceSelectionError("selected records do not describe exactly one MPC trace session")

    previous_ts = -1
    for record in records:
        if record.ts_ms < previous_ts:
            raise TraceSelectionError("selected control trace timestamps are not ordered")
        previous_ts = record.ts_ms

    updates: list[tuple[int, MpcUpdatePayload]] = []
    complete_outputs: dict[int, list[tuple[int, AppliedOutputPayload]]] = {}
    seed_outputs = 0
    partial_outputs: list[tuple[int, AppliedOutputPayload]] = []
    actuated_revisions: set[int] = set()
    previous_revision = -1
    previous_wall_ms = -1
    seed_allowed = False
    for index, record in enumerate(records):
        payload = record.payload
        if isinstance(payload, SessionPayload):
            seed_allowed = True
            continue
        if isinstance(payload, AppliedOutputPayload) and payload.result_revision == 0:
            seed_outputs += 1
            if (
                not seed_allowed
                or seed_outputs != 1
                or payload.output_source is not OutputSource.SEED
                or not payload.sample_complete
            ):
                raise TraceSelectionError("revision zero applied output must be the one complete initial seed")
            seed_allowed = False
            continue
        if isinstance(payload, MpcUpdatePayload):
            revision = payload.result_revision
            if revision <= previous_revision:
                raise TraceSelectionError("MPC control-update revisions are not strictly ordered")
            if payload.wall_ms < previous_wall_ms:
                raise TraceSelectionError("MPC control-update timestamps are not ordered")
            if payload.failure_state is not MpcFailureState.SUCCESS or payload.stale:
                raise TraceSelectionError(f"MPC revision {revision} is incomplete")
            if payload.inhibit_reason is not InhibitReason.NONE:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited")
            updates.append((index, payload))
            previous_revision = revision
            previous_wall_ms = payload.wall_ms
            if payload.actuation_mode is ActuationMode.FIXED_CYCLE:
                seed_allowed = False
        elif isinstance(payload, AppliedOutputPayload):
            seed_allowed = False
            revision = payload.result_revision
            if payload.output_source is not OutputSource.CONTROLLER:
                raise TraceSelectionError(f"MPC revision {revision} is inhibited by {payload.output_source.value}")
            if payload.sample_complete:
                if payload.realized_combustion_load is None:
                    raise TraceSelectionError(f"MPC revision {revision} has no realized combustion load")
                complete_outputs.setdefault(revision, []).append((index, payload))
            else:
                if payload.realized_combustion_load is not None:
                    raise TraceSelectionError(
                        f"MPC revision {revision} has a malformed incomplete applied-output interval"
                    )
                partial_outputs.append((index, payload))
        elif isinstance(payload, FixedCycleFramePayload):
            if payload.result_revision > 0:
                seed_allowed = False
                if payload.inhibit_reason is InhibitReason.NONE:
                    actuated_revisions.add(payload.result_revision)
        elif isinstance(payload, FramedPulseFramePayload):
            if payload.result_revision > 0:
                seed_allowed = False
                if payload.inhibit_reason is InhibitReason.NONE and not payload.skipped:
                    actuated_revisions.add(payload.result_revision)

    if len(updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two completed MPC control updates")

    latest_revision = updates[-1][1].result_revision
    if partial_outputs:
        if len(partial_outputs) != 1:
            raise TraceSelectionError("selected control trace has multiple incomplete applied-output intervals")
        partial_index, partial = partial_outputs[0]
        if partial.result_revision != latest_revision:
            raise TraceSelectionError("terminal incomplete applied-output interval does not match its latest update")
        if partial_index <= updates[-1][0]:
            raise TraceSelectionError("terminal incomplete applied-output interval does not follow its latest update")
        if any(
            isinstance(record.payload, (MpcUpdatePayload, AppliedOutputPayload))
            for record in records[partial_index + 1 :]
        ):
            raise TraceSelectionError("terminal incomplete applied-output interval has a later update or output")
        if complete_outputs:
            latest_complete = max(
                (item for revision_outputs in complete_outputs.values() for item in revision_outputs),
                key=lambda item: item[0],
            )[1]
            if partial.interval_start_ms < latest_complete.interval_end_ms:
                raise TraceSelectionError(
                    "terminal incomplete applied-output interval does not follow its complete interval"
                )

    update_indices = {payload.result_revision: index for index, payload in updates}
    for revision, revision_outputs in complete_outputs.items():
        if revision not in update_indices:
            raise TraceSelectionError("complete applied output does not match an accepted MPC update")
        previous_end_ms: int | None = None
        for output_index, output in revision_outputs:
            if output_index <= update_indices[revision]:
                raise TraceSelectionError("complete applied output must follow its accepted update")
            duration_ms = output.interval_end_ms - output.interval_start_ms
            if duration_ms < 0:
                raise TraceSelectionError("complete applied output interval must not run backwards")
            if len(revision_outputs) > 1 and duration_ms == 0:
                raise TraceSelectionError("repeated complete applied outputs must span positive intervals")
            if previous_end_ms is not None and output.interval_start_ms != previous_end_ms:
                raise TraceSelectionError("repeated complete applied outputs must cover contiguous intervals")
            previous_end_ms = output.interval_end_ms

    terminal_partial_revision = partial_outputs[0][1].result_revision if partial_outputs else None
    missing_actuated_revisions = sorted(
        revision
        for revision in actuated_revisions
        if revision in update_indices and revision not in complete_outputs and revision != terminal_partial_revision
    )
    if missing_actuated_revisions:
        raise TraceSelectionError(
            f"MPC revision {missing_actuated_revisions[0]} was actuated without a complete applied output"
        )

    def realized_load(revision_outputs: list[tuple[int, AppliedOutputPayload]]) -> float:
        if len(revision_outputs) == 1:
            return float(revision_outputs[0][1].realized_combustion_load)
        duration_ms = sum(output.interval_end_ms - output.interval_start_ms for _, output in revision_outputs)
        return (
            sum(
                float(output.realized_combustion_load) * (output.interval_end_ms - output.interval_start_ms)
                for _, output in revision_outputs
            )
            / duration_ms
        )

    paired_updates = [(index, update) for index, update in updates if update.result_revision in complete_outputs]
    if len(paired_updates) < 2:
        raise TraceSelectionError("selected control trace requires at least two applied MPC control updates")
    start_ms = paired_updates[0][1].wall_ms
    return (
        np.asarray([(update.wall_ms - start_ms) / 1000.0 for _, update in paired_updates], dtype=float),
        np.asarray([update.measured_temperature for _, update in paired_updates], dtype=float),
        np.asarray(
            [realized_load(complete_outputs[update.result_revision]) for _, update in paired_updates],
            dtype=float,
        ),
    )


# Said in both output modes, so neither can be the one that stays quiet.
_NOT_CONVERGED = (
    "WARNING: the solver ran out of evaluations after {nfev} without meeting a\n"
    "         convergence criterion. These parameters are its best point so far, not a\n"
    "         finished fit -- a better one for this log may exist. Treat the RMSE as a\n"
    "         description of this point only, and do not read the parameters as this\n"
    "         grill's measured values."
)


def _sim_kwargs(params):
    return {k: params[k] for k in _SIM_KEYS}


def _log_or_floor(value, floor):
    """log(value), or `floor` when there is no logarithm to take."""
    value = float(value)
    return math.log(value) if value > 0.0 and math.isfinite(value) else floor


def fit_params(t, temp, Q, *, T_amb, init, sigma=0.0, n_delay=0):
    """Fit the free grey-box parameters to a logged temperature series.

    `sigma` is a starting value like every other in `init`, and gets the same
    treatment: moved if `_FREE` names it, returned exactly as passed if not. It
    is a separate argument only because every caller has it to hand apart from
    the parameters it is fitting.

    THE SOLVE IS IN LOG SPACE. Every free parameter is a strictly positive
    scale -- a capacitance, a gain, a duration -- so what a step of the solve
    should mean is a RATIO, not a difference. scipy's finite-difference step is
    eps**0.5 * max(1, |x|), whose max(1, ...) makes that step absolute rather
    than relative for anything below 1, so parameters decades apart in size
    would otherwise be probed with wildly different effective precision.
    Optimising log(parameter) makes every step a true relative one at every
    point of the solve rather than only at the starting point, and makes the
    positivity structural instead of a bound the solver has to respect.

    It also decides the answer on the record this model most has to fit. The
    chamber equation is close to invariant under scaling C_c and K_Q together
    -- the loss terms shrink against them, and the limit is a pure integrator
    that describes a heat-up ramp nearly as well as the real model does. That
    is a straight line in the parameters and a curve in their logarithms, so a
    solve in the raw parameters slides down it: from the shipped starting point
    the real 1240 s MAK cook in tests/unit/mpc/fixtures ends at C_c 1.4e9 and
    an RMSE of 11.98 C, while the same solve in log space reaches the actual
    minimum, C_c 3558 and 2.55 C, in ten evaluations. On the eight synthetic
    scenarios across both plants in controller/grill_sim.py, where no such
    escape is open, the two agree to three decimals on every one.

    The result carries `converged` alongside the parameters. A least-squares
    solve that runs out of evaluations still returns its best point so far, and
    that point can look entirely reasonable -- it simply has not been shown to
    be a minimum. A caller deciding whether to put this model on a live grill
    needs to tell the two apart, so the answer travels with the parameters
    rather than being something the caller must think to ask for.
    """
    temp = np.asarray(temp, dtype=float)
    init = dict(init, sigma=sigma)
    # Everything the solve does not move stays where the caller put it. Which
    # parameters those are is `_FREE`'s business alone, so shrinking that set
    # holds the parameters it drops rather than dropping them from the model.
    held = {k: float(init[k]) for k in _FIT_KEYS if k not in _FREE}
    lo = math.log(_LOWER_BOUND)
    # A non-positive or non-finite starting value has no logarithm to start
    # from, so it starts at the floor rather than taking the solve down with it.
    x0 = np.array([_log_or_floor(init[k], lo) for k in _FREE], dtype=float)

    def simulate(z):
        """The trajectory at `z`, or None where the model cannot be simulated.

        A parameter set the solve is only trying out can drive the chamber
        integration away, and it does so in either of two shapes. The chamber
        state is a Python float, so its radiative term raises OverflowError
        past about 1e77 -- an exception out of the middle of a fit, not a
        number -- while an intermediate that goes through numpy instead
        produces inf and NaN. Both are the same event and both are caught
        here, because a caller that has to remember which one a given
        parameter set produces has not been given a guard.
        """
        params = dict(held)
        params.update(zip(_FREE, (math.exp(v) for v in z)))
        params.update(n_delay=n_delay)
        try:
            y = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
        except OverflowError:
            return None
        return y if np.all(np.isfinite(y)) else None

    def residual(z):
        y = simulate(z)
        # NaN reaching least_squares is not a large residual -- every
        # comparison against it is False, so the step that produced it is
        # neither accepted nor rejected on its merits and the solve wanders
        # from there. `_DIVERGED` is finite, so such a point is simply a very
        # bad one and the trust region shrinks away from it.
        if y is None:
            return np.full_like(temp, _DIVERGED)
        return y - temp

    res = least_squares(residual, x0, method="trf", bounds=(lo, np.inf), max_nfev=_MAX_NFEV)
    out = dict(held)
    out.update(zip(_FREE, (math.exp(float(v)) for v in res.x)))
    # status 0 is scipy's "the evaluation budget ran out"; every other
    # non-negative status is one of its convergence criteria being met. A point
    # the model cannot even be simulated at is not a fit whatever scipy makes
    # of the residuals around it, so the finite check is ANDed in rather than
    # left to the caller: this result is about to be offered to a live grill.
    out.update(converged=bool(res.status > 0) and simulate(res.x) is not None, nfev=int(res.nfev))
    out.update(n_delay=int(n_delay), T_amb=float(T_amb))
    return out


def fit_quality(t, temp, Q, fitted, *, T_amb):
    """(RMSE, max absolute error) in degrees C between the fit and the log.

    Infinite in both where the model cannot be simulated at `fitted` at all,
    in either of the two shapes `fit_params.simulate` describes -- a raised
    OverflowError out of the chamber's float arithmetic and a quiet NaN out of
    numpy. Neither is a large error; both are the absence of a trajectory to
    take an error against.

    Infinity rather than an exception because the caller comparing two models
    is the one that owns the judgement. `model_promotion.evaluate` refuses a
    non-finite RMSE and its reason names WHICH of the two models could not be
    scored, while an exception raised here arrives at `Controller.
    refit_from_cook` -- whose `try` catches ValueError and FloatingPointError
    only, so the raised shape would leave it altogether, into a grill teardown
    that has a cool-down fan to start.
    """
    temp = np.asarray(temp, dtype=float)
    params = dict(fitted)
    params["T_amb"] = T_amb
    try:
        sim = simulate_grey_box(t, Q, T_amb=T_amb, T0=float(temp[0]), **_sim_kwargs(params))
    except OverflowError:
        return math.inf, math.inf
    if not np.all(np.isfinite(sim)):
        return math.inf, math.inf
    err = sim - temp
    return float(np.sqrt(np.mean(err**2))), float(np.max(np.abs(err)))


# One part in a thousand of a log, for the central differences below. The solve
# itself works in log space, so this probes the parameters in the space they are
# identified in; small enough that the model is linear across the step and large
# enough to stay well clear of the simulator's own resolution.
_IDENT_STEP = 1e-3


def identifiability(t, Q, fitted, *, T_amb, T0):
    """How well this record pins down the free parameters, in C RMS per e-fold.

    The smallest singular value of the prediction's Jacobian with respect to
    `_FREE` in log space, normalised per sample. Its units are degrees C RMS per
    e-fold of the orthonormal parameter direction this record constrains least,
    so it answers: how far can this record's best-fitting parameters be moved,
    in the direction it pins down worst, before the prediction moves at all? A
    record that leaves some direction free scores near zero, and the model it
    produces is determined by the starting point rather than by the cook.

    NO MEASURED TEMPERATURE ENTERS THIS BEYOND `T0`, and the signature is the
    guarantee: the record's temperature series is not an argument, so there is
    no channel through which the fit residual could reach the arithmetic below.
    What is read is `t`, `Q`, the starting temperature and the fitted point --
    what the record ASKED the grill to do, not how well the fit turned out.

    That is what lets this stand beside a residual statistic and say something
    the residual cannot. An in-sample RMSE is minimised just as neatly on a
    record that determines nothing -- more neatly, in fact, since there is less
    for the model to disagree with -- so the two rank records in opposite
    orders, and only this one ranks them the way the risk runs.

    `None` where no such measurement exists: a free parameter that is not a
    positive finite scale has no logarithm to perturb, and a simulation that
    leaves the reals says nothing about the record. Both shapes of the latter
    are caught -- a raised OverflowError out of the chamber's float arithmetic
    and a quiet NaN out of numpy -- for the reason `fit_params.simulate` states.
    The raised shape is the one that matters to the caller: `Controller.
    refit_from_cook` runs this inside a `try` that catches ValueError and
    FloatingPointError only, so an OverflowError leaving here would leave
    `refit_from_cook` altogether, into a grill teardown that has a cool-down
    fan to start.
    """
    cols = []
    for key in _FREE:
        base = float(fitted[key])
        if not (base > 0.0 and math.isfinite(base)):
            return None
        try:
            up = _sim_at(t, Q, fitted, key, base * math.exp(_IDENT_STEP), T_amb=T_amb, T0=T0)
            dn = _sim_at(t, Q, fitted, key, base * math.exp(-_IDENT_STEP), T_amb=T_amb, T0=T0)
        except OverflowError:
            return None
        if not (np.all(np.isfinite(up)) and np.all(np.isfinite(dn))):
            return None
        cols.append((up - dn) / (2.0 * _IDENT_STEP))
    jacobian = np.column_stack(cols) / math.sqrt(len(t))
    svals = np.linalg.svd(jacobian, compute_uv=False)
    return float(svals[-1])


def _sim_at(t, Q, fitted, key, value, *, T_amb, T0):
    """The model's trajectory with one parameter moved off the fitted point."""
    params = dict(fitted)
    params[key] = value
    return simulate_grey_box(t, Q, T_amb=T_amb, T0=float(T0), **_sim_kwargs(params))


def _dump_json(document):
    """Encode the machine-readable document, refusing any non-finite number.

    RFC 8259 has no `Infinity`, `-Infinity` or `NaN` literal, and Python's own
    decoder accepts all three, so an unconverted non-finite value would leave
    here as text only Python can read back. `allow_nan=False` makes that a
    ValueError at the emit, beside the value that caused it.
    """
    return json.dumps(document, indent=2, allow_nan=False)


def main():
    ap = argparse.ArgumentParser(description="Fit MPC grey-box parameters to a typed SQLite control trace.")
    selection = ap.add_mutually_exclusive_group(required=True)
    selection.add_argument("--cook", dest="cook_id", help="Cook ID containing exactly one MPC trace session")
    selection.add_argument("--session", dest="session_id", help="MPC control trace session ID")
    ap.add_argument("--database", default=None, help="Optional path to the SQLite trace database")
    ap.add_argument("--t-amb", type=float, default=None, help="Ambient temperature in C")
    ap.add_argument("--json", action="store_true", help="Print only the fitted config JSON")
    args = ap.parse_args()

    try:
        t, temp, Q = load_trace_samples(
            cook_id=args.cook_id,
            session_id=args.session_id,
            database_path=args.database,
        )
    except TraceSelectionError as exc:
        ap.error(str(exc))

    from controller.mpc import _DEFAULTS, _optional_float

    T_amb = args.t_amb if args.t_amb is not None else float(_DEFAULTS["T_amb"])
    init = {k: float(_DEFAULTS[k]) for k in ("C_c", "h_amb", "K_Q", "theta")}
    fitted = fit_params(
        t,
        temp,
        Q,
        T_amb=T_amb,
        init=init,
        sigma=float(_DEFAULTS["sigma"]),
        n_delay=int(_DEFAULTS["n_delay"]),
    )
    payload = {k: fitted[k] for k in CONFIG_KEYS}

    if args.json:
        # The config keys stay in their own object so they can still be pasted
        # or ingested whole, but they no longer travel without the fit's own
        # verdict on itself: this is the mode something else consumes, and a
        # machine reading an exhausted solve as a finished one is the failure
        # the `converged` flag exists to prevent. The human-readable warning
        # goes to stderr so stdout remains parseable JSON.
        #
        # The two errors go through `_optional_float`, so a model the grey box
        # cannot be simulated at reports `null` rather than the infinities
        # `fit_quality` returns for it -- the same encoding controller/mpc.py's
        # snapshot uses for an RMSE nobody could measure, so a consumer meets
        # one convention across both. The keys stay present: dropped, they
        # would be indistinguishable from an older build of this utility, and
        # "unmeasurable" is exactly what the reader needs told.
        rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
        print(
            _dump_json(
                {
                    "config": payload,
                    "fit": {
                        "converged": fitted["converged"],
                        "nfev": fitted["nfev"],
                        "rmse_c": _optional_float(rmse),
                        "max_error_c": _optional_float(max_err),
                    },
                }
            )
        )
        if not fitted["converged"]:
            print(_NOT_CONVERGED.format(nfev=fitted["nfev"]), file=sys.stderr)
        return

    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
    print(f"Fit quality: RMSE {rmse:.2f} C, max error {max_err:.2f} C")
    if not fitted["converged"]:
        print(_NOT_CONVERGED.format(nfev=fitted["nfev"]))
    if rmse > 10.0:
        print(
            "WARNING: RMSE above 10 C. This fit does not describe the log. Check that the log\n"
            "         covers a full heat-up and at least one step down, and that the fan was\n"
            "         under the controller's command throughout."
        )
    # The radiation-aware time constant describes the fitted chamber response.
    print(
        f"Chamber time constant: {effective_tau(payload, T_HAZARD_C):.0f} s at "
        f"{T_HAZARD_C:.0f} C rising to {effective_tau(payload, T_FLOOR_C):.0f} s at {T_FLOOR_C:.0f} C"
    )

    # A cook that never approaches steady state cannot determine this, so it is
    # where a fit that has traded the chamber's parameters against each other
    # along a direction the log could not see says something visibly absurd. It
    # is printed rather than gated on: a reader who knows what this grill peaks
    # at can judge it, and a threshold that separated sound from absurd here
    # would have to be drawn much finer than the evidence supports.
    t_ss = steady_state_at_full_fire(payload)
    print(f"Implied steady state at full fire: {t_ss:.0f} C ({t_ss * 9.0 / 5.0 + 32.0:.0f} F)")
    print("\nPaste into Settings > Controller (controller.config.mpc):")
    print(_dump_json(payload))


if __name__ == "__main__":
    main()
