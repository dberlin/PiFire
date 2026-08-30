"""A deliberately naive reference guards the vectorized bank.

Both references below are written from the equations in
docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md, NOT
adapted from the production code. A reference derived by refactoring the
implementation proves only that it agrees with itself.
"""

import numpy as np
import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.fopdt_identifier import (
    AMBIENT_F,
    DELAYS,
    DISTRUST_RATIO,
    DISTRUST_WINDOW,
    EW_ALPHA,
    FORM_FOPDT,
    FORM_IPDT,
    GAIN_MAX,
    GAIN_MIN,
    LAM,
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
    P0,
    RESTORE_BOUNDS,
    T_REF,
    T_SCALE,
    TAU_MAX,
    TAU_MIN,
    DutyHistory,
    FOPDTIdentifier,
    RLSBank,
    gate_mask,
    promote,
    recover_parameters,
    relative_standard_errors,
)

# ---------------------------------------------------------------- scalar oracle


def _oracle_rls(observations, n):
    """One plain 3x3 RLS update per candidate, in a Python loop."""
    theta = [np.zeros(3) for _ in range(n)]
    p = [P0 * np.eye(3) for _ in range(n)]
    resid = [0.0] * n
    for phi_all, y in observations:
        for j in range(n):
            phi = np.asarray(phi_all[j], dtype=float)
            pphi = p[j] @ phi
            denom = LAM + float(phi @ pphi)
            gain = pphi / denom
            err = float(y - phi @ theta[j])
            theta[j] = theta[j] + gain * err
            p[j] = (p[j] - np.outer(gain, pphi)) / LAM
            p[j] = 0.5 * (p[j] + p[j].T)
            resid[j] = EW_ALPHA * err**2 + (1.0 - EW_ALPHA) * resid[j]
    return np.array(theta), np.array(p), np.array(resid)


def _oracle_delayed_average(records, t_start, t_end, delays):
    """Average duty over [t_start - theta, t_end - theta) by direct scan."""
    out, valid = [], []
    for theta in delays:
        lo, hi = t_start - theta, t_end - theta
        if lo < records[0][0]:
            out.append(0.0)
            valid.append(False)
            continue
        total = 0.0
        for k, (t, u) in enumerate(records):
            seg_lo = t
            seg_hi = records[k + 1][0] if k + 1 < len(records) else max(hi, t)
            a, b = max(seg_lo, lo), min(seg_hi, hi)
            if b > a:
                total += u * (b - a)
        out.append(total / (t_end - t_start))
        valid.append(True)
    return np.asarray(out), np.asarray(valid)


# ------------------------------------------------------------------- sequences


def _messy_sequence(seed=7):
    """Variable dt, windows straddling several segments and reaching back before
    retained history, duty constant over some stretches and stepping over others."""
    rng = np.random.default_rng(seed)
    records, t, u = [], 0.0, 0.15
    for k in range(60):
        if k % 7 < 3:
            pass  # hold the duty constant for a stretch
        else:
            u = float(rng.choice([0.0, 0.15, 0.4, 0.75, 0.9]))
        records.append((t, u))
        t += float(rng.uniform(3.0, 40.0))
    return records


# ------------------------------------------------------------------------ tests


