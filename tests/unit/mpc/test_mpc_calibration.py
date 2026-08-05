"""The fitter must recover parameters through the same dynamics the MPC uses."""

import math
import os

import numpy as np
import pandas as pd
import pytest

from common.control_trace import (
    ActuationMode,
    AppliedOutputPayload,
    ControlTraceRecord,
    ControllerType,
    InhibitReason,
    MpcFailureState,
    MpcUpdatePayload,
    SessionPayload,
    TraceEventKind,
    TraceSetting,
)
from common.datastore_accessors import append_control_trace
from controller.applied_output import OutputSource
from controller.model_promotion import T_FLOOR_C, T_HAZARD_C, effective_tau, longest_braking_distance
from controller.mpc import _DEFAULTS
from controller.mpc_model import simulate_grey_box
from controller.update_mpc import CONFIG_KEYS, fit_params, fit_quality, load_trace_samples

#: The grill these tests fit: an order of magnitude slower than the shipped
#: default, nearly ten times its gain, and twice its dead time.
#:
#: h_amb matches `_init()`, which puts this grill inside what a fit can
#: express. `_FREE` holds h_amb and sigma both, so it fixes sigma/h_amb -- the
#: share of the chamber's loss that is radiative -- and a grill whose share
#: differs is outside the estimator's model class: fitting its own noiseless
#: data leaves a residual, and C_c absorbs the difference. These tests ask
#: whether the fitter recovers what it is given, which needs a grill it can
#: represent. The cost of that restriction is its own question, measured
#: against a mismatched grill in tests/unit/mpc/test_model_promotion.py.
TRUTH = dict(C_c=11000.0, h_amb=0.5, K_Q=32.0, theta=110.0)
T_AMB = 20.0
N_DELAY = _DEFAULTS["n_delay"]
SIGMA = 1.4e-9


def _dataset():
    """A heat-up to a plateau then a step down -- enough excitation to identify
    the gain, the loss and the deadtime."""
    t = np.arange(0.0, 6000.0, 5.0)
    Q = np.where(t < 3000.0, 100.0, 20.0)
    temp = simulate_grey_box(t, Q, T0=25.0, T_amb=T_AMB, sigma=SIGMA, n_delay=N_DELAY, **TRUTH)
    return t, Q, temp


def _init():
    """The shipped starting point, exactly as controller/mpc.py's _REFIT_INIT."""
    return dict(C_c=320.0, h_amb=0.5, K_Q=3.5, theta=50.0)


