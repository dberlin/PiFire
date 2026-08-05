"""A deliberately naive reference guards the vectorized bank.

Both references below are written from the equations in
docs/superpowers/specs/2026-08-01-adaptive-smith-predictor-design.md, NOT
adapted from the production code. A reference derived by refactoring the
implementation proves only that it agrees with itself.
"""

import numpy as np
import pytest

from controller.fopdt_identifier import DELAYS, EW_ALPHA, LAM, P0, DutyHistory, RLSBank


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