def test_delayed_average_matches_the_direct_scan():
    records = _messy_sequence()
    history = DutyHistory(float(DELAYS.max()))
    for t, u in records:
        history.record(t, u)
    # collapse repeats the way DutyHistory does, so the oracle sees the same steps
    collapsed = [records[0]]
    for t, u in records[1:]:
        if u != collapsed[-1][1]:
            collapsed.append((t, u))

    for t_start in (200.0, 450.0, 700.0):
        t_end = t_start + 30.0
        got, got_valid = history.average(t_start, t_end, DELAYS)
        want, want_valid = _oracle_delayed_average(collapsed, t_start, t_end, DELAYS)
        assert got_valid.tolist() == want_valid.tolist()
        np.testing.assert_allclose(got[got_valid], want[want_valid], rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize(
    "retention_s",
    [True, "120", 0.0, -1.0, float("nan"), float("inf")],
)
def test_duty_history_rejects_invalid_retention(retention_s):
    history = DutyHistory(120.0)

    with pytest.raises((TypeError, ValueError)):
        history.set_retention_s(retention_s)

    assert history.retention_s == 120.0


def test_interval_duty_belongs_to_the_completed_interval():
    identifier = FOPDTIdentifier()

    identifier.observe_interval(0.0, 20.0, 0.25, 100.0)
    identifier.observe_interval(20.0, 40.0, 0.75, 102.0)

    assert identifier._history.segments(0.0, 40.0) == [
        pytest.approx((20.0, 0.25)),
        pytest.approx((20.0, 0.75)),
    ]


def _candidate_estimator_state(identifier, index):
    return tuple(
        values[index].tobytes()
        for bank in (identifier._bank, identifier._ibank)
        for values in (bank.Theta, bank.P, bank.resid_ew)
    )


def test_exact_interval_pruning_retains_the_window_needed_by_the_max_delay_candidate():
    identifier = FOPDTIdentifier()
    max_delay_index = DELAYS.size - 1

    for step in range(6):
        start = step * 20.0
        identifier.observe_interval(
            start,
            start + 20.0,
            0.2 if step % 2 == 0 else 0.7,
            100.0 + step,
        )

    before = _candidate_estimator_state(identifier, max_delay_index)
    accepted = identifier.observe_interval(120.0, 140.0, 0.2, 106.0)

    assert accepted is True
    assert identifier._history.covers(0.0, 20.0)
    assert _candidate_estimator_state(identifier, max_delay_index) != before


def test_gap_crossing_candidate_state_is_unchanged_while_covered_candidate_updates():
    identifier = FOPDTIdentifier()
    covered_index = int(np.flatnonzero(DELAYS == 0.0)[0])
    gap_index = int(np.flatnonzero(DELAYS == 40.0)[0])

    identifier.observe_interval(0.0, 20.0, 0.2, 100.0)
    identifier.observe_interval(40.0, 60.0, 0.6, 104.0)
    covered_before = _candidate_estimator_state(identifier, covered_index)
    gap_before = _candidate_estimator_state(identifier, gap_index)

    accepted = identifier.observe_interval(60.0, 80.0, 0.8, 108.0)

    assert accepted is True
    assert _candidate_estimator_state(identifier, covered_index) != covered_before
    assert _candidate_estimator_state(identifier, gap_index) == gap_before


def test_batched_bank_matches_the_scalar_loop():
    rng = np.random.default_rng(11)
    n = DELAYS.size
    observations = []
    for _ in range(200):
        shared = np.array([1.0, rng.normal(0.0, 1.0)])
        third = rng.uniform(0.0, 1.0, size=n)
        phi_all = np.column_stack([np.repeat(shared[0], n), np.repeat(shared[1], n), third])
        observations.append((phi_all, float(rng.normal(0.0, 0.05))))

    bank = RLSBank(n)
    # _oracle_rls models no degeneracy reset. If a candidate ever trips
    # RLSBank._reset_degenerate during this run, the two diverge for a reason
    # that has nothing to do with batching -- catch that here, not as an
    # unexplained assert_allclose mismatch below.
    original_reset = bank.reset

    def guarded_reset(mask):
        mask = np.asarray(mask, dtype=bool)
        assert not mask.any(), (
            "RLSBank._reset_degenerate fired during this parity run (candidates "
            f"{np.flatnonzero(mask).tolist()}) -- _oracle_rls does not model a reset, "
            "so any mismatch is that divergence, not a batching bug. Regenerate the "
            "fixture (seed, observation count, or regressor range) rather than loosening "
            "this assertion or the tolerances below."
        )
        return original_reset(mask)

    bank.reset = guarded_reset

    for phi_all, y in observations:
        bank.update(phi_all, y)
    theta, p, resid = _oracle_rls(observations, n)

    np.testing.assert_allclose(bank.Theta, theta, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(bank.P, p, rtol=1e-9, atol=1e-12)
    np.testing.assert_allclose(bank.resid_ew, resid, rtol=1e-9, atol=1e-12)


def test_covariance_stays_symmetric():
    rng = np.random.default_rng(3)
    bank = RLSBank(DELAYS.size)
    for _ in range(500):
        phi = rng.normal(size=(DELAYS.size, 3))
        bank.update(phi, float(rng.normal()))
    np.testing.assert_allclose(bank.P, bank.P.transpose(0, 2, 1), rtol=0, atol=0)


def test_reset_clears_only_the_masked_candidates():
    rng = np.random.default_rng(5)
    bank = RLSBank(DELAYS.size)
    for _ in range(50):
        bank.update(rng.normal(size=(DELAYS.size, 3)), float(rng.normal()))
    before = bank.Theta.copy()
    mask = np.zeros(DELAYS.size, dtype=bool)
    mask[3] = True
    bank.reset(mask)
    assert np.all(bank.Theta[3] == 0.0)
    np.testing.assert_allclose(bank.P[3], P0 * np.eye(3))
    assert bank.resid_ew[3] == 0.0
    np.testing.assert_allclose(bank.Theta[~mask], before[~mask])


# --------------------------------------------------------- recovery and gates


def _theta_for(K, tau, T_offset, n=1):
    """Invert the recovery: build the coefficient row a true model produces."""
    beta_T = -1.0 / tau
    beta_u = -K * beta_T
    beta_0 = -T_offset * beta_T
    # scaled coefficients: c_T = beta_T * T_SCALE, c_0 = beta_0 + beta_T * T_REF
    return np.tile([beta_0 + beta_T * T_REF, beta_T * T_SCALE, beta_u], (n, 1))


def test_recovery_inverts_a_known_model():
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0, n=3)
    params = recover_parameters(theta)
    np.testing.assert_allclose(params["K"], 800.0)
    np.testing.assert_allclose(params["tau"], 600.0)
    np.testing.assert_allclose(params["T_offset"], 70.0)


def test_recovery_masks_a_degenerate_candidate_instead_of_raising():
    theta = np.array([[0.0, 0.0, 0.0]])  # beta_T == 0 -> tau is infinite
    params = recover_parameters(theta)
    assert not np.isfinite(params["tau"]).any() or params["tau"][0] > TAU_MAX


@pytest.mark.parametrize(
    "K,tau,expected",
    [
        (800.0, 600.0, True),
        (GAIN_MIN - 1.0, 600.0, False),
        (GAIN_MAX + 1.0, 600.0, False),
        (-800.0, 600.0, False),
        (800.0, TAU_MIN - 1.0, False),
        (800.0, TAU_MAX + 1.0, False),
        (800.0, -600.0, False),
    ],
)
def test_gate_mask_rejects_unphysical_estimates(K, tau, expected):
    params = recover_parameters(_theta_for(K=K, tau=tau, T_offset=70.0))
    mask = gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.05]))
    assert bool(mask[0]) is expected