def _seed_trace(t, temp, Q, *, cook_id="calibration-cook", session_id="calibration-session"):
    records = [
        ControlTraceRecord(
            ts_ms=0,
            session_id=session_id,
            cook_id=cook_id,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.SESSION,
            payload=SessionPayload(
                controller=ControllerType.MPC,
                controller_config=(TraceSetting(key="policy", value="nlp"),),
                temperature_unit="C",
                control_period_seconds=5.0,
                model_revision=1,
                model_provenance="configured",
                u_min=0.1,
                u_max=0.9,
                hold_cycle_seconds=None,
                pulse_slot_seconds=2.0,
                pulse_frame_seconds=20.0,
                fan_authority=True,
                fan_pwm_capable=True,
                fan_min_duty=40.0,
                fan_max_duty=100.0,
                setpoint=250.0,
                ambient_temperature=T_AMB,
                software_version="test",
                build_version="test",
            ),
        )
    ]
    records.append(
        ControlTraceRecord(
            ts_ms=0,
            session_id=session_id,
            cook_id=cook_id,
            controller=ControllerType.MPC,
            event_kind=TraceEventKind.APPLIED_OUTPUT,
            payload=AppliedOutputPayload(
                result_revision=0,
                interval_start_ms=0,
                interval_end_ms=0,
                realized_auger_duty=0.0,
                realized_combustion_load=None,
                actual_fan_duty=None,
                sample_complete=True,
                output_source=OutputSource.SEED,
            ),
        )
    )
    previous_timestamp_ms: int | None = None
    for revision, (time_s, temperature, load) in enumerate(zip(t, temp, Q), start=1):
        timestamp_ms = int(float(time_s) * 1000)
        load = float(load)
        records.append(
            ControlTraceRecord(
                ts_ms=timestamp_ms,
                session_id=session_id,
                cook_id=cook_id,
                controller=ControllerType.MPC,
                event_kind=TraceEventKind.CONTROL_UPDATE,
                payload=MpcUpdatePayload(
                    monotonic_ms=timestamp_ms,
                    wall_ms=timestamp_ms,
                    result_revision=revision,
                    result_age_ms=0,
                    control_period_seconds=5.0,
                    observed_dt_seconds=5.0,
                    setpoint=250.0,
                    measured_temperature=float(temperature),
                    raw_output=load,
                    requested_output=load,
                    actuation_mode=ActuationMode.FIXED_CYCLE,
                    prior_requested_auger_duty=0.2,
                    prior_realized_auger_duty=0.2,
                    requested_fan_duty=100.0,
                    applied_fan_duty=100.0,
                    output_source=OutputSource.CONTROLLER,
                    inhibit_reason=InhibitReason.NONE,
                    state_names=("temperature", "disturbance"),
                    state_values=(float(temperature), 0.0),
                    disturbance_estimate=0.0,
                    model_revision=1,
                    model_provenance="configured",
                    raw_policy_firing_load=load,
                    equilibrium_feed_forward=load,
                    residual_move=0.0,
                    bounded_firing_load=load,
                    policy_kind="nlp",
                    failure_state=MpcFailureState.SUCCESS,
                    solve_start_ms=timestamp_ms,
                    solve_end_ms=timestamp_ms,
                    deadline_miss_count=0,
                    stale=False,
                    recovered=False,
                    predicted_feasible=True,
                    predicted_steady_load=load,
                ),
            )
        )
        interval_start_ms = timestamp_ms if previous_timestamp_ms is None else previous_timestamp_ms
        realized_load = load
        records.append(
            ControlTraceRecord(
                ts_ms=timestamp_ms + 1,
                session_id=session_id,
                cook_id=cook_id,
                controller=ControllerType.MPC,
                event_kind=TraceEventKind.APPLIED_OUTPUT,
                payload=AppliedOutputPayload(
                    result_revision=revision,
                    interval_start_ms=interval_start_ms,
                    interval_end_ms=timestamp_ms,
                    realized_auger_duty=0.2,
                    realized_combustion_load=realized_load,
                    actual_fan_duty=100.0,
                    sample_complete=True,
                    output_source=OutputSource.CONTROLLER,
                ),
            )
        )
        previous_timestamp_ms = timestamp_ms
    append_control_trace(records)


def test_fit_recovers_the_generating_parameters():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    # C_c and K_Q are recovered as a ratio with h_amb, so compare the quantity
    # the controller's braking distance actually depends on: the time constant.
    assert fitted["C_c"] / fitted["h_amb"] == pytest.approx(TRUTH["C_c"] / TRUTH["h_amb"], rel=0.20)
    assert fitted["theta"] == pytest.approx(TRUTH["theta"], rel=0.30)


def test_fit_quality_is_reported_and_is_tight_on_its_own_data():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    rmse, max_err = fit_quality(t, temp, Q, fitted, T_amb=T_AMB)
    assert rmse < 2.0
    assert max_err < 10.0


def test_the_fitted_dict_carries_every_key_the_controller_config_needs():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    for key in ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma"):
        assert key in fitted


