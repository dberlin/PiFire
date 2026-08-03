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

 Provides the do-mpc model used by the controller and a Kalman filter over the
 same augmented linear model used as the state/disturbance estimator.

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
 controller/mpc.py's _DEFAULTS for why 8 and not more.

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

 MHE-only tuning parameters (moving-horizon estimator)

   mhe_horizon   Number of past steps in the estimation window.
   pw_state / pw_dist   Process-noise weights (inverse-variance) for the state
                        and disturbance in the MHE objective.
   px_state / px_dist   Arrival-cost (prior) weights on the state and
                        disturbance at the start of the horizon.

*****************************************
"""

import numpy as np
from scipy.linalg import expm
# NOTE: do_mpc (and its CasADi/IPOPT stack) is imported LAZILY inside the do-mpc
# model builder and the MHE. The Kalman/EKF estimators and the neural-net policy
# are pure numpy/scipy, so an IPOPT-free deployment (net policy + EKF) does not
# require do_mpc to be installed at all.


_KELVIN = 273.15

#: Which model STRUCTURE this module implements -- what the state vector is and
#: what the parameters mean. Bumped whenever either changes, which is not the
#: same event as a parameter being recalibrated.
#:
#: 1  two lumps: [q0..q_{n_delay-1}, T_f, T_c, d], parameters C_f/C_c/h_fc/h_amb
#: 2  one lump:  [q0..q_{n_delay-1}, T_c, d],      parameters C_c/h_amb
#:
#: Anything that outlives the process and describes this model has to carry it,
#: because the alternative is comparing the model's own scalars and hoping they
#: differ. They did not: collapsing to one lump left C_c, h_amb, K_Q, theta,
#: sigma, T_amb and n_delay ALL numerically identical between the two
#: structures, so a stale neural-policy artifact matched a calibration check on
#: every field it had. The two consumers are controller/mpc.py's persisted
#: model snapshot and controller/mpc_net.py's policy artifact; both refuse a
#: record carrying a different value rather than trying to read it.
MODEL_SCHEMA = 2


def _rad_loss(T_c, T_amb, sigma):
    # Radiative chamber loss (Stefan-Boltzmann-like). sigma=0 -> purely linear.
    return sigma * ((T_c + _KELVIN) ** 4 - (T_amb + _KELVIN) ** 4)


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
    Q,
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
    advances the state from `t[i]` to `t[i+1]` under the input `Q[i]`. The
    disturbance state `d` is absent: it exists to absorb model error at run
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
    sub-step and INDEPENDENT of theta and n_delay -- measured at 0.099 C RMS per
    second of sub-step on the real MAK cook in tests/unit/mpc/fixtures,
    uniformly to within 5% across theta from 3 s to 200 s and n_delay from 4 to
    20. The 0.125 s default therefore holds the numerical error to 0.014 C RMS,
    a ninth of the 0.16 C of model fidelity the single-lump structure was
    adopted at, so that what a fit reports is the grill rather than the
    integrator. The measurement is
    docs/superpowers/experiments/substep_convergence.py.
    """
    t = np.asarray(t, dtype=float)
    Q = np.asarray(Q, dtype=float)
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
        u = float(Q[i])
        if lag_tau > 0.0:
            # lags(k*dt) = u + exp(A*k*dt) @ (lags(0) - u), exactly, for every
            # sub-step k at once: `dev` is the state's departure from the
            # equilibrium this constant input drives it to, and exp(A*k*dt) is
            # lower-triangular Toeplitz in the Erlang coefficients. `heat` is
            # the tail stage, which is what feeds the chamber; `lags` carries
            # the whole chain into the next interval.
            dev = lags - u
            coef = _erlang_coefficients(n, np.arange(1, steps + 1) * (dt / lag_tau))
            # .tolist() because the chamber loop below is scalar Python: reading
            # numpy scalars out of an array and doing float arithmetic on them
            # costs more than the whole exact-chain build does.
            heat = (u + coef @ dev[::-1]).tolist()
            lags = u + np.convolve(coef[-1], dev)[:n]
        else:
            heat = [u] * steps
        for k in range(steps):
            dT_c = (K_Q * heat[k] - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c
            T_c += dt * dT_c
    return out


def build_do_mpc_model(*, C_c, h_amb, T_amb, theta=0.0, n_delay=0, K_Q=1.0, sigma=0.0):
    """Build the do-mpc continuous model of the augmented grey-box plant.

    Parameters are the physical/model parameters documented in the module
    docstring: chamber capacitance C_c, ambient conductance h_amb, ambient
    T_amb, firing-rate gain K_Q, radiative coefficient sigma, and the optional
    transport-delay pair (theta mean deadtime, n_delay lag states).
    """
    import do_mpc

    model = do_mpc.model.Model("continuous")
    q = [model.set_variable("_x", f"q{i}") for i in range(n_delay)]
    T_c = model.set_variable("_x", "T_c")
    d = model.set_variable("_x", "d")
    Q = model.set_variable("_u", "Q")
    model.set_variable("_tvp", "T_set")
    if n_delay > 0:
        tau_d = theta / n_delay
        model.set_rhs("q0", (Q - q[0]) / tau_d)
        for i in range(1, n_delay):
            model.set_rhs(f"q{i}", (q[i - 1] - q[i]) / tau_d)
        heat_in = q[n_delay - 1]
    else:
        heat_in = Q
    # K_Q maps the abstract firing rate to actual heat (calibrated to grill power)
    model.set_rhs("T_c", (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma) + d) / C_c)
    model.set_rhs("d", d * 0)
    model.setup()
    return model


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

    def update(self, Q_applied, y_measured):
        # predict
        self.x = self.Ad @ self.x + self.Bd.flatten() * Q_applied + self.bd.flatten()
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

    def update(self, Q_applied, y_measured):
        Ad, Bd, bd = self._discretize()
        # predict
        self.x = Ad @ self.x + Bd.flatten() * Q_applied + bd.flatten()
        self.P = Ad @ self.P @ Ad.T + self.Qkf
        # update
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
        self.P = (np.eye(self.n) - K @ self.H) @ self.P
        return self.x