def test_gate_mask_rejects_an_uncertain_estimate():
    params = recover_parameters(_theta_for(K=800.0, tau=600.0, T_offset=70.0))
    assert not gate_mask(params, rse_K=np.array([0.25]), rse_tau=np.array([0.05]))[0]
    assert not gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.30]))[0]


def test_gate_mask_rejects_a_non_finite_estimate():
    params = {"K": np.array([np.nan]), "tau": np.array([600.0]), "T_offset": np.array([70.0])}
    assert not gate_mask(params, rse_K=np.array([0.05]), rse_tau=np.array([0.05]))[0]


def test_relative_standard_errors_shrink_as_the_residual_shrinks():
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0, n=2)
    P = np.tile(np.eye(3) * 1e-4, (2, 1, 1))
    loud = relative_standard_errors(theta, P, np.array([1.0, 1.0]))
    quiet = relative_standard_errors(theta, P, np.array([1e-6, 1e-6]))
    assert quiet[0][0] < loud[0][0]
    assert quiet[1][0] < loud[1][0]


def test_promote_requires_a_clear_margin_over_the_runner_up():
    mask = np.ones(4, dtype=bool)
    # winner 10% below runner-up exactly: accepted at the boundary
    winner, margin = promote(np.array([0.90, 1.00, 1.20, 1.50]), mask)
    assert winner == 0
    assert margin == pytest.approx(0.10)
    # indistinguishable: refused
    winner, margin = promote(np.array([0.99, 1.00, 1.20, 1.50]), mask)
    assert winner is None


