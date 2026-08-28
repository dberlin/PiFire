"""
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

Fits the grey-box thermal parameters to one typed SQLite MPC control-trace
session. Fitting runs through controller.mpc_model's shared forward simulator,
so the parameters produced describe the same dynamics the controller plans
against -- radiative loss and transport deadtime included.

The selected session must contain uninterrupted, completed MPC control updates,
allocations, framed pulses, and complete same-revision applied combustion
intervals. Capture the cook with the fan under the controller's command: a
trace taken with the fan pinned at one duty only describes the grill at that
duty.

Usage: python -m controller.update_mpc (--cook COOK_ID | --session SESSION_ID)
       [--database PATH] [--t-amb 20] [--json]
*****************************************
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys

import numpy as np

from common.persistence.control_trace import read_control_trace_cook, read_control_trace_session
from controller.model_learning.trace import TraceSelectionError, calibration_samples
from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau, steady_state_at_full_fire

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
_TIMESTAMP_TOLERANCE_S = 1e-9




def _load_trace_calibration(
    *,
    cook_id: str | None = None,
    session_id: str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Load fitting arrays and the recorded canonical ambient temperature."""
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

    samples = calibration_samples(records)
    if len(samples) < 2:
        raise TraceSelectionError("selected control trace requires at least two learning observations")
    ambient_c = samples[0].ambient_c
    if any(not math.isclose(sample.ambient_c, ambient_c, rel_tol=0.0, abs_tol=1e-9) for sample in samples[1:]):
        raise TraceSelectionError("selected control trace has inconsistent recorded ambient temperatures")
    return (
        np.asarray([sample.time_s for sample in samples], dtype=float),
        np.asarray([sample.temp_c for sample in samples], dtype=float),
        np.asarray([sample.combustion_load for sample in samples], dtype=float),
        ambient_c,
    )


