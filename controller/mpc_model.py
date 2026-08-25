#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Grey-box Thermal Model + Estimator
*****************************************

 One lumped thermal mass (the chamber T_c) driven by a scalar firing-rate Q,
 plus an integrating disturbance state d for offset-free tracking. Optionally
 an input transport delay (the feed -> combustion -> sensor deadtime) is
 modeled as a chain of n_delay first-order lag states (an Erlang /
 distributed-delay approximation of mean duration theta), which lets the MPC
 predict across the deadtime instead of over-correcting.

 State order: [q0 .. q_{n_delay-1}, T_c, d].

 Provides the grey-box plant simulation and Kalman estimators used by the
 generated acados controller.

 -----------------------------------------------------------------------------
 Continuous-time dynamics (temperatures in degrees C, time in seconds)

     dT_c/dt = (K_Q * heat_in - h_amb * (T_c - T_amb)
                - sigma * ((T_c+273.15)^4 - (T_amb+273.15)^4) + d) / C_c
     dd/dt   = 0                                 (integrating disturbance)

 where heat_in = Q when n_delay == 0, else the tail of the transport-lag chain.

 -----------------------------------------------------------------------------
 WHY ONE LUMP AND NOT TWO

 This model carried a second lump -- a firepot T_f fed heat and coupled to the
 chamber through a conductance h_fc -- until a parameter-parsimony study
 measured what it earned. Fitted jointly across seven scenarios on each of the
 two plants in controller/grill_sim.py, dropping it costs 0.16 C on the MAK
 plant and 0.07 C on the generic plant, the latter inside that plant's own
 0.60 C seed-to-seed noise floor; on the real 247-row MAK cook in
 tests/unit/mpc/fixtures it costs 0.04 C. The eighth scenario, the lid cook, is
 excluded from those numbers because no structure here can explain it -- none
 has a lid input -- so including it would price a shortcoming both structures
 share.

 What it removes is two parameters neither plant can determine: h_fc's profile
 is flat from 0.33x to 10x its fitted value on every record measured, and C_f's
 from 0.1x to 10x. A good synthetic cook resolves four parameter directions and
 the real cook resolves three, so a nine-parameter model was carrying more
 structure than any log could speak about.

 The two structures the same study found load-bearing are both still here: the
 transport delay (removing it costs 1.7 C on the MAK plant) and the radiative
 loss term (3.3 C on MAK, 18 C on the generic plant, whose chamber reaches
 370 C).

 At an unchanged n_delay the reduction would cost the quantities that actually
 decide overshoot: the firepot's own ~7 s stage leaves the cascade, so against
 the MAK plant the dead time the fitted model predicts falls 67 -> 63 s and the
 coast 21.3 -> 21.1 C. Both were already under-predicted by far more than that
 -- the plant's own dead time is 112 s -- because the Erlang chain at
 n_delay=4 smears the delay rather than transporting it.

 n_delay is a structure constant and costs no parameters, so it pays for both:
 at the shipped n_delay=8 the single lump predicts 79 s and 22.5 C, ahead of
 the two-lump model's 67 s and 21.3 C rather than behind it. See
 controller/mpc_config.py's DEFAULT_MPC_CONFIG for why 8 and not more.

 -----------------------------------------------------------------------------
 Physical / model parameters (shared by the builder and all three estimators)

   C_c     Chamber thermal capacitance. The big lumped mass the meat sees;
           sets how sluggishly chamber temperature responds.  (~O(300))
   h_amb   Chamber->ambient conductance. The (linear) heat-loss term to the
           outside world -- larger h_amb means more heat leaks out per degree
           of chamber-over-ambient, so the grill needs more fuel to hold temp.
           This is the dominant driver of steady-state fuel demand.  (W per degree C)
   T_amb   Ambient (outside-air) temperature in degrees C. Sets the loss
           reference: chamber loses heat proportional to (T_c - T_amb), so a
           cold day raises fuel demand. Also seeds the estimator's initial
           T_c guess.
   sigma   Radiative-loss coefficient (Stefan-Boltzmann-like) on the chamber.
           Adds a (T_c^4 - T_amb^4) loss that only matters at high temp, where
           it captures the extra fuel needed at searing temps that a purely
           linear h_amb underpredicts. sigma == 0 -> purely linear model (and
           GreyBoxEKF then reduces exactly to GreyBoxKF).
   K_Q     Firing-rate heat gain: maps the abstract scalar firing rate Q into
           actual heat into the chamber, calibrated to this grill's power. K_Q
           sets the steady gain jointly with h_amb (K_Q/h_amb is the degrees of
           rise per unit of firing rate), so a fit holds h_amb and tunes K_Q
           (see update_mpc.py).
   theta   Mean input transport deadtime in seconds (feed -> combustion ->
           sensor). Only used when n_delay > 0.
   n_delay Number of first-order lag states approximating that deadtime as an
           Erlang chain (each lag has time constant theta / n_delay). 0 disables
           deadtime modeling; larger n_delay -> sharper (more plug-flow) delay.

 Estimator (Kalman / EKF) tuning parameters

   t_step  Real interval in seconds between update() calls (the control period).
           The discretization is matched to this cadence, so changing the
           control rate keeps the estimator consistent.
   q_temp  Process-noise variance on the temperature/lag states. Higher ->
           trust the model less, track the measurement faster (noisier).
   q_dist  Process-noise variance on the disturbance state d. Deliberately
           small: a fast d chases unmeasured noise; a slow d gives clean
           offset-free bias correction.
   r_meas  Measurement-noise variance on the chamber-temp sensor. Higher ->
           smooth harder, trust each reading less.
   x0      Optional initial state vector; defaults to [0..0, T_amb, 0].


