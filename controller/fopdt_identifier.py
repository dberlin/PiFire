#!/usr/bin/env python3

"""
*****************************************
 PiFire FOPDT Identifier
*****************************************

 Description: Online identification of a first-order-plus-dead-time grill model
 from applied auger duty and measured temperature.

     T(t) = T_offset + x_d(t)
     dx/dt = (K * u - x) / tau
     x_d(t) = x(t - theta)

 Dead time is not estimated continuously. A bank of recursive-least-squares
 estimators runs one candidate delay each, 0 to 120 s in 5 s steps, and the bank
 is a single batched numpy update rather than a loop -- fixed shapes, bounded
 work, no Python iteration over candidates anywhere.

*****************************************
"""

import numpy as np

#: Dead-time candidates, seconds.
DELAYS = np.arange(0.0, 125.0, 5.0)
N_CANDIDATES = DELAYS.size


class DutyHistory:
    """Applied auger duty as a step function, with a running cumulative integral.

    An auger is on or off, so duty between reports really is piecewise constant
    and the integral is exact rather than approximated. That turns a delayed
    window average -- needed for every candidate delay on every observation --
    into one searchsorted plus a linear interpolation.
    """

    def __init__(self, max_delay):
        self._max_delay = float(max_delay)
        self._t = []  # segment start times
        self._u = []  # duty in force from _t[i] until _t[i + 1]
        self._i = []  # integral of duty dt from _t[0] to _t[i]
        self._ta = np.empty(0)
        self._ua = np.empty(0)
        self._ia = np.empty(0)

    def __len__(self):
        return len(self._t)

    def earliest(self):
        return self._t[0] if self._t else None

    def record(self, timestamp, ratio):
        """Append a duty segment. Ignores a non-advancing timestamp or a repeat."""
        timestamp = float(timestamp)
        ratio = float(ratio)
        if self._t:
            if timestamp <= self._t[-1]:
                return
            if ratio == self._u[-1]:
                return
            self._i.append(self._i[-1] + self._u[-1] * (timestamp - self._t[-1]))
        else:
            self._i.append(0.0)
        self._t.append(timestamp)
        self._u.append(ratio)
        self._sync()

    def _sync(self):
        self._ta = np.asarray(self._t, dtype=float)
        self._ua = np.asarray(self._u, dtype=float)
        self._ia = np.asarray(self._i, dtype=float)

    def integral(self, times):
        """Integral of duty from the earliest retained time to each of `times`.

        Times after the last record extrapolate the last duty forward, which is
        what the auger is actually doing until the next report.
        """
        times = np.asarray(times, dtype=float)
        if self._ta.size == 0:
            return np.zeros_like(times)
        idx = np.clip(np.searchsorted(self._ta, times, side="right") - 1, 0, self._ta.size - 1)
        return self._ia[idx] + self._ua[idx] * np.maximum(times - self._ta[idx], 0.0)

    def average(self, t_start, t_end, delays):
        """Mean duty over [t_start - theta, t_end - theta) for every theta.

        Returns (values, valid). A candidate is invalid when its window reaches
        back before the earliest retained segment: there is no duty to average
        there, and guessing one would fabricate an observation.
        """
        delays = np.asarray(delays, dtype=float)
        span = float(t_end) - float(t_start)
        if span <= 0.0 or self._ta.size == 0:
            return np.zeros_like(delays), np.zeros(delays.shape, dtype=bool)
        lo = float(t_start) - delays
        hi = float(t_end) - delays
        values = (self.integral(hi) - self.integral(lo)) / span
        return values, lo >= self._ta[0]

    def segments(self, t_start, t_end):
        """[(duration, duty)] covering [t_start, t_end), split at every change."""
        t_start, t_end = float(t_start), float(t_end)
        if t_end <= t_start or self._ta.size == 0:
            return []
        edges = [t_start]
        edges.extend(t for t in self._t if t_start < t < t_end)
        edges.append(t_end)
        out = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            if hi <= lo:
                continue
            idx = max(int(np.searchsorted(self._ta, lo, side="right")) - 1, 0)
            out.append((hi - lo, float(self._ua[idx])))
        return out

    def prune(self, now):
        """Drop segments no candidate delay can still reach."""
        horizon = float(now) - self._max_delay
        keep = 0
        while keep + 1 < len(self._t) and self._t[keep + 1] <= horizon:
            keep += 1
        if keep:
            del self._t[:keep]
            del self._u[:keep]
            del self._i[:keep]
            self._sync()


#: Forgetting factor. Slow enough that an hour of observations still counts,
#: fast enough that a re-seasoned grill is eventually re-learned.
LAM = 0.9995
#: Initial covariance: no prior belief about the coefficients.
P0 = 1e6
#: Weight of the newest squared residual in the exponentially weighted mean.
EW_ALPHA = 0.02

#: Temperature regressors are centered and scaled before each update and
#: transformed back afterwards -- absolute grill temperatures condition the
#: matrix badly. The reference is FIXED rather than a running mean: a moving
#: reference would silently change the meaning of the covariance already
#: accumulated under the old one.
T_REF = 250.0
T_SCALE = 100.0


