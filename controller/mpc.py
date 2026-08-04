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

import collections
import math
import os
import time

import numpy as np
# do_mpc (CasADi/IPOPT) is imported lazily only when the NLP policy is built; the
# net policy + EKF path is pure numpy/scipy and never imports it.

from controller.base import ControllerBase
from controller.model_promotion import Verdict as _Verdict
from controller.model_promotion import _HORIZON_CAP_S, built_n_horizon, longest_braking_distance
from controller.mpc_model import build_do_mpc_model, GreyBoxKF, GreyBoxEKF, GreyBoxMHE, MODEL_SCHEMA
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
    C_c=320.0,
    h_amb=0.50,
    T_amb=20.0,
    theta=50.0,
    # n_delay is a STRUCTURE constant, not a fitted parameter, so raising it
    # costs no degrees of freedom -- it buys a sharper, more plug-flow delay
    # for solver time alone. 8 is where that trade stops paying: against the
    # MAK plant, going 4 -> 8 recovers the dead time the model predicts from
    # 0.56x of the plant's own to 0.71x and the coast from 0.84x to 0.90x, for
    # 27% more NLP solve time; 8 -> 12 buys a third as much for three times the
    # increment, and 16 and 20 less again for more. Measured in
    # docs/superpowers/experiments/ndelay_sweep.py.
    n_delay=8,
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

# One row per control period. At the 5 s default that is ~12 hours, which is
# longer than any single cook; a longer one loses its beginning rather than
# its end, and the end is what describes the grill's current state. This is
# also what bounds a refit: the longest cook the fit can ever be handed off
# the teardown path is one full history.
_HISTORY_MAX = 8640

# Below this a record is an interrupted cook rather than a description of a
# grill, and fitting it would produce a confident answer from nothing. There
# is deliberately no upper limit to match: a refit fits the whole cook.
#
# Thinning the rows first looks like it should be cheaper and is not.
# mpc_model.simulate_grey_box sub-steps every interval to max_dt, so one
# residual evaluation costs (cook duration / max_dt) integration steps no
# matter how many rows were sampled from it -- the fit's price is set by how
# long the cook was and how many iterations the solver takes, and row count
# enters neither. Dropping rows therefore discards evidence for nothing, and
# the worse-conditioned problem left behind takes MORE iterations, so it is
# not even faster in practice. It costs an order of magnitude of accuracy in
# the chamber parameters, and the braking distance model_promotion sizes the
# horizon from is read off the same fit.
_REFIT_MIN_SAMPLES = 120

# Parameters the least-squares solve starts from, and the magnitudes it scales
# by. A fixed reference, so that a cook's fit describes the grill and nothing
# else: it cannot inherit whatever the previous fit happened to land on, and
# two identical cooks a season apart give the same answer. Seeding it from the
# running model instead would make each result a function of every result
# before it, through a solver path -- which basin the solve lands in depends on
# where it starts -- that no measurement of the finished model can unwind.
#
# Every parameter the fit reads a starting value for -- the ones update_mpc's
# `_FREE` moves and the ones it holds at the value they came in with. T_amb,
# sigma and n_delay are not here: the fit passes them through unchanged, so
# they come from the running config where an operator's own calibration of them
# lives.
_REFIT_INIT = {key: float(_DEFAULTS[key]) for key in ("C_c", "h_amb", "K_Q", "theta")}


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


def _optional_int(value):
    """Cast to int, or None when there is no number to report.

    Distinguishes "not recorded" from a recorded zero, which for a count of
    solver work are opposite claims.
    """
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _optional_float(value):
    """Cast to a finite float, or None when there is no number to report.

    Same distinction as `_optional_int`, and additionally refuses inf/NaN:
    those are not measurements either, and the model store's validator encodes
    with allow_nan=False, so a snapshot carrying one could never be written.
    """
    try:
        value = float(value)
    except TypeError, ValueError:
        return None
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