class GreyBoxMHE:
    """
    Moving-horizon estimator over the (possibly nonlinear) augmented model,
    x = [q0..q_{n_delay-1}, T_c, d]. The control input Q is modeled as a
    KNOWN time-varying parameter (fed the applied-input history) so the
    disturbance state d -- not the input -- absorbs model mismatch, giving
    offset-free tracking. Required for the nonlinear (radiative) model, where a
    linear Kalman filter does not apply. Same update(Q_applied, y) interface as
    GreyBoxKF.

    See the module docstring for the meaning of every constructor parameter: the
    physical set (C_c/h_amb/T_amb/sigma/K_Q/theta/n_delay) plus the MHE-only
    tuning (mhe_horizon, pw_state/pw_dist, px_state/px_dist, r_meas).
    """

    def __init__(
        self,
        *,
        C_c,
        h_amb,
        T_amb,
        t_step,
        theta=0.0,
        n_delay=0,
        K_Q=1.0,
        sigma=0.0,
        r_meas=0.04,
        pw_state=10.0,
        pw_dist=0.5,
        px_state=1.0,
        px_dist=0.5,
        mhe_horizon=10,
    ):
        import do_mpc
        from collections import deque

        n = n_delay + 2
        self.n = n
        self._N = int(mhe_horizon)

        model = do_mpc.model.Model("continuous")
        q = [model.set_variable("_x", f"q{i}") for i in range(n_delay)]
        T_c = model.set_variable("_x", "T_c")
        d = model.set_variable("_x", "d")
        Q_app = model.set_variable("_tvp", "Q_app")  # applied input, KNOWN
        if n_delay > 0:
            tau_d = theta / n_delay
            model.set_rhs("q0", (Q_app - q[0]) / tau_d, process_noise=True)
            for i in range(1, n_delay):
                model.set_rhs(f"q{i}", (q[i - 1] - q[i]) / tau_d, process_noise=True)
            heat_in = q[n_delay - 1]
        else:
            heat_in = Q_app
        model.set_rhs(
            "T_c",
            (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma) + d) / C_c,
            process_noise=True,
        )
        model.set_rhs("d", d * 0, process_noise=True)
        model.set_meas("T_c_meas", T_c, meas_noise=True)
        model.setup()

        mhe = do_mpc.estimator.MHE(model, [])
        mhe.set_param(
            n_horizon=self._N,
            t_step=t_step,
            store_full_solution=False,
            meas_from_data=True,
            nlpsol_opts={"ipopt.print_level": 0, "print_time": 0, "ipopt.sb": "yes"},
        )
        P_x = np.diag([px_state] * (n_delay + 1) + [px_dist])
        P_v = np.array([[1.0 / r_meas]])
        P_w = np.diag([pw_state] * (n_delay + 1) + [pw_dist])
        mhe.set_default_objective(P_x, P_v, P_w=P_w)

        self._qhist = deque([0.0] * (self._N + 1), maxlen=self._N + 1)
        tvp_template = mhe.get_tvp_template()

        def tvp_fun(t_now):
            for k in range(self._N + 1):
                tvp_template["_tvp", k, "Q_app"] = self._qhist[k]
            return tvp_template

        mhe.set_tvp_fun(tvp_fun)
        mhe.setup()

        x0 = np.zeros((n, 1))
        x0[n_delay, 0] = T_amb
        mhe.x0 = x0
        mhe.set_initial_guess()
        self.mhe = mhe

    def update(self, Q_applied, y_measured):
        self._qhist.append(float(Q_applied))
        return np.asarray(self.mhe.make_step(np.array([[float(y_measured)]]))).flatten()