def test_promote_ignores_gated_out_candidates():
    mask = np.array([False, True, True, True])
    winner, _ = promote(np.array([0.01, 0.90, 1.50, 1.60]), mask)
    assert winner == 1


def test_promote_refuses_when_nothing_passes_the_gates():
    winner, _ = promote(np.array([0.1, 0.2]), np.zeros(2, dtype=bool))
    assert winner is None


def test_promote_refuses_with_a_single_surviving_candidate():
    """One candidate cannot be 10% better than a runner-up that does not exist."""
    winner, _ = promote(np.array([0.1, 0.2]), np.array([True, False]))
    assert winner is None


def test_relative_standard_errors_reports_the_covariance_cross_term():
    """A P with a genuinely non-zero cu/cT covariance must move rse_K by the
    delta-method cross term, not just by the two diagonal variances -- the
    only existing coverage used a diagonal P, where this term vanishes
    identically and a sign flip or deletion would go unnoticed."""
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0)
    P = np.array([[[1e-4, 0.0, 0.0], [0.0, 1e-4, 3e-5], [0.0, 3e-5, 1e-4]]])
    rse_K, rse_tau = relative_standard_errors(theta, P, np.array([1.0]))
    # rel_u2 + rel_T2 - 2*cov_uT/(cu*cT), worked by hand from cT = -1/6,
    # cu = 4/3, var_T = var_u = 1e-4, cov_uT = 3e-5: 5.625e-5 + 0.0036 +
    # 2.7e-4 = 0.00392625. Flipping the cross term's sign gives 0.00338625
    # (rse_K 0.05819...) and dropping it gives 0.00365625 (rse_K 0.06047...)
    # -- both about 4-8% away from the value below, not a last-ulp difference.
    assert rse_K[0] == pytest.approx(0.06265979572261626)
    assert rse_tau[0] == pytest.approx(0.06)


def test_relative_standard_errors_negative_variance_is_not_finite():
    """A P whose off-diagonal is large enough that the delta-method K variance
    goes negative -- only possible when P is not actually positive
    semi-definite, i.e. the estimate is numerically broken -- must come back
    non-finite, never floored to 0.0. A floored 0.0 reads as perfect certainty
    and gate_mask would then promote the single most broken candidate in the
    bank."""
    theta = _theta_for(K=800.0, tau=600.0, T_offset=70.0)
    P = np.array([[[1e-4, 0.0, 0.0], [0.0, 1e-4, -9e-4], [0.0, -9e-4, 1e-4]]])
    rse_K, rse_tau = relative_standard_errors(theta, P, np.array([1.0]))
    assert not np.isfinite(rse_K).any()
    params = recover_parameters(theta)
    assert not gate_mask(params, rse_K, rse_tau)[0]


# ------------------------------------------------------------------ identifier


class _FOPDTPlant:
    """The exact process the identifier assumes. A true answer exists here."""

    def __init__(self, K=800.0, tau=600.0, theta=35.0, T_offset=70.0, dt=20.0):
        self.K, self.tau, self.theta, self.T_offset, self.dt = K, tau, theta, T_offset, dt
        self.x = 0.0
        self.t = 0.0
        self._history = [(0.0, 0.0)]

    def step(self, u):
        self._history.append((self.t, u))
        # delayed input
        target = self.t - self.theta
        u_d = self._history[0][1]
        for ts, uu in self._history:
            if ts <= target:
                u_d = uu
        self.x += (self.K * u_d - self.x) / self.tau * self.dt
        self.t += self.dt
        return self.T_offset + self.x


def _drive(identifier, plant, duties, commanded=True):
    """Run the plant on a duty schedule, reporting and observing each step."""
    for u in duties:
        identifier.record_output(
            AppliedOutput(
                ratio=u,
                source=OutputSource.CONTROLLER if commanded else OutputSource.LID_OPEN,
                timestamp=plant.t,
            )
        )
        temp = plant.step(u)
        identifier.observe(temp, plant.t)


