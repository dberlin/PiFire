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

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.linalg import expm

from common.learning_trajectory import (
    FrameDeliveryCertainty,
    LearningTrajectoryFrame,
)

# Runtime estimators depend only on NumPy/SciPy. Native solver generation owns
# CasADi and acados-template in the isolated codegen dependency group.


_KELVIN = 273.15

#: Which model STRUCTURE this module implements -- what the state vector is and
#: what the parameters mean. Bumped whenever either changes, which is not the
#: same event as a parameter being recalibrated.
#:
#: 3  migration input only: variable-delay grey snapshot with retired nested learners
#: 4  migration input only: fixed-delay checkpoint with inline challenger authority
#: 5  migration input only: fixed-delay checkpoint with retired cook-refit state
#: 6  migration input only: fixed eight-delay checkpoint with durable challenger reference
#: 7  installation-bound fixed eight-delay grey checkpoint
#:
#: Versions 3 through 6 are interpreted only by startup migration. Runtime
#: restore accepts legacy checkpoints only as inert migration input; current
#: writers emit installation-bound version 7 exclusively.
MODEL_SCHEMA = 7


@dataclass(frozen=True, slots=True)
class EstimatorSeed:
    """Immutable trajectory-derived state used before an MPC estimator first runs."""

    delay_states: tuple[float, ...]
    chamber_temperature_c: float
    disturbance: float
    segment_id: str
    pre_roll_digest: str
    pre_roll_frame_count: int
    required_frame_count: int
    status: Literal["exact", "short", "absent", "uncertain"]

    def __post_init__(self) -> None:
        states = tuple(float(value) for value in self.delay_states)
        if not all(np.isfinite(value) and 0.0 <= value <= 1.0 for value in states):
            raise ValueError("estimator seed delay states must be finite and within [0, 1]")
        object.__setattr__(self, "delay_states", states)
        chamber = float(self.chamber_temperature_c)
        disturbance = float(self.disturbance)
        if not np.isfinite(chamber):
            raise ValueError("estimator seed chamber temperature must be finite")
        if not np.isfinite(disturbance) or disturbance != 0.0:
            raise ValueError("trajectory estimator seed disturbance must be zero")
        object.__setattr__(self, "chamber_temperature_c", chamber)
        object.__setattr__(self, "disturbance", disturbance)
        if not isinstance(self.segment_id, str) or not self.segment_id:
            raise ValueError("estimator seed segment id must be non-blank")
        if (
            not isinstance(self.pre_roll_digest, str)
            or len(self.pre_roll_digest) != 64
            or any(character not in "0123456789abcdef" for character in self.pre_roll_digest)
        ):
            raise ValueError("estimator seed pre-roll digest must be lowercase SHA-256")
        counts = (self.pre_roll_frame_count, self.required_frame_count)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("estimator seed frame counts must be nonnegative integers")
        if self.pre_roll_frame_count > self.required_frame_count:
            raise ValueError("estimator seed cannot contain more than its required suffix")
        if self.status not in {"exact", "short", "absent", "uncertain"}:
            raise ValueError("unsupported estimator seed status")
        if self.status == "exact" and self.pre_roll_frame_count != self.required_frame_count:
            raise ValueError("exact estimator seed must contain its complete suffix")
        if self.status == "short" and not (0 < self.pre_roll_frame_count < self.required_frame_count):
            raise ValueError("short estimator seed must contain an incomplete suffix")
        if self.status in {"absent", "uncertain"} and (self.pre_roll_frame_count != 0 or states):
            raise ValueError("absent or uncertain estimator seed cannot fabricate delay state")


def _rad_loss(T_c, T_amb, sigma):
    # Radiative chamber loss (Stefan-Boltzmann-like). sigma=0 -> purely linear.
    return sigma * ((T_c + _KELVIN) ** 4 - (T_amb + _KELVIN) ** 4)


def _normalized_load(value):
    load = float(value)
    if not np.isfinite(load) or not 0.0 <= load <= 1.0:
        raise ValueError("normalized combustion load must be finite and within [0, 1]")
    return load


def _validated_delay_states(
    delay_states,
    n_delay,
    fallback,
):
    if delay_states is None:
        return np.full(n_delay, fallback, dtype=float)
    values = np.asarray(delay_states, dtype=float)
    if values.shape != (n_delay,) or not np.isfinite(values).all():
        raise ValueError("delay states must match the configured finite delay chain")
    if np.any(values < 0.0) or np.any(values > 1.0):
        raise ValueError("delay states must remain within [0, 1]")
    return values