def load_trace_samples(
    *,
    cook_id: str | None = None,
    session_id: str | None = None,
    database_path: str | os.PathLike[str] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load calibration arrays while retaining the historic public shape."""
    time_s, temp_c, combustion_load, _ = _load_trace_calibration(
        cook_id=cook_id,
        session_id=session_id,
        database_path=database_path,
    )
    return time_s, temp_c, combustion_load


# Said in both output modes, so neither can be the one that stays quiet.
_NOT_CONVERGED = (
    "WARNING: the solver ran out of evaluations after {nfev} without meeting a\n"
    "         convergence criterion. These parameters are its best point so far, not a\n"
    "         finished fit -- a better one for this log may exist. Treat the RMSE as a\n"
    "         description of this point only, and do not read the parameters as this\n"
    "         grill's measured values."
)






def _canonical_digest(document):
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trace_fit_job(t, temp, Q, *, T_amb, init, sigma, n_delay, initial_load=None):
    """Build one explicit-anchor segmented job from a canonical typed trace."""

    from common.learning_trajectory import FitCorpusIdentity, FitCorpusSlice
    from controller.acados.contracts import GreyBoxMPCConfig
    from controller.model_learning.contracts import (
        CandidateOrigin,
        FitRequest,
        FitWindowIdentity,
    )
    from controller.runtime.model_fitting import (
        FIT_CADENCE_S,
        GreyFitJob,
        GreyFitSegmentArrays,
    )

    times = np.asarray(t, dtype=float)
    temperatures = np.asarray(temp, dtype=float)
    loads = np.asarray(Q, dtype=float)
    if times.ndim != 1 or temperatures.ndim != 1 or loads.ndim != 1:
        raise ValueError("trace fit arrays must be one-dimensional")
    if len(times) < 2 or len(times) != len(temperatures) or len(times) != len(loads):
        raise ValueError("trace fit arrays must have the same length and at least two rows")
    if not (
        np.all(np.isfinite(times))
        and np.all(np.isfinite(temperatures))
        and np.all(np.isfinite(loads))
    ):
        raise ValueError("trace fit arrays must contain only finite values")
    durations = np.diff(times)
    if np.any(durations <= 0.0):
        raise ValueError("trace fit times must be strictly increasing")
    source_cadence_s = float(np.median(durations))
    if source_cadence_s > FIT_CADENCE_S + _TIMESTAMP_TOLERANCE_S:
        raise ValueError(
            f"trace fit cadence must not exceed the nominal {FIT_CADENCE_S:g}-second cadence"
        )
    compatible_gap_s = min(FIT_CADENCE_S, 1.5 * source_cadence_s)
    if np.any((loads < 0.0) | (loads > 1.0)):
        raise ValueError("trace loads must be normalized to [0, 1]")

    elapsed_s = times - times[0]
    scored_count = math.floor(
        (float(elapsed_s[-1]) + _TIMESTAMP_TOLERANCE_S) / FIT_CADENCE_S
    )
    if scored_count == 0:
        raise ValueError("trace fit requires at least one complete scored interval")
    scored_boundaries_s = FIT_CADENCE_S * np.arange(
        1,
        scored_count + 1,
        dtype=float,
    )
    scored_interval_mask = elapsed_s[:-1] < (
        float(scored_boundaries_s[-1]) - _TIMESTAMP_TOLERANCE_S
    )
    if np.any(
        durations[scored_interval_mask]
        > compatible_gap_s + _TIMESTAMP_TOLERANCE_S
    ):
        raise ValueError("trace fit times contain an incompatible sampling gap")
    cumulative_load_time = np.concatenate(
        (
            np.asarray([0.0]),
            np.cumsum(loads[:-1] * durations),
        )
    )
    boundary_load_time: list[float] = []
    boundary_temperature_c: list[float] = []
    for boundary_s in scored_boundaries_s:
        right_index = int(
            np.searchsorted(
                elapsed_s,
                boundary_s - _TIMESTAMP_TOLERANCE_S,
                side="left",
            )
        )
        if right_index >= len(times):
            raise ValueError("trace fit has no sample bracketing a scored boundary")
        if math.isclose(
            float(elapsed_s[right_index]),
            float(boundary_s),
            rel_tol=0.0,
            abs_tol=_TIMESTAMP_TOLERANCE_S,
        ):
            boundary_load_time.append(float(cumulative_load_time[right_index]))
            boundary_temperature_c.append(float(temperatures[right_index]))
            continue
        left_index = right_index - 1
        if left_index < 0:
            raise ValueError("trace fit has no sample bracketing a scored boundary")
        bracket_s = float(elapsed_s[right_index] - elapsed_s[left_index])
        if bracket_s > compatible_gap_s + _TIMESTAMP_TOLERANCE_S:
            raise ValueError("trace fit times contain an incompatible sampling gap")
        offset_s = float(boundary_s - elapsed_s[left_index])
        boundary_load_time.append(
            float(cumulative_load_time[left_index])
            + float(loads[left_index]) * offset_s
        )
        fraction = offset_s / bracket_s
        boundary_temperature_c.append(
            float(temperatures[left_index])
            + fraction
            * float(temperatures[right_index] - temperatures[left_index])
        )
    scored_load = np.diff(
        np.asarray([0.0, *boundary_load_time], dtype=float)
    ) / FIT_CADENCE_S
    scored_load = np.clip(scored_load, 0.0, 1.0)
    scored_duration_s = np.full(scored_count, FIT_CADENCE_S, dtype=float)
    scored_temperature_c = np.asarray(boundary_temperature_c, dtype=float)
    pre_roll_count = 0
    pre_roll_duration_s = np.asarray((), dtype=float)
    pre_roll_load = np.asarray((), dtype=float)
    pre_roll_temperature_c = np.asarray((), dtype=float)
    hold_anchor_c = float(temperatures[0])

    delay_initial_load = float(loads[0]) if initial_load is None else float(initial_load)
    if not math.isfinite(delay_initial_load) or not 0.0 <= delay_initial_load <= 1.0:
        raise ValueError("trace initial load must be finite and normalized to [0, 1]")
    config = GreyBoxMPCConfig(
        C_c=float(init["C_c"]),
        h_amb=float(init["h_amb"]),
        T_amb=float(T_amb),
        theta=float(init["theta"]),
        K_Q=float(init["K_Q"]),
        sigma=float(sigma),
    )
    if int(n_delay) != config.delay_states:
        raise ValueError(f"segmented grey fitting requires n_delay={config.delay_states}")
    partition_digest = _canonical_digest(
        {
            "schema_version": 1,
            "cadence_s": FIT_CADENCE_S,
            "n_delay": config.delay_states,
            "h_amb": config.h_amb,
            "sigma": config.sigma,
            "ambient_semantics": "per-frame-canonical-celsius",
        }
    )
    segment_id = "typed-trace-calibration"
    cook_id = "typed-trace-calibration"
    sequences = tuple(range(1, scored_count + 1))
    prefix_digest = _canonical_digest(
        {
            "schema_version": 1,
            "segment_id": segment_id,
            "observation_sequences": list(sequences),
            "initial_load": delay_initial_load,
            "pre_roll_duration_s": pre_roll_duration_s.tolist(),
            "pre_roll_load": pre_roll_load.tolist(),
            "pre_roll_temperature_c": pre_roll_temperature_c.tolist(),
            "hold_anchor_c": hold_anchor_c,
            "scored_duration_s": scored_duration_s.tolist(),
            "scored_load": scored_load.tolist(),
            "scored_ambient_c": [float(T_amb)] * scored_count,
            "scored_temperature_c": scored_temperature_c.tolist(),
        }
    )
    segment = GreyFitSegmentArrays(
        segment_id=segment_id,
        cook_id=cook_id,
        through_ordinal=pre_roll_count + scored_count - 1,
        prefix_digest=prefix_digest,
        fit_partition_digest=partition_digest,
        observation_sequences=sequences,
        initial_load=delay_initial_load,
        pre_roll_duration_s=pre_roll_duration_s,
        pre_roll_load=pre_roll_load,
        pre_roll_temperature_c=pre_roll_temperature_c,
        hold_anchor_c=hold_anchor_c,
        scored_duration_s=scored_duration_s,
        scored_load=scored_load,
        scored_ambient_c=np.full(scored_count, float(T_amb), dtype=float),
        scored_temperature_c=scored_temperature_c,
        calibration_origin=np.ones(scored_count, dtype=bool),
    )
    corpus_slice = FitCorpusSlice(
        segment_id=segment.segment_id,
        through_ordinal=segment.through_ordinal,
        prefix_digest=segment.prefix_digest,
        pre_roll_count=pre_roll_count,
        scored_count=scored_count,
    )
    corpus_payload = {
        "schema_version": 1,
        "corpus_revision": 0,
        "fit_partition_digest": partition_digest,
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": corpus_slice.through_ordinal,
                "prefix_digest": corpus_slice.prefix_digest,
                "pre_roll_count": corpus_slice.pre_roll_count,
                "scored_count": corpus_slice.scored_count,
            }
        ],
    }
    corpus = FitCorpusIdentity(
        schema_version=1,
        corpus_revision=0,
        fit_partition_digest=partition_digest,
        slices=(corpus_slice,),
        corpus_digest=_canonical_digest(corpus_payload),
    )
    request_id = _canonical_digest(
        {
            "origin": CandidateOrigin.OPERATOR_CALIBRATION.value,
            "corpus_digest": corpus.corpus_digest,
            "incumbent": {key: getattr(config, key) for key in _FREE},
        }
    )
    request = FitRequest(
        request_id=request_id,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        window=FitWindowIdentity(
            session_id="typed-trace-calibration",
            cook_id=cook_id,
            first_observation_sequence=1,
            last_observation_sequence=scored_count,
            configuration_digest=partition_digest,
            incumbent_digest=_canonical_digest(
                {key: getattr(config, key) for key in ("C_c", "K_Q", "theta")}
            ),
            role_generation=0,
        ),
        candidate_generation=0,
    )
    return GreyFitJob(
        request=request,
        corpus=corpus,
        segments=(segment,),
        config=config,
    )


def _fit_trace_segmented(t, temp, Q, *, T_amb, init, sigma, n_delay):
    from controller.runtime.model_fitting import fit_segmented_grey

    return fit_segmented_grey(
        trace_fit_job(
            t,
            temp,
            Q,
            T_amb=T_amb,
            init=init,
            sigma=sigma,
            n_delay=n_delay,
        )
    )


def _fit_mapping(outcome, *, T_amb, init, sigma, n_delay):
    from controller.runtime.model_fitting import GreyFitSuccess

    source = outcome.config if isinstance(outcome, GreyFitSuccess) else None
    fitted = {
        "C_c": float(source.C_c if source is not None else init["C_c"]),
        "h_amb": float(source.h_amb if source is not None else init["h_amb"]),
        "K_Q": float(source.K_Q if source is not None else init["K_Q"]),
        "sigma": float(source.sigma if source is not None else sigma),
        "theta": float(source.theta if source is not None else init["theta"]),
        "n_delay": int(n_delay),
        "T_amb": float(T_amb),
        "converged": source is not None,
        "nfev": int(outcome.nfev if source is not None else 0),
    }
    return fitted




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
        t, temp, Q, recorded_ambient_c = _load_trace_calibration(
            cook_id=args.cook_id,
            session_id=args.session_id,
            database_path=args.database,
        )
    except TraceSelectionError as exc:
        ap.error(str(exc))

    from controller.mpc_config import DEFAULT_MPC_CONFIG, optional_float

    T_amb = recorded_ambient_c
    if args.t_amb is not None and not math.isclose(args.t_amb, T_amb, rel_tol=0.0, abs_tol=1e-9):
        ap.error("--t-amb must match the trace's recorded ambient temperature")
    init = {k: float(DEFAULT_MPC_CONFIG[k]) for k in ("C_c", "h_amb", "K_Q", "theta")}
    sigma = float(DEFAULT_MPC_CONFIG["sigma"])
    n_delay = int(DEFAULT_MPC_CONFIG["n_delay"])
    outcome = _fit_trace_segmented(
        t,
        temp,
        Q,
        T_amb=T_amb,
        init=init,
        sigma=sigma,
        n_delay=n_delay,
    )
    fitted = _fit_mapping(
        outcome,
        T_amb=T_amb,
        init=init,
        sigma=sigma,
        n_delay=n_delay,
    )
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    from controller.runtime.model_fitting import GreyFitSuccess

    if isinstance(outcome, GreyFitSuccess) and outcome.metrics is not None:
        rmse = outcome.metrics.pooled.rmse_c
        max_err = outcome.metrics.pooled.max_error_c
    else:
        rmse = math.inf
        max_err = math.inf

    if args.json:
        # The config keys stay in their own object so they can still be pasted
        # or ingested whole, but they no longer travel without the fit's own
        # verdict on itself: this is the mode something else consumes, and a
        # machine reading an exhausted solve as a finished one is the failure
        # the `converged` flag exists to prevent. The human-readable warning
        # goes to stderr so stdout remains parseable JSON.
        #
        # The two errors go through `optional_float`, so a model the grey box
        # cannot be simulated at reports `null` rather than the infinities
        # the segmented outcome reports for an unmeasurable model -- the same
        # encoding controller/mpc.py's snapshot uses for an RMSE nobody could
        # measure, so a consumer meets one convention across both. The keys stay present: dropped, they
        # would be indistinguishable from an older build of this utility, and
        # "unmeasurable" is exactly what the reader needs told.
        print(
            _dump_json(
                {
                    "config": payload,
                    "fit": {
                        "converged": fitted["converged"],
                        "nfev": fitted["nfev"],
                        "rmse_c": optional_float(rmse),
                        "max_error_c": optional_float(max_err),
                    },
                }
            )
        )
        if not fitted["converged"]:
            print(_NOT_CONVERGED.format(nfev=fitted["nfev"]), file=sys.stderr)
        return

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
