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
import copy
import json
import math
import os
from collections.abc import Mapping
import time

import numpy as np
# do_mpc (CasADi/IPOPT) is imported lazily only when the NLP policy is built; the
# net policy + EKF path is pure numpy/scipy and never imports it.

from controller.base import ControllerBase, MpcFailureState, MpcTraceDiagnostics
from controller.model_promotion import Verdict as _Verdict
from controller.model_promotion import feasibility_report
from controller.mpc_model import (
    MODEL_SCHEMA,
    GreyBoxEKF,
    GreyBoxKF,
    GreyBoxMHE,
    build_do_mpc_model,
    steady_combustion_load,
)
from controller.mpc_allocator import AllocationResult, allocate, normalized_load_from_auger_duty
from common.controller_model_state import MAX_SNAPSHOT_BYTES
from controller.linear_mpc.adaptation import AdaptationPolicy, OnlineAdaptation
from controller.linear_mpc.arx import ScheduledARX, ScheduledARXConfig
from controller.linear_mpc.contracts import FrameObservation, ModelUpdate
from controller.linear_mpc.grey_box import GreyBoxPredictionAdapter
from controller.linear_mpc.policy import LinearMPC, LinearMPCConfig

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
    # Experimental: the existing grey-box controller remains authoritative
    # unless a scheduled ARX challenger has earned an explicit promotion.
    enable_online_adaptation=False,
)

# One row per control period. At the 5 s default that is ~12 hours, which is
# longer than any single cook; a longer one loses its beginning rather than
# its end, and the end is what describes the grill's current state. This is

# also what bounds a refit: the longest cook the fit can ever be handed off
# the teardown path is one full history.
_HISTORY_MAX = 8640
# The online model is identified on 20 s framed-pulse evidence.  Keep the
# independently validated 600 s / 30-frame horizon and bakeoff penalties out
# of legacy grey-box controller settings.
_SCHEDULED_ARX_LINEAR_CONFIG = LinearMPCConfig(
    horizon_steps=30,
    temperature_weight=1.0,
    terminal_weight=4.0,
    move_weight=0.05,
    tolerance=1e-3,
)

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
    """Cast to a finite float, or None when there is no number to report."""
    try:
        value = float(value)
    except TypeError, ValueError:
        return None
    return value if math.isfinite(value) else None


def _online_count(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _online_optional_string(value, name):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be null or a non-blank string")
    return value


def _online_optional_score(value, name):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be null or finite")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be null or finite")
    return value


_EVALUATION_KEYS = frozenset(
    (
        "decision_id",
        "evaluated_at_s",
        "role_generation",
        "promoted",
        "committed",
        "consecutive_wins",
        "rejection_reasons",
        "incumbent_prediction_score",
        "challenger_prediction_score",
        "incumbent_braking_score",
        "challenger_braking_score",
        "sample_count",
        "prospective_digest",
    )
)
_LIFECYCLE_KEYS = frozenset(
    (
        "event",
        "model_revision",
        "provenance",
        "detail",
        "model_kind",
        "model_schema",
        "role_generation",
        "snapshot_digest",
        "parameters",
    )
)


def _online_evaluation(value):
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _EVALUATION_KEYS:
        raise ValueError("last_evaluation has an invalid schema")
    if not isinstance(value["decision_id"], str) or not value["decision_id"]:
        raise ValueError("evaluation decision_id is invalid")
    evaluated = _online_optional_score(value["evaluated_at_s"], "evaluated_at_s")
    if evaluated is None:
        raise ValueError("evaluated_at_s is required")
    for key in ("role_generation", "consecutive_wins", "sample_count"):
        _online_count(value[key], key)
    for key in ("promoted", "committed"):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key} must be bool")
    reasons = value["rejection_reasons"]
    if not isinstance(reasons, (list, tuple)) or any(not isinstance(reason, str) or not reason for reason in reasons):
        raise ValueError("evaluation rejection_reasons are invalid")
    for key in (
        "incumbent_prediction_score",
        "challenger_prediction_score",
        "incumbent_braking_score",
        "challenger_braking_score",
    ):
        _online_optional_score(value[key], key)
    _online_optional_string(value["prospective_digest"], "prospective_digest")
    return copy.deepcopy(dict(value))


def _online_lifecycle_metadata(value):
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _LIFECYCLE_KEYS:
        raise ValueError("last_lifecycle has an invalid schema")
    for key in ("event", "provenance", "detail", "model_kind", "model_schema", "snapshot_digest"):
        _online_optional_string(value[key], key)
        if value[key] is None:
            raise ValueError(f"{key} is required")
    for key in ("model_revision", "role_generation"):
        _online_count(value[key], key)
    if value["parameters"] != () and value["parameters"] != []:
        raise ValueError("lifecycle parameters must be empty")
    return copy.deepcopy(dict(value))


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

    The net approximates the NLP policy at one configured planning horizon, so
    an artifact trained for any other horizon must be rejected.
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
    return net