def _joseph_covariance(P, K, H, R):
    innovation = np.eye(P.shape[0]) - K @ H
    covariance = innovation @ P @ innovation.T + K @ R @ K.T
    return (covariance + covariance.T) * 0.5


def _project_delay_covariance(x, P, Q, n_delay):
    original = x[:n_delay].copy()
    np.clip(original, 0.0, 1.0, out=x[:n_delay])
    active = np.flatnonzero(original != x[:n_delay])
    if active.size:
        P[active, :] = 0.0
        P[:, active] = 0.0
        P[active, active] = np.diag(Q)[active]
        P[:n_delay, n_delay:] = 0.0
        P[n_delay:, :n_delay] = 0.0
    P[:] = (P + P.T) * 0.5


def _thermal_parameters(params):
    """Return the finite physical coefficients shared by steady-state helpers."""
    if not isinstance(params, dict):
        raise TypeError("a thermal model parameter mapping is required")
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


def _numeric_vector(values, name):
    """Return one finite numeric vector without changing the caller's storage."""

    try:
        vector = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a numeric sequence") from error
    if vector.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must contain only finite values")
    return vector


def _delay_stage_rate(*, theta, n_delay):
    if isinstance(n_delay, bool) or not isinstance(n_delay, (int, np.integer)) or n_delay < 0:
        raise ValueError("delay-state count must be a nonnegative integer")
    count = int(n_delay)
    if count == 0:
        return count, 0.0
    if (
        isinstance(theta, bool)
        or not isinstance(theta, (int, float, np.integer, np.floating))
        or not np.isfinite(float(theta))
        or float(theta) <= 0.0
    ):
        raise ValueError("delay-chain theta must be positive and finite")
    return count, count / float(theta)


def replay_delay_chain_arrays(
    duration_s,
    combustion_load,
    *,
    theta,
    n_delay,
    initial_load,
):
    """Replay compact exact-load arrays through an Erlang chain analytically."""

    durations = _numeric_vector(duration_s, "delay replay durations")
    loads = _numeric_vector(combustion_load, "delay replay loads")
    if len(durations) != len(loads):
        raise ValueError("delay replay durations and loads must have the same length")
    if np.any(durations <= 0.0):
        raise ValueError("delay replay durations must be positive")
    if np.any((loads < 0.0) | (loads > 1.0)):
        raise ValueError("delay replay loads must be normalized to [0, 1]")
    initial = _normalized_load(initial_load)
    count, stage_rate = _delay_stage_rate(theta=theta, n_delay=n_delay)
    if count == 0:
        states = np.empty(0, dtype=float)
        states.setflags(write=False)
        return states

    states = np.full(count, initial, dtype=float)
    for duration, load in zip(durations, loads, strict=True):
        coefficients = _erlang_coefficients(count, np.asarray([stage_rate * float(duration)]))[0]
        normalized_load = float(load)
        states = normalized_load + np.convolve(coefficients, states - normalized_load)[:count]
    states.setflags(write=False)
    return states


def replay_delay_chain(
    intervals: tuple[LearningTrajectoryFrame, ...],
    *,
    theta: float,
    n_delay: int,
    initial_load: float,
) -> tuple[float, ...]:
    """Replay exact delivered load through an Erlang chain without integration."""

    durations: list[float] = []
    loads: list[float] = []
    previous_end_ms: int | None = None
    for frame in intervals:
        if not isinstance(frame, LearningTrajectoryFrame):
            raise TypeError("delay replay intervals must be LearningTrajectoryFrame values")
        start_ms = frame.monotonic_start_ms
        end_ms = frame.monotonic_end_ms
        if (
            isinstance(start_ms, bool)
            or isinstance(end_ms, bool)
            or not isinstance(start_ms, int)
            or not isinstance(end_ms, int)
            or end_ms <= start_ms
        ):
            raise ValueError("delay replay intervals must have positive chronology")
        if previous_end_ms is not None and start_ms < previous_end_ms:
            raise ValueError("delay replay intervals must not overlap or reverse")
        if frame.auger_delivery_certainty is not FrameDeliveryCertainty.EXACT:
            raise ValueError("delay replay requires exact auger delivery")
        durations.append((end_ms - start_ms) / 1_000)
        loads.append(_normalized_load(frame.normalized_combustion_load))
        previous_end_ms = end_ms
    states = replay_delay_chain_arrays(
        durations,
        loads,
        theta=theta,
        n_delay=n_delay,
        initial_load=initial_load,
    )
    return tuple(float(value) for value in states)


