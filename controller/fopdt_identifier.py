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


#: Trust gates. Profile-independent, so no cook shape is privileged.
MIN_ACCEPTED_SECONDS = 3600.0
MIN_ACCEPTED = 240
MIN_DUTY_STD = 0.05
MIN_TRANSITION = 0.05
MIN_TRANSITION_HOLD = 60.0
MIN_TEMP_SPAN_F = 15.0
CONFIRM_WINDOW = 20
CONFIRM_K_TOL = 0.05
CONFIRM_TAU_TOL = 0.075
#: After initial trust, a candidate is a revision only when it moves this far.
MATERIAL_K = 0.05
MATERIAL_TAU = 0.05
MATERIAL_THETA = 5.0
#: How much of a passing revision blends into the trusted values.
BLEND = 0.1
#: A dt outside this band is a clock jump or a stalled loop, not an observation.
DT_MIN, DT_MAX = 1.0, 600.0


class FOPDTIdentifier:
    """Passive online identification of the grill's FOPDT parameters.

    Nothing here perturbs the auger: the identifier learns from whatever
    excitation the controller's own regulation happens to produce, and stays
    untrusted until the gates say the data earned it.
    """

    def __init__(self):
        self._bank = RLSBank(N_CANDIDATES)
        self._history = DutyHistory(float(DELAYS.max()))
        self._prev = None  # (timestamp, temperature) anchor
        self._gap = True  # the next observation would span an undriven interval
        self._commanded = True  # whether the most recent report was controller-driven
        self._accepted = 0
        self._accepted_seconds = 0.0
        self._temp_lo = None
        self._temp_hi = None
        self._duty_n = 0
        self._duty_sum = 0.0
        self._duty_sq = 0.0
        self._transition_seen = False
        self._transition_from = None
        self._transition_at = None
        self._trusted = None
        self._revision = 0
        self._confirm = None

    # -------------------------------------------------------------- properties
    @property
    def Theta(self):
        return self._bank.Theta

    # ------------------------------------------------------------------ intake
    def record_output(self, applied):
        """Take an AppliedOutput. Every command enters the duty history -- the
        grill really did run at that duty -- but one the controller did not
        command opens a gap that suppresses identification across it."""
        now = float(applied.timestamp)
        self._history.record(now, applied.ratio)
        self._history.prune(now)
        self._commanded = applied.controller_commanded
        if not applied.controller_commanded:
            self._gap = True
            return
        self._note_transition(now, applied.ratio)

    def _note_transition(self, now, ratio):
        """A sustained duty change is the excitation this design waits for."""
        if self._transition_from is None:
            self._transition_from, self._transition_at = ratio, now
            return
        if abs(ratio - self._transition_from) >= MIN_TRANSITION:
            if now - self._transition_at >= MIN_TRANSITION_HOLD:
                self._transition_seen = True
            self._transition_from, self._transition_at = ratio, now

    def observe(self, temperature_f, timestamp):
        """One temperature sample. True when it became a regression row."""
        now = float(timestamp)
        temp = float(temperature_f)
        if not np.isfinite(temp):
            self._prev = None
            self._gap = True
            return False
        prev, self._prev = self._prev, (now, temp)
        if prev is None or self._gap:
            # A gap only closes on a commanded report: an uncommanded step's own
            # rejection must not clear it, or the very next window would still
            # reach back across the gap while reading as clean.
            if self._commanded:
                self._gap = False
            return False
        t0, y0 = prev
        dt = now - t0
        if not (DT_MIN <= dt <= DT_MAX):
            return False
        duty, valid = self._history.average(t0, now, DELAYS)
        if not valid.any():
            return False
        # A candidate whose window predates retained history contributes its
        # last known duty rather than dropping the whole observation.
        duty = np.where(valid, duty, duty[valid][0])

        shared = np.array([1.0, (y0 - T_REF) / T_SCALE])
        phi = np.empty((N_CANDIDATES, 3))
        phi[:, 0] = shared[0]
        phi[:, 1] = shared[1]
        phi[:, 2] = duty
        self._bank.update(phi, (temp - y0) / dt)

        self._accepted += 1
        self._accepted_seconds += dt
        self._temp_lo = temp if self._temp_lo is None else min(self._temp_lo, temp)
        self._temp_hi = temp if self._temp_hi is None else max(self._temp_hi, temp)
        mean_duty = float(duty[valid].mean())
        self._duty_n += 1
        self._duty_sum += mean_duty
        self._duty_sq += mean_duty * mean_duty
        self._evaluate()
        return True

    # ------------------------------------------------------------------- trust
    def _duty_std(self):
        if self._duty_n < 2:
            return 0.0
        mean = self._duty_sum / self._duty_n
        var = max(self._duty_sq / self._duty_n - mean * mean, 0.0)
        return float(np.sqrt(var))

    def _excited(self):
        return (
            self._accepted >= MIN_ACCEPTED
            and self._accepted_seconds >= MIN_ACCEPTED_SECONDS
            and self._duty_std() >= MIN_DUTY_STD
            and self._transition_seen
            and self._temp_lo is not None
            and (self._temp_hi - self._temp_lo) >= MIN_TEMP_SPAN_F
        )

    def _evaluate(self):
        if not self._excited():
            return
        params = recover_parameters(self._bank.Theta)
        rse_K, rse_tau = relative_standard_errors(self._bank.Theta, self._bank.P, self._bank.resid_ew)
        mask = gate_mask(params, rse_K, rse_tau)
        winner, _ = promote(self._bank.resid_ew, mask)
        if winner is None:
            self._confirm = None
            return
        candidate = {
            "K": float(params["K"][winner]),
            "tau": float(params["tau"][winner]),
            "theta": float(DELAYS[winner]),
        }
        if self._trusted is not None and not self._material(candidate):
            self._confirm = None
            return
        if not self._confirmed(candidate):
            return
        self._adopt(candidate)

    def _material(self, candidate):
        return (
            abs(candidate["K"] - self._trusted["K"]) / self._trusted["K"] >= MATERIAL_K
            or abs(candidate["tau"] - self._trusted["tau"]) / self._trusted["tau"] >= MATERIAL_TAU
            or abs(candidate["theta"] - self._trusted["theta"]) >= MATERIAL_THETA
        )

    def _confirmed(self, candidate):
        """A candidate must hold still for a full window before it is believed."""
        window = self._confirm
        if window is None or window["theta"] != candidate["theta"]:
            self._confirm = {"n": 1, **candidate}
            return False
        if (
            abs(candidate["K"] - window["K"]) / window["K"] > CONFIRM_K_TOL
            or abs(candidate["tau"] - window["tau"]) / window["tau"] > CONFIRM_TAU_TOL
        ):
            self._confirm = {"n": 1, **candidate}
            return False
        window["n"] += 1
        window["K"], window["tau"] = candidate["K"], candidate["tau"]
        return window["n"] >= CONFIRM_WINDOW

    def _adopt(self, candidate):
        self._confirm = None
        if self._trusted is None:
            self._trusted = dict(candidate)
        else:
            # Delay moves outright once confirmed; the continuous parameters
            # blend, so one noisy window cannot swing the model.
            self._trusted = {
                "K": (1.0 - BLEND) * self._trusted["K"] + BLEND * candidate["K"],
                "tau": (1.0 - BLEND) * self._trusted["tau"] + BLEND * candidate["tau"],
                "theta": candidate["theta"],
            }
        self._revision += 1

    def trusted_model(self):
        if self._trusted is None:
            return None
        return {**self._trusted, "revision": self._revision}

    def restore(self, model):
        """Adopt a persisted model, re-checking the physics the store does not
        judge. A restored model is trusted immediately: a process restart is not
        a reason to doubt parameters that were earned."""
        if not isinstance(model, dict):
            return False
        try:
            K, tau, theta = float(model["K"]), float(model["tau"]), float(model["theta"])
            revision = int(model["revision"])
        except KeyError, TypeError, ValueError:
            return False
        if not all(np.isfinite([K, tau, theta])) or revision < 0:
            return False
        if not (GAIN_MIN <= K <= GAIN_MAX) or not (TAU_MIN <= tau <= TAU_MAX):
            return False
        if theta < float(DELAYS.min()) or theta > float(DELAYS.max()):
            return False
        self._trusted = {"K": K, "tau": tau, "theta": theta}
        self._revision = revision
        # A confirmation window built against the pre-restore trusted state must
        # not count toward confirming a candidate against this one.
        self._confirm = None
        return True

    def status(self):
        resid = self._bank.resid_ew
        ordered = np.partition(resid, 1)[:2] if resid.size > 1 else np.array([0.0, 0.0])
        params = recover_parameters(self._bank.Theta)
        rse_K, rse_tau = relative_standard_errors(self._bank.Theta, self._bank.P, self._bank.resid_ew)
        return {
            "accepted": self._accepted,
            "accepted_seconds": round(self._accepted_seconds, 1),
            "duty_std": round(self._duty_std(), 4),
            "temp_span": round((self._temp_hi - self._temp_lo) if self._temp_lo is not None else 0.0, 2),
            "transition_seen": self._transition_seen,
            "duty_segments": len(self._history),
            "best_residual": float(ordered[0]),
            "runner_up_residual": float(ordered[1]),
            "candidates_passing": int(gate_mask(params, rse_K, rse_tau).sum()),
            "confirming": None if self._confirm is None else self._confirm["n"],
            "trusted": self.trusted_model(),
        }