def _excitation_schedule(n, dt=20.0):
    """Alternating sustained duty levels that clear the excitation gates."""
    out = []
    for k in range(n):
        out.append(0.25 if (k // 30) % 2 == 0 else 0.55)
    return out


def test_synthetic_fopdt_candidates_never_become_authoritative():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=800.0, tau=600.0, theta=35.0)

    _drive(identifier, plant, _excitation_schedule(600))

    assert identifier.status()["raw_candidates_passing"] > 0
    assert identifier.trusted_model() is None


def test_no_promotion_under_constant_duty():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, [0.4] * 600)
    assert identifier.trusted_model() is None
    assert identifier.status()["duty_std"] < 0.05


def test_no_promotion_without_enough_temperature_span():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=5.0)  # barely moves the temperature
    _drive(identifier, plant, _excitation_schedule(600))
    assert identifier.trusted_model() is None


def test_no_promotion_when_duty_std_is_the_sole_blocker():
    """Duty constant at 0.40 except a single 4-step excursion to 0.46: enough to
    register a held transition, not enough to move duty_std past its gate."""
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    duties = [0.46 if 300 <= k < 304 else 0.40 for k in range(600)]
    _drive(identifier, plant, duties)
    status = identifier.status()
    assert status["accepted"] >= MIN_ACCEPTED
    assert status["accepted_seconds"] >= MIN_ACCEPTED_SECONDS
    assert status["transition_seen"] is True
    assert (status["temp_span"]) >= 15.0
    assert status["duty_std"] < MIN_DUTY_STD
    # positive control: candidates are actually recoverable here, so the block
    # is the duty_std gate, not a dead identifier.
    assert status["raw_candidates_passing"] > 0
    assert identifier.trusted_model() is None


def test_no_promotion_when_temp_span_is_the_sole_blocker():
    """A low-gain plant (K=100, safely inside [GAIN_MIN, GAIN_MAX]) pre-warmed to
    steady state, then driven with a sustained duty swing large enough to clear
    duty_std and hold long enough to register a transition -- but too gentle,
    against this gain, to move the temperature 15 F."""
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(K=100.0)
    for _ in range(400):
        plant.step(0.40)  # settle near steady state before any observation counts
    duties = [0.55 if (k // 10) % 2 == 0 else 0.25 for k in range(900)]
    _drive(identifier, plant, duties)
    status = identifier.status()
    assert status["accepted"] >= MIN_ACCEPTED
    assert status["accepted_seconds"] >= MIN_ACCEPTED_SECONDS
    assert status["duty_std"] >= MIN_DUTY_STD
    assert status["transition_seen"] is True
    assert status["temp_span"] < MIN_TEMP_SPAN_F
    assert status["raw_candidates_passing"] > 0
    assert identifier.trusted_model() is None


def test_no_promotion_when_the_count_gate_is_satisfied_but_the_time_gate_is_not():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(dt=5.0)
    _drive(identifier, plant, _excitation_schedule(80))
    status = identifier.status()
    assert status["accepted"] >= MIN_ACCEPTED
    assert status["accepted_seconds"] < MIN_ACCEPTED_SECONDS
    assert status["raw_candidates_passing"] > 0
    assert identifier.trusted_model() is None


def test_no_promotion_when_the_time_gate_is_satisfied_but_the_count_gate_is_not():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant(dt=60.0)
    duties = [0.25 if (index // 2) % 2 == 0 else 0.55 for index in range(20)]
    _drive(identifier, plant, duties)
    status = identifier.status()
    assert status["accepted"] < MIN_ACCEPTED
    assert status["accepted_seconds"] >= MIN_ACCEPTED_SECONDS
    assert status["raw_candidates_passing"] > 0
    assert identifier.trusted_model() is None


def test_a_paused_interval_creates_no_cross_gap_observation():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, _excitation_schedule(300))
    before = identifier.status()["accepted"]
    _drive(identifier, plant, [0.0] * 10, commanded=False)
    assert identifier.status()["accepted"] == before
    # the first observation AFTER the gap is also rejected: it would span it
    _drive(identifier, plant, _excitation_schedule(1))
    assert identifier.status()["accepted"] == before
    _drive(identifier, plant, _excitation_schedule(2))
    assert identifier.status()["accepted"] > before


def test_a_non_finite_temperature_is_rejected():
    identifier = FOPDTIdentifier()
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    identifier.observe(200.0, 0.0)
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 20.0))
    identifier.observe(210.0, 20.0)
    before = identifier.status()["accepted"]
    assert before > 0
    assert identifier.observe(float("nan"), 40.0) is False
    assert identifier.status()["accepted"] == before
    # the NaN opened a gap: the very next observation would span it and is
    # rejected too, even though it is itself finite and evenly spaced.
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 60.0))
    assert identifier.observe(220.0, 60.0) is False
    assert identifier.status()["accepted"] == before