def test_the_model_is_invariant_under_a_common_scaling_of_its_parameters():
    """Why `_FREE` has to hold a parameter at all.

    Both state equations are homogeneous in the capacitances, the conductances
    and the input gain, so scaling all six together leaves the trajectory of
    the one measured state identical. Six parameters, five identifiable
    degrees of freedom -- a log determines the ratios, not the values, and one
    parameter must be held to fix the scale. If this ever stops holding, the
    reasoning in `_FREE` needs revisiting rather than quietly rotting.
    """
    t, Q, temp = _dataset()

    # The quantity the braking argument rests on is one of the invariants.
    def tau(params, sigma, T=250.0):
        return params["C_c"] / (params["h_amb"] + 4.0 * sigma * (T + 273.15) ** 3)

    # Powers of two scale every intermediate exactly, so the invariance shows
    # up bit-for-bit rather than merely to a tolerance; other factors differ
    # only by float rounding, which is what the looser bound below allows.
    for lam, exact in ((0.25, True), (0.5, True), (2.0, True), (4.0, True), (0.1, False), (7.0, False)):
        scaled = {k: (v * lam if k != "theta" else v) for k, v in TRUTH.items()}
        other = simulate_grey_box(t, Q, T0=25.0, T_amb=T_AMB, sigma=SIGMA * lam, n_delay=N_DELAY, **scaled)
        if exact:
            assert np.max(np.abs(other - temp)) == 0.0
        else:
            assert np.max(np.abs(other - temp)) < 1e-9
        assert tau(scaled, SIGMA * lam) == pytest.approx(tau(TRUTH, SIGMA), rel=1e-12)


def test_a_held_parameter_is_returned_exactly_as_it_was_passed():
    """Whatever `_FREE` does not name comes back byte-identical.

    This used to assert it of sigma alone. sigma is still held, but it is no
    longer the only thing pinning a scale -- h_amb holds the chamber's, see
    `_FREE` -- so the property is asserted of every parameter `_FREE` leaves
    out. A later change to that set then cannot leave this checking something
    it no longer covers.

    Byte-identical, not merely close: a caller that round-trips a model through
    this fitter must get the same held values back, or a parameter that was
    supposed to be pinning a gauge has drifted.
    """
    from controller.update_mpc import _FIT_KEYS, _FREE

    t, Q, temp = _dataset()
    held = [key for key in _FIT_KEYS if key not in _FREE]
    assert {"h_amb", "sigma"} <= set(held)
    for sigma in (0.0, SIGMA, 2.7182818e-9):
        for scale in (1.0, 0.5, 2.7182818):
            init = {k: v * scale for k, v in _init().items()}
            fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=init, sigma=sigma, n_delay=N_DELAY)
            supplied = dict(init, sigma=sigma)
            for key in held:
                assert fitted[key] == supplied[key]


def test_the_reported_time_constant_accounts_for_radiative_conductance():
    """Radiative conductance is most of the chamber's loss on a hot grill.

    Asserted against the formula rather than a ratio: what makes this the right
    number is that it is C_c over the linear PLUS linearized-radiative
    conductance, and an equality says so exactly where a threshold would only
    say "smaller than C_c/h_amb by roughly enough".
    """
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    for t_ref in (T_FLOOR_C, T_HAZARD_C):
        expected = payload["C_c"] / (payload["h_amb"] + 4.0 * payload["sigma"] * (t_ref + 273.15) ** 3)
        assert effective_tau(payload, t_ref) == pytest.approx(expected, rel=1e-12)
    # Radiation shortens it, and more so the hotter the chamber gets. The
    # radiation-free C_c/h_amb is written out rather than imported: it no
    # longer names anything the module computes, and it is here only as the
    # value the effective tau has to stay under.
    assert effective_tau(payload, T_HAZARD_C) < effective_tau(payload, T_FLOOR_C) < payload["C_c"] / payload["h_amb"]


def test_the_cli_reports_the_radiation_aware_time_constant(ds, capsys, monkeypatch):
    """The kept CLI fix, exercised through `main()` rather than around it.

    The utility reports two different things and must not confuse them: the
    effective time constant at each end of the operating range, which is what
    the chamber's response is, and the braking distance, which is what the
    horizon has to cover. This checks the reporting, not the trigger.
    """
    import controller.update_mpc as U

    t, Q, temp = _dataset()
    _seed_trace(t, temp, Q)
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--database", str(ds.DB_PATH), "--t-amb", str(T_AMB)],
    )
    U.main()
    out = capsys.readouterr().out

    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    payload = {k: fitted[k] for k in CONFIG_KEYS}
    hot = effective_tau(payload, T_HAZARD_C)
    cold = effective_tau(payload, T_FLOOR_C)
    assert f"{hot:.0f} s at {T_HAZARD_C:.0f} C" in out
    assert f"{cold:.0f} s at {T_FLOOR_C:.0f} C" in out
    # The radiation-free number must not be what got printed as the response.
    assert f"Chamber time constant: {payload['C_c'] / payload['h_amb']:.0f} s" not in out
    # ...and the braking distance is reported as its own quantity, not folded
    # into the time constant the line above prints.
    assert f"Braking distance after a fuel cut: up to {longest_braking_distance(payload):.0f} s" in out