def _simulate_grey_intervals(
    duration_s,
    combustion_load,
    ambient_c,
    *,
    C_c,
    h_amb,
    T0,
    K_Q,
    sigma,
    theta,
    n_delay,
    initial_delay_states,
    max_dt,
):
    """Advance the shared grey physics and return temperature after each interval."""

    count, stage_rate = _delay_stage_rate(theta=theta, n_delay=n_delay)
    delay_states = np.array(initial_delay_states, dtype=float, copy=True)
    if delay_states.ndim != 1 or len(delay_states) != count or not np.all(np.isfinite(delay_states)):
        raise ValueError("initial delay states must be a finite vector matching n_delay")
    if np.any((delay_states < 0.0) | (delay_states > 1.0)):
        raise ValueError("initial delay states must be normalized to [0, 1]")
    capacitance = float(C_c)
    chamber = float(T0)
    loss = float(h_amb)
    gain = float(K_Q)
    radiation = float(sigma)
    step_limit = float(max_dt)
    scalars = (capacitance, chamber, loss, gain, radiation, step_limit)
    if not all(np.isfinite(value) for value in scalars):
        raise ValueError("grey simulation parameters must be finite")
    if capacitance <= 0.0 or step_limit <= 0.0:
        raise ValueError("grey capacitance and maximum step must be positive")

    out = np.empty(len(duration_s), dtype=float)
    for index, (span_value, load_value, ambient_value) in enumerate(
        zip(duration_s, combustion_load, ambient_c, strict=True)
    ):
        span = float(span_value)
        if span <= 0.0:
            out[index] = chamber
            continue
        steps = max(1, int(np.ceil(span / step_limit)))
        dt = span / steps
        load = float(load_value)
        ambient = float(ambient_value)
        if count:
            deviation = delay_states - load
            coefficients = _erlang_coefficients(
                count,
                np.arange(1, steps + 1, dtype=float) * (stage_rate * dt),
            )
            heat = load + coefficients @ deviation[::-1]
            delay_states = load + np.convolve(coefficients[-1], deviation)[:count]
        else:
            heat = None
        for substep in range(steps):
            delayed_load = load if heat is None else float(heat[substep])
            chamber += (
                dt
                * (gain * delayed_load - loss * (chamber - ambient) - _rad_loss(chamber, ambient, radiation))
                / capacitance
            )
        out[index] = chamber
    return out