@pytest.mark.parametrize("dt", [0.0, -20.0, 0.5, 100000.0])
def test_an_implausible_dt_is_rejected(dt):
    identifier = FOPDTIdentifier()
    identifier.record_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 0.0))
    identifier.observe(200.0, 100.0)
    before = identifier.status()["accepted"]
    identifier.observe(210.0, 100.0 + dt)
    assert identifier.status()["accepted"] == before


def test_memory_is_bounded():
    identifier = FOPDTIdentifier()
    plant = _FOPDTPlant()
    _drive(identifier, plant, _excitation_schedule(3000))
    assert identifier.status()["duty_segments"] < 40
    assert identifier.Theta.shape == (DELAYS.size, 3)


def test_restore_adopts_a_valid_model_and_rejects_an_impossible_one():
    identifier = FOPDTIdentifier()
    assert identifier.restore({"K": 800.0, "tau": 600.0, "theta": 35.0, "revision": 4}) is True
    assert identifier.trusted_model()["K"] == 800.0
    assert identifier.trusted_model()["revision"] == 4
    assert identifier.restore({"K": -1.0, "tau": 600.0, "theta": 35.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 1.0, "theta": 35.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 600.0, "theta": 999.0, "revision": 5}) is False
    assert identifier.restore({"K": 800.0, "tau": 600.0}) is False


def _ipdt(**overrides):
    model = {"form": FORM_IPDT, "K_i": 0.55, "c0": -0.113, "theta": 90.0, "revision": 4, "identified_at_f": 450.0}
    return {**model, **overrides}


def test_a_restored_model_answers_the_hold_duty_the_bank_cannot_yet():
    """The bank starts every cook empty, so it cannot repeat last cook's finding
    until it has re-earned it well into the climb -- which is after the approach
    the operating point exists to shape. A trusted model answers immediately."""
    identifier = FOPDTIdentifier()
    assert identifier.hold_duty() is None  # nothing learned, nothing to say
    assert identifier.restore(_ipdt()) is True
    assert identifier.hold_duty() == pytest.approx(0.113 / 0.55)


def test_the_hold_duty_moves_with_the_set_point_it_is_asked_about():
    """c0 is the chamber's loss at the temperature the fit was taken at, and loss
    is proportional to the rise above ambient, so the duty that holds another
    temperature scales with the ratio of rises."""
    identifier = FOPDTIdentifier()
    identifier.restore(_ipdt())
    at_450 = identifier.hold_duty(target_f=450.0)
    at_225 = identifier.hold_duty(target_f=225.0)
    assert at_450 == pytest.approx(0.113 / 0.55)
    assert at_225 == pytest.approx(at_450 * (225.0 - AMBIENT_F) / (450.0 - AMBIENT_F))
    # Asking about a colder chamber must lower the duty, not merely change it.
    assert at_225 < at_450


def test_retarget_moves_a_restored_model_to_the_new_operating_point():
    """Carrying a 450 F model into a 225 F cook unchanged asserts 450 F losses at
    225 F. Measured before this: the loop held the chamber at 231 F against a
    225 F target -- right duty, wrong temperature -- and 0.8% of the cook landed
    within 5 F."""
    identifier = FOPDTIdentifier()
    identifier.restore(_ipdt())
    before = identifier.trusted_model()["c0"]
    before_revision = identifier.trusted_model()["revision"]

    assert identifier.retarget(225.0) is True

    after = identifier.trusted_model()["c0"]
    assert after == pytest.approx(before * (225.0 - AMBIENT_F) / (450.0 - AMBIENT_F))
    # Losses at a cooler chamber are smaller, and c0 is negative.
    assert after > before
    assert identifier.trusted_model()["revision"] == before_revision + 1
    # Moved once, it now describes 225 and must not be moved again for it.
    assert identifier.retarget(225.0) is False
    assert identifier.trusted_model()["c0"] == pytest.approx(after)
    assert identifier.trusted_model()["revision"] == before_revision + 1


