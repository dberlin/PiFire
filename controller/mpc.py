#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Controller (cascade: firing-rate + combustion allocator)
*****************************************

 Outer loop manipulates a scalar firing-rate demand Q against a grey-box thermal
 model with an integrating-disturbance state (offset-free tracking). The inner
 combustion allocator maps Q to auger/fan. Returns a dict:
 {'cycle_ratio': auger_duty, 'fan': {'duty': pct or None}}.

 State is estimated by the EKF (default), MHE, or KF. The firing-rate policy is
 either the do-mpc NLP solve ('nlp') or a pure-numpy neural approximation
 ('net', no IPOPT/CasADi) that falls back to the NLP if its artifact is missing
 or mismatched. net policy + EKF needs only numpy/scipy at runtime.

 Operates internally in Celsius.

*****************************************
"""

import math
import os
import time

import numpy as np
# do_mpc (CasADi/IPOPT) is imported lazily only when the NLP policy is built; the
# net policy + EKF path is pure numpy/scipy and never imports it.

from controller.base import ControllerBase
from controller.mpc_model import build_do_mpc_model, GreyBoxKF, GreyBoxEKF, GreyBoxMHE
from controller.mpc_allocator import allocate

_DEFAULTS = dict(
    # R_dQ (firing-move penalty) kept low: 1.0 was over-damped -> sluggish rise AND
    # a looser steady band. 0.1 gives ~4x faster setpoint-step rise and a tighter
    # band, at a modest step-overshoot increase.
    n_horizon=24,
    t_step=25.0,
    control_period=5.0,
    Q_w=1.0,
    R_dQ=0.1,
    Q_min=5.0,
    Q_max=100.0,
    # Nominal grey-box thermal params -- CALIBRATE to your grill via update_mpc.py.
    C_f=9.0,
    C_c=320.0,
    h_fc=1.3,
    h_amb=0.50,
    T_amb=20.0,
    theta=50.0,
    n_delay=4,
    K_Q=3.5,
    sigma=1.4e-9,
    # 'ekf' linearizes the nonlinear radiative term each step (~us, default);
    # 'mhe' solves an NLP (nonlinear, slower); 'kf' is linear-only.
    estimator="ekf",
    # Firing-rate policy: 'nlp' solves the MPC NLP each step (do_mpc/IPOPT);
    # 'net' uses a pure-numpy neural approximation of the policy (no IPOPT/CasADi)
    # and falls back to 'nlp' if the artifact is missing or its calibration does
    # not match this config.
    policy="nlp",
    policy_net_path="./controller/mpc_policy_net.npz",
    fan_min_pct=40.0,
    fan_max_pct=100.0,
    enable_fan_input=False,
    # est_q_dist deliberately slow: a fast disturbance estimate chases unmeasured
    # transients and worsens setpoint-step overshoot; 0.05 cut step overshoot ~30%
    # with no change to the steady-state band.
    est_q_temp=1e-2,
    est_q_dist=0.05,
    est_r_meas=0.04,
    # Optional logging of (time_s, temp_c, Q) for the offline calibration utility.
    log_data=False,
    log_path="./logs/mpc_calibration_log.csv",
)


def _to_c(value, units):
    return (value - 32.0) * 5.0 / 9.0 if units == "F" else value


def _finite_float(value):
    """Cast to float, or None if the result is not finite.

    A diverged solve or estimator can produce NaN/inf; json.dumps(allow_nan=False)
    rejects NaN outright, and the MQTT handler's default allow_nan=True instead
    emits the bare token NaN, which is not valid JSON. None survives both.
    """
    value = float(value)
    return value if math.isfinite(value) else None


def _sanitized_copy(mapping):
    """A copy of `mapping`, safe for a caller to own outright.

    Every float value is passed through `_finite_float`; non-float values
    (ints, strings) are kept as-is so e.g. an int setting is not silently
    turned into a float. A copy rather than the live object, since this feeds
    controller_state(), whose contract is that the caller owns the mapping --
    `mapping` itself may be a live settings dict a consumer must not reach.
    """
    return {key: (_finite_float(value) if isinstance(value, float) else value) for key, value in mapping.items()}


def _load_net_policy(cfg):
    """Load the numpy net policy, or return None to fall back to the NLP.

    Module-level (not a method) because `requires_modules` below has to ask the
    same question before any Controller exists -- and asking it any other way
    would mean re-deriving this logic in a second place.
    """
    from controller.mpc_net import NetPolicy, net_path_for

    base = cfg.get("policy_net_path")
    path = net_path_for(base, bool(cfg.get("enable_fan_input"))) if base else base
    if not path or not os.path.exists(path):
        print(f"[mpc] policy=net but artifact not found ({path}); using NLP")
        return None
    try:
        net = NetPolicy.load(path)
    except Exception as e:
        print(f"[mpc] could not load net policy ({e}); using NLP")
        return None
    if not net.matches_config(cfg):
        print("[mpc] net policy calibration does not match config; using NLP")
        return None
    return net


_PHYSICAL_PARAMS = ("C_f", "C_c", "h_fc", "h_amb", "theta", "n_delay", "K_Q", "sigma")


def _warn_about_model(cfg):
    """Report a model that cannot govern this grill well.

    Both conditions are advisory: the shipped parameters are a legitimate
    starting point for a first cook, and a controller that refuses to run is
    worse than one that says what is wrong.
    """
    if all(cfg.get(k) == _DEFAULTS[k] for k in _PHYSICAL_PARAMS):
        print(
            "[mpc] model is uncalibrated (every thermal parameter is still the shipped default). "
            "Expect large overshoot until you fit this grill with controller/update_mpc.py."
        )
    h_amb = float(cfg.get("h_amb") or 0.0)
    if h_amb > 0.0:
        tau = float(cfg["C_c"]) / h_amb
        horizon = float(cfg["n_horizon"]) * float(cfg["t_step"])
        if horizon < tau:
            print(
                f"[mpc] prediction horizon is {horizon:.0f} s but the model's chamber time "
                f"constant is {tau:.0f} s; the controller cannot see far enough ahead to stop "
                "in time. Raise n_horizon or t_step."
            )


def requires_modules(config):
    """Import names `Controller(config, ...)` will need but a base install lacks.

    do-mpc's CasADi/IPOPT stack publishes no Linux-ARM wheel and so builds from
    source, which is why it is a PiFire *optional* dependency -- the `mpc` extra
    in pyproject.toml -- installed only when someone selects this controller.
    Whether a given MPC config actually needs it depends on the config, so the
    settings-save gate (common/controller_deps.py) asks THIS function rather
    than re-deriving __init__'s branch structure, which would silently drift the
    moment the policy/estimator wiring changes.

    Returns an empty tuple when the base install is sufficient.
    """
    cfg = dict(_DEFAULTS)
    cfg.update(config or {})
    # GreyBoxMHE solves an NLP, so it imports do_mpc (controller/mpc_model.py)
    # regardless of which firing-rate policy is selected.
    if str(cfg.get("estimator", "ekf")).lower() == "mhe":
        return ("do_mpc",)
    # policy='net' is pure numpy/scipy -- but only if its artifact actually loads
    # AND matches this config; otherwise __init__ falls back to the NLP, which
    # does need do_mpc.
    if str(cfg.get("policy", "nlp")).lower() == "net" and _load_net_policy(cfg) is not None:
        return ()
    return ("do_mpc",)


class Controller(ControllerBase):
    def __init__(self, config, units, cycle_data):
        super().__init__(config, units, cycle_data)

        cfg = dict(_DEFAULTS)
        cfg.update(config or {})
        self.cfg = cfg
        _warn_about_model(cfg)
        self.u_min = cycle_data.get("u_min", 0.1)
        self.u_max = cycle_data.get("u_max", 0.9)

        self._set_point_c = 0.0
        self._last_Q = cfg["Q_min"]
        self._applied_Q = float(cfg["Q_min"])
        self._x_hat = None
        self._policy_u_prev = float(cfg["Q_min"])
        self._last_Q_raw = float(cfg["Q_min"])
        self._last_solve_failed = False

        n_delay = int(cfg["n_delay"])

        # State/disturbance estimator (independent of the policy). EKF linearizes
        # the nonlinear radiative term each step (default); MHE solves an NLP; KF
        # is linear-only. All expose update(Q_applied, y) -> state estimate and are
        # discretized at the control period so faster re-solves track elapsed time.
        est_kind = str(cfg.get("estimator", "ekf")).lower()
        if est_kind == "kf":
            self.estimator = GreyBoxKF(
                C_f=cfg["C_f"],
                C_c=cfg["C_c"],
                h_fc=cfg["h_fc"],
                h_amb=cfg["h_amb"],
                T_amb=cfg["T_amb"],
                t_step=float(cfg["control_period"]),
                q_temp=cfg["est_q_temp"],
                q_dist=cfg["est_q_dist"],
                r_meas=cfg["est_r_meas"],
                theta=float(cfg["theta"]),
                n_delay=n_delay,
                K_Q=float(cfg["K_Q"]),
            )
        elif est_kind == "ekf":
            self.estimator = GreyBoxEKF(
                C_f=cfg["C_f"],
                C_c=cfg["C_c"],
                h_fc=cfg["h_fc"],
                h_amb=cfg["h_amb"],
                T_amb=cfg["T_amb"],
                t_step=float(cfg["control_period"]),
                q_temp=cfg["est_q_temp"],
                q_dist=cfg["est_q_dist"],
                r_meas=cfg["est_r_meas"],
                theta=float(cfg["theta"]),
                n_delay=n_delay,
                K_Q=float(cfg["K_Q"]),
                sigma=float(cfg["sigma"]),
            )
        else:
            self.estimator = GreyBoxMHE(
                C_f=cfg["C_f"],
                C_c=cfg["C_c"],
                h_fc=cfg["h_fc"],
                h_amb=cfg["h_amb"],
                T_amb=cfg["T_amb"],
                t_step=float(cfg["control_period"]),
                theta=float(cfg["theta"]),
                n_delay=n_delay,
                K_Q=float(cfg["K_Q"]),
                sigma=float(cfg["sigma"]),
                r_meas=cfg["est_r_meas"],
            )

        # Firing-rate policy. 'net' uses the pure-numpy neural approximation (no
        # IPOPT/CasADi); it falls back to the NLP if the artifact is missing or its
        # calibration does not match this config.
        self.model = None
        self.mpc = None
        self._net = None
        if str(cfg.get("policy", "nlp")).lower() == "net":
            self._net = _load_net_policy(cfg)
        if self._net is None:
            self._build_nlp(cfg, n_delay)

        # Optional data logging for offline calibration (update_mpc.py): one
        # (time_s, temp_c, Q) row per control step. Logs internal Celsius.
        self._log_path = cfg["log_path"] if cfg.get("log_data") else None
        if self._log_path and (not os.path.exists(self._log_path) or os.path.getsize(self._log_path) == 0):
            try:
                with open(self._log_path, "a") as f:
                    f.write("time_s,temp_c,Q\n")
            except OSError:
                self._log_path = None  # disable logging if the path is unwritable

    def _build_nlp(self, cfg, n_delay):
        """Build the do-mpc NLP policy (lazily imports do_mpc/CasADi/IPOPT)."""
        import do_mpc

        self.model = build_do_mpc_model(
            C_f=cfg["C_f"],
            C_c=cfg["C_c"],
            h_fc=cfg["h_fc"],
            h_amb=cfg["h_amb"],
            T_amb=cfg["T_amb"],
            theta=float(cfg["theta"]),
            n_delay=n_delay,
            K_Q=float(cfg["K_Q"]),
            sigma=float(cfg["sigma"]),
        )
        self.mpc = do_mpc.controller.MPC(self.model)
        self.mpc.set_param(
            n_horizon=int(cfg["n_horizon"]),
            t_step=float(cfg["t_step"]),
            store_full_solution=False,
            nlpsol_opts={
                "ipopt.print_level": 0,
                "print_time": 0,
                "ipopt.sb": "yes",
                # do_mpc supplies the previous solve's primal AND dual point on
                # every step after the first; IPOPT ignores the duals unless
                # warm starting is on. The cap bounds the tail -- the cold
                # start needs ~60 iterations with nothing to warm from, while
                # the median warm solve needs 6, so 10 truncates the spike
                # without touching the typical step.
                "ipopt.warm_start_init_point": "yes",
                "ipopt.max_iter": 10,
            },
        )
        T_c = self.model.x["T_c"]
        T_set = self.model.tvp["T_set"]
        self.mpc.set_objective(mterm=cfg["Q_w"] * (T_c - T_set) ** 2, lterm=cfg["Q_w"] * (T_c - T_set) ** 2)
        self.mpc.set_rterm(Q=cfg["R_dQ"])
        self.mpc.bounds["lower", "_u", "Q"] = cfg["Q_min"]
        self.mpc.bounds["upper", "_u", "Q"] = cfg["Q_max"]

        tvp_template = self.mpc.get_tvp_template()

        def tvp_fun(t_now):
            for k in range(int(cfg["n_horizon"]) + 1):
                tvp_template["_tvp", k, "T_set"] = self._set_point_c
            return tvp_template

        self.mpc.set_tvp_fun(tvp_fun)
        self.mpc.setup()

        x0 = np.zeros((n_delay + 3, 1))
        x0[n_delay, 0] = cfg["T_amb"]  # T_f
        x0[n_delay + 1, 0] = cfg["T_amb"]  # T_c
        self.mpc.x0 = x0
        self.mpc.set_initial_guess()

    def _log_row(self, temp_c, Q):
        try:
            with open(self._log_path, "a") as f:
                f.write(f"{time.time():.3f},{temp_c:.3f},{Q:.4f}\n")
        except OSError:
            self._log_path = None  # stop trying after a write failure

    def set_target(self, set_point):
        self.set_point = set_point
        self._set_point_c = _to_c(set_point, self.units)

    def get_control_period(self):
        return float(self.cfg["control_period"])

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    def get_status(self):
        return {
            "set_point": _finite_float(self.set_point),
            "set_point_c": _finite_float(self._set_point_c),
            "last_Q": _finite_float(self._last_Q),
            "applied_Q": _finite_float(self._applied_Q),
            "policy": "net" if self._net is not None else "nlp",
            "u_min": _finite_float(self.u_min),
            "u_max": _finite_float(self.u_max),
            "x_hat": None
            if self._x_hat is None
            else tuple(_finite_float(v) for v in np.asarray(self._x_hat).reshape(-1)),
            # The __dict__ fallback this replaces reached the pid_cycle_data mqtt
            # topic only through notify()'s nested-dict recursion over this same
            # attribute; publish it explicitly so that topic keeps working.
            # cycle_data is core.__dict__'s live reference to settings["cycle_data"]
            # (see _build_core) -- _sanitized_copy hands back a copy, not that
            # live settings mapping.
            "cycle_data": _sanitized_copy(self.cycle_data),
        }

    def set_output(self, applied):
        """Take the auger duty that actually ran and recover the firing rate.

        allocate() is affine on [u_min, u_max], so this inverts it exactly
        there. Below u_min the affine inverse would extrapolate to a negative
        Q -- but mpc_model.py's heat_in = K_Q * Q has no offset, so a negative
        Q reads as negative heat, and a paused auger delivers none, not
        negative heat. Below u_min the inverse instead blends linearly to the
        origin (duty 0 -> Q 0), agreeing with the affine branch exactly at
        u_min. The result can still land below Q_min -- that floor-crossing
        is the signal this method exists to report, reported one call at a
        time (a shorter or mid-interval pause is invisible between reports) --
        it just never goes negative.

        Fidelity below u_min depends on u_min > 0. At u_min == 0 the blend
        branch is unreachable for any non-negative ratio, so the affine
        branch alone handles duty 0 and folds it back to Q_min --
        indistinguishable from a minimum command, the exact defect this
        method exists to fix. u_min == 0 is a valid cycle_data configuration
        this method does not special-case further.
        """
        span = self.u_max - self.u_min
        if span <= 0:
            return
        ratio = float(applied.ratio)
        Q_min, Q_max = self.cfg["Q_min"], self.cfg["Q_max"]
        # Mirrors allocate()'s own guard against a degenerate Q span, so the
        # two maps stay consistent instead of this inverse flipping sign on a
        # nonsense config.
        q_span = (Q_max - Q_min) if Q_max > Q_min else 1.0
        if self.u_min > 0 and ratio < self.u_min:
            self._applied_Q = Q_min * ratio / self.u_min
        else:
            self._applied_Q = Q_min + (ratio - self.u_min) / span * q_span

    def update(self, current):
        y = _to_c(current, self.units)
        # 1) estimate states from the duty that actually reached the auger --
        #    not the command -- so a clamp, a lid-open pause, or a manual
        #    override is visible to the estimator instead of silently assumed
        #    away.
        x_hat = self.estimator.update(self._applied_Q, y)
        self._x_hat = x_hat
        # The net's Q_prev feature was trained on values the sampler always
        # drove inside [Q_min, Q_max]; clamp for the net only, the estimator
        # above already saw the unclamped value.
        self._policy_u_prev = float(np.clip(self._applied_Q, self.cfg["Q_min"], self.cfg["Q_max"]))
        # 2) compute firing rate Q from the active policy (net or NLP). On any
        #    error we hold the previous move so the control loop never breaks.
        try:
            if self._net is not None:
                Q = self._net.firing_rate(x_hat, self._policy_u_prev, self._set_point_c)
            else:
                Q = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
            self._last_solve_failed = False
        except Exception:
            Q = self._last_Q
            self._last_solve_failed = True
        self._last_Q_raw = float(Q)
        Q = float(np.clip(Q, self.cfg["Q_min"], self.cfg["Q_max"]))
        self._last_Q = Q
        # Assume the command is applied until a report says otherwise.
        self._applied_Q = Q
        if self._log_path:
            self._log_row(y, Q)
        # 3) allocate Q -> actuators
        auger, fan_duty = allocate(
            Q,
            Q_min=self.cfg["Q_min"],
            Q_max=self.cfg["Q_max"],
            u_min=self.u_min,
            u_max=self.u_max,
            fan_min_pct=self.cfg["fan_min_pct"],
            fan_max_pct=self.cfg["fan_max_pct"],
            enable_fan=bool(self.cfg["enable_fan_input"]),
        )
        return {"cycle_ratio": auger, "fan": {"duty": fan_duty}}