def test_the_cli_says_so_when_the_solver_ran_out_of_evaluations(ds, capsys, monkeypatch):
    """An exhausted solve must not be presented as a finished fit."""
    import controller.update_mpc as U

    t, Q, temp = _dataset()
    _seed_trace(t, temp, Q)
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--database", str(ds.DB_PATH), "--t-amb", str(T_AMB)],
    )

    monkeypatch.setattr(U, "_MAX_NFEV", 3)  # far too few to converge
    U.main()
    starved = capsys.readouterr().out
    assert "ran out of evaluations" in starved

    monkeypatch.setattr(U, "_MAX_NFEV", 2000)
    U.main()
    assert "ran out of evaluations" not in capsys.readouterr().out


def test_the_reported_sigma_is_the_one_the_fit_actually_simulated():
    """The returned model must be the model that produced the returned error.

    `fit_params` reports `sigma` from the value it was handed while the solve
    uses it separately, so nothing else in this file notices if the two come
    apart -- every other test either reads the reported value or scores the
    reported model, and both agree with each other while disagreeing with what
    was simulated. Re-deriving the residual from the REPORTED dict and matching
    it against the solver's own final cost closes that: they can only agree if
    the model handed back is the model that was fitted.
    """
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    reported = simulate_grey_box(
        t,
        Q,
        T_amb=T_AMB,
        T0=float(temp[0]),
        **{k: fitted[k] for k in ("C_c", "h_amb", "K_Q", "sigma", "theta", "n_delay")},
    )
    # This dataset is generated from the model, so a fit that simulated what it
    # reports reproduces it to numerical precision. A solve that used a
    # different sigma than it reported would have optimised a different
    # objective, and the reported model cannot then be this good.
    assert np.sqrt(np.mean((reported - temp) ** 2)) < 1e-3

    # And the radiative term must genuinely be in play, or the check above
    # would pass for any sigma at all.
    without = simulate_grey_box(
        t,
        Q,
        T_amb=T_AMB,
        T0=float(temp[0]),
        **{k: fitted[k] for k in ("C_c", "h_amb", "K_Q", "theta", "n_delay")},
        sigma=0.0,
    )
    assert np.max(np.abs(without - reported)) > 1.0


def test_the_json_mode_carries_the_convergence_verdict(ds, capsys, monkeypatch):
    """--json is the mode something else consumes; it must not be the quiet one."""
    import json

    import controller.update_mpc as U

    t, Q, temp = _dataset()
    _seed_trace(t, temp, Q)
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--database", str(ds.DB_PATH), "--t-amb", str(T_AMB), "--json"],
    )

    monkeypatch.setattr(U, "_MAX_NFEV", 3)
    U.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # stdout stays parseable JSON
    assert payload["fit"]["converged"] is False
    assert "ran out of evaluations" in captured.err  # ...and a human is told, on stderr
    assert all(key in payload["config"] for key in CONFIG_KEYS)

    monkeypatch.setattr(U, "_MAX_NFEV", 2000)
    U.main()
    captured = capsys.readouterr()
    assert json.loads(captured.out)["fit"]["converged"] is True
    assert "ran out of evaluations" not in captured.err


def _reject_constant(literal):
    """A `json.loads` hook that turns Python's Infinity/NaN extension into a refusal."""
    raise ValueError(f"not RFC 8259: {literal}")