def _load_net_policy(cfg, n_horizon):
    """Load the numpy net policy, or return None to fall back to the NLP.

    The net approximates the NLP's policy at one horizon, so the horizon it is
    judged against is the EFFECTIVE one the NLP would be built at, not cfg's
    floor. An artifact trained at the shorter length is a policy that brakes
    from a shorter view of the coast, which is the whole thing being fixed.

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
    if not net.matches_config({**cfg, "n_horizon": n_horizon}):
        if net.input_dim != net.expected_input_dim(cfg):
            print(
                f"[mpc] net policy at {path} takes a {net.input_dim}-wide input but this model "
                f"produces {net.expected_input_dim(cfg)}; it was trained against a different "
                "state vector. Using NLP -- regenerate it with tools/regenerate_mpc_net.py."
            )
        else:
            print("[mpc] net policy calibration does not match config; using NLP")
        return None
    return net


_PHYSICAL_PARAMS = ("C_c", "h_amb", "theta", "n_delay", "K_Q", "sigma")

#: Parameters of the two-lump model this controller used to plan against. A
#: settings record written before the firepot state was dropped still carries
#: them, and an operator's own calibration may too. They are reported and
#: ignored rather than refused: they name nothing in the model any more, so
#: there is no value they could be given that would mean something, and a
#: controller that will not start because an obsolete key is present is worse
#: for the grill than one that says the key does nothing.
_RETIRED_PARAMS = ("C_f", "h_fc")


def _warn_about_model(cfg):
    """Report a model that cannot govern this grill well.

    Every condition is advisory: the shipped parameters are a legitimate
    starting point for a first cook, and a controller that refuses to run is
    worse than one that says what is wrong.
    """
    retired = [k for k in _RETIRED_PARAMS if k in cfg]
    if retired:
        print(
            f"[mpc] ignoring {', '.join(retired)}: the model is a single chamber lump and no "
            "longer has a firepot state for them to describe. Remove them from "
            "Settings > Controller."
        )
    if all(cfg.get(k) == _DEFAULTS[k] for k in _PHYSICAL_PARAMS):
        print(
            "[mpc] model is uncalibrated (every thermal parameter is still the shipped default). "
            "Expect large overshoot until you fit this grill with controller/update_mpc.py."
        )
    # The same quantity model_promotion.evaluate() sizes horizon_needed from,
    # through the same function, so this message and the verdict a refit prints
    # cannot describe one model in two ways.
    brake = longest_braking_distance(cfg)
    horizon = float(cfg["n_horizon"]) * float(cfg["t_step"])
    if horizon < brake:
        # This does not ask for n_horizon to be raised to the derived value. The
        # build already reaches that far, and a configured floor that high would
        # stop a later, quicker model from bringing the horizon down again --
        # the hand-made version of the ratchet the derivation exists to avoid.
        #
        # The build's own step bound is read here, not the demand: where the two
        # differ the horizon really is short, and saying so is the whole point
        # of this message. Running under-horizoned in silence is the defect it
        # exists to prevent.
        steps = built_n_horizon(cfg, n_horizon=cfg["n_horizon"], t_step=cfg["t_step"])
        covered = steps * float(cfg["t_step"])
        # A model that predicts no end to the coast has no number to report, and
        # is the one case where no horizon is enough at any length.
        coast = f"for {brake:.0f} s" if math.isfinite(brake) else "with no end this model predicts"
        if covered >= brake:
            reach = f"planning over {steps} steps ({covered:.0f} s) instead"
        elif brake <= _HORIZON_CAP_S:
            # The shortfall is _HORIZON_CAP_STEPS truncating a demand the seconds
            # bound would have allowed, which only happens at a t_step fine
            # enough to turn an ordinary coast into an extraordinary NLP. A
            # longer t_step spans the same seconds in fewer steps, so it buys
            # the missing window at no extra solve cost. It is offered rather
            # than taken because it also re-discretizes the model the MPC
            # solves -- a larger change to controller behaviour than lengthening
            # the window, and not one to make on a grill's behalf.
            reach = (
                f"planning over {steps} steps ({covered:.0f} s), the largest solve this controller "
                "builds, which does not reach the end of that coast. Raise t_step in "
                "Settings > Controller: it spans the same window in fewer steps, so the horizon "
                "lengthens and the solve does not grow"
            )
        else:
            # The seconds bound is what truncated, and no setting moves it: the
            # horizon is capped at _HORIZON_CAP_S seconds however t_step slices
            # them, so this coast is outside the range the controller plans for
            # whatever the configuration. Offering a lever here would be advice
            # that cannot work.
            reach = (
                f"planning over {steps} steps ({covered:.0f} s), the furthest ahead this controller "
                f"plans at any setting. A coast past {_HORIZON_CAP_S:.0f} s is longer than this "
                "controller is built to brake, so it is the model that is out of range and not the "
                "configuration; refit this grill with controller/update_mpc.py"
            )
        print(
            f"[mpc] configured prediction horizon is {horizon:.0f} s but the chamber keeps rising "
            f"{coast} after a full fuel cut; {reach}."
        )


def requires_modules(config):
    """Import names `Controller(config, ...)` will need but a base install lacks.

    do-mpc's CasADi/IPOPT stack publishes no Linux-ARM wheel and so builds from
    source, which is why it is a PiFire *optional* dependency -- the `mpc` extra
    in pyproject.toml -- installed only when someone selects this controller.

    Every MPC config needs it. The gate used to exempt `policy=net` when the
    artifact's calibration matched, but that answer is only true until the
    calibration changes: a learned model, or any hand-edited thermal parameter,
    makes the artifact stale and drops the controller onto the NLP mid-cook,
    where the import would fail on a machine this gate had already cleared.
    An answer that expires is worse than a conservative one.
    """
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
        # How long the output has been frozen. A single failure is a hiccup the
        # held command covers; a run of them means nothing is steering.
        self._consecutive_policy_failures = 0
        self._history = collections.deque(maxlen=_HISTORY_MAX)
        self._model_revision = 0
        self._model_meta = None  # provenance of an adopted model, or None

        n_delay = int(cfg["n_delay"])
        # The chain length everything below is BUILT at, kept so `restore_model`
        # can refuse a snapshot that disagrees with it. Nothing built here is
        # rebuilt afterwards, so the config key on its own stops describing the
        # running model the moment a restore writes a different value into it.
        self._built_n_delay = n_delay

        # The horizon everything below is BUILT at: the configured n_horizon
        # raised, where this model's coast needs it, towards a length that
        # contains the end of a full brake, and held to the largest NLP this
        # controller has a measured solve time for. Derived here rather than
        # written into cfg so a later, quicker model shortens it again and the
        # operator's setting keeps meaning what they set. Where that step bound
        # leaves the horizon short of the coast, `_warn_about_model` has already
        # said so above.
        self._built_n_horizon = built_n_horizon(cfg, n_horizon=cfg["n_horizon"], t_step=cfg["t_step"])

        # State/disturbance estimator (independent of the policy). EKF linearizes
        # the nonlinear radiative term each step (default); MHE solves an NLP; KF
        # is linear-only. All expose update(Q_applied, y) -> state estimate and are
        # discretized at the control period so faster re-solves track elapsed time.
        est_kind = str(cfg.get("estimator", "ekf")).lower()
        if est_kind == "kf":
            self.estimator = GreyBoxKF(
                C_c=cfg["C_c"],
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
                C_c=cfg["C_c"],
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
                C_c=cfg["C_c"],
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
            self._net = _load_net_policy(cfg, self._built_n_horizon)
        if self._net is None:
            self._build_nlp(cfg, n_delay, self._built_n_horizon)

        # Optional data logging for offline calibration (update_mpc.py): one
        # (time_s, temp_c, Q) row per control step. Logs internal Celsius.
        self._log_path = cfg["log_path"] if cfg.get("log_data") else None
        if self._log_path and (not os.path.exists(self._log_path) or os.path.getsize(self._log_path) == 0):
            try:
                with open(self._log_path, "a") as f:
                    f.write("time_s,temp_c,Q\n")
            except OSError:
                self._log_path = None  # disable logging if the path is unwritable

    def _build_nlp(self, cfg, n_delay, n_horizon):
        """Build the do-mpc NLP policy (lazily imports do_mpc/CasADi/IPOPT).

        `n_horizon` is the effective horizon, which is at least cfg's and may
        be longer; cfg's own value is the floor it was derived from and is
        never the length built.
        """
        import do_mpc

        self.model = build_do_mpc_model(
            C_c=cfg["C_c"],
            h_amb=cfg["h_amb"],
            T_amb=cfg["T_amb"],
            theta=float(cfg["theta"]),
            n_delay=n_delay,
            K_Q=float(cfg["K_Q"]),
            sigma=float(cfg["sigma"]),
        )
        self.mpc = do_mpc.controller.MPC(self.model)
        self.mpc.set_param(
            n_horizon=int(n_horizon),
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
            for k in range(int(n_horizon) + 1):
                tvp_template["_tvp", k, "T_set"] = self._set_point_c
            return tvp_template

        self.mpc.set_tvp_fun(tvp_fun)
        self.mpc.setup()

        x0 = np.zeros((n_delay + 2, 1))
        x0[n_delay, 0] = cfg["T_amb"]  # T_c
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
            # Non-zero means update() is returning a held command rather than a
            # computed one, so the number this reports is how many control
            # periods the grill has been running open-loop.
            "policy_failures": int(self._consecutive_policy_failures),
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
            # None until a model has been adopted this process (fresh install,
            # or before the first fit completes): a model identified at one
            # temperature does not describe another, so the band it was fit
            # over travels with the fit error rather than being assumed global.
            "model": None
            if self._model_meta is None
            else {
                "band_c": [_finite_float(v) for v in self._model_meta["band_c"]],
                "rmse": _optional_float(self._model_meta["rmse"]),
            },
        }

    #: The model structure a snapshot describes, shared with every other thing
    #: that outlives the process and claims to describe this model (see
    #: mpc_model.MODEL_SCHEMA) rather than counted separately here -- two
    #: numbers meaning the same thing is how they drift.
    #:
    #: A version 1 record describes the two-lump model this controller no
    #: longer has: its C_f and h_fc name nothing, and the C_c, h_amb and K_Q
    #: beside them were fitted against a chamber that was fed through a
    #: firepot, so they are not this model's parameters under a shorter name.
    #: Applying the subset that still has matching keys would put a stranger's
    #: numbers on a live grill, so `restore_model` refuses the record and says
    #: so; the next cook refits from scratch, which is what a fresh install
    #: does anyway.
    _MODEL_SCHEMA = MODEL_SCHEMA
    _MODEL_PARAM_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

    def _adopt_model(self, params, *, rmse, samples, band_c, nfev=None):
        """Take `params` into the running config and bump the revision.

        Only `_MODEL_PARAM_KEYS` cross into the config, so a fitter's own
        bookkeeping -- `converged`, `nfev` -- travels alongside a fit without
        ever becoming part of the model.

        Rebuilding the NLP is the CALLER's business: adoption between cooks
        needs no rebuild because the next Hold builds fresh, and adoption
        during one is rate-limited elsewhere.
        """
        self.cfg.update({k: params[k] for k in self._MODEL_PARAM_KEYS if k in params})
        self._model_revision += 1
        self._model_meta = {
            # Provenance, not a comparison input: what this model achieved on
            # the cook it was fit from. model_promotion.evaluate() always
            # RECOMPUTES the incumbent's error on the candidate's own data
            # before adopting anything new, so this stored value is never fed
            # into that comparison -- it is a record, not a gate.
            "rmse": float(rmse),
            "samples": int(samples),
            "band_c": [float(band_c[0]), float(band_c[1])],
            # How hard the solve worked for these numbers. A converged solve
            # that took one evaluation moved nowhere, and reads identically to
            # a hard-won one in every other field. None -- never 0 -- stands in
            # for "not recorded", so an unknown effort cannot be misread as a
            # measured absence of effort.
            "nfev": None if nfev is None else int(nfev),
        }

    def get_model_snapshot(self):
        if self._model_meta is None:
            return None
        return {
            "version": self._MODEL_SCHEMA,
            "revision": int(self._model_revision),
            "params": {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS},
            **self._model_meta,
            # Rebuilt rather than shared: every other value here is an
            # immutable scalar, so a shallow copy of this mapping would leave
            # the caller holding the live list a later adoption overwrites.
            "band_c": list(self._model_meta["band_c"]),
        }

    def restore_model(self, snapshot):
        from controller.model_promotion import PROMOTION_BOUNDS, n_delay_is_whole

        if not isinstance(snapshot, dict):
            return False
        version = snapshot.get("version")
        if version != self._MODEL_SCHEMA:
            # Said out loud rather than refused quietly. A grill that has been
            # learning for a season arrives here once after the upgrade, and
            # the operator is owed the reason its model went back to the
            # shipped defaults instead of finding it in the overshoot.
            print(
                f"[mpc] discarding a version {version!r} model snapshot: this controller "
                f"stores version {self._MODEL_SCHEMA}, the single-lump model. The next "
                "cook refits from scratch."
            )
            return False
        params, revision = snapshot.get("params"), snapshot.get("revision")
        if not isinstance(params, dict) or not isinstance(revision, int):
            return False
        for key, (lo, hi) in PROMOTION_BOUNDS.items():
            value = params.get(key)
            try:
                value = float(value)
            except TypeError, ValueError:
                return False
            if not (lo <= value <= hi):
                return False
            # n_delay sizes the estimator's lag-state chain one whole state at
            # a time, so a fractional count is nonsense even though it lies
            # inside the numeric bounds above. Imports model_promotion's own
            # predicate rather than re-deriving it, so the two cannot drift
            # apart on what "whole" means.
            if key == "n_delay" and not n_delay_is_whole(value):
                return False
        # n_delay is not a parameter this can adopt: it sizes the estimator, the
        # NLP and the net policy, all of which were built in __init__ and none
        # of which is rebuilt here. Adopting it would leave cfg["n_delay"]
        # describing a chain the running model does not have, and every
        # consumer that reads it back -- longest_braking_distance, the promotion
        # gate's horizon requirement, _warn_about_model -- would size against
        # the snapshot instead of against what is actually solving. Refused out
        # loud in the same shape as the schema mismatch above, and for the same
        # reason: the operator is owed the reason the season's model went back
        # to the shipped defaults.
        snapshot_n_delay = int(float(params["n_delay"]))
        if snapshot_n_delay != self._built_n_delay:
            print(
                f"[mpc] discarding a model snapshot fitted at n_delay {snapshot_n_delay}: this "
                f"controller was built with {self._built_n_delay} lag states and does not rebuild "
                "them on restore. The next cook refits from scratch."
            )
            return False
        self.cfg.update({k: float(params[k]) for k in self._MODEL_PARAM_KEYS if k in params})
        # Continue the persisted counter rather than starting a new one: the
        # store rejects a revision that does not advance, permanently.
        self._model_revision = revision
        self._model_meta = {
            # Provenance only, exactly as in _adopt_model. None -- never 0.0,
            # and never inf -- stands in for "unknown": 0.0 would read as a
            # perfect fit and, wired into evaluate() as an incumbent_rmse,
            # would permanently refuse any replacement, while inf is a float
            # the store cannot persist at all (its validator encodes with
            # allow_nan=False). evaluate() refuses a None incumbent_rmse
            # outright, which is the honest answer for an error nobody
            # measured.
            "rmse": _optional_float(snapshot.get("rmse")),
            "samples": int(snapshot.get("samples", 0)),
            "band_c": [float(v) for v in snapshot.get("band_c", (0.0, 0.0))],
            # Carried back across the store so the field means the same thing
            # on both sides of it. A snapshot written before this field existed
            # has no effort to report, which is None, not zero.
            "nfev": _optional_int(snapshot.get("nfev")),
        }
        return True

    def cook_history(self):
        """The cook's (time_s, temp_c, Q_applied) rows, oldest first."""
        return list(self._history)

    def refit_from_cook(self, history=None):
        """Refit the thermal model from a finished cook and judge the result.

        Between cooks only: a refit re-simulates the whole history once per
        least-squares evaluation, so it belongs nowhere near the control path.
        It runs synchronously on its caller's thread and takes seconds, bounded
        by `_HISTORY_MAX` -- see HoldMode._refit_model for why spending them at
        teardown is safe. An accepted model changes `cfg` but rebuilds nothing:
        it reaches the grill through the next cook's restore.
        """
        from controller.model_promotion import evaluate
        from controller.update_mpc import fit_params, fit_quality, identifiability

        rows = list(history if history is not None else self._history)
        if len(rows) < _REFIT_MIN_SAMPLES:
            return _Verdict(False, f"only {len(rows)} samples; need {_REFIT_MIN_SAMPLES}")

        started = time.perf_counter()
        t = np.array([r[0] for r in rows], dtype=float)
        temp = np.array([r[1] for r in rows], dtype=float)
        Q = np.array([r[2] for r in rows], dtype=float)
        t = t - t[0]

        T_amb = float(self.cfg["T_amb"])
        try:
            fitted = fit_params(
                t,
                temp,
                Q,
                T_amb=T_amb,
                init=dict(_REFIT_INIT),
                sigma=float(self.cfg["sigma"]),
                n_delay=int(self.cfg["n_delay"]),
            )
            # The candidate starts from a fixed reference, but it is judged
            # against the model actually driving the grill: the question this
            # answers is whether to replace THAT, on this cook's own data.
            incumbent = {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS}
            cand_rmse, _ = fit_quality(t, temp, Q, fitted, T_amb=T_amb)
            inc_rmse, _ = fit_quality(t, temp, Q, incumbent, T_amb=T_amb)
            # How much this cook actually determined, measured at the point the
            # solve landed on. Six more simulations against the solve's own
            # hundreds, and the only thing asked here that the fit residual
            # cannot say -- a flat cook fits itself perfectly and pins nothing.
            ident = identifiability(t, Q, fitted, T_amb=T_amb, T0=float(temp[0]))
        except (ValueError, FloatingPointError) as e:
            return _Verdict(False, f"fit failed: {e}")

        # A solve that ran out of evaluations reports its best point so far, and
        # that point has not been shown to be a minimum -- so it is refused.
        # The converse is not available: scipy calls a stalled step and a
        # stalled cost "converged" too, and a one-evaluation solve that moved
        # nowhere reports the same flag as a hard-won fit. Convergence can only
        # veto here; what earns a promotion is the error comparison and the
        # bounds below.
        if not fitted["converged"]:
            print(
                f"[mpc] refit: abandoned after {fitted['nfev']} evaluations over "
                f"{len(rows)} samples in {time.perf_counter() - started:.1f} s"
            )
            return _Verdict(False, f"the solve did not converge within {fitted['nfev']} evaluations")

        verdict = evaluate(
            fitted,
            incumbent,
            candidate_rmse=cand_rmse,
            incumbent_rmse=inc_rmse,
            identifiability=ident,
            n_horizon=int(self.cfg["n_horizon"]),
            t_step=float(self.cfg["t_step"]),
        )
        print(
            f"[mpc] refit: {verdict.reason} (candidate RMSE {cand_rmse:.2f} C, "
            f"incumbent {inc_rmse:.2f} C, {fitted['nfev']} evaluations over "
            f"{len(rows)} samples in {time.perf_counter() - started:.1f} s)"
        )
        if verdict.accepted and verdict.horizon_needed:
            # Reported only where it has a consequence. A refused model is not
            # what the next build plans with, so what horizon it would have
            # wanted is not a fact about this grill.
            print(
                f"[mpc] refit: this model's coast needs {verdict.horizon_needed} prediction steps "
                f"at t_step {self.cfg['t_step']:.0f} s, past the configured n_horizon "
                f"{int(self.cfg['n_horizon'])}; the next build plans over that many."
            )
        if verdict.accepted:
            self._adopt_model(
                fitted,
                rmse=cand_rmse,
                samples=len(rows),
                band_c=(float(temp.min()), float(temp.max())),
                nfev=fitted["nfev"],
            )
        return verdict

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
        applied_Q = self._applied_Q
        x_hat = self.estimator.update(applied_Q, y)
        self._x_hat = x_hat
        # The rate the plant actually received over the interval ending now --
        # the same value just handed to the estimator, not the Q computed below
        # for the next interval -- so a fit against this row credits the plant,
        # not the model, for a paused or clamped auger.
        self._history.append((time.time(), float(y), float(applied_Q)))
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
            if self._consecutive_policy_failures:
                print(f"[mpc] policy recovered after {self._consecutive_policy_failures} failed step(s)")
            self._consecutive_policy_failures = 0
            self._last_solve_failed = False
        except Exception as e:
            # Holding the last move is the right reflex for ONE bad solve: the
            # control loop must not break, and the previous firing rate is the
            # best available guess for the next few seconds. It is the wrong
            # answer forever. A policy that raises on every step -- an artifact
            # whose state vector does not match the model is the way that
            # happens -- leaves the grill on a frozen command, and this except
            # is what makes that invisible, so the failure has to announce
            # itself. Reported at a widening interval rather than every step,
            # since a per-step message on a 5 s loop buries the first one, and
            # surfaced in get_status() so it is visible without reading logs.
            Q = self._last_Q
            self._last_solve_failed = True
            self._consecutive_policy_failures += 1
            n = self._consecutive_policy_failures
            if n == 1 or n in (10, 60) or n % 300 == 0:
                print(
                    f"[mpc] policy has failed {n} consecutive step(s) ({type(e).__name__}: {e}); "
                    f"holding the last firing rate {Q:.1f}. The grill is not being controlled to "
                    "setpoint -- check the policy artifact and the model configuration."
                )
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