def test_every_diagnostic_form_can_be_restored():
    assert set(RESTORE_BOUNDS) == {FORM_FOPDT, FORM_IPDT, "sopdt"}


@pytest.mark.parametrize(
    "model",
    [
        {"form": FORM_FOPDT, "K": 800.0, "tau": 600.0, "theta": 35.0, "revision": 4},
        {"form": FORM_IPDT, "K_i": 0.46, "c0": -0.033, "theta": 90.0, "revision": 4},
    ],
    ids=[FORM_FOPDT, FORM_IPDT],
)
def test_a_persisted_model_round_trips_through_restore(model):
    """Both ends of the store, for every form: what a cook hands out is what
    the next cook gets back."""
    identifier = FOPDTIdentifier()
    assert identifier.restore(dict(model)) is True
    assert identifier.trusted_model() == model


def test_restore_rejects_an_impossible_integrating_model():
    identifier = FOPDTIdentifier()
    good = {"form": FORM_IPDT, "K_i": 0.46, "c0": -0.033, "theta": 90.0, "revision": 4}
    assert identifier.restore(dict(good)) is True
    # A chamber that climbs with the auger off is not a chamber.
    assert identifier.restore({**good, "c0": 0.5}) is False
    assert identifier.restore({**good, "K_i": 500.0}) is False
    assert identifier.restore({**good, "theta": 999.0}) is False
    # The first-order parameters are not this form's parameters.
    assert identifier.restore({"form": FORM_IPDT, "K": 800.0, "tau": 600.0, "theta": 90.0, "revision": 4}) is False
    assert identifier.restore({**good, "form": "no-such-form"}) is False


def test_restore_accepts_profiled_delay_through_the_adaptive_bound():
    identifier = FOPDTIdentifier()

    assert (
        identifier.restore(
            {
                "form": FORM_FOPDT,
                "K": 800.0,
                "tau": 600.0,
                "theta": 205.0,
                "revision": 4,
            }
        )
        is True
    )
    assert identifier.trusted_model()["theta"] == 205.0


def test_restore_rejects_delay_beyond_the_adaptive_bound():
    identifier = FOPDTIdentifier()

    assert (
        identifier.restore(
            {
                "form": FORM_FOPDT,
                "K": 800.0,
                "tau": 600.0,
                "theta": 905.0,
                "revision": 4,
            }
        )
        is False
    )


def test_raw_bank_cannot_distrust_a_model_outside_its_delay_support():
    identifier = FOPDTIdentifier()
    assert identifier.restore(
        {
            "form": FORM_FOPDT,
            "K": 800.0,
            "tau": 600.0,
            "theta": 205.0,
            "revision": 4,
        }
    )
    identifier._bank.resid_ew[:] = 1.0
    identifier._bank.resid_ew[-1] = 100.0

    assert identifier._distrust_ratio() is None
    for _ in range(DISTRUST_WINDOW):
        identifier._check_distrust()
    assert identifier.trusted_model() is not None
    assert identifier.status()["distrust_count"] == 0


def test_raw_candidates_do_not_mutate_an_explicitly_restored_model():
    identifier = FOPDTIdentifier()
    assert identifier.restore(
        {
            "K": 800.0,
            "tau": 600.0,
            "theta": 40.0,
            "revision": 7,
        }
    )
    restored = identifier.trusted_model()
    plant = _FOPDTPlant(K=802.0, tau=602.0, theta=35.0)

    _drive(identifier, plant, _excitation_schedule(600))

    assert identifier.status()["raw_candidates_passing"] > 0
    assert identifier.trusted_model() == restored


# ---------------------------------------------------------------------- distrust