def test_the_json_mode_reports_an_unscorable_fit_as_null_not_as_infinity(ds, capsys, monkeypatch):
    """`Infinity` is not JSON, and Python's own decoder is the reason that hides.

    `fit_quality` answers infinity where the grey box cannot be simulated at
    the fitted parameters at all -- correct at that layer, and what its caller
    in `model_promotion.evaluate` compares against. But RFC 8259 has no such
    literal, so the number cannot be serialised as itself: `json.dumps` writes
    a bare `Infinity` that only Python reads back. The encoding this codebase
    already chose for an error nobody could measure is `None` -- see
    `controller/mpc.py`'s snapshot -- and the CLI owes a consumer the same one.

    The strict parse is the assertion that matters. `json.loads` alone accepts
    `Infinity` and would pass against the defect, which is exactly how it
    shipped.
    """
    import json

    import controller.update_mpc as U

    t, Q, temp = _dataset()
    _seed_trace(t, temp, Q)
    monkeypatch.setattr(
        "sys.argv",
        ["update_mpc", "--cook", "calibration-cook", "--database", str(ds.DB_PATH), "--t-amb", str(T_AMB), "--json"],
    )

    # The premise as a negative control: the solve starts where the chamber's
    # own float arithmetic overflows, so `fit_quality` really is reporting the
    # absence of a trajectory here and not merely a bad one.
    with pytest.raises(OverflowError):
        simulate_grey_box(
            t,
            Q,
            T_amb=T_AMB,
            T0=float(temp[0]),
            C_c=1e-9,
            h_amb=float(_DEFAULTS["h_amb"]),
            K_Q=float(_DEFAULTS["K_Q"]),
            theta=float(_DEFAULTS["theta"]),
            sigma=float(_DEFAULTS["sigma"]),
            n_delay=N_DELAY,
        )
    shipped_C_c = float(_DEFAULTS["C_c"])
    monkeypatch.setitem(_DEFAULTS, "C_c", 1e-9)

    U.main()
    unscorable = capsys.readouterr().out
    for literal in ("Infinity", "NaN"):
        assert literal not in unscorable
    payload = json.loads(unscorable, parse_constant=_reject_constant)
    assert payload["fit"]["converged"] is False
    # None only ever arrives here from a non-finite float, so this is the same
    # statement as "the error could not be measured", in the JSON that says so.
    assert payload["fit"]["rmse_c"] is None
    assert payload["fit"]["max_error_c"] is None
    assert all(key in payload["config"] for key in CONFIG_KEYS)

    # And the ordinary fit still reports numbers: `null` marks the unmeasurable
    # case only, so the same record from a simulable start must not read as one.
    monkeypatch.setitem(_DEFAULTS, "C_c", shipped_C_c)
    U.main()
    scored = json.loads(capsys.readouterr().out, parse_constant=_reject_constant)
    assert scored["fit"]["converged"] is True
    assert math.isfinite(scored["fit"]["rmse_c"])
    assert math.isfinite(scored["fit"]["max_error_c"])


def test_the_json_encoder_refuses_a_non_finite_number_rather_than_emitting_one():
    """The guard behind the conversion, for every value that is not yet converted.

    Substituting `None` fixes the error this utility knows about today. This
    fixes the class: any non-finite number reaching the emit raises here,
    beside the value that produced it, instead of leaving as text a consumer
    cannot parse. It is what `controller/mpc.py`'s snapshot validator already
    encodes with.
    """
    import controller.update_mpc as U

    for bad in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValueError):
            U._dump_json({"fit": {"rmse_c": bad}})

    # The negative control: the refusal is of the value, not of the document.
    assert '"rmse_c": 1.5' in U._dump_json({"fit": {"rmse_c": 1.5}})


def test_the_fit_reports_whether_it_converged():
    t, Q, temp = _dataset()
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    assert fitted["converged"] is True
    assert 0 < fitted["nfev"] <= 2000

    import controller.update_mpc as U

    original = U._MAX_NFEV
    U._MAX_NFEV = 3
    try:
        starved = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    finally:
        U._MAX_NFEV = original
    assert starved["converged"] is False
    # The parameters still come back -- that is exactly why the flag is needed.
    assert all(key in starved for key in CONFIG_KEYS)


