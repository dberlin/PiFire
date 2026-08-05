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
