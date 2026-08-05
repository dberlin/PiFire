"""A deliberately naive reference guards the vectorized bank.

Both references below are written from the equations in
docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md, NOT
adapted from the production code. A reference derived by refactoring the
implementation proves only that it agrees with itself.
"""

import numpy as np
import pytest

from controller.fopdt_identifier import (
    DELAYS,
    EW_ALPHA,
    GAIN_MAX,
    GAIN_MIN,
    LAM,
    P0,
    T_REF,
    T_SCALE,
    TAU_MAX,
    TAU_MIN,
    DutyHistory,
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