class _GreyBoxAdaptiveModel:
    """Immutable grey-box forecast origin with the coordinator model protocol."""

    _SCHEMA = "grey-box-adapter/v1"

    def __init__(self, adapter):
        self._adapter = adapter

    @classmethod
    def from_controller(cls, controller):
        return cls(GreyBoxPredictionAdapter.from_controller(controller))

    def track(self, observation):
        # The frozen origin intentionally does not learn.  Its one-step
        # innovation is the chamber origin error, which is finite by adapter
        # construction and sufficient for prequential comparison.
        predicted = float(self._adapter.chamber_origin_c)
        return ModelUpdate(predicted, observation.temp_c, observation.temp_c - predicted, False)

    observe = track

    def affine_prediction(self, horizon_steps, q_previous, ambient_future):
        return self._adapter.affine_prediction(horizon_steps, q_previous, ambient_future)

    def snapshot(self):
        adapter = self._adapter
        return {
            "schema": self._SCHEMA,
            "state": adapter.state.tolist(),
            "transition": adapter.transition.tolist(),
            "q_gain": adapter.q_gain.tolist(),
            "ambient_gain": adapter.ambient_gain.tolist(),
            "affine_offset": adapter.affine_offset.tolist(),
            "radiation_constant_gain": adapter.radiation_constant_gain.tolist(),
            "temperature_index": adapter.temperature_index,
            "radiation_sigma": adapter.radiation_sigma,
            "radiation_slope": adapter.radiation_slope,
            "chamber_origin_c": adapter.chamber_origin_c,
        }

    @classmethod
    def from_snapshot(cls, snapshot):
        if not isinstance(snapshot, Mapping) or snapshot.get("schema") != cls._SCHEMA:
            raise ValueError("invalid grey-box adapter snapshot")
        fields = {
            key: snapshot[key]
            for key in (
                "state",
                "transition",
                "q_gain",
                "ambient_gain",
                "affine_offset",
                "radiation_constant_gain",
                "temperature_index",
                "radiation_sigma",
                "radiation_slope",
                "chamber_origin_c",
            )
        }
        return cls(GreyBoxPredictionAdapter(**fields))


_PHYSICAL_PARAMS = ("C_c", "h_amb", "theta", "n_delay", "K_Q", "sigma")

_LEARNED_RESIDUAL_WEIGHT = 1_000.0