*****************************************
"""

import numpy as np
from scipy.linalg import expm
# Runtime estimators depend only on NumPy/SciPy. Native solver generation owns
# CasADi and acados-template in the isolated codegen dependency group.


_KELVIN = 273.15

#: Which model STRUCTURE this module implements -- what the state vector is and
#: what the parameters mean. Bumped whenever either changes, which is not the
#: same event as a parameter being recalibrated.
#:
#: 3  migration input only: variable-delay grey snapshot with retired nested learners
#: 4  fixed eight-delay grey-only adaptation record
#:
#: Version 3 is interpreted only by the one-shot startup migration.  Runtime
#: restore and every current writer accept/emit version 4 exclusively.
MODEL_SCHEMA = 4


def _rad_loss(T_c, T_amb, sigma):
    # Radiative chamber loss (Stefan-Boltzmann-like). sigma=0 -> purely linear.
    return sigma * ((T_c + _KELVIN) ** 4 - (T_amb + _KELVIN) ** 4)


def _normalized_load(value):
    load = float(value)
    if not np.isfinite(load) or not 0.0 <= load <= 1.0:
        raise ValueError("normalized combustion load must be finite and within [0, 1]")
    return load


def _thermal_parameters(params):
    """Return the finite physical coefficients shared by steady-state helpers."""
    if not isinstance(params, dict):
        raise ValueError("a thermal model parameter mapping is required")
    try:
        h_amb = float(params["h_amb"])
        t_amb = float(params["T_amb"])
        k_q = float(params["K_Q"])
        sigma = float(params.get("sigma", 0.0))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("thermal model is missing required steady-state parameters") from error
    if not all(np.isfinite(value) for value in (h_amb, t_amb, k_q, sigma)):
        raise ValueError("thermal model steady-state parameters must be finite")
    if h_amb < 0.0 or k_q <= 0.0 or sigma < 0.0 or (h_amb == 0.0 and sigma == 0.0):
        raise ValueError("thermal model steady-state parameters must be physical")
    return h_amb, t_amb, k_q, sigma


def steady_combustion_load(params, setpoint, disturbance=0.0):
    """Return the unclipped normalized load that holds ``setpoint`` steady."""
    h_amb, t_amb, k_q, sigma = _thermal_parameters(params)
    try:
        t_set = float(setpoint)
        d = float(disturbance)
    except (TypeError, ValueError) as error:
        raise ValueError("steady-state target and disturbance must be finite") from error
    if not np.isfinite(t_set) or not np.isfinite(d):
        raise ValueError("steady-state target and disturbance must be finite")
    return (h_amb * (t_set - t_amb) + _rad_loss(t_set, t_amb, sigma) - d) / k_q


def steady_temperature(params, combustion_load, disturbance=0):
    """Invert :func:`steady_combustion_load` on the same physical model."""
    h_amb, t_amb, k_q, sigma = _thermal_parameters(params)
    try:
        load = float(combustion_load)
        d = float(disturbance)
    except (TypeError, ValueError) as error:
        raise ValueError("steady-state load and disturbance must be finite") from error
    if not np.isfinite(load) or not np.isfinite(d):
        raise ValueError("steady-state load and disturbance must be finite")
    heat_required = k_q * load + d
    if sigma == 0.0:
        return t_amb + heat_required / h_amb

    lower = -_KELVIN + 1e-9

    def residual(t_c):
        return h_amb * (t_c - t_amb) + _rad_loss(t_c, t_amb, sigma) - heat_required

    if residual(lower) > 0.0:
        raise ValueError("steady-state load has no physical temperature inverse")
    upper = max(t_amb, lower + 1.0)
    while residual(upper) < 0.0:
        upper = lower + 2.0 * (upper - lower)
        if upper > 1e9:
            raise ValueError("steady-state load has no finite temperature inverse")
    for _ in range(80):
        midpoint = 0.5 * (lower + upper)
        if residual(midpoint) < 0.0:
            lower = midpoint
        else:
            upper = midpoint
    return 0.5 * (lower + upper)


def _erlang_coefficients(n, a):
    """exp(-a) * a**m / m! for m = 0 .. n-1, evaluated over a vector of a.

    These are the entries of exp(A*dt) for the n-stage Erlang chain, which is a
    lower-triangular Toeplitz matrix in them. Built by the recurrence
    c_m = c_{m-1} * a / m rather than from a**m directly, so that a sub-step
    long against the stage length (large a, meaning the chain has simply
    finished responding) underflows to zero instead of overflowing a**m on the
    way to an exp that would have divided it back down.
    """
    coef = np.empty((np.size(a), n))
    coef[:, 0] = np.exp(-a)
    for m in range(1, n):
        coef[:, m] = coef[:, m - 1] * a / m
    return coef


def simulate_grey_box(
    t,
    combustion_load,
    *,
    C_c,
    h_amb,
    T_amb,
    T0,
    K_Q=1.0,
    sigma=0.0,
    theta=0.0,
    n_delay=0,
    max_dt=0.125,
):
    """Forward-simulate chamber temperature for the plant the MPC plans against.

    The single executable statement of the dynamics documented at the top of
    this module, including the radiative loss and the Erlang transport-delay
    chain. The offline calibration utility fits through this function so the
    parameters it produces describe the model that consumes them.

    `out[i]` is the chamber temperature AT `t[i]`, so `out[0] == T0`; each step
    advances the state from `t[i]` to `t[i+1]` under normalized
    `combustion_load[i]`. The disturbance state `d` is absent: it exists to
    time, and fitting against it would let it absorb the very mismatch a
    calibration exists to remove.

    THE DELAY CHAIN IS ADVANCED EXACTLY, NOT INTEGRATED. It is linear and its
    input is constant across a sample interval, so its state at every sub-step
    is available in closed form (`_erlang_coefficients`). Integrating it with
    explicit Euler instead -- as this function used to -- costs two things this
    model cannot afford. It is stable only for a sub-step below 2*theta/n_delay,
    and `theta` is a FITTED parameter with no lower bound worth the name, so a
    grill with a short feed path could overflow the residual the calibration is
    solving against; and it under-delays each stage, which the solve then
    compensates for by inflating `theta` by about n_delay * sub-step. The
    estimators below and the do-mpc NLP both discretize this same continuous
    model exactly, so that inflation was a disagreement between what the fit
    measured and what the controller then planned against.

    `max_dt` is what remains: the sub-step for the chamber's own explicit Euler
    step. With the chain exact, the resulting error is first order in the
    sub-step and INDEPENDENT of theta and n_delay -- measured at 0.099 to 0.114 C
    RMS per second of sub-step on the real MAK cook in tests/unit/mpc/fixtures,
    uniformly to within 5% across theta from 3 s to 200 s and n_delay from 4 to
    20. The coefficient is 0.114 at the default below and 0.099 at the coarsest
    step measured, so the default is the expensive end of that range rather than
    the flattering one. The default holds the numerical error to 0.014 C RMS,
    a ninth of the 0.16 C of model fidelity the single-lump structure was
    adopted at, so that what a fit reports is the grill rather than the
    integrator. The measurement is
    docs/superpowers/experiments/substep_convergence.py.
    """
    t = np.asarray(t, dtype=float)
    combustion_load = np.asarray(combustion_load, dtype=float)
    if not np.all(np.isfinite(combustion_load)):
        raise ValueError("combustion load samples must be finite")
    n = max(int(n_delay), 0)
    lag_tau = (float(theta) / n) if (n > 0 and theta > 0.0) else 0.0
    lags = np.zeros(n)
    T_c = float(T0)
    out = np.empty_like(t)
    for i in range(len(t)):
        out[i] = T_c
        if i == len(t) - 1:
            break
        span = float(t[i + 1] - t[i])
        if span <= 0.0:
            continue
        steps = max(1, int(np.ceil(span / max_dt)))
        dt = span / steps
        load = float(combustion_load[i])
        if lag_tau > 0.0:
            # lags(k*dt) = load + exp(A*k*dt) @ (lags(0) - load), exactly, for
            # every sub-step k at once.
            dev = lags - load
            coef = _erlang_coefficients(n, np.arange(1, steps + 1) * (dt / lag_tau))
            heat = (load + coef @ dev[::-1]).tolist()
            lags = load + np.convolve(coef[-1], dev)[:n]
        else:
            heat = [load] * steps
        for k in range(steps):
            dT_c = (K_Q * heat[k] - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c
            T_c += dt * dT_c
    return out


class GreyBoxKF:
    """
    Kalman filter over the augmented linear model
    x = [q0..q_{n_delay-1}, T_c, d], input Q. The constant ambient term
    enters as an affine input (held at 1). `t_step` is the real interval between
    update() calls (i.e. the control period) - the discretization matches the
    cadence so faster control re-solves are estimated correctly.

    See the module docstring for the meaning of every constructor parameter
    (C_c, h_amb, T_amb, ... and the q_temp/q_dist/r_meas tuning).
    """

    def __init__(self, *, C_c, h_amb, T_amb, t_step, q_temp, q_dist, r_meas, theta=0.0, n_delay=0, K_Q=1.0, x0=None):
        n = n_delay + 2
        iTc, iD = n_delay, n_delay + 1

        A = np.zeros((n, n))
        if n_delay > 0:
            tau_d = theta / n_delay
            for i in range(n_delay):
                A[i, i] = -1.0 / tau_d
                if i > 0:
                    A[i, i - 1] = 1.0 / tau_d
            A[iTc, n_delay - 1] = K_Q / C_c  # last lag feeds the chamber (scaled by K_Q)
        A[iTc, iTc] = -h_amb / C_c
        A[iTc, iD] = 1.0 / C_c

        # columns: [Q input, affine constant=1]
        Baug = np.zeros((n, 2))
        if n_delay > 0:
            Baug[0, 0] = 1.0 / (theta / n_delay)  # Q enters the first transport lag
        else:
            Baug[iTc, 0] = K_Q / C_c  # no deadtime: Q enters the chamber (scaled by K_Q)
        Baug[iTc, 1] = h_amb * T_amb / C_c

        Mblk = np.zeros((n + 2, n + 2))
        Mblk[:n, :n] = A
        Mblk[:n, n:] = Baug
        Md = expm(Mblk * t_step)
        self.Ad = Md[:n, :n]
        self.Bd = Md[:n, n : n + 1]  # for Q
        self.bd = Md[:n, n + 1 : n + 2]  # affine (constant input = 1)
        self.H = np.zeros((1, n))
        self.H[0, iTc] = 1.0
        self.Qkf = np.diag([q_temp] * (n_delay + 1) + [q_dist])
        self.Rkf = np.array([[r_meas]])
        if x0 is None:
            x0 = [0.0] * n_delay + [T_amb, 0.0]
        self.x = np.array(x0, dtype=float)
        self.P = np.eye(n) * 5.0
        self.n = n

    def update(self, normalized_combustion_load, y_measured):
        load = _normalized_load(normalized_combustion_load)
        # predict
        self.x = self.Ad @ self.x + self.Bd.flatten() * load + self.bd.flatten()
        self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
        # update
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
        self.P = (np.eye(self.n) - K @ self.H) @ self.P
        return self.x


class GreyBoxEKF:
    """
    Extended Kalman filter over the augmented model with the nonlinear radiative
    chamber loss. The only nonlinearity is the Stefan-Boltzmann term on T_c, so
    each step we linearize it about the current T_c estimate (slope
    4*sigma*(T_c+273.15)^3) and fold the linearization offset into the affine
    input -- this reproduces the nonlinear loss exactly at the operating point
    and to first order nearby, while keeping the exact expm propagation for the
    stiff linear part. Reduces EXACTLY to GreyBoxKF when sigma=0. Nonlinear-capable
    like the MHE but ~us/step (one small expm) instead of an NLP solve. Same
    integrating-disturbance state d gives offset-free tracking, and the same
    update(Q_applied, y) interface as GreyBoxKF / GreyBoxMHE.

    See the module docstring for the meaning of every constructor parameter
    (the physical C_c/h_amb/T_amb/sigma set plus q_temp/q_dist/r_meas).
    """

    def __init__(
        self,
        *,
        C_c,
        h_amb,
        T_amb,
        t_step,
        q_temp,
        q_dist,
        r_meas,
        theta=0.0,
        n_delay=0,
        K_Q=1.0,
        sigma=0.0,
        x0=None,
    ):
        n = n_delay + 2
        iTc, iD = n_delay, n_delay + 1

        A = np.zeros((n, n))
        if n_delay > 0:
            tau_d = theta / n_delay
            for i in range(n_delay):
                A[i, i] = -1.0 / tau_d
                if i > 0:
                    A[i, i - 1] = 1.0 / tau_d
            A[iTc, n_delay - 1] = K_Q / C_c
        A[iTc, iTc] = -h_amb / C_c
        A[iTc, iD] = 1.0 / C_c

        Baug = np.zeros((n, 2))
        if n_delay > 0:
            Baug[0, 0] = 1.0 / (theta / n_delay)
        else:
            Baug[iTc, 0] = K_Q / C_c
        Baug[iTc, 1] = h_amb * T_amb / C_c

        self.A_lin, self.Baug = A, Baug
        self.n, self.iTc = n, iTc
        self.C_c, self.T_amb, self.sigma = C_c, T_amb, sigma
        self.t_step = t_step
        self.H = np.zeros((1, n))
        self.H[0, iTc] = 1.0
        self.Qkf = np.diag([q_temp] * (n_delay + 1) + [q_dist])
        self.Rkf = np.array([[r_meas]])
        if x0 is None:
            x0 = [0.0] * n_delay + [T_amb, 0.0]
        self.x = np.array(x0, dtype=float)
        self.P = np.eye(n) * 5.0

    def _discretize(self):
        # linearize the radiative term about the current chamber estimate
        n, iTc, C_c = self.n, self.iTc, self.C_c
        T_c0 = self.x[iTc]
        rp = 4.0 * self.sigma * (T_c0 + _KELVIN) ** 3  # d(rad)/dT_c
        r0 = _rad_loss(T_c0, self.T_amb, self.sigma)  # rad loss at T_c0
        A = self.A_lin.copy()
        A[iTc, iTc] += -rp / C_c
        Baug = self.Baug.copy()
        Baug[iTc, 1] += -(r0 - rp * T_c0) / C_c
        Mblk = np.zeros((n + 2, n + 2))
        Mblk[:n, :n] = A
        Mblk[:n, n:] = Baug
        Md = expm(Mblk * self.t_step)
        return Md[:n, :n], Md[:n, n : n + 1], Md[:n, n + 1 : n + 2]

    def update(self, normalized_combustion_load, y_measured):
        load = _normalized_load(normalized_combustion_load)
        Ad, Bd, bd = self._discretize()
        # predict
        self.x = Ad @ self.x + Bd.flatten() * load + bd.flatten()
        self.P = Ad @ self.P @ Ad.T + self.Qkf
        # update
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
        self.P = (np.eye(self.n) - K @ self.H) @ self.P
        return self.x
