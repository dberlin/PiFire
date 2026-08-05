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

from common.control_trace import ActuationMode
from controller.base import ControllerBase, MpcFailureState, MpcTraceDiagnostics
from controller.model_promotion import Verdict as _Verdict
from controller.model_promotion import _MAX_CONFIGURABLE_HORIZON_S, built_n_horizon, longest_braking_distance
from controller.mpc_model import build_do_mpc_model, GreyBoxKF, GreyBoxEKF, GreyBoxMHE, MODEL_SCHEMA
from controller.mpc_allocator import AllocationResult, allocate, normalized_load_from_auger_duty

_DEFAULTS = dict(
    # R_dQ (firing-move penalty) kept low: 1.0 was over-damped -> sluggish rise AND
    # a looser steady band. 0.1 gives ~4x faster setpoint-step rise and a tighter
    # band, at a modest step-overshoot increase.
    n_horizon=24,
    t_step=25.0,
    control_period=5.0,
    Q_w=1.0,
    R_dQ=0.1,
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
    K_Q=350.0,
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
        if net.model_schema != MODEL_SCHEMA:
            print(
                f"[mpc] net policy at {path} uses model schema {net.model_schema}, but normalized combustion "
                f"load requires schema {MODEL_SCHEMA}; using NLP -- regenerate it with tools/regenerate_mpc_net.py."
            )
        elif net.input_dim != net.expected_input_dim(cfg):
            print(
                f"[mpc] net policy at {path} takes a {net.input_dim}-wide input but this model "
                f"produces {net.expected_input_dim(cfg)}; it was trained against a different "
                "state vector. Using NLP -- regenerate it with tools/regenerate_mpc_net.py."
            )
        else:
            print("[mpc] net policy calibration does not match config; using NLP")
        return None


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
        # What decides whether the operator has anything to change is whether ANY
        # setting spans this coast, not which of the two caps truncated the
        # raise. Both caps hold down the raise only, so a configured horizon is
        # always built in full -- which means a coast past the caps can still be
        # inside a configuration, and saying otherwise sends an operator who
        # could fix this away to refit a model that is not the problem.
        if covered >= brake:
            reach = f"planning over {steps} steps ({covered:.0f} s) instead"
        elif brake <= _MAX_CONFIGURABLE_HORIZON_S:
            # A setting exists, so the useful thing to say is which one and how
            # far. Both levers are named because either reaches it, and t_step
            # is marked as the cheaper because it spans the same window in fewer
            # steps -- n_horizon buys the same seconds by growing the NLP. It is
            # offered rather than taken: t_step also re-discretizes the model the
            # MPC solves, a larger change to controller behaviour than
            # lengthening the window and not one to make on a grill's behalf.
            reach = (
                f"planning over {steps} steps ({covered:.0f} s), which does not reach the end of "
                f"that coast. Raise n_horizon and/or t_step in Settings > Controller until their "
                f"product reaches {brake:.0f} s; t_step is the cheaper of the two, since a longer "
                "step spans the same window in fewer steps and does not grow the solve"
            )
        else:
            # Past every reachable configuration, so there is no lever to offer
            # and naming one would be advice that cannot work.
            reach = (
                f"planning over {steps} steps ({covered:.0f} s). No setting reaches the end of this "
                f"coast -- the furthest this controller can be configured to plan is "
                f"{_MAX_CONFIGURABLE_HORIZON_S:.0f} s -- so it is the model that is out of range "
                "and not the configuration; refit this grill with controller/update_mpc.py"
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
        self.u_max = float(cycle_data.get("u_max", 0.9))

        self._set_point_c = 0.0
        self._last_combustion_load = 0.0
        self._applied_combustion_load = 0.0
        self._x_hat = None
        self._policy_u_prev = 0.0
        self._last_raw_combustion_load = 0.0
        self._last_solve_failed = False
        # How long the output has been frozen. A single failure is a hiccup the
        # held command covers; a run of them means nothing is steering.
        self._consecutive_policy_failures = 0
        self._history = collections.deque(maxlen=_HISTORY_MAX)
        self._model_revision = 0
        self._model_meta = None  # provenance of an adopted model, or None
        self._trace_diagnostics = None
        self._trace_allocation: AllocationResult | None = None

        # Everything the thermal parameters size -- the estimator, the horizon
        # and the policy -- is built through the same call `restore_model` uses,
        # so a model that arrives from the store reaches the solver the same way
        # a configured one does.
        self.estimator, self._net, self.model, self.mpc, self._built_n_horizon = self._build_for(cfg)

    def _build_for(self, cfg):
        """Everything the thermal parameters size, built from `cfg`.

        Returns the parts rather than assigning them, so a caller adopting a
        model can build against the merged config and commit only once every
        part exists -- a failed build leaves the running controller untouched
        rather than half-replaced.

        The horizon is derived here rather than written into cfg: the
        configured n_horizon is raised, where this model's coast needs it,
        towards a length that contains the end of a full brake, and held to the
        largest NLP this controller has a measured solve time for. Keeping cfg's
        own value means a later, quicker model shortens the horizon again and
        the operator's setting keeps meaning what they set.
        """
        n_delay = int(cfg["n_delay"])
        n_horizon = built_n_horizon(cfg, n_horizon=cfg["n_horizon"], t_step=cfg["t_step"])
        estimator = self._build_estimator(cfg, n_delay)
        # Firing-rate policy. 'net' uses the pure-numpy neural approximation (no
        # IPOPT/CasADi); it falls back to the NLP if the artifact is missing or
        # its calibration does not match this config -- which is what a model
        # learned since the artifact was fit does.
        net, model, mpc = None, None, None
        if str(cfg.get("policy", "nlp")).lower() == "net":
            net = _load_net_policy(cfg, n_horizon)
        if net is None:
            model, mpc = self._build_nlp(cfg, n_delay, n_horizon)
        return estimator, net, model, mpc, n_horizon

    def _build_estimator(self, cfg, n_delay):
        """State/disturbance estimator (independent of the policy). EKF linearizes
        the nonlinear radiative term each step (default); MHE solves an NLP; KF
        is linear-only. All expose update(Q_applied, y) -> state estimate and are
        discretized at the control period so faster re-solves track elapsed time.
        """
        est_kind = str(cfg.get("estimator", "ekf")).lower()
        if est_kind == "kf":
            return GreyBoxKF(
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
        if est_kind == "ekf":
            return GreyBoxEKF(
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
        return GreyBoxMHE(
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

    def _build_nlp(self, cfg, n_delay, n_horizon):
        """Build the do-mpc NLP policy (lazily imports do_mpc/CasADi/IPOPT).

        `n_horizon` is the effective horizon, which is at least cfg's and may
        be longer; cfg's own value is the floor it was derived from and is
        never the length built.
        """
        import do_mpc

        model = build_do_mpc_model(
            C_c=cfg["C_c"],
            h_amb=cfg["h_amb"],
            T_amb=cfg["T_amb"],
            theta=float(cfg["theta"]),
            n_delay=n_delay,
            K_Q=float(cfg["K_Q"]),
            sigma=float(cfg["sigma"]),
        )
        mpc = do_mpc.controller.MPC(model)
        mpc.set_param(
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
        T_c = model.x["T_c"]
        T_set = model.tvp["T_set"]
        mpc.set_objective(mterm=cfg["Q_w"] * (T_c - T_set) ** 2, lterm=cfg["Q_w"] * (T_c - T_set) ** 2)
        mpc.set_rterm(combustion_load=cfg["R_dQ"])
        mpc.bounds["lower", "_u", "combustion_load"] = 0.0
        mpc.bounds["upper", "_u", "combustion_load"] = 1.0

        tvp_template = mpc.get_tvp_template()

        def tvp_fun(t_now):
            for k in range(int(n_horizon) + 1):
                tvp_template["_tvp", k, "T_set"] = self._set_point_c
            return tvp_template

        mpc.set_tvp_fun(tvp_fun)
        mpc.setup()

        x0 = np.zeros((n_delay + 2, 1))
        x0[n_delay, 0] = cfg["T_amb"]  # T_c
        mpc.x0 = x0
        mpc.set_initial_guess()
        return model, mpc

    def set_target(self, set_point):
        self.set_point = set_point
        self._set_point_c = _to_c(set_point, self.units)

    def get_control_period(self):
        return float(self.cfg["control_period"])

    def actuation_mode(self) -> ActuationMode:
        return ActuationMode.FRAMED_PULSE

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    def get_status(self):
        return {
            "set_point": _finite_float(self.set_point),
            "set_point_c": _finite_float(self._set_point_c),
            "last_combustion_load": _finite_float(self._last_combustion_load),
            "applied_combustion_load": _finite_float(self._applied_combustion_load),
            "policy": "net" if self._net is not None else "nlp",
            # Non-zero means update() is returning a held command rather than a
            # computed one, so the number this reports is how many control
            # periods the grill has been running open-loop.
            "policy_failures": int(self._consecutive_policy_failures),
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
        needs no rebuild because the next Hold's `restore_model` builds against
        the model it restores, and adoption during one is rate-limited
        elsewhere.
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
        # n_delay is the one parameter here a refit never learns -- `fit_params`
        # is handed the configured chain length and fits the rest against it --
        # so a snapshot that disagrees came from an install the operator has
        # since reconfigured. The whole record goes, not just the count:
        # adopting it would override a setting the operator deliberately
        # changed, and adopting the rest without it would put parameters fitted
        # against one lag chain onto a different one, where the dead time they
        # absorbed is not the dead time being solved. Refused out loud in the
        # same shape as the schema mismatch above, and for the same reason: the
        # operator is owed the reason the season's model went back to the
        # shipped defaults.
        configured_n_delay = int(self.cfg["n_delay"])
        snapshot_n_delay = int(float(params["n_delay"]))
        if snapshot_n_delay != configured_n_delay:
            print(
                f"[mpc] discarding a model snapshot fitted at n_delay {snapshot_n_delay}: this "
                f"controller is configured for {configured_n_delay} lag states, and a model fitted "
                "against a different chain is not this model's parameters. The next cook refits "
                "from scratch."
            )
            return False
        merged = dict(self.cfg)
        merged.update({k: float(params[k]) for k in self._MODEL_PARAM_KEYS if k in params})
        # The restored parameters have to reach the estimator, the horizon and
        # the policy, not just the config those three were sized from -- a
        # config-only restore leaves the season's learning inert and, where the
        # restored model coasts further than the shipped one, plans over a
        # horizon that stops short of the brake. Built before anything is
        # committed so a build that fails leaves the controller solving the
        # model it already had.
        try:
            rebuilt = self._build_for(merged)
        except Exception as exc:
            print(f"[mpc] a stored model could not be built ({exc}); keeping the model this controller started with.")
            return False
        self.cfg.update(merged)
        self.estimator, self._net, self.model, self.mpc, self._built_n_horizon = rebuilt
        # The state estimate belonged to the estimator just replaced. The new
        # one starts from its own initial state, so there is nothing to report
        # until it has seen a measurement.
        self._x_hat = None
        # Said for the model that will actually solve. __init__'s own call saw
        # only the configured parameters, which for a grill that has been
        # learning are not the ones about to steer it.
        _warn_about_model(self.cfg)
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
            # A solve that ran out of evaluations reports its best point so
            # far, and that point has not been shown to be a minimum -- so it
            # is refused. The converse is not available: scipy calls a stalled
            # step and a stalled cost "converged" too, and a one-evaluation
            # solve that moved nowhere reports the same flag as a hard-won
            # fit. Convergence can only veto here; what earns a promotion is
            # the error comparison and the bounds below.
            #
            # It vetoes before anything is measured, so no statistic is ever
            # taken at a point that is already refused -- including at one the
            # model cannot be simulated at, which a diverging solve's best
            # point can be.
            if not fitted["converged"]:
                print(
                    f"[mpc] refit: abandoned after {fitted['nfev']} evaluations over "
                    f"{len(rows)} samples in {time.perf_counter() - started:.1f} s"
                )
                return _Verdict(False, f"the solve did not converge within {fitted['nfev']} evaluations")
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
        """Recover the normalized applied load from measured mean auger duty."""
        self._applied_combustion_load = normalized_load_from_auger_duty(applied.ratio, u_max=self.u_max)

    def update(self, current):
        y = _to_c(current, self.units)
        applied_combustion_load = self._applied_combustion_load
        x_hat = self.estimator.update(applied_combustion_load, y)
        self._x_hat = x_hat
        state_values = tuple(float(value) for value in np.asarray(x_hat).reshape(-1))
        state_names = tuple(f"q{index}" for index in range(int(self.cfg["n_delay"]))) + ("T_c", "d")
        disturbance = state_values[-1]
        self._history.append((time.time(), float(y), float(applied_combustion_load)))
        self._policy_u_prev = self._applied_combustion_load

        solve_start = time.monotonic()
        failure_state = MpcFailureState.SUCCESS
        failure_error = None
        try:
            if self._net is not None:
                raw_policy = (
                    self._net.firing_rate_raw if hasattr(self._net, "firing_rate_raw") else self._net.firing_rate
                )
                combustion_load = raw_policy(x_hat, self._policy_u_prev, self._set_point_c)
            else:
                combustion_load = float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])
        except Exception as error:
            combustion_load = self._last_combustion_load
            failure_state = MpcFailureState.POLICY_EXCEPTION
            failure_error = error
        finally:
            solve_end = time.monotonic()

        if failure_state is MpcFailureState.SUCCESS:
            if self._consecutive_policy_failures:
                print(f"[mpc] policy recovered after {self._consecutive_policy_failures} failed step(s)")
            self._consecutive_policy_failures = 0
            self._last_solve_failed = False
            raw_firing_load = float(combustion_load)
            self._last_raw_combustion_load = raw_firing_load
        else:
            self._last_solve_failed = True
            self._consecutive_policy_failures += 1
            n = self._consecutive_policy_failures
            if n == 1 or n in (10, 60) or n % 300 == 0:
                print(
                    f"[mpc] policy has failed {n} consecutive step(s) ({type(failure_error).__name__}: {failure_error}); "
                    f"holding normalized combustion load {combustion_load:.3f}. The grill is not being controlled to "
                    "setpoint -- check the policy artifact and the model configuration."
                )
            raw_firing_load = None

        combustion_load = float(np.clip(combustion_load, 0.0, 1.0))
        self._last_combustion_load = combustion_load
        self._applied_combustion_load = combustion_load
        allocation = allocate(
            combustion_load,
            u_max=self.u_max,
            fan_min_pct=self.cfg["fan_min_pct"],
            fan_max_pct=self.cfg["fan_max_pct"],
            enable_fan=bool(self.cfg["enable_fan_input"]),
        )
        auger = allocation.auger_duty
        self._trace_allocation = allocation
        fan_duty = allocation.fan_duty
        if raw_firing_load is None:
            equilibrium = None
            residual_move = None
        else:
            equilibrium = (
                self.cfg["h_amb"] * (self._set_point_c - self.cfg["T_amb"])
                + self.cfg["sigma"] * ((self._set_point_c + 273.15) ** 4 - (self.cfg["T_amb"] + 273.15) ** 4)
                - disturbance
            ) / self.cfg["K_Q"]
            equilibrium = float(equilibrium)
            residual_move = raw_firing_load - equilibrium
        self._trace_diagnostics = MpcTraceDiagnostics(
            state_names=state_names,
            state_values=state_values,
            disturbance_estimate=disturbance,
            model_revision=self._model_revision,
            model_provenance="adopted" if self._model_meta is not None else "configured",
            raw_policy_firing_load=raw_firing_load,
            equilibrium_feed_forward=equilibrium,
            residual_move=residual_move,
            bounded_firing_load=combustion_load,
            applied_combustion_load=applied_combustion_load,
            policy_kind="net" if self._net is not None else "nlp",
            failure_state=failure_state,
            consecutive_policy_failures=self._consecutive_policy_failures,
            solve_start_monotonic=solve_start,
            solve_end_monotonic=solve_end,
            solve_duration_seconds=solve_end - solve_start,
        )
        return {"cycle_ratio": auger, "fan": {"duty": fan_duty}}

    def trace_diagnostics(self) -> MpcTraceDiagnostics | None:
        return self._trace_diagnostics

    def trace_allocation(self) -> AllocationResult | None:
        return self._trace_allocation