def _resid_setup(identifier, theta, other_resid=1.0):
    """Point identifier at a trusted delay and hand back its bank index, so a
    test can drive resid_ew directly without a multi-hour simulation."""
    idx = int(np.where(DELAYS == theta)[0][0])
    other = 0 if idx != 0 else 1
    identifier._bank.resid_ew[:] = other_resid
    return idx, other


def test_a_materially_degraded_residual_drops_trust():
    """When the trusted delay's residual runs far worse than the best
    candidate's for DISTRUST_WINDOW straight observations, trust is dropped
    and the count in status() moves -- this is the load-bearing change: a
    wrong-gain model degrades control without spiking _safe's residual
    envelope, so this is the only thing that can catch it."""
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 5})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 100.0  # unambiguously worse than the best
    for _ in range(DISTRUST_WINDOW):
        identifier._check_distrust()
    assert identifier.trusted_model() is None
    assert identifier.status()["distrust_count"] == 1


def test_a_healthy_residual_is_not_dropped():
    """The anti-flapping direction: a trusted delay whose residual stays
    competitive with the best candidate's must never be dropped, no matter
    how long it is observed. This is the one that fails if DISTRUST_RATIO or
    DISTRUST_WINDOW is too tight."""
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 5})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 1.0  # ties the best candidate
    for _ in range(DISTRUST_WINDOW * 5):
        identifier._check_distrust()
    assert identifier.trusted_model() is not None
    assert identifier.status()["distrust_count"] == 0


def test_distrust_ratio_boundary_does_not_trip_exactly_at_the_threshold():
    """DISTRUST_RATIO's own value (8.0) is asserted as a literal here, not
    read back from the constant: the check is a strict `>`, so a ratio of
    exactly 8.0 must never trip it, and writing the boundary as a fixed
    number means a future change to the constant itself still gets checked
    against this same literal boundary rather than silently moving with it."""
    assert DISTRUST_RATIO == 8.0
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 1})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 8.0
    for _ in range(DISTRUST_WINDOW * 2):
        identifier._check_distrust()
    assert identifier.trusted_model() is not None


def test_distrust_ratio_boundary_trips_just_above_the_threshold():
    assert DISTRUST_RATIO == 8.0
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 1})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 8.01
    for _ in range(DISTRUST_WINDOW):
        identifier._check_distrust()
    assert identifier.trusted_model() is None


def test_distrust_window_boundary_requires_the_full_sustain_count():
    """DISTRUST_WINDOW straight bad observations are required; one short must
    not trip it."""
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 1})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 100.0
    for _ in range(DISTRUST_WINDOW - 1):
        identifier._check_distrust()
    assert identifier.trusted_model() is not None
    identifier._check_distrust()  # the DISTRUST_WINDOW-th straight bad observation
    assert identifier.trusted_model() is None


def test_distrust_is_not_sticky():
    """Once cleared, distrust leaves no latch: a model restored at the same
    delay, now with a healthy residual, is not immediately redropped, and the
    count does not move again."""
    identifier = FOPDTIdentifier()
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 5})
    idx, _other = _resid_setup(identifier, 20.0)
    identifier._bank.resid_ew[idx] = 100.0
    for _ in range(DISTRUST_WINDOW):
        identifier._check_distrust()
    assert identifier.trusted_model() is None
    assert identifier.status()["distrust_count"] == 1

    identifier._bank.resid_ew[idx] = 1.0  # the same candidate now looks fine
    identifier.restore({"K": 800.0, "tau": 600.0, "theta": 20.0, "revision": 6})
    for _ in range(DISTRUST_WINDOW * 2):
        identifier._check_distrust()
    assert identifier.trusted_model() is not None
    assert identifier.status()["distrust_count"] == 1  # unchanged: no re-trip


def test_status_reports_what_the_gates_are_waiting_for():
    identifier = FOPDTIdentifier()
    status = identifier.status()
    for key in (
        "accepted",
        "accepted_seconds",
        "duty_std",
        "temp_span",
        "duty_segments",
        "raw_best_residual",
        "raw_runner_up_residual",
        "trusted",
        "raw_candidates_passing",
        "distrust_count",
        "distrust_ratio",
    ):
        assert key in status