def _model_is_identified(cfg, model_meta=None):
    """Whether thermal parameters came from calibration rather than shipped defaults."""
    return model_meta is not None or any(cfg.get(key) != _DEFAULTS[key] for key in _PHYSICAL_PARAMS)


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
        cfg.pop("feed_forward", None)
        self.cfg = cfg
        _warn_about_model(cfg)
        self.u_max = float(cycle_data.get("u_max", 0.9))

        self._set_point_c = 0.0
        self._online_enabled = cfg.get("enable_online_adaptation") is True
        self._online = None
        self._linear_config = None
        self._linear_policy = None
        self._online_next_evaluation_s = None
        self._online_last_evaluation = None
        self._online_last_lifecycle_reason = None
        self._online_promotion_count = 0
        self._online_rollback_count = 0
        self._online_eligible_updates = 0
        self._online_rejected_updates = 0
        self._online_last_rejection_reason = None
        self._online_learner_duration = None
        self._online_evaluation_duration = None
        self._online_linear_duration = None
        self._online_last_snapshot = None
        self._last_combustion_load = 0.0
        self._applied_combustion_load = 0.0
        self._x_hat = None
        self._online_pending_lifecycle = None
        self._policy_u_prev = 0.0
        self._online_previous_setpoint = None
        self._last_raw_combustion_load = 0.0
        self._last_equilibrium_load = None
        self._last_residual_load = None
        self._last_feasibility = None
        self._policy_equilibrium_load = 0.0
        self._last_solve_failed = False
        # How long the output has been frozen. A single failure is a hiccup the
        # held command covers; a run of them means nothing is steering.
        self._online_last_lifecycle = None
        self._consecutive_policy_failures = 0
        self._history = collections.deque(maxlen=_HISTORY_MAX)
        self._model_revision = 0
        self._model_meta = None  # provenance of an adopted model, or None
        self._trace_diagnostics = None
        self._trace_allocation: AllocationResult | None = None

        self.estimator, self._net, self.model, self.mpc = self._build_for(cfg)
        if self._online_enabled:
            self._initialize_online_adaptation()

    def _new_scheduled_arx(self):
        return ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0))

    def _new_linear_policy(self):
        return _SCHEDULED_ARX_LINEAR_CONFIG, LinearMPC(_SCHEDULED_ARX_LINEAR_CONFIG)

    def _new_grey_box_model(self):
        return _GreyBoxAdaptiveModel.from_controller(self)

    def _new_online_adaptation(self, incumbent, challenger):
        coordinator = OnlineAdaptation(incumbent, challenger, AdaptationPolicy(), accepted_sources=("controller",))
        # ScheduledARX only predicts after its complete lag window exists.  Keep
        # adaptation in tracking mode until then, rather than letting the
        # coordinator invoke observe() against an incomplete learner.
        coordinator._lag_warmup_remaining = max(
            challenger.config.na + 1,
            max(challenger.config.delays) + challenger.config.nb + 1,
        )
        return coordinator

    def _initialize_online_adaptation(self):
        challenger = self._new_scheduled_arx()
        self._linear_config, self._linear_policy = self._new_linear_policy()
        self._online = self._new_online_adaptation(self._new_grey_box_model(), challenger)

    def _build_for(self, cfg, *, model_identified=None):
        """Build thermal components at the configured planning horizon."""
        if model_identified is None:
            model_identified = _model_is_identified(cfg, self._model_meta)
        n_delay = int(cfg["n_delay"])
        n_horizon = int(cfg["n_horizon"])
        estimator = self._build_estimator(cfg, n_delay)
        net, model, mpc = None, None, None
        if str(cfg.get("policy", "nlp")).lower() == "net":
            if model_identified:
                print(
                    "[mpc] net policy artifacts do not encode the learned residual objective; "
                    "using NLP for the identified model"
                )
            else:
                net = _load_net_policy(cfg, n_horizon)
        if net is None:
            residual_weight = _LEARNED_RESIDUAL_WEIGHT if model_identified else 0.0
            model, mpc = self._build_nlp(cfg, n_delay, n_horizon, residual_weight=residual_weight)
        return estimator, net, model, mpc

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

    def _build_nlp(self, cfg, n_delay, n_horizon, *, residual_weight):
        """Build the do-mpc NLP policy at the configured horizon."""
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
        combustion_residual = model.u["combustion_residual"]
        tracking_cost = cfg["Q_w"] * (T_c - T_set) ** 2
        stage_cost = tracking_cost + residual_weight * combustion_residual**2
        mpc.set_objective(mterm=tracking_cost, lterm=stage_cost)
        total_load = model.tvp["equilibrium_load"] + combustion_residual
        mpc.set_rterm(combustion_residual=cfg["R_dQ"])
        mpc.set_nl_cons("normalized_load_upper", total_load, ub=1.0)
        mpc.set_nl_cons("normalized_load_lower", -total_load, ub=0.0)

        tvp_template = mpc.get_tvp_template()

        def tvp_fun(t_now):
            for k in range(int(n_horizon) + 1):
                tvp_template["_tvp", k, "T_set"] = self._set_point_c
                tvp_template["_tvp", k, "equilibrium_load"] = self._policy_equilibrium_load
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

    def commands_fan(self):
        return bool(self.cfg.get("enable_fan_input", False))

    def wants_async(self):
        return True

    @staticmethod
    def _linear_certificate_rejection(solve, config):
        """Return the precise controller-boundary certificate rejection reason."""
        try:
            sequence = np.asarray(solve.sequence_q, dtype=float)
            objective = float(solve.objective)
            kkt = float(solve.kkt_residual)
            iterations = solve.iterations
            condition = float(solve.hessian_condition)
        except AttributeError, TypeError, ValueError:
            return "invalid-linear-certificate"
        if (
            sequence.shape != (config.horizon_steps,)
            or not np.isfinite(sequence).all()
            or not np.all((0.0 <= sequence) & (sequence <= 1.0))
            or not math.isfinite(objective)
            or not isinstance(iterations, int)
            or isinstance(iterations, bool)
            or iterations < 0
            or not math.isfinite(condition)
            or condition < 1.0
        ):
            return "invalid-linear-certificate"
        if not math.isfinite(kkt) or not 0.0 <= kkt <= config.tolerance:
            return "invalid-kkt-certificate"
        return None

    @staticmethod
    def _valid_linear_solve(solve, config):
        """Compatibility predicate for callers/tests needing a boolean."""
        return Controller._linear_certificate_rejection(solve, config) is None

    @staticmethod
    def _normalized_forecast_failure(error):
        """Normalize model-library finite-value errors to the lifecycle reason."""
        if isinstance(error, (ValueError, FloatingPointError, RuntimeError)) and "finite" in str(error).lower():
            return ValueError("non-finite-forecast")
        return error

    def _active_arx(self):
        return self._online is not None and isinstance(self._online.incumbent, ScheduledARX)

    def _online_status(self):
        coordinator = self._online
        if coordinator is None:
            return {
                "enabled": False,
                "active_model_kind": "grey-box",
                "role_generation": 0,
                "eligible_updates": 0,
                "rejected_updates": 0,
                "current_rejection_reason": None,
                "active_delay": None,
                "effective_samples": 0,
                "last_evaluation_s": None,
                "last_evaluation_outcome": None,
                "incumbent_prediction_score": None,
                "candidate_prediction_score": None,
                "promotion_count": 0,
                "rollback_count": 0,
                "learner_duration_seconds": None,
                "evaluation_duration_seconds": None,
                "linear_solve_duration_seconds": None,
            }
        incumbent = coordinator.incumbent
        return {
            "enabled": True,
            "active_model_kind": "scheduled-arx" if isinstance(incumbent, ScheduledARX) else "grey-box",
            "role_generation": coordinator.role_generation,
            "eligible_updates": self._online_eligible_updates,
            "rejected_updates": self._online_rejected_updates,
            "current_rejection_reason": self._online_last_rejection_reason,
            "active_delay": incumbent.snapshot().get("active_delay") if isinstance(incumbent, ScheduledARX) else None,
            "effective_samples": coordinator.effective_updates,
            "last_evaluation_s": None
            if self._online_last_evaluation is None
            else self._online_last_evaluation.get("evaluated_at_s"),
            "last_evaluation_outcome": self._online_last_evaluation,
            "incumbent_prediction_score": None
            if self._online_last_evaluation is None
            else self._online_last_evaluation.get("incumbent_prediction_score"),
            "candidate_prediction_score": None
            if self._online_last_evaluation is None
            else self._online_last_evaluation.get("challenger_prediction_score"),
            "promotion_count": self._online_promotion_count,
            "rollback_count": self._online_rollback_count,
            "learner_duration_seconds": self._online_learner_duration,
            "evaluation_duration_seconds": self._online_evaluation_duration,
            "linear_solve_duration_seconds": self._online_linear_duration,
        }

    def _record_evaluation(self, decision, *, committed=None):
        self._online_last_evaluation = {
            "decision_id": decision.decision_id,
            "evaluated_at_s": decision.evaluated_at_s,
            "role_generation": decision.generation,
            "promoted": decision.promoted,
            "committed": decision.committed if committed is None else committed,
            "consecutive_wins": decision.consecutive_wins,
            "rejection_reasons": tuple(reason.value for reason in decision.reasons),
            "incumbent_prediction_score": decision.incumbent_prediction_score,
            "challenger_prediction_score": decision.candidate_prediction_score,
            "incumbent_braking_score": decision.incumbent_braking_score,
            "challenger_braking_score": decision.candidate_braking_score,
            "sample_count": decision.sample_count,
            "prospective_digest": decision.prospective_digest,
        }

    def _online_lifecycle(self, event, detail):
        model = self._online.incumbent
        snapshot = model.snapshot()
        return {
            "event": event,
            "model_revision": self._model_revision,
            "provenance": "online-adaptation",
            "detail": detail,
            "model_kind": "scheduled-arx" if isinstance(model, ScheduledARX) else "grey-box",
            "model_schema": snapshot.get("schema"),
            "role_generation": self._online.role_generation,
            "snapshot_digest": OnlineAdaptation.model_digest(model),
            "parameters": (),
        }

    def _evaluate_online(self, observation):
        if self._online_next_evaluation_s is None:
            self._online_next_evaluation_s = observation.frame_start_s + self._online.policy.evaluation_interval_s
            return None
        if observation.frame_end_s < self._online_next_evaluation_s:
            return None
        self._online_next_evaluation_s = observation.frame_end_s + self._online.policy.evaluation_interval_s
        started = time.monotonic()
        decision = self._online.evaluate_due(observation.frame_end_s)
        self._online_evaluation_duration = time.monotonic() - started
        self._record_evaluation(decision)
        self._model_revision += 1
        if not decision.promoted:
            return {"evaluation": self._online_last_evaluation}
        try:
            candidate = self._online.prospective_model(decision.decision_id)
            try:
                prediction = candidate.affine_prediction(
                    self._linear_config.horizon_steps,
                    self._applied_combustion_load,
                    np.full(self._linear_config.horizon_steps, self.cfg["T_amb"]),
                )
            except (ValueError, FloatingPointError, RuntimeError) as error:
                raise self._normalized_forecast_failure(error) from error
            disturbance = None if self._x_hat is None else float(np.asarray(self._x_hat).reshape(-1)[-1])
            if disturbance is None or not math.isfinite(disturbance):
                raise ValueError("invalid-disturbance")
            started = time.monotonic()
            solve = self._linear_policy.solve(
                prediction,
                setpoint_c=self._set_point_c,
                q_previous=self._applied_combustion_load,
                equilibrium_q=self._equilibrium_load(self._set_point_c, disturbance),
            )
            self._online_linear_duration = time.monotonic() - started
            certificate_rejection = self._linear_certificate_rejection(solve, self._linear_config)
            if certificate_rejection is not None:
                raise ValueError(certificate_rejection)
        except Exception as error:
            detail = str(error)
            self._online.reject_prospective(decision.decision_id, detail)
            self._online_last_lifecycle_reason = detail
            lifecycle = self._online_lifecycle("reject", detail)
            self._online_last_rejection_reason = detail
            self._online_last_lifecycle = lifecycle
            return {"evaluation": self._online_last_evaluation, "lifecycle": lifecycle}
        if not self._online.commit_promotion(decision.decision_id, solve):
            return {"evaluation": self._online_last_evaluation}
        self._online_promotion_count += 1
        self._online_last_lifecycle_reason = "promotion"
        self._model_revision += 1
        self._record_evaluation(decision, committed=True)
        lifecycle = self._online_lifecycle("adopt", "promotion")
        self._online_last_lifecycle = lifecycle
        return {"evaluation": self._online_last_evaluation, "lifecycle": lifecycle}

    def observe_frame(self, observation):
        """Consume one completed framed-pulse observation on Hold's worker."""
        if not self._online_enabled:
            return None
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        generation = observation.role_generation
        if generation != self._online.role_generation:
            self._online_last_rejection_reason = "stale-generation"
            self._online.reset_continuity()
            self._online_rejected_updates += 1
            return {
                "role_generation": generation,
                "eligible": False,
                "rejection_reasons": ("stale-generation",),
                "input_variance": 0.0,
                "input_levels": 0,
                "incumbent_innovation_c": None,
                "challenger_innovation_c": None,
                "effective_updates": self._online.effective_updates,
                "model_digest": OnlineAdaptation.model_digest(self._online.challenger),
            }
        if not self._active_arx():
            # The incumbent is a frozen origin, not a live estimator reference.
            # Refresh it only at this frame boundary so its comparison forecast
            # is contemporaneous while every captured origin stays immutable.
            self._online.incumbent = self._new_grey_box_model()
        actuation_known = observation.output_source != "unknown"
        braking = observation.realized_q <= 0.05 or (
            self._online_previous_setpoint is not None and observation.setpoint_c < self._online_previous_setpoint
        )
        started = time.monotonic()
        outcome = self._online.observe(
            observation,
            actuation_known=actuation_known,
            ambient_future=np.full(15, observation.ambient_c),
            braking=braking,
        )
        self._online_previous_setpoint = observation.setpoint_c
        self._online_learner_duration = time.monotonic() - started
        reasons = tuple(reason.value for reason in outcome.gate.reasons)
        if outcome.gate.permitted:
            self._online_eligible_updates += 1
            self._online_last_rejection_reason = None
        else:
            self._online_rejected_updates += 1
            if not actuation_known:
                reasons = ("unknown-actuation",)
            self._online_last_rejection_reason = reasons[0]
        result = {
            "role_generation": generation,
            "eligible": outcome.gate.permitted,
            "rejection_reasons": reasons,
            "input_variance": outcome.gate.input_variance,
            "input_levels": outcome.gate.input_levels,
            "incumbent_innovation_c": None if outcome.incumbent is None else outcome.incumbent.innovation_c,
            "challenger_innovation_c": None if outcome.challenger is None else outcome.challenger.innovation_c,
            "effective_updates": outcome.effective_updates,
            "model_digest": OnlineAdaptation.model_digest(self._online.challenger),
        }
        event = self._evaluate_online(observation)
        if event:
            result.update(event)
        return result

    def get_status(self):
        return {
            "set_point": _finite_float(self.set_point),
            "set_point_c": _finite_float(self._set_point_c),
            "last_combustion_load": _finite_float(self._last_combustion_load),
            "last_raw_combustion_load": _optional_float(self._last_raw_combustion_load),
            "last_equilibrium_load": _optional_float(self._last_equilibrium_load),
            "last_residual_load": _optional_float(self._last_residual_load),
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
            "feasibility": None if self._last_feasibility is None else self._last_feasibility.as_status(),
            "adaptation": self._online_status(),
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

    def _online_snapshot(self):
        return {
            **self._online.snapshot(),
            "active_model_kind": "scheduled-arx" if self._active_arx() else "grey-box",
            "eligible_updates": self._online_eligible_updates,
            "rejected_updates": self._online_rejected_updates,
            "promotion_count": self._online_promotion_count,
            "rollback_count": self._online_rollback_count,
            "last_lifecycle_reason": self._online_last_lifecycle_reason,
            "last_evaluation": copy.deepcopy(self._online_last_evaluation),
            "last_lifecycle": copy.deepcopy(self._online_last_lifecycle),
        }

    def get_model_snapshot(self):
        if not self._online_enabled:
            if self._model_meta is None:
                return None
            return {
                "version": self._MODEL_SCHEMA,
                "revision": int(self._model_revision),
                "params": {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS},
                **self._model_meta,
                "band_c": list(self._model_meta["band_c"]),
            }
        snapshot = {
            "version": self._MODEL_SCHEMA,
            "revision": int(self._model_revision),
            "params": {k: float(self.cfg[k]) for k in self._MODEL_PARAM_KEYS},
            "grey_box_identified": self._model_meta is not None,
            "online_adaptation": self._online_snapshot(),
        }
        if self._model_meta is not None:
            snapshot.update(self._model_meta)
            snapshot["band_c"] = list(self._model_meta["band_c"])
        try:
            encoded = json.dumps(snapshot, allow_nan=False).encode()
            if len(encoded) > MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot exceeds store limit")
        except TypeError, ValueError, OverflowError:
            return None if self._online_last_snapshot is None else copy.deepcopy(self._online_last_snapshot)
        self._online_last_snapshot = copy.deepcopy(snapshot)
        return copy.deepcopy(snapshot)

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
        # Snapshots historically serialize all physical parameters as floats,
        # but the runtime delay is a structural state count.  Preserve that
        # wire compatibility while restoring the operational config as an int
        # before either grey-box adapter construction or estimator rebuild.
        merged["n_delay"] = snapshot_n_delay
        # The restored parameters have to reach the estimator, the horizon and
        # the policy, not just the config those three were sized from -- a
        # config-only restore leaves the season's learning inert and, where the
        # restored model coasts further than the shipped one, plans over a
        # horizon that stops short of the brake. Built before anything is
        # committed so a build that fails leaves the controller solving the
        # model it already had.
        try:
            rebuilt = self._build_for(merged, model_identified=bool(snapshot.get("grey_box_identified", True)))
        except Exception as exc:
            print(f"[mpc] a stored model could not be built ({exc}); keeping the model this controller started with.")
            return False
        self.cfg.update(merged)
        self.estimator, self._net, self.model, self.mpc = rebuilt
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
        if not snapshot.get("grey_box_identified", True):
            self._model_meta = None
        if self._online_enabled:
            # Build a fresh local wrapper first.  A malformed nested record must
            # never undo the independently validated grey-box restoration.
            self._initialize_online_adaptation()
            self._online_eligible_updates = 0
            self._online_rejected_updates = 0
            self._online_promotion_count = 0
            self._online_rollback_count = 0
            self._online_last_rejection_reason = None
            self._online_last_lifecycle_reason = None
            self._online_last_lifecycle = None
            self._online_last_evaluation = None
            payload = snapshot.get("online_adaptation")
            if payload is not None:
                try:
                    required_metadata = {
                        "active_model_kind",
                        "eligible_updates",
                        "rejected_updates",
                        "promotion_count",
                        "rollback_count",
                        "last_lifecycle_reason",
                        "last_evaluation",
                        "last_lifecycle",
                    }
                    if not required_metadata.issubset(payload):
                        raise ValueError("online snapshot lacks controller metadata")

                    def load_model(model_snapshot):
                        schema = model_snapshot.get("schema")
                        if schema == "scheduled-arx/v2":
                            return ScheduledARX.from_snapshot(model_snapshot)
                        if schema == _GreyBoxAdaptiveModel._SCHEMA:
                            return _GreyBoxAdaptiveModel.from_snapshot(model_snapshot)
                        raise ValueError("unsupported online model schema")

                    restored = OnlineAdaptation.from_snapshot(payload, model_loader=load_model)
                    active_kind = payload.get("active_model_kind")
                    actual_kind = "scheduled-arx" if isinstance(restored.incumbent, ScheduledARX) else "grey-box"
                    if active_kind != actual_kind:
                        raise ValueError("online active model kind does not match incumbent")
                    previous = restored._previous_incumbent
                    if actual_kind == "scheduled-arx":
                        if (
                            not isinstance(restored.challenger, _GreyBoxAdaptiveModel)
                            or not isinstance(previous, _GreyBoxAdaptiveModel)
                            or restored.previous_incumbent_digest is None
                        ):
                            raise ValueError("active ARX lacks a valid grey-box rollback owner")
                    elif (
                        not isinstance(restored.challenger, ScheduledARX)
                        or previous is not None
                        or restored.previous_incumbent_digest is not None
                    ):
                        raise ValueError("active grey-box has invalid rollback ownership")
                    eligible_updates = _online_count(payload["eligible_updates"], "eligible_updates")
                    rejected_updates = _online_count(payload["rejected_updates"], "rejected_updates")
                    promotion_count = _online_count(payload["promotion_count"], "promotion_count")
                    rollback_count = _online_count(payload["rollback_count"], "rollback_count")
                    lifecycle_reason = _online_optional_string(
                        payload["last_lifecycle_reason"], "last_lifecycle_reason"
                    )
                    last_evaluation = _online_evaluation(payload["last_evaluation"])
                    last_lifecycle = _online_lifecycle_metadata(payload["last_lifecycle"])
                    self._online = restored
                    self._online.begin_restored_session()
                    self._online_eligible_updates = eligible_updates
                    self._online_rejected_updates = rejected_updates
                    self._online_promotion_count = promotion_count
                    self._online_rollback_count = rollback_count
                    self._online_last_lifecycle_reason = lifecycle_reason
                    self._online_last_evaluation = last_evaluation
                    self._online_last_lifecycle = last_lifecycle
                except TypeError, ValueError, KeyError:
                    pass
        self._online_last_snapshot = None
        self.get_model_snapshot()
        return True

    def _online_teardown_checkpoint(self, verdict):
        """Persist the learner exactly once for every refit teardown outcome."""
        if self._online_enabled:
            self._model_revision += 1
            self.get_model_snapshot()
        return verdict

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

        if self._online_enabled and not bool(self.cfg.get("enable_identification", False)):
            return self._online_teardown_checkpoint(_Verdict(False, "online adaptation checkpoint"))

        rows = list(history if history is not None else self._history)
        if len(rows) < _REFIT_MIN_SAMPLES:
            return self._online_teardown_checkpoint(
                _Verdict(False, f"only {len(rows)} samples; need {_REFIT_MIN_SAMPLES}")
            )

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
                return self._online_teardown_checkpoint(
                    _Verdict(False, f"the solve did not converge within {fitted['nfev']} evaluations")
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
            return self._online_teardown_checkpoint(_Verdict(False, f"fit failed: {e}"))
        except Exception:
            self._online_teardown_checkpoint(_Verdict(False, "fit failed"))
            raise

        verdict = evaluate(
            fitted,
            incumbent,
            candidate_rmse=cand_rmse,
            incumbent_rmse=inc_rmse,
            identifiability=ident,
        )
        print(
            f"[mpc] refit: {verdict.reason} (candidate RMSE {cand_rmse:.2f} C, "
            f"incumbent {inc_rmse:.2f} C, {fitted['nfev']} evaluations over "
            f"{len(rows)} samples in {time.perf_counter() - started:.1f} s)"
        )
        if verdict.accepted:
            self._adopt_model(
                fitted,
                rmse=cand_rmse,
                samples=len(rows),
                band_c=(float(temp.min()), float(temp.max())),
                nfev=fitted["nfev"],
            )
        return self._online_teardown_checkpoint(verdict)

    def set_output(self, applied):
        """Recover the normalized applied load from measured mean auger duty."""
        self._applied_combustion_load = normalized_load_from_auger_duty(applied.ratio, u_max=self.u_max)

    def _equilibrium_load(self, target, disturbance):
        """Private experiment seam for the identified-model equilibrium baseline."""
        if not _model_is_identified(self.cfg, self._model_meta):
            return 0.0
        return steady_combustion_load(self.cfg, target, disturbance)

    def _policy_residual(self, x_hat, previous_load, equilibrium_load):
        """Return the policy move that outer composition adds to its injected equilibrium."""
        self._policy_equilibrium_load = equilibrium_load
        if self._net is not None:
            return float(self._net.firing_rate_raw(x_hat, previous_load, self._set_point_c)) - equilibrium_load
        return float(np.asarray(self.mpc.make_step(x_hat.reshape(-1, 1))).flatten()[0])

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
        model_provenance = "adopted" if self._model_meta is not None else "configured"
        model_identified = _model_is_identified(self.cfg, self._model_meta)
        feasibility = feasibility_report(
            self.cfg if model_identified else None,
            self._set_point_c,
            disturbance=disturbance,
            model_revision=self._model_revision if model_identified else None,
            model_provenance=model_provenance if model_identified else None,
        )
        self._last_feasibility = feasibility
        equilibrium = self._equilibrium_load(self._set_point_c, disturbance)

        solve_start = time.monotonic()
        failure_state = MpcFailureState.SUCCESS
        failure_error = None
        active_arx = self._active_arx()
        try:
            if active_arx:
                try:
                    prediction = self._online.incumbent.affine_prediction(
                        self._linear_config.horizon_steps,
                        self._applied_combustion_load,
                        np.full(self._linear_config.horizon_steps, self.cfg["T_amb"]),
                    )
                except (ValueError, FloatingPointError, RuntimeError) as error:
                    raise self._normalized_forecast_failure(error) from error
                if not (np.isfinite(prediction.free_output_c).all() and np.isfinite(prediction.input_response_c).all()):
                    raise ValueError("non-finite-forecast")
                linear_started = time.monotonic()
                solve = self._linear_policy.solve(
                    prediction,
                    setpoint_c=self._set_point_c,
                    q_previous=self._applied_combustion_load,
                    equilibrium_q=equilibrium,
                )
                self._online_linear_duration = time.monotonic() - linear_started
                certificate_rejection = self._linear_certificate_rejection(solve, self._linear_config)
                if certificate_rejection is not None:
                    raise ValueError(certificate_rejection)
                combustion_load = float(solve.sequence_q[0])
                raw_firing_load = combustion_load
                residual_move = combustion_load - equilibrium
            else:
                residual_move = self._policy_residual(x_hat, self._policy_u_prev, equilibrium)
                raw_firing_load = equilibrium + residual_move
                combustion_load = float(np.clip(raw_firing_load, 0.0, 1.0))
        except Exception as error:
            combustion_load = self._last_combustion_load
            failure_state = MpcFailureState.POLICY_EXCEPTION
            failure_error = error
            equilibrium = None
            residual_move = None
            raw_firing_load = None
        finally:
            solve_end = time.monotonic()

        if failure_state is MpcFailureState.SUCCESS:
            if self._consecutive_policy_failures:
                print(f"[mpc] policy recovered after {self._consecutive_policy_failures} failed step(s)")
            self._consecutive_policy_failures = 0
            self._last_solve_failed = False
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
            if active_arx and self._online is not None:
                message = str(failure_error)
                immediate = message in (
                    "non-finite-forecast",
                    "invalid-linear-certificate",
                    "invalid-kkt-certificate",
                )
                if (immediate or n >= 2) and self._online.rollback():
                    reason = message if immediate else "repeated-solve-failure"
                    self._online_rollback_count += 1
                    self._online_last_lifecycle_reason = reason
                    self._online_last_rejection_reason = reason
                    self._model_revision += 1
                    self._online_pending_lifecycle = self._online_lifecycle("reject", reason)
                    self._online_last_lifecycle = self._online_pending_lifecycle
                    self._consecutive_policy_failures = 0

        self._last_equilibrium_load = equilibrium
        self._last_residual_load = residual_move
        self._last_raw_combustion_load = raw_firing_load
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
        self._trace_diagnostics = MpcTraceDiagnostics(
            state_names=state_names,
            state_values=state_values,
            disturbance_estimate=disturbance,
            model_revision=self._model_revision,
            model_provenance=model_provenance,
            raw_policy_firing_load=raw_firing_load,
            equilibrium_feed_forward=equilibrium,
            residual_move=residual_move,
            bounded_firing_load=combustion_load,
            applied_combustion_load=applied_combustion_load,
            policy_kind="linear-mpc" if active_arx else ("net" if self._net is not None else "nlp"),
            failure_state=failure_state,
            consecutive_policy_failures=self._consecutive_policy_failures,
            solve_start_monotonic=solve_start,
            solve_end_monotonic=solve_end,
            solve_duration_seconds=solve_end - solve_start,
            feasibility=feasibility,
            model_lifecycle=self._online_pending_lifecycle,
        )
        self._online_pending_lifecycle = None
        return {"cycle_ratio": auger, "fan": {"duty": fan_duty}}

    def trace_diagnostics(self) -> MpcTraceDiagnostics | None:
        return self._trace_diagnostics

    def trace_allocation(self) -> AllocationResult | None:
        return self._trace_allocation