class RLSBank:
    """One recursive-least-squares estimator per dead-time candidate, batched.

    Theta is (N, 3), P is (N, 3, 3), resid_ew is (N,). Only the third regressor
    column differs per candidate; [1, T_scaled] is shared and broadcast by the
    caller.
    """

    def __init__(self, n_candidates):
        self._n = int(n_candidates)
        self.Theta = np.zeros((self._n, 3))
        self.P = np.tile(P0 * np.eye(3), (self._n, 1, 1))
        self.resid_ew = np.zeros(self._n)

    def update(self, phi, y):
        """One accepted observation into the whole bank. `phi` is (N, 3)."""
        phi = np.asarray(phi, dtype=float)
        Pphi = np.einsum("nij,nj->ni", self.P, phi)
        denom = LAM + np.einsum("ni,ni->n", phi, Pphi)
        gain = Pphi / denom[:, None]
        err = y - np.einsum("ni,ni->n", phi, self.Theta)
        self.Theta += gain * err[:, None]
        self.P = (self.P - np.einsum("ni,nj->nij", gain, Pphi)) / LAM
        # Hold P symmetric against accumulated float drift.
        self.P = 0.5 * (self.P + self.P.transpose(0, 2, 1))
        self.resid_ew = EW_ALPHA * err**2 + (1.0 - EW_ALPHA) * self.resid_ew
        self._reset_degenerate()

    def reset(self, mask):
        """Return the masked candidates to their initial state."""
        mask = np.asarray(mask, dtype=bool)
        if not mask.any():
            return
        self.Theta[mask] = 0.0
        self.P[mask] = P0 * np.eye(3)
        self.resid_ew[mask] = 0.0

    def _reset_degenerate(self):
        """A candidate whose covariance diagonal has gone non-positive, or whose
        Theta/P/resid_ew has gone non-finite, starts over rather than poisoning
        the bank."""
        bad = ~np.isfinite(self.Theta).all(axis=1)
        bad |= ~np.isfinite(self.P).all(axis=(1, 2))
        bad |= ~np.isfinite(self.resid_ew)
        diag = np.einsum("nii->ni", self.P)
        bad |= (diag <= 0.0).any(axis=1)
        self.reset(bad)


#: Physical bounds a grill's identified model must satisfy.
GAIN_MIN, GAIN_MAX = 50.0, 2000.0  # F per unit duty
TAU_MIN, TAU_MAX = 300.0, 20000.0  # seconds
RSE_K_MAX = 0.20
RSE_TAU_MAX = 0.25
#: A winner must beat the runner-up by this fraction of its residual.
PROMOTION_MARGIN = 0.10


def recover_parameters(Theta):
    """Physical parameters from the scaled regression coefficients.

    Undoes the fixed centering and scaling, then
    tau = -1/beta_T, K = -beta_u/beta_T, T_offset = -beta_0/beta_T.
    A candidate whose recovery is not finite comes back non-finite rather than
    raising; gate_mask drops it.
    """
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    c0, cT, cu = Theta[:, 0], Theta[:, 1], Theta[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        beta_T = cT / T_SCALE
        beta_0 = c0 - beta_T * T_REF
        tau = -1.0 / beta_T
        K = -cu / beta_T
        T_offset = -beta_0 / beta_T
    return {"K": K, "tau": tau, "T_offset": T_offset}


def relative_standard_errors(Theta, P, resid_ew):
    """Delta-method relative standard errors for K and tau.

    tau is proportional to 1/c_T, so its relative error is c_T's. K is a ratio
    of two estimated coefficients, so its relative variance carries their
    covariance term.
    """
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    P = np.asarray(P, dtype=float)
    resid_ew = np.asarray(resid_ew, dtype=float)
    cT, cu = Theta[:, 1], Theta[:, 2]
    var_T = resid_ew * P[:, 1, 1]
    var_u = resid_ew * P[:, 2, 2]
    cov_uT = resid_ew * P[:, 2, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel_T2 = var_T / cT**2
        rel_u2 = var_u / cu**2
        # rel_T2 is a variance over a square and cannot go negative for a sane
        # resid_ew/P[1,1], so flooring it is harmless. The combined K variance
        # is a variance of a difference and stays non-negative only while P is
        # genuinely PSD; when it is not, the floor must NOT hide that by
        # reporting perfect certainty -- let it go negative so sqrt reports
        # non-finite and gate_mask's isfinite check drops the candidate.
        rse_tau = np.sqrt(np.maximum(rel_T2, 0.0))
        rse_K = np.sqrt(rel_u2 + rel_T2 - 2.0 * cov_uT / (cu * cT))
    return rse_K, rse_tau


def gate_mask(params, rse_K, rse_tau):
    """Candidates whose estimate is finite, physical and sufficiently certain."""
    K, tau = np.asarray(params["K"]), np.asarray(params["tau"])
    rse_K, rse_tau = np.asarray(rse_K), np.asarray(rse_tau)
    with np.errstate(invalid="ignore"):
        mask = np.isfinite(K) & np.isfinite(tau) & np.isfinite(rse_K) & np.isfinite(rse_tau)
        mask &= (K >= GAIN_MIN) & (K <= GAIN_MAX)
        mask &= (tau >= TAU_MIN) & (tau <= TAU_MAX)
        mask &= rse_K <= RSE_K_MAX
        mask &= rse_tau <= RSE_TAU_MAX
    return mask


def promote(resid_ew, mask):
    """The winning candidate index and its margin, or (None, 0.0).

    A candidate is never promoted merely for having the lowest residual. If the
    best two are statistically indistinguishable there is no evidence for either
    delay, and refusing keeps the controller on measured temperature.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.sum() < 2:
        return None, 0.0
    resid = np.where(mask, np.asarray(resid_ew, dtype=float), np.inf)
    best, runner_up = np.partition(resid, 1)[:2]
    if not np.isfinite(runner_up) or runner_up <= 0.0:
        return None, 0.0
    margin = (runner_up - best) / runner_up
    # Gate on the ratio directly rather than on `margin`: subtracting two
    # decimal literals that are not exact in binary can land `margin` a couple
    # of ULP below a literal threshold even when the true margin is exactly
    # PROMOTION_MARGIN, which would wrongly refuse a boundary-exact winner.
    if best > runner_up * (1.0 - PROMOTION_MARGIN):
        return None, 0.0
    return int(np.argmin(resid)), float(margin)