def test_a_fit_the_model_cannot_be_simulated_at_is_reported_as_not_converged():
    """A solve that reaches a point the grey box blows up at is not a result.

    C_c is a FREE parameter bounded below only by `_LOWER_BOUND`, and the
    chamber's own Euler step is stable only for a sub-step under 2*C_c/h_amb,
    so a small enough C_c takes the chamber away inside one sample interval.
    The radiative term is what actually goes first: the chamber state is a
    Python float, so `(T_c + 273.15)**4` raises OverflowError rather than
    returning inf, and a guard that only asks `np.isfinite` never sees it.
    Either shape must come back as "not converged", never as an exception out
    of the middle of `refit_from_cook` and never as parameters a live grill
    is then offered.

    The starting point is used as the non-simulable one because that is the
    only place a record can put the solve deterministically; what is being
    pinned is `fit_params`' behaviour at such a point, not how it got there.
    """
    t, Q, temp = _dataset()

    # The negative control for the premise: this really is a parameter set the
    # shipped simulator cannot produce a number for. Without it the test would
    # pass on any init that merely fits badly.
    with pytest.raises(OverflowError):
        simulate_grey_box(
            t, Q, C_c=1e-9, h_amb=0.5, T_amb=T_AMB, T0=float(temp[0]), K_Q=32.0, sigma=SIGMA, theta=110.0, n_delay=8
        )

    diverged = fit_params(t, temp, Q, T_amb=T_AMB, init=dict(_init(), C_c=1e-9), sigma=SIGMA, n_delay=N_DELAY)
    assert diverged["converged"] is False
    # And it is a complete result, not a half-built one: the caller reads the
    # flag, so everything else has to be there to be read alongside it.
    assert all(key in diverged for key in CONFIG_KEYS)

    # The same record from a simulable start does converge, so what the
    # assertion above catches is the non-simulable point and not the record.
    assert fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)["converged"] is True


def test_a_fit_whose_simulation_goes_non_finite_without_raising_is_also_refused():
    """The other half of the same guard: NaN that arrives quietly.

    The test above pins the shape that announces itself. This pins the shape
    that does not. A large enough `sigma` makes the radiative term inf by
    ordinary float multiplication -- which does NOT raise, unlike the `**4`
    that overflows -- and the next chamber step subtracts inf from inf and
    carries NaN to the end of the record. `except OverflowError` never fires
    here; only `np.all(np.isfinite(y))` stands between that and a set of
    parameters offered to a live grill as a converged fit.

    Both halves need their own record because the two shapes are reached by
    different parameters, and a guard is only known to be doing its job where
    something makes it fire.
    """
    t, Q, temp = _dataset()
    quiet_nan = dict(sigma=1e300, n_delay=N_DELAY)

    # The premise, as a negative control: non-finite AND no exception. If a
    # future change made this raise instead, the test would still pass while
    # having stopped testing the isfinite branch, so both halves are asserted.
    y = simulate_grey_box(t, Q, T_amb=T_AMB, T0=float(temp[0]), **_init(), **quiet_nan)
    assert not np.all(np.isfinite(y))

    diverged = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), **quiet_nan)
    assert diverged["converged"] is False
    assert all(key in diverged for key in CONFIG_KEYS)

    # And the same record with a sigma the model survives converges, so what
    # the assertion above catches is the non-finite simulation, not the data.
    assert fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)["converged"] is True


def test_database_trace_of_committed_mak_evidence_reproduces_established_fit(ds):
    """Historical CSV is test evidence only; calibration reads typed SQLite rows."""
    mak = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    _seed_trace(mak["time_s"].values, mak["temp_c"].values, mak["Q"].values, cook_id="mak-evidence")

    t, temp, Q = load_trace_samples(cook_id="mak-evidence", database_path=ds.DB_PATH)
    fitted = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    rmse, _ = fit_quality(t, temp, Q, fitted, T_amb=T_AMB)

    assert rmse == pytest.approx(2.3358, abs=0.02)