def simulate_grey_box_intervals(
    duration_s,
    combustion_load,
    ambient_c,
    *,
    C_c,
    h_amb,
    T0,
    K_Q=1.0,
    sigma=0.0,
    theta=0.0,
    n_delay=0,
    initial_delay_states=(),
    max_dt=0.125,
):
    """Simulate compact independent intervals from explicit candidate delay state."""

    durations = _numeric_vector(duration_s, "grey interval durations")
    loads = _numeric_vector(combustion_load, "combustion load samples")
    ambient = _numeric_vector(ambient_c, "ambient temperature samples")
    if len(durations) != len(loads) or len(durations) != len(ambient):
        raise ValueError("grey interval durations, loads, and ambient values must have the same length")
    if np.any(durations <= 0.0):
        raise ValueError("grey interval durations must be positive")
    if np.any((loads < 0.0) | (loads > 1.0)):
        raise ValueError("combustion load samples must be normalized to [0, 1]")
    return _simulate_grey_intervals(
        durations,
        loads,
        ambient,
        C_c=C_c,
        h_amb=h_amb,
        T0=T0,
        K_Q=K_Q,
        sigma=sigma,
        theta=theta,
        n_delay=n_delay,
        initial_delay_states=initial_delay_states,
        max_dt=max_dt,
    )


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

    ``out[i]`` is the chamber temperature at ``t[i]``.  The delay chain is
    advanced analytically and the chamber uses the same bounded explicit step
    as compact segmented fitting.
    """

    times = _numeric_vector(t, "grey simulation times")
    loads = _numeric_vector(combustion_load, "combustion load samples")
    if len(times) != len(loads):
        raise ValueError("grey simulation times and loads must have the same length")
    if np.any((loads < 0.0) | (loads > 1.0)):
        raise ValueError("combustion load samples must be normalized to [0, 1]")
    out = np.empty(len(times), dtype=float)
    if not len(times):
        return out
    out[0] = float(T0)
    if len(times) == 1:
        return out

    ambient_values = np.asarray(T_amb, dtype=float)
    if ambient_values.ndim == 0:
        ambient = np.full(len(times) - 1, float(ambient_values), dtype=float)
    elif ambient_values.ndim == 1 and len(ambient_values) == len(times):
        ambient = ambient_values[:-1]
    elif ambient_values.ndim == 1 and len(ambient_values) == len(times) - 1:
        ambient = ambient_values
    else:
        raise ValueError("ambient temperature must be scalar or match the simulation intervals")
    if not np.all(np.isfinite(ambient)):
        raise ValueError("ambient temperature samples must be finite")
    count, _ = _delay_stage_rate(theta=theta, n_delay=n_delay)
    out[1:] = _simulate_grey_intervals(
        np.diff(times),
        loads[:-1],
        ambient,
        C_c=C_c,
        h_amb=h_amb,
        T0=T0,
        K_Q=K_Q,
        sigma=sigma,
        theta=theta,
        n_delay=n_delay,
        initial_delay_states=np.zeros(count, dtype=float),
        max_dt=max_dt,
    )
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
        self.T_amb = T_amb
        self.H = np.zeros((1, n))
        self.H[0, iTc] = 1.0
        self.Qkf = np.diag([q_temp] * (n_delay + 1) + [q_dist])
        self.Rkf = np.array([[r_meas]])
        initialized = x0 is not None
        if x0 is None:
            x0 = [0.0] * n_delay + [T_amb, 0.0]
        self.x = np.array(x0, dtype=float)
        self.P = np.eye(n) * 5.0
        self.n_delay = n_delay
        self.iTc = iTc
        self._initialized = initialized
        self.n = n

    def reset(
        self,
        normalized_combustion_load,
        measured_temperature,
        *,
        delay_states=None,
        disturbance=0.0,
    ):
        load = _normalized_load(normalized_combustion_load)
        disturbance = float(disturbance)
        if not np.isfinite(disturbance):
            raise ValueError("disturbance must be finite")
        self.x[: self.n_delay] = _validated_delay_states(delay_states, self.n_delay, load)
        self.x[self.iTc + 1] = disturbance
        if measured_temperature is None:
            self.x[self.iTc] = self.T_amb
            self._initialized = False
        else:
            measured = float(measured_temperature)
            if not np.isfinite(measured):
                raise ValueError("measured temperature must be finite")
            self.x[self.iTc] = measured
            self._initialized = True
        self.P = np.eye(self.n) * 5.0
        return self.x.copy() if self._initialized else None

    def update(self, normalized_combustion_load, y_measured):
        load = _normalized_load(normalized_combustion_load)
        if not self._initialized:
            return self.reset(load, y_measured)
        # predict
        self.x = self.Ad @ self.x + self.Bd.flatten() * load + self.bd.flatten()
        self.P = self.Ad @ self.P @ self.Ad.T + self.Qkf
        # update
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
        self.P = _joseph_covariance(self.P, K, self.H, self.Rkf)
        _project_delay_covariance(self.x, self.P, self.Qkf, self.n_delay)
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
        initialized = x0 is not None
        if x0 is None:
            x0 = [0.0] * n_delay + [T_amb, 0.0]
        self.x = np.array(x0, dtype=float)
        self.P = np.eye(n) * 5.0
        self.n_delay = n_delay
        self._initialized = initialized

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

    def reset(
        self,
        normalized_combustion_load,
        measured_temperature,
        *,
        delay_states=None,
        disturbance=0.0,
    ):
        load = _normalized_load(normalized_combustion_load)
        disturbance = float(disturbance)
        if not np.isfinite(disturbance):
            raise ValueError("disturbance must be finite")
        self.x[: self.n_delay] = _validated_delay_states(delay_states, self.n_delay, load)
        self.x[self.iTc + 1] = disturbance
        if measured_temperature is None:
            self.x[self.iTc] = self.T_amb
            self._initialized = False
        else:
            measured = float(measured_temperature)
            if not np.isfinite(measured):
                raise ValueError("measured temperature must be finite")
            self.x[self.iTc] = measured
            self._initialized = True
        self.P = np.eye(self.n) * 5.0
        return self.x.copy() if self._initialized else None

    def update(self, normalized_combustion_load, y_measured):
        load = _normalized_load(normalized_combustion_load)
        if not self._initialized:
            return self.reset(load, y_measured)
        Ad, Bd, bd = self._discretize()
        # predict
        self.x = Ad @ self.x + Bd.flatten() * load + bd.flatten()
        self.P = Ad @ self.P @ Ad.T + self.Qkf
        # update
        S = self.H @ self.P @ self.H.T + self.Rkf
        K = (self.P @ self.H.T) / S
        self.x = self.x + K.flatten() * (y_measured - (self.H @ self.x)[0])
        self.P = _joseph_covariance(self.P, K, self.H, self.Rkf)
        _project_delay_covariance(self.x, self.P, self.Qkf, self.n_delay)
        return self.x
