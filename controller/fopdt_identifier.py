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

    Theta is (N, M), P is (N, M, M), resid_ew is (N,), where M is the number of
    regressors. Only the delayed-duty column differs per candidate; the rest is
    shared and broadcast by the caller.

    M is a parameter because the same bank serves two model forms. The
    first-order fit carries [1, T_scaled, u]; the integrating fit drops the
    temperature column, which is the one whose coefficient is near zero on a
    slow chamber and whose inverse the first-order time constant is.
    """

    def __init__(self, n_candidates, n_params=3):
        self._n = int(n_candidates)
        self._m = int(n_params)
        self.Theta = np.zeros((self._n, self._m))
        self.P = np.tile(P0 * np.eye(self._m), (self._n, 1, 1))
        self.resid_ew = np.zeros(self._n)

    def update(self, phi, y):
        """One accepted observation into the whole bank. `phi` is (N, M)."""
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
        self.P[mask] = P0 * np.eye(self._m)
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


#: Physical bounds for the integrating form. `K_i` is the rate the chamber
#: climbs at full duty, in F per second per unit duty: 0.05 spans a grill that
#: takes an hour to gain 180 F, 5.0 one that gains 18000 F in the same hour, so
#: anything outside is not a grill. Fitted values seen on the two plants sit at
#: 0.44 and 2.19.
GAIN_RATE_MIN, GAIN_RATE_MAX = 0.05, 5.0  # F per second per unit duty
RSE_GAIN_RATE_MAX = 0.20


def recover_integrating_parameters(Theta):
    """Physical parameters for the integrating form, dT/dt = K_i*u(t-theta) + c0.

    Nothing has to be undone here: the regressand is already a rate and the duty
    column is unscaled, so the fitted coefficient IS the gain in F per second per
    unit duty, and the intercept IS the loss rate at the operating point. That
    directness is the point of this form -- the first-order fit reports its time
    constant as the reciprocal of a coefficient whose true value is near zero on
    a slow chamber, so noise across zero inverts its sign and drags the gain with
    it.
    """
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    return {"K_i": Theta[:, 1], "c0": Theta[:, 0]}


def integrating_relative_standard_errors(Theta, P, resid_ew):
    """Relative standard error of K_i. It is a single fitted coefficient rather
    than a ratio of two, so no covariance term arises."""
    Theta = np.atleast_2d(np.asarray(Theta, dtype=float))
    P = np.asarray(P, dtype=float)
    resid_ew = np.asarray(resid_ew, dtype=float)
    K_i = Theta[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.sqrt(resid_ew * P[:, 1, 1]) / np.abs(K_i)


def integrating_gate_mask(params, rse_K_i):
    """Candidates whose integrating estimate is finite, physical and certain."""
    K_i = np.asarray(params["K_i"])
    c0 = np.asarray(params["c0"])
    rse = np.asarray(rse_K_i)
    with np.errstate(invalid="ignore"):
        mask = np.isfinite(K_i) & np.isfinite(c0) & np.isfinite(rse)
        mask &= (K_i >= GAIN_RATE_MIN) & (K_i <= GAIN_RATE_MAX)
        # A chamber loses heat to ambient, so the term that is not explained by
        # duty cannot be a gain. A positive one means the fit has attributed
        # heating to something other than the auger.
        mask &= c0 <= 0.0
        mask &= rse <= RSE_GAIN_RATE_MAX
    return mask


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


#: Initial trust can begin after 25 accepted observations spanning 500 s.
#: At PID-SP's fixed 20 s cadence, the subsequent 20-sample confirmation
#: window makes 900 s the earliest activation. Every evidence-dependent gate
#: below remains authoritative, so a slow or unexcited grill keeps learning.
MIN_ACCEPTED_SECONDS = 500.0
MIN_ACCEPTED = 25
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


#: The two model forms the identifier can promote, and which of each form's
#: parameters blend on adoption, with the fraction of change that counts as
#: material. `theta` is handled separately in every case -- it moves outright.
FORM_FOPDT = "fopdt"
FORM_IPDT = "ipdt"
FORM_PARAMS = {
    FORM_FOPDT: (("K", MATERIAL_K), ("tau", MATERIAL_TAU)),
    FORM_IPDT: (("K_i", MATERIAL_K), ("c0", MATERIAL_TAU)),
}
CONFIRM_TOL = {"K": CONFIRM_K_TOL, "tau": CONFIRM_TAU_TOL, "K_i": CONFIRM_K_TOL, "c0": CONFIRM_TAU_TOL}
#: The parameters a persisted record of each form must carry, and the range each
#: must land in to be worth restoring. The model store keeps bytes and judges
#: nothing, so every bound the live gates apply is re-applied here. A rising
#: chamber that coasts upward with the auger off is not a chamber, hence c0 <= 0.
RESTORE_BOUNDS = {
    FORM_FOPDT: (("K", GAIN_MIN, GAIN_MAX), ("tau", TAU_MIN, TAU_MAX)),
    FORM_IPDT: (("K_i", GAIN_RATE_MIN, GAIN_RATE_MAX), ("c0", -np.inf, 0.0)),
}
#: How much of a passing revision blends into the trusted values.
BLEND = 0.1
#: Still air around the grill. The hold duty scales with the chamber's rise above
#: it, so carrying an operating point to another set point needs a floor to
#: measure the rise FROM; a few degrees either way moves the ratio very little.
AMBIENT_F = 70.0
#: Below this rise, the ratio's denominator is small enough that its own error
#: dominates, and the identified duty is used unscaled instead.
MIN_RISE_F = 20.0
#: A dt outside this band is a clock jump or a stalled loop, not an observation.
DT_MIN, DT_MAX = 1.0, 600.0

#: A model that no longer describes the plant degrades control without
#: spiking _safe's one-step residual envelope (a wrong-gain error is modest
#: and persistent, not a spike), so it is caught here instead: the RLS bank
#: keeps fitting every delay candidate against the raw measurement regardless
#: of what is trusted, and the trusted candidate's residual is compared
#: against the best one on every accepted observation. Measured on GrillSim
#: (closed loop, pid_sp + FOPDTIdentifier, a model transplanted from a
#: different operating point onto a plant already at temperature): a
#: correctly-trusted model's ratio never exceeded ~7.6 across 42 seeded 4-6
#: hour runs, while a transplanted model sustains it above 10 within minutes.
#: 8.0 sits with margin above the observed correct-model ceiling; requiring it
#: sustained for DISTRUST_WINDOW straight observations (not a single sample)
#: adds a further margin against a one-tick noise spike.
DISTRUST_RATIO = 8.0
DISTRUST_WINDOW = 20


class FOPDTIdentifier:
    """Passive online identification of the grill's FOPDT parameters.

    Nothing here perturbs the auger: the identifier learns from whatever
    excitation the controller's own regulation happens to produce, and stays
    untrusted until the gates say the data earned it.
    """

    def __init__(self):
        self._bank = RLSBank(N_CANDIDATES)
        # The integrating fit runs on the same observations, without the
        # temperature regressor. It is a second bank rather than a slice of the
        # first because a coefficient estimated alongside an ill-conditioned one
        # inherits its noise -- separating them is the whole point.
        self._ibank = RLSBank(N_CANDIDATES, n_params=2)
        self._history = DutyHistory(float(DELAYS.max()))
        self._prev = None  # (timestamp, temperature) anchor
        self._gap = True  # the next observation would span an undriven interval
        self._commanded = True  # whether the most recent report was controller-driven
        self._accepted = 0
        self._accepted_seconds = 0.0
        self._temp_lo = None
        self._temp_hi = None
        self._identified_at_f = None
        self._duty_n = 0
        self._duty_sum = 0.0
        self._duty_sq = 0.0
        self._transition_seen = False
        self._transition_from = None
        self._transition_at = None
        self._trusted = None
        self._revision = 0
        self._confirm = None
        # A model just restored from a previous cook has not yet been
        # confirmed against THIS plant, so the materiality gate leaves it
        # alone until an adoption earns that protection (see _evaluate).
        self._restored = False
        self._distrust_confirm = 0
        self._distrust_count = 0

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
        rate = (temp - y0) / dt
        self._bank.update(phi, rate)
        iphi = np.empty((N_CANDIDATES, 2))
        iphi[:, 0] = shared[0]
        iphi[:, 1] = duty
        self._ibank.update(iphi, rate)

        self._accepted += 1
        self._accepted_seconds += dt
        self._temp_lo = temp if self._temp_lo is None else min(self._temp_lo, temp)
        self._temp_hi = temp if self._temp_hi is None else max(self._temp_hi, temp)
        mean_duty = float(duty[valid].mean())
        self._duty_n += 1
        self._duty_sum += mean_duty
        self._duty_sq += mean_duty * mean_duty
        self._check_distrust()
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

    def _trusted_index(self):
        return int(np.argmin(np.abs(DELAYS - self._trusted["theta"])))

    def _distrust_ratio(self):
        """The trusted delay's residual relative to the best candidate's, or
        None while untrusted. Unconditional on `_excited()`: this is exactly
        the regime -- degraded control suppressing the excitation promote()
        needs -- the distrust check exists to catch."""
        if self._trusted is None:
            return None
        resid = self._bank.resid_ew
        best = float(np.min(resid))
        if best <= 0.0:
            return None
        return float(resid[self._trusted_index()]) / best

    def _check_distrust(self):
        """Drop trust once the trusted delay's residual has run materially
        worse than the best candidate's for DISTRUST_WINDOW straight
        observations. Not sticky: clearing `_trusted` here is the only
        effect: the identifier is simply untrusted again and re-promotes
        through the normal machinery once the evidence supports it."""
        ratio = self._distrust_ratio()
        if ratio is None:
            self._distrust_confirm = 0
            return
        if ratio > DISTRUST_RATIO:
            self._distrust_confirm += 1
        else:
            self._distrust_confirm = 0
        if self._distrust_confirm >= DISTRUST_WINDOW:
            self._distrust_count += 1
            self._distrust_confirm = 0
            self._trusted = None
            self._confirm = None

    def _evaluate(self):
        if not self._excited():
            return
        params = recover_parameters(self._bank.Theta)
        rse_K, rse_tau = relative_standard_errors(self._bank.Theta, self._bank.P, self._bank.resid_ew)
        mask = gate_mask(params, rse_K, rse_tau)
        winner, _ = promote(self._bank.resid_ew, mask)
        if winner is not None:
            candidate = {
                "form": FORM_FOPDT,
                "K": float(params["K"][winner]),
                "tau": float(params["tau"][winner]),
                "theta": float(DELAYS[winner]),
            }
        else:
            # A chamber slow relative to the observation window looks like an
            # integrator over it, and a first-order fit of one returns a negative
            # time constant and a negative gain together -- confidently
            # impossible, so the gate above rejects every candidate and the
            # controller learns nothing. The integrating form is what that
            # chamber actually is.
            iparams = recover_integrating_parameters(self._ibank.Theta)
            irse = integrating_relative_standard_errors(self._ibank.Theta, self._ibank.P, self._ibank.resid_ew)
            imask = integrating_gate_mask(iparams, irse)
            iwinner, _ = promote(self._ibank.resid_ew, imask)
            if iwinner is None:
                # One evaluation with no gated winner is a noisy sample, not a
                # verdict on the samples already agreeing: discarding the window
                # here made a 19-deep confirmation die one short and start over,
                # so whether a chamber was ever identified came down to the
                # noise draw. Pause instead, as a loss of excitation already
                # does above -- the parameter agreement `_confirmed` demands is
                # what keeps a window honest across the gap.
                return
            candidate = {
                "form": FORM_IPDT,
                "K_i": float(iparams["K_i"][iwinner]),
                "c0": float(iparams["c0"][iwinner]),
                "theta": float(DELAYS[iwinner]),
            }
        # A restored model has not yet been confirmed against this cook's
        # plant, so it has not earned the churn protection the materiality
        # gate gives a model this cook already confirmed -- any candidate
        # that survives confirmation below replaces it outright.
        if self._trusted is not None and not self._restored and not self._material(candidate):
            self._confirm = None
            return
        if not self._confirmed(candidate):
            return
        self._adopt(candidate)

    @staticmethod
    def _continuous_params(candidate):
        """The candidate's parameters that blend, by model form.

        `theta` is excluded everywhere it appears below: a delay moves outright
        once confirmed, because a delay half way between two candidates is not a
        delay any candidate had.
        """
        return FORM_PARAMS[candidate.get("form", FORM_FOPDT)]

    def _material(self, candidate):
        if candidate.get("form", FORM_FOPDT) != self._trusted.get("form", FORM_FOPDT):
            # A different model form is not a revision of the trusted one, so
            # there is no small-change threshold that could hold it back.
            return True
        if abs(candidate["theta"] - self._trusted["theta"]) >= MATERIAL_THETA:
            return True
        return any(
            abs(candidate[name] - self._trusted[name]) / abs(self._trusted[name]) >= tol
            for name, tol in self._continuous_params(candidate)
        )

    def _confirmed(self, candidate):
        """A candidate must hold still for a full window before it is believed."""
        window = self._confirm
        if (
            window is None
            or window["theta"] != candidate["theta"]
            or window.get("form", FORM_FOPDT) != candidate.get("form", FORM_FOPDT)
        ):
            self._confirm = {"n": 1, **candidate}
            return False
        for name, _material_tol in self._continuous_params(candidate):
            tol = CONFIRM_TOL[name]
            if abs(candidate[name] - window[name]) / abs(window[name]) > tol:
                self._confirm = {"n": 1, **candidate}
                return False
        window["n"] += 1
        for name, _ in self._continuous_params(candidate):
            window[name] = candidate[name]
        return window["n"] >= CONFIRM_WINDOW

    def _adopt(self, candidate):
        self._confirm = None
        if self._trusted is None or self._trusted.get("form", FORM_FOPDT) != candidate.get("form", FORM_FOPDT):
            # Nothing to blend against across a change of form: the parameters
            # do not even mean the same thing.
            self._trusted = dict(candidate)
        else:
            # Delay moves outright once confirmed; the continuous parameters
            # blend, so one noisy window cannot swing the model.
            blended = {"form": candidate.get("form", FORM_FOPDT), "theta": candidate["theta"]}
            for name, _ in self._continuous_params(candidate):
                blended[name] = (1.0 - BLEND) * self._trusted[name] + BLEND * candidate[name]
            self._trusted = blended
        self._revision += 1
        # The chamber temperature this fit describes. c0 absorbs the heat loss at
        # it, so the hold duty the model implies is a statement about THIS
        # temperature and needs rescaling to speak about another.
        self._identified_at_f = None if self._prev is None else float(self._prev[1])
        # This adoption is evidence from the current cook's own plant, so the
        # model has now earned the churn protection a restored one lacks.
        self._restored = False

    #: Accepted observations before an integrating gain is worth acting on. Far
    #: below what promotion needs, because promotion is about telling one dead
    #: time from another and this is not: the gain and the loss rate come from
    #: the duty/rate relationship alone, which is well conditioned long before
    #: any delay is distinguishable.
    MIN_HOLD_DUTY_SAMPLES = 60

    def hold_duty(self, u_max=1.0, target_f=None):
        """The duty that holds `target_f`, or the identified operating point.

        The integrating fit says the chamber's rate is `K_i*u + c0`, so it holds
        still at `u = -c0/K_i`. That is the operating point a controller would
        otherwise have to discover with its integral, and it needs no dead time
        to compute -- every delay candidate estimates the same gain, differing
        only in which duty history it attributes it to.

        That duty describes ONE temperature, because c0 is the chamber's heat
        loss at the temperature the fit was taken at. Loss is very nearly
        proportional to the rise above ambient, so the duty that holds another
        temperature scales with the ratio of rises. Without this, a model earned
        at 450 F puts a 450 F duty under a 225 F cook: measured at under 1% of
        the cook within 5 F, against 92% at the setpoint it was learned at.

        A trusted integrating model answers this outright, and is preferred: it
        has already passed the physics gate and a full confirmation window, and
        on a restored model it is the ONLY answer available, because the bank
        below starts every cook empty and cannot repeat last cook's finding
        until it has re-earned it well into the climb.

        Falling back to the bank, the median across candidates that pass the
        physics gate rather than the lowest-residual one, because with the delay
        undetermined no single candidate is the right one to trust.
        """
        trusted = self._trusted
        if trusted is not None and trusted.get("form", FORM_FOPDT) == FORM_IPDT and trusted["K_i"]:
            return self._holdable(self._at_target(-trusted["c0"] / trusted["K_i"], target_f), u_max)
        if self._accepted < self.MIN_HOLD_DUTY_SAMPLES or not self._duty_std() >= MIN_DUTY_STD:
            return None
        params = recover_integrating_parameters(self._ibank.Theta)
        rse = integrating_relative_standard_errors(self._ibank.Theta, self._ibank.P, self._ibank.resid_ew)
        mask = integrating_gate_mask(params, rse)
        if not mask.any():
            return None
        with np.errstate(divide="ignore", invalid="ignore"):
            held = -np.asarray(params["c0"])[mask] / np.asarray(params["K_i"])[mask]
        held = held[np.isfinite(held)]
        if held.size == 0:
            return None
        # The bank is this cook's own data, so it already describes wherever the
        # chamber has been sitting; only a carried model needs moving.
        return self._holdable(float(np.median(held)), u_max)

    def _at_target(self, held, target_f):
        """Move a hold duty from the temperature it describes to another."""
        identified_at = self._identified_at_f
        if target_f is None or identified_at is None:
            return held
        rise = float(identified_at) - AMBIENT_F
        if rise < MIN_RISE_F:
            return held
        return held * (float(target_f) - AMBIENT_F) / rise

    def retarget(self, target_f):
        """Move a trusted integrating model to a different operating point.

        `K_i*u + c0` is a linearisation about one chamber temperature: K_i is the
        firepot's heating rate and barely moves, but c0 is the loss at that
        temperature and is proportional to the rise above ambient. Carrying a
        450 F model into a 225 F cook unchanged asserts 450 F losses at 225 F,
        and the loop then holds the chamber 6 F above target -- correct duty,
        wrong temperature, because the predictor it feeds is biased.

        Only a restored model is moved. A model this cook confirmed against its
        own plant already describes where the chamber has been.
        """
        trusted = self._trusted
        if (
            trusted is None
            or not self._restored
            or trusted.get("form", FORM_FOPDT) != FORM_IPDT
            or self._identified_at_f is None
            or target_f is None
        ):
            return False
        rise = self._identified_at_f - AMBIENT_F
        target_rise = float(target_f) - AMBIENT_F
        if rise < MIN_RISE_F or target_rise < MIN_RISE_F:
            return False
        trusted["c0"] = trusted["c0"] * target_rise / rise
        self._identified_at_f = float(target_f)
        return True

    @staticmethod
    def _holdable(value, u_max):
        """A hold duty outside the actuator's own range is not a statement about
        this grill, whatever the fit says."""
        value = float(value)
        if not np.isfinite(value):
            return None
        return value if 0.0 < value <= u_max else None

    @staticmethod
    def _holdable(value, u_max):
        """A hold duty outside the actuator's own range is not a statement about
        this grill, whatever the fit says."""
        value = float(value)
        if not np.isfinite(value):
            return None
        return value if 0.0 < value <= u_max else None

    def trusted_model(self):
        if self._trusted is None:
            return None
        model = {**self._trusted, "revision": self._revision}
        # Only when known, so a model that never named its operating point does
        # not start claiming one of None across the store.
        if self._identified_at_f is not None:
            model["identified_at_f"] = self._identified_at_f
        return model

    def restore(self, model):
        """Adopt a persisted model, re-checking the physics the store does not
        judge. A restored model is trusted immediately: a process restart is not
        a reason to doubt parameters that were earned."""
        if not isinstance(model, dict):
            return False
        # A record written before the identifier could promote an integrating
        # chamber names no form, and every such record is a first-order fit.
        form = model.get("form", FORM_FOPDT)
        bounds = RESTORE_BOUNDS.get(form)
        if bounds is None:
            return False
        try:
            values = {name: float(model[name]) for name, _lo, _hi in bounds}
            theta = float(model["theta"])
            revision = int(model["revision"])
        except KeyError, TypeError, ValueError:
            return False
        if not all(np.isfinite([*values.values(), theta])) or revision < 0:
            return False
        if any(not lo <= values[name] <= hi for name, lo, hi in bounds):
            return False
        if theta < float(DELAYS.min()) or theta > float(DELAYS.max()):
            return False
        self._trusted = {"form": form, "theta": theta, **values}
        # The operating point this fit describes. Records predating the field
        # named it only as provenance, and that is the same temperature. Kept
        # only when it is a temperature a chamber could have been holding: the
        # provenance is written unconditionally and reads 0 on a controller that
        # never had a target.
        identified_at = model.get("identified_at_f", model.get("setpoint_f"))
        self._identified_at_f = (
            float(identified_at)
            if identified_at is not None and float(identified_at) > AMBIENT_F + MIN_RISE_F
            else None
        )
        self._revision = revision
        # A confirmation window built against the pre-restore trusted state must
        # not count toward confirming a candidate against this one.
        self._confirm = None
        # Not yet confirmed against this cook's plant -- see _evaluate.
        self._restored = True
        self._distrust_confirm = 0
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
            "distrust_count": self._distrust_count,
            "distrust_ratio": self._distrust_ratio(),
        }