def test_a_quality_score_where_the_simulation_raises_is_infinite_not_an_exception():
    """Scoring a model is asking a question, and it must always get an answer.

    `fit_quality` is handed whatever parameters its caller holds -- including
    an incumbent that arrived from imported or hand-edited settings, which
    nothing on the way in checks can be simulated. The raised shape is the one
    that escapes: `Controller.refit_from_cook` scores two models inside a
    `try` that catches ValueError and FloatingPointError only, and it runs in
    HoldMode's teardown, where the next thing owed to the grill is the
    cool-down fan.
    """
    t, Q, temp = _dataset()
    runaway = dict(TRUTH, C_c=1e-9, sigma=SIGMA, n_delay=N_DELAY)

    # The premise as a negative control: the simulator really does RAISE at
    # this parameter set, so what is asserted below is the OverflowError
    # branch and not the isfinite one.
    with pytest.raises(OverflowError):
        simulate_grey_box(t, Q, T_amb=T_AMB, T0=float(temp[0]), **runaway)

    assert fit_quality(t, temp, Q, runaway, T_amb=T_AMB) == (math.inf, math.inf)


def test_a_quality_score_where_the_simulation_goes_quietly_non_finite_is_also_infinite():
    """The half of the same guard that announces nothing.

    A large enough `sigma` makes the radiative term inf by ordinary float
    multiplication, which does not raise, and NaN reaches the end of the
    record. `except OverflowError` never fires here, and a NaN RMSE is worse
    than an infinite one: every comparison against it is False, so a model
    that could not be simulated at all would neither beat nor lose to the one
    driving the grill.
    """
    t, Q, temp = _dataset()
    quiet_nan = dict(TRUTH, sigma=1e300, n_delay=N_DELAY)

    # Non-finite AND no exception: without both halves this could pass while
    # having stopped covering the isfinite branch.
    y = simulate_grey_box(t, Q, T_amb=T_AMB, T0=float(temp[0]), **quiet_nan)
    assert not np.all(np.isfinite(y))

    assert fit_quality(t, temp, Q, quiet_nan, T_amb=T_AMB) == (math.inf, math.inf)

    # The same record at a parameter set the model survives scores finitely,
    # so what the infinities report is the simulation and not the data.
    survivable = fit_quality(t, temp, Q, dict(TRUTH, sigma=SIGMA, n_delay=N_DELAY), T_amb=T_AMB)
    assert all(math.isfinite(v) for v in survivable)


def test_a_fit_to_a_real_cook_lands_where_the_promotion_policy_can_accept_it():
    """A fit outside PROMOTION_BOUNDS is refused however well it describes the log.

    The firepot is quasi-static over most of a cook, so the chamber's response
    depends only on C_c/h_amb, K_Q/h_amb and sigma/h_amb -- and freeing C_c,
    h_amb and K_Q together leaves a direction along which all three grow while
    the first two ratios hold and the third, with sigma held, shrinks to
    nothing. Moving along it deletes the radiative term for a small residual
    gain, and the solver takes the trade: on this cook, with h_amb free, it
    converges at C_c 2.6e7 and h_amb 7.4e3 against bounds of 1e6 and 1e3.

    `_FREE` holds h_amb to pin that direction. This asserts the outcome on the
    real cook rather than the reasoning, and the counter-case below is what
    shows the assertion can fail.
    """
    import controller.update_mpc as U
    from controller.model_promotion import PROMOTION_BOUNDS

    mak = pd.read_csv(os.path.join(os.path.dirname(__file__), "fixtures", "mak_cook_2026-08-02.csv"))
    t, temp, Q = mak["time_s"].values, mak["temp_c"].values, mak["Q"].values
    kwargs = dict(T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    def outside(fitted):
        return [k for k, (lo, hi) in PROMOTION_BOUNDS.items() if k in fitted and not (lo <= fitted[k] <= hi)]

    assert outside(fit_params(t, temp, Q, **kwargs)) == []

    original = U._FREE
    U._FREE = original + ("h_amb",)
    try:
        assert "h_amb" in outside(fit_params(t, temp, Q, **kwargs))
    finally:
        U._FREE = original


def test_the_solve_moves_every_free_parameter_by_ratio_not_by_amount():
    """A step of the solve means the same thing in every direction.

    scipy sizes its finite-difference step and its trust region in the
    coordinates it is given, so a solve posed directly in C_c (thousands) and
    K_Q (single digits) probes and moves those two on wildly different scales.
    Optimising the logarithms makes every coordinate dimensionless, so a step
    is a ratio.

    Asserted as the property that follows from it rather than by reaching into
    the transform: the same grill restated in different units -- every
    parameter and the temperatures it explains scaled together -- must be
    recovered as the same rescaled answer, to the solve's own tolerance. A
    solve conditioned on absolute magnitudes cannot do that, because the two
    problems are the same shape but probed at different resolutions.
    """
    t, Q, temp = _dataset()
    base = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)

    # The same physics with heat measured in units 1000x smaller: C_c, K_Q,
    # h_amb and sigma all scale together and the trajectory is bit-identical.
    scaled_init = {k: (v * 1000.0 if k in ("C_c", "h_amb", "K_Q") else v) for k, v in _init().items()}
    scaled = fit_params(t, temp, Q, T_amb=T_AMB, init=scaled_init, sigma=SIGMA * 1000.0, n_delay=N_DELAY)

    for key in ("C_c", "K_Q"):
        assert scaled[key] / 1000.0 == pytest.approx(base[key], rel=1e-3), key
    assert scaled["theta"] == pytest.approx(base["theta"], rel=1e-3)


def test_the_solve_starts_from_the_caller_s_own_values():
    """A refit starts from an already-fitted model, not from the defaults.

    So the solve has to begin where `init` puts it, and the log transform has
    to carry the caller's value through rather than substituting a reference of
    its own.
    """
    from controller.update_mpc import _LOWER_BOUND, _log_or_floor

    t, Q, temp = _dataset()
    near = fit_params(t, temp, Q, T_amb=T_AMB, init=dict(_init(), C_c=TRUTH["C_c"]), sigma=SIGMA, n_delay=N_DELAY)
    assert near["C_c"] == pytest.approx(TRUTH["C_c"], rel=0.35)

    floor = math.log(_LOWER_BOUND)
    # The caller's own value, not a table's.
    assert _log_or_floor(8000.0, floor) == math.log(8000.0)
    # Values with no logarithm to take go to the floor rather than raising or
    # returning -inf, either of which would take the whole solve with them.
    for degenerate in (0.0, -1.0, float("nan"), float("inf")):
        assert _log_or_floor(degenerate, floor) == floor


@pytest.mark.parametrize(
    "label,sigma,n_delay",
    [
        ("no radiative term", 0.0, N_DELAY),
        ("no delay chain", SIGMA, 0),
        ("neither", 0.0, 0),
    ],
)
def test_a_structure_missing_either_term_cannot_explain_this_dataset(label, sigma, n_delay):
    """The negative control for the defect this replaces: the old fitter had no
    delay chain and no radiative term, so it could only absorb them into the
    capacitances.

    Each term is stripped SEPARATELY as well as together, because a single
    combined case only proves that *something* was missing. The threshold sits
    just above the full structure's own error rather than at a round number:
    with both terms present the fit reproduces this dataset exactly, so any
    residual at all is the missing term showing through -- and a bar set high
    enough to catch only the worse of the two cripplings would let the other
    pass, which is what this test previously did.
    """
    from controller.update_mpc import _FREE

    # Zeroing sigma only removes the radiative term while `_FREE` holds sigma.
    # If it were ever fitted, this arm would be moving the starting point and
    # crippling nothing.
    assert "sigma" not in _FREE

    t, Q, temp = _dataset()

    full = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=SIGMA, n_delay=N_DELAY)
    exact, _ = fit_quality(t, temp, Q, full, T_amb=T_AMB)
    assert exact < 0.01, "the full structure should reproduce its own generating model"

    crippled = fit_params(t, temp, Q, T_amb=T_AMB, init=_init(), sigma=sigma, n_delay=n_delay)
    rmse, _ = fit_quality(t, temp, Q, crippled, T_amb=T_AMB)
    assert rmse > 0.5, f"{label} was absorbed rather than showing up as error"
