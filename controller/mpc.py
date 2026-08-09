#!/usr/bin/env python3

"""Grey-box MPC control through the repository-published acados runtime.

The active controller owns one EKF/KF estimator and one native solver.  The
configured control period is estimator/runner cadence; the prediction model is
the fixed 25-second, eight-delay generated map.
"""
from __future__ import annotations


import collections
import copy
import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
import time

import numpy as np

from common.control_trace import (
    AmbientSource,
    CompletedOriginEvidence,
    HorizonScoreEvidence,
    ModelEvaluationPayload,
    StateSpaceRefreshPayload,
)
from controller.base import ControllerBase, MpcFailureState, MpcTraceDiagnostics
from controller.applied_output import FrameFeedbackDisposition
from controller.model_promotion import Verdict as _Verdict
from controller.model_promotion import feasibility_report
from controller.mpc_model import MODEL_SCHEMA, GreyBoxEKF, GreyBoxKF, steady_combustion_load
from controller.mpc_allocator import AllocationResult, allocate, normalized_load_from_auger_duty
from common.controller_model_state import MAX_SNAPSHOT_BYTES
from common.model_evidence import ForecastOriginEvidence, ModelEvidenceRecord, RefreshDiagnosticsEvidence
from controller.acados import (
    AcadosGreyBoxMPC,
    GreyBoxMPCConfig,
    SolverDiagnostics,
    SolverError,
)
from controller.runtime.model_fitting import (
    CandidatePair,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TargetTimingEvidence,
    grey_config_digest,
)
from controller.model_learning.contracts import CandidateOrigin, FrameObservation
from controller.grey_box import GreyBoxPredictionAdapter
from controller.model_learning.calibration import (
    CalibrationCommand as _CoordinatorCalibrationCommand,
    CalibrationCoordinator,
    CalibrationDecision,
    CalibrationProgress,
    CalibrationRuntimeContext,
)

_DEFAULTS = dict(
    # R_dQ (firing-move penalty) kept low: 1.0 was over-damped -> sluggish rise AND
    # a looser steady band. 0.1 gives ~4x faster setpoint-step rise and a tighter
    # band, at a modest step-overshoot increase.
    n_horizon=24,
    # The generated prediction map is fixed at 25 seconds.
    control_period=5.0,
    Q_w=1.0,
    R_dQ=0.1,
    # Nominal grey-box thermal params -- CALIBRATE to your grill via update_mpc.py.
    C_c=320.0,
    h_amb=0.50,
    T_amb=20.0,
    theta=50.0,
    # The generated grey model always has eight delay states.
    n_delay=8,
    K_Q=350.0,
    sigma=1.4e-9,
    # The only supported live estimators are EKF and KF.
    estimator="ekf",
    fan_min_pct=40.0,
    fan_max_pct=100.0,
    enable_fan_input=False,
    # est_q_dist deliberately slow: a fast disturbance estimate chases unmeasured
    # transients and worsens setpoint-step overshoot; 0.05 cut step overshoot ~30%
    # with no change to the steady-state band.
    est_q_temp=1e-2,
    est_q_dist=0.05,
    est_r_meas=0.04,
    enable_online_adaptation=False,
)

# One row per control period. At the 5 s default that is ~12 hours, which is
# longer than any single cook; a longer one loses its beginning rather than
# its end, and the end is what describes the grill's current state. This is

# also what bounds a refit: the longest cook the fit can ever be handed off
# the teardown path is one full history.
_HISTORY_MAX = 8640
_SCHEDULED_ARX_LINEAR_CONFIG = None
GREY_BOX_KIND = "grey-box"
STATE_SPACE_KIND = "innovation-state-space"

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


def _online_required_score(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _online_matches_completed_rmse(errors, reported):
    if not errors:
        return reported is None
    if reported is None or reported < 0.0:
        return False
    expected = math.sqrt(sum(error * error for error in errors) / len(errors))
    return math.isclose(reported, expected, rel_tol=1e-12, abs_tol=1e-12)


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
        "window_start_s",
        "window_end_s",
        "incumbent_digest",
        "challenger_digest",
        "completed_origins",
        "horizon_scores",
        "evaluation_duration_ms",
        "challenger_model_kind",
        "state_space_refresh",
    )
)
_LEGACY_EVALUATION_KEYS = _EVALUATION_KEYS - {"challenger_model_kind", "state_space_refresh"}
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
_ORIGIN_EVIDENCE_KEYS = frozenset(
    (
        "origin_time_s",
        "completion_time_s",
        "horizon_steps",
        "generation",
        "observed_temperature_c",
        "incumbent_error_c",
        "challenger_error_c",
        "braking",
        "observation_sequence",
        "incumbent_digest",
        "challenger_digest",
        "incumbent_prediction_c",
        "challenger_prediction_c",
        "temperature_band",
        "ambient_source",
    )
)
_HORIZON_EVIDENCE_KEYS = frozenset(("horizon_steps", "incumbent_rmse_c", "challenger_rmse_c", "sample_count"))


def _online_nonnegative_score(value, name):
    score = _online_required_score(value, name)
    if score < 0:
        raise ValueError(f"{name} must be a non-negative finite number")
    return score


def _online_digest(value, name):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return value


def _online_horizon(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value not in _ONLINE_HORIZONS:
        raise ValueError(f"{name} must be one of {_ONLINE_HORIZONS}")
    return value


def _online_evaluation(value):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("last_evaluation has an invalid schema")
    evaluation_keys = set(value)
    if evaluation_keys not in (_EVALUATION_KEYS, _LEGACY_EVALUATION_KEYS):
        raise ValueError("last_evaluation has an invalid schema")
    has_model_extension = evaluation_keys == _EVALUATION_KEYS
    if not isinstance(value["decision_id"], str) or not value["decision_id"].strip():
        raise ValueError("evaluation decision_id is invalid")
    evaluated = _online_nonnegative_score(value["evaluated_at_s"], "evaluated_at_s")
    role_generation = _online_count(value["role_generation"], "role_generation")
    consecutive_wins = _online_count(value["consecutive_wins"], "consecutive_wins")
    sample_count = _online_count(value["sample_count"], "sample_count")
    for key in ("promoted", "committed"):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key} must be bool")
    if value["committed"] and not value["promoted"]:
        raise ValueError("committed evaluation must be promoted")
    reasons = value["rejection_reasons"]
    if (
        not isinstance(reasons, (list, tuple))
        or len(reasons) > 32
        or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
    ):
        raise ValueError("evaluation rejection_reasons are invalid")
    if value["promoted"] and reasons:
        raise ValueError("promoted evaluation cannot have rejection reasons")
    for key in (
        "incumbent_prediction_score",
        "challenger_prediction_score",
        "incumbent_braking_score",
        "challenger_braking_score",
    ):
        _online_optional_score(value[key], key)
    prospective_digest = value["prospective_digest"]
    if (prospective_digest is not None) != value["promoted"]:
        raise ValueError("evaluation prospective digest must match promotion")
    if prospective_digest is not None:
        _online_digest(prospective_digest, "prospective_digest")
    window_start = _online_nonnegative_score(value["window_start_s"], "window_start_s")
    window_end = _online_nonnegative_score(value["window_end_s"], "window_end_s")
    if window_start > window_end or evaluated < window_end:
        raise ValueError("evaluation window is invalid")
    _online_digest(value["incumbent_digest"], "incumbent_digest")
    _online_digest(value["challenger_digest"], "challenger_digest")
    origins = value["completed_origins"]
    if not isinstance(origins, (list, tuple)) or len(origins) > 1800 or sample_count != len(origins):
        raise ValueError("evaluation completed origins are invalid")
    actual_horizon_counts = {horizon: 0 for horizon in _ONLINE_HORIZONS}
    horizon_errors = {horizon: ([], []) for horizon in _ONLINE_HORIZONS}
    origin_starts = []
    origin_ends = []
    for index, origin in enumerate(origins):
        if not isinstance(origin, Mapping) or set(origin) != _ORIGIN_EVIDENCE_KEYS:
            raise ValueError(f"completed origin {index} has an invalid schema")
        origin_start = _online_nonnegative_score(origin["origin_time_s"], f"completed origin {index} start")
        origin_end = _online_nonnegative_score(origin["completion_time_s"], f"completed origin {index} end")
        if origin_start >= origin_end:
            raise ValueError("completed origin interval must be positive")
        horizon = _online_horizon(origin["horizon_steps"], f"completed origin {index} horizon")
        if _online_count(origin["generation"], f"completed origin {index} generation") != role_generation:
            raise ValueError("completed origin generation must match evaluation")
        _online_required_score(origin["observed_temperature_c"], f"completed origin {index} observed_temperature_c")
        incumbent_error = _online_required_score(
            origin["incumbent_error_c"], f"completed origin {index} incumbent_error_c"
        )
        challenger_error = _online_required_score(
            origin["challenger_error_c"], f"completed origin {index} challenger_error_c"
        )
        if not isinstance(origin["braking"], bool):
            raise ValueError("completed origin braking must be bool")
        _online_count(origin["observation_sequence"], f"completed origin {index} observation sequence")
        _online_digest(origin["incumbent_digest"], f"completed origin {index} incumbent digest")
        _online_digest(origin["challenger_digest"], f"completed origin {index} challenger digest")
        _online_required_score(origin["incumbent_prediction_c"], f"completed origin {index} incumbent prediction")
        _online_required_score(origin["challenger_prediction_c"], f"completed origin {index} challenger prediction")
        if not isinstance(origin["temperature_band"], str) or not origin["temperature_band"].strip():
            raise ValueError(f"completed origin {index} temperature_band is invalid")
        try:
            AmbientSource(origin["ambient_source"])
        except ValueError, TypeError:
            raise ValueError(f"completed origin {index} ambient_source is invalid") from None
        actual_horizon_counts[horizon] += 1
        incumbent_errors, challenger_errors = horizon_errors[horizon]
        incumbent_errors.append(incumbent_error)
        challenger_errors.append(challenger_error)
        origin_starts.append(origin_start)
        origin_ends.append(origin_end)
    if origins:
        if window_start != min(origin_starts) or window_end != max(origin_ends):
            raise ValueError("evaluation window must bound completed origins exactly")
    elif window_start != window_end or window_end != evaluated:
        raise ValueError("empty evaluation window must coincide with evaluation time")
    scores = value["horizon_scores"]
    if not isinstance(scores, (list, tuple)) or len(scores) != len(_ONLINE_HORIZONS):
        raise ValueError("evaluation horizon scores are invalid")
    scored_horizons = set()
    complete_horizon_evidence = True
    for index, score in enumerate(scores):
        if not isinstance(score, Mapping) or set(score) != _HORIZON_EVIDENCE_KEYS:
            raise ValueError(f"horizon score {index} has an invalid schema")
        horizon = _online_horizon(score["horizon_steps"], f"horizon score {index} horizon")
        scored_horizons.add(horizon)
        horizon_sample_count = _online_count(score["sample_count"], f"horizon score {index} sample_count")
        complete_horizon_evidence = complete_horizon_evidence and horizon_sample_count > 0
        if horizon_sample_count > 0:
            incumbent = _online_nonnegative_score(score["incumbent_rmse_c"], f"horizon score {index} incumbent_rmse_c")
            challenger = _online_nonnegative_score(
                score["challenger_rmse_c"], f"horizon score {index} challenger_rmse_c"
            )
        else:
            incumbent = score["incumbent_rmse_c"]
            challenger = score["challenger_rmse_c"]
        incumbent_errors, challenger_errors = horizon_errors[horizon]
        if (
            horizon_sample_count != actual_horizon_counts[horizon]
            or not _online_matches_completed_rmse(incumbent_errors, incumbent)
            or not _online_matches_completed_rmse(challenger_errors, challenger)
        ):
            raise ValueError("evaluation horizon score is inconsistent")
    if scored_horizons != set(_ONLINE_HORIZONS):
        raise ValueError(f"evaluation horizon scores must contain {_ONLINE_HORIZONS}")
    if not reasons and consecutive_wins == 0:
        raise ValueError("successful evaluation must advance the win count")
    if reasons and complete_horizon_evidence and consecutive_wins != 0:
        raise ValueError("rejected complete evaluation must reset the win count")
    _online_nonnegative_score(value["evaluation_duration_ms"], "evaluation_duration_ms")
    restored = copy.deepcopy(dict(value))
    if not has_model_extension:
        return restored
    challenger_model_kind = value["challenger_model_kind"]
    if challenger_model_kind not in {"scheduled-arx", "innovation-state-space"}:
        raise ValueError("evaluation challenger_model_kind is invalid")
    refresh = value["state_space_refresh"]
    if challenger_model_kind == "scheduled-arx":
        if refresh is not None:
            raise ValueError("scheduled-arx evaluation cannot include state-space refresh evidence")
        return restored
    if not isinstance(refresh, Mapping):
        raise ValueError("state-space evaluation requires refresh evidence")
    try:
        restored["state_space_refresh"] = asdict(StateSpaceRefreshPayload(**dict(refresh)))
    except TypeError, ValueError:
        raise ValueError("evaluation state_space_refresh is invalid") from None
    return restored


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


class _StateSpaceShadow:
    """Buffer complete worker frames until the production realization can fit."""

    _SCHEMA = "innovation-state-space-shadow/v1"

    def __init__(self) -> None:
        self._config = StateSpaceConfig(orders=(1, 2), delays=(1, 2, 3))
        self._model = InnovationStateSpace(self._config)
        self._frames: collections.deque[FrameObservation] = collections.deque(maxlen=self._config.max_buffer_samples)
        self._fitted = False
        self._refresh_attempts = 0
        self._last_refresh_attempt_s: float | None = None

    @classmethod
    def from_fitted_snapshot(cls, snapshot):
        """Restore a fitted realization behind the session-resettable wrapper."""
        shadow = cls.__new__(cls)
        shadow._model = InnovationStateSpace.from_snapshot(snapshot)
        shadow._config = shadow._model._config
        shadow._frames = collections.deque(maxlen=shadow._config.max_buffer_samples)
        shadow._fitted = True
        shadow._refresh_attempts = 0
        shadow._last_refresh_attempt_s = None
        return shadow

    @property
    def refresh_attempts(self) -> int:
        return self._refresh_attempts

    @property
    def model_kind(self) -> str:
        return "innovation-state-space"

    def reset_lag_history(self) -> None:
        """Discard every pre-gap state and frame before renewed shadow learning."""
        self._model = InnovationStateSpace(self._config)
        self._frames.clear()
        self._fitted = False
        self._last_refresh_attempt_s = None

    def _minimum_samples(self) -> int:
        return max(
            self._config.max_buffer_samples // 100,
            max(self._config.orders) + max(self._config.delays) + 6,
            2 * self._config.block_rows + 3,
        )

    def _bootstrap(self, observation: FrameObservation) -> bool:
        self._frames.append(observation)
        if len(self._frames) < self._minimum_samples():
            return False
        diagnostics = self._model.fit(tuple(self._frames))
        self._fitted = diagnostics.accepted
        if self._fitted:
            self._last_refresh_attempt_s = observation.frame_end_s
        return self._fitted

    def track(self, observation: FrameObservation) -> ModelUpdate:
        if not self._fitted:
            self._bootstrap(observation)
            return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, False)
        return self._model.track(observation)

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        if not self._fitted:
            self._bootstrap(observation)
            return ModelUpdate(observation.temp_c, observation.temp_c, 0.0, False)
        if (
            self._last_refresh_attempt_s is not None
            and observation.frame_end_s - self._last_refresh_attempt_s >= self._config.refresh_interval_s
        ):
            self._refresh_attempts += 1
            self._last_refresh_attempt_s = observation.frame_end_s
        return self._model.observe(observation)

    def affine_prediction(self, horizon_steps, q_previous, ambient_future):
        if not self._fitted:
            raise RuntimeError("state-space shadow has not accumulated a complete fit window")
        return self._model.affine_prediction(horizon_steps, q_previous, ambient_future)

    def snapshot(self):
        if self._fitted:
            return self._model.snapshot()
        return {
            "schema": self._SCHEMA,
            "config": asdict(self._config),
            "effective_samples": len(self._frames),
            "poles": (0.0,),
            "steady_gain": 1.0,
            "delay_steps": (1,),
            "diagnostics": {
                "accepted": False,
                "terminal_reason": "insufficient-samples",
                "attempts": (),
            },
            "status": {"alignment_evidence": "measured", "alignment_error_c": None},
        }


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
    """The live controller has no optional Python dependency."""
    return ()


@dataclass(frozen=True, slots=True)
class CalibrationCommand:
    """One revisioned operator calibration request at the runtime boundary."""

    action: str
    command_revision: int
    ambient_c: float
    ambient_source: str
    empty_grill_confirmed: bool
    pellets_confirmed: bool
    seed: int = 0

    def __post_init__(self) -> None:
        if self.action not in {"start", "pause", "resume", "stop", "reset-progress"}:
            raise ValueError("invalid calibration action")
        if (
            isinstance(self.command_revision, bool)
            or not isinstance(self.command_revision, int)
            or self.command_revision < 1
        ):
            raise ValueError("calibration command revision must be positive")
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (self.ambient_c,)
        ):
            raise ValueError("calibration temperatures must be finite")
        if self.ambient_source not in {"measured", "manual", "weather", "configured"}:
            raise ValueError("invalid calibration ambient source")
        if self.empty_grill_confirmed is not True or self.pellets_confirmed is not True:
            raise ValueError("calibration confirmations are required")


class Controller(ControllerBase):
    def __init__(self, config, units, cycle_data, *, _online_challenger_kind=None):
        if _online_challenger_kind is not None:
            raise ValueError("legacy online challenger selection is retired")
        super().__init__(config, units, cycle_data)

        self._activation_configuration = {
            "controller": "mpc",
            "config": copy.deepcopy(config or {}),
            "cycle_data": copy.deepcopy(cycle_data),
            "units": units,
        }
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
        self._online_challenger_kind = None
        self._online_experiment_active = False
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
        self._calibration = CalibrationCoordinator(predict_max_c=self._predict_calibration_max)
        self._calibration_operations: collections.deque[tuple[str, object]] = collections.deque()
        self._calibration_last_revision = 0
        self._calibration_ambient_c = float(cfg["T_amb"])
        self._calibration_safety_ceiling_c = 0.0
        self._calibration_frame_results: dict[int, tuple[float, CalibrationDecision]] = {}
        self._calibration_feedback: collections.deque[
            tuple[float, float, bool, bool, FrameFeedbackDisposition, int, int, str, int]
        ] = collections.deque()
        self._calibration_generation = 0
        self._calibration_last_feedback_timestamp: float | None = None
        self._trace_calibration = CalibrationDecision(False, 0.0, None, CalibrationProgress())
        self._trace_baseline_allocation: AllocationResult | None = None
        self._trace_allocation: AllocationResult | None = None
        self._activation_manager: ActivationManager | None = None
        self._activation_events: collections.deque[ModelEvidenceRecord] = collections.deque()
        self._activation_expected_temperature_c: float | None = None
        self._activation_residual_failures = 0

        self._online_pending_parameter_promotion = None
        self.estimator, self._net, self.model, self.mpc = self._build_for(cfg)
        self._native_failure_diagnostics: SolverDiagnostics | None = None
        self._closed = False
        self._learning_session_id = "runtime"
        self._learning_cook_id = None
        self._learning_role_generation = self._model_revision
        self._learning = self._build_learning() if self._online_enabled else None

    def _new_scheduled_arx(self):
        return ScheduledARX(ScheduledARXConfig(na=2, nb=2, delays=(1, 2, 3), initial_covariance=10.0))

    def _new_state_space_challenger(self):
        return _StateSpaceShadow()

    def _new_linear_policy(self):
        return _SCHEDULED_ARX_LINEAR_CONFIG, LinearMPC(_SCHEDULED_ARX_LINEAR_CONFIG)

    def _new_grey_box_model(self):
        return _GreyBoxAdaptiveModel.from_controller(self)

    def _new_online_adaptation(self, incumbent, challenger):
        coordinator = OnlineAdaptation(incumbent, challenger, AdaptationPolicy(), accepted_sources=("controller",))
        # ScheduledARX only predicts after its complete lag window exists.  Keep
        # adaptation in tracking mode until then, rather than letting the
        # coordinator invoke observe() against an incomplete learner.
        if isinstance(challenger, ScheduledARX):
            coordinator._lag_warmup_remaining = max(
                challenger.config.na + 1,
                max(challenger.config.delays) + challenger.config.nb + 1,
            )
        return coordinator

    def _initialize_online_adaptation(self):
        state_space_experiment = self._online_challenger_kind == "state-space"
        challenger = self._new_state_space_challenger() if state_space_experiment else self._new_scheduled_arx()
        incumbent = self._new_scheduled_arx() if state_space_experiment else self._new_grey_box_model()
        self._linear_config, self._linear_policy = self._new_linear_policy()
        self._online = self._new_online_adaptation(incumbent, challenger)

    def _build_for(self, cfg, *, model_identified=None):
        """Build one complete estimator/native-solver pair or leave no owner."""
        if model_identified is None:
            model_identified = _model_is_identified(cfg, self._model_meta)
        n_delay = int(cfg.get("n_delay", 8))
        if n_delay != 8:
            raise ValueError("the generated grey-box controller requires exactly eight delay states")
        estimator = self._build_estimator(cfg, n_delay)
        residual_weight = _LEARNED_RESIDUAL_WEIGHT if model_identified else 0.0
        native_config = GreyBoxMPCConfig(
            C_c=cfg["C_c"],
            h_amb=cfg["h_amb"],
            T_amb=cfg["T_amb"],
            theta=cfg["theta"],
            K_Q=cfg["K_Q"],
            sigma=cfg["sigma"],
            horizon_steps=cfg["n_horizon"],
            delay_states=8,
            state_size=10,
            timestep_s=25.0,
            temperature_weight=cfg["Q_w"],
            terminal_weight=cfg["Q_w"],
            move_weight=cfg["R_dQ"],
            residual_weight=residual_weight,
            max_iterations=10,
        )
        try:
            solver = AcadosGreyBoxMPC(native_config)
        except BaseException:
            self._close_component(estimator)
            raise
        return estimator, None, None, solver

    def _build_estimator(self, cfg, n_delay):
        """Build the selected estimator at the runtime control cadence."""
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
        raise ValueError("estimator must be 'ekf' or 'kf'")

    @staticmethod
    def _close_component(component):
        close = getattr(component, "close", None)
        if callable(close):
            close()

    def _candidate_estimator(self, native_config):
        candidate = dict(self.cfg)
        for name in ("C_c", "h_amb", "T_amb", "theta", "K_Q", "sigma"):
            candidate[name] = getattr(native_config, name)
        return self._build_estimator(candidate, 8)

    def _candidate_timing(self, solver):
        state = np.zeros(10, dtype=float)
        state[8] = float(solver.config.T_amb)
        durations = []
        for _ in range(5):
            started = time.monotonic()
            solver.solve(
                state,
                setpoint_c=float(solver.config.T_amb) + 50.0,
                q_previous=0.0,
                equilibrium_q=0.4,
            )
            durations.append((time.monotonic() - started) * 1_000.0)
        return TargetTimingEvidence(
            target="active-runtime",
            samples=len(durations),
            p99_ms=max(durations),
            limit_ms=self.get_control_period() * 200.0,
        )

    def _learning_identity(self):
        document = {
            "config": self.cfg,
            "cycle_data": self.cycle_data,
            "units": self.units,
        }
        encoded = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return LiveLearningIdentity(
            session_id=self._learning_session_id,
            cook_id=self._learning_cook_id,
            configuration_digest=hashlib.sha256(encoded).hexdigest(),
            incumbent_digest=grey_config_digest(self.mpc.config),
            role_generation=self._learning_role_generation,
            candidate_generation=self._learning_role_generation + 1,
        )

    def _build_learning(self):
        return GreyLearningOrchestrator(
            identity=self._learning_identity(),
            config=self.mpc.config,
            incumbent_pair=CandidatePair(self.estimator, self.mpc),
            estimator_factory=self._candidate_estimator,
            controller_factory=AcadosGreyBoxMPC,
            timing_probe=self._candidate_timing,
        )

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

    def _active_state_space(self):
        manager = self._activation_manager
        return manager is not None and manager.active_kind == STATE_SPACE_KIND and manager.active_model is not None

    def _activation_prospective_solve(self, candidate):
        if self._linear_config is None or self._linear_policy is None:
            self._linear_config, self._linear_policy = self._new_linear_policy()
        prediction = candidate.affine_prediction(
            self._linear_config.horizon_steps,
            self._applied_combustion_load,
            np.full(self._linear_config.horizon_steps, self.cfg["T_amb"]),
        )
        if not (np.isfinite(prediction.free_output_c).all() and np.isfinite(prediction.input_response_c).all()):
            raise ValueError("non-finite-forecast")
        disturbance = 0.0 if self._x_hat is None else float(np.asarray(self._x_hat).reshape(-1)[-1])
        solve = self._linear_policy.solve(
            prediction,
            setpoint_c=self._set_point_c,
            q_previous=self._applied_combustion_load,
            equilibrium_q=self._equilibrium_load(self._set_point_c, disturbance),
        )
        rejection = self._linear_certificate_rejection(solve, self._linear_config)
        if rejection is not None:
            raise ValueError(rejection)
        return float(solve.sequence_q[0])

    def _synchronize_active_adaptation(self):
        """Mirror exact activation ownership into an isolated incumbent/challenger pair."""
        manager = self._activation_manager
        if self._online is None or manager is None:
            return
        state = manager.state
        if state.active_kind == STATE_SPACE_KIND and manager.active_snapshot is not None:
            active_snapshot = manager.active_snapshot
            active_digest = OnlineAdaptation.model_digest(InnovationStateSpace.from_snapshot(active_snapshot))
            if not (
                isinstance(self._online.incumbent, InnovationStateSpace)
                and OnlineAdaptation.model_digest(self._online.incumbent) == active_digest
            ):
                incumbent = InnovationStateSpace.from_snapshot(active_snapshot)
                challenger = InnovationStateSpace.from_snapshot(active_snapshot)
                self._online = self._new_online_adaptation(incumbent, challenger)
                self._online._active_generation = state.role_generation
                self._online._challenger_generation = state.role_generation + 1
            self._online._active_generation = state.role_generation
            self._online._challenger_generation = max(
                self._online._challenger_generation,
                state.role_generation + 1,
            )
            rollback_snapshot = manager.rollback_snapshot
            if rollback_snapshot is not None:
                rollback_model_snapshot = rollback_snapshot
                online = rollback_snapshot.get("online_adaptation")
                if isinstance(online, Mapping) and isinstance(online.get("incumbent"), Mapping):
                    rollback_model_snapshot = online["incumbent"]
                if rollback_model_snapshot.get("schema") == "innovation-state-space/v2":
                    rollback = InnovationStateSpace.from_snapshot(rollback_model_snapshot)
                elif rollback_model_snapshot.get("schema") == _GreyBoxAdaptiveModel._SCHEMA:
                    rollback = _GreyBoxAdaptiveModel.from_snapshot(rollback_model_snapshot)
                else:
                    rollback = None
                if rollback is not None:
                    self._online._previous_incumbent = rollback
                    self._online._previous_incumbent_snapshot = copy.deepcopy(dict(rollback_model_snapshot))
                    self._online._previous_incumbent_digest = OnlineAdaptation.model_digest(rollback)
                    if self._online._rollback_generation is None:
                        self._online._rollback_generation = max(0, state.role_generation - 1)
        else:
            challenger = self._new_state_space_challenger()
            self._online = self._new_online_adaptation(self._new_grey_box_model(), challenger)
            self._online._active_generation = state.role_generation
            self._online._challenger_generation = state.role_generation + 1
        self._online._role_generation = state.role_generation
        self._online._failed_generations.update(state.failed_generations)
        self._online._begin_role_generation()

    def commit_active_parameter_promotion(self, manager, decision_id, confidence):
        """Publish the exact durable activation without re-adjudicating its confidence."""
        if not isinstance(manager, ActivationManager):
            raise TypeError("manager must be ActivationManager")
        pending = self._online_pending_parameter_promotion
        if self._online is None or pending is None or pending[0] != decision_id:
            return False
        state = manager.state
        active_snapshot = manager.active_snapshot
        if not isinstance(active_snapshot, Mapping):
            return False
        try:
            prospective = self._online.prospective_model(decision_id)
            committed_digest = OnlineAdaptation.model_digest(InnovationStateSpace.from_snapshot(active_snapshot))
        except KeyError, TypeError, ValueError, RuntimeError:
            return False
        if (
            state.active_kind != STATE_SPACE_KIND
            or state.decision_id != decision_id
            or state.active_digest != committed_digest
            or committed_digest != OnlineAdaptation.model_digest(prospective)
            or state.role_generation <= self._online.role_generation
            or state.role_generation in state.failed_generations
        ):
            return False

        # ActivationManager's durable CAS is the sole post-persistence authority.
        # Reconstructing from it consumes the old online pending decision by
        # replacing that role pair; a later caller-supplied confidence view must
        # not reverse ownership that the durable ledger already committed.
        self._activation_manager = manager
        self._synchronize_active_adaptation()
        self._online_pending_parameter_promotion = None
        self._online_promotion_count += 1
        self._model_revision += 1
        self._online_last_lifecycle_reason = "parameter-promotion"
        self._online_last_lifecycle = self._online_lifecycle("adopt", "parameter-promotion")
        return True

    def restore_activation(self, persisted, records):
        """Restore one durable activation without bypassing its exact lineage."""
        manager = ActivationManager(
            tuple(records),
            candidate_snapshot={},
            rollback_snapshot={},
            controller_configuration=self._activation_configuration,
            prospective_solve=lambda candidate, _configuration: self._activation_prospective_solve(candidate),
            persist_activation=lambda _record: False,
            append_evidence=self._activation_events.append,
            session_id="mpc-runtime-activation",
        )
        decision = manager.restore(persisted)
        self._activation_manager = manager
        self._activation_expected_temperature_c = None
        self._activation_residual_failures = 0
        restored_fallback = decision.reason == "restore-generation-already-failed"
        if not decision.accepted and not restored_fallback:
            return False
        self._synchronize_active_adaptation()
        if self._online is not None:
            self._online.reset_continuity()
        self._model_revision += 1
        return True

    def activation_runtime_failure(self, reason):
        """Immediately leave active state-space ownership for a named failure."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("activation fallback reason must be non-blank")
        manager = self._activation_manager
        if manager is None or manager.active_kind != STATE_SPACE_KIND:
            return False
        manager.fallback(
            reason.strip(),
            last_safe_command=self._last_combustion_load,
        )
        self._activation_expected_temperature_c = None
        self._activation_residual_failures = 0
        if self._online is not None:
            self._online.fence_active_generation(reason.strip())
            self._synchronize_active_adaptation()
            self._online.reset_continuity()
        self._model_revision += 1
        self._online_pending_lifecycle = {
            "event": "reject",
            "detail": reason.strip(),
            "model_revision": self._model_revision,
            "model_kind": STATE_SPACE_KIND,
            "role_generation": manager.state.failed_generation,
            "model_digest": manager.state.failed_digest,
            "fallback_kind": manager.state.fallback_kind,
            "last_safe_command": manager.state.last_safe_command,
        }
        return True

    def rollback_activation(self, reason):
        """Apply a persisted operator rollback without duplicating its decision."""
        manager = self._activation_manager
        if manager is None or manager.active_kind != STATE_SPACE_KIND:
            return False
        manager.fallback(reason, last_safe_command=self._last_combustion_load)
        self._activation_expected_temperature_c = None
        self._activation_residual_failures = 0
        if self._online is not None:
            self._online.fence_active_generation(reason)
            self._synchronize_active_adaptation()
            self._online.reset_continuity()
        self._model_revision += 1
        return True

    def drain_activation_events(self):
        events = tuple(self._activation_events)
        self._activation_events.clear()
        return events

    @staticmethod
    def _is_state_space_model(model):
        return isinstance(model, (InnovationStateSpace, _StateSpaceShadow))

    def _state_space_refresh_evidence(self, model):
        snapshot = model.snapshot()
        latest = (
            model.diagnostics
            if isinstance(model, InnovationStateSpace)
            else model._model.diagnostics
            if isinstance(model, _StateSpaceShadow) and model._fitted
            else None
        )
        if latest is None:
            diagnostics = snapshot.get("diagnostics")
            if not isinstance(diagnostics, Mapping):
                return None
            attempts = tuple(
                {
                    "order": attempt["order"],
                    "delay": attempt["delay"],
                    "sample_count": attempt["sample_count"],
                    "hankel_shape": tuple(attempt["hankel_shape"]),
                    "singular_values": tuple(attempt["singular_values"]),
                    "effective_rank": attempt["effective_rank"],
                    "alignment_error_c": attempt["alignment_error_c"],
                    "rejection_reasons": tuple(attempt["rejection_reasons"]),
                    "elapsed_ms": attempt["elapsed_ms"],
                }
                for attempt in diagnostics.get("attempts", ())
                if isinstance(attempt, Mapping)
            )
            accepted = diagnostics.get("accepted")
            terminal_reason = diagnostics.get("terminal_reason")
            selected_order = diagnostics.get("selected_order")
            selected_delay = diagnostics.get("selected_delay")
        else:
            attempts = tuple(
                {
                    "order": attempt.order,
                    "delay": attempt.delay,
                    "sample_count": attempt.sample_count,
                    "hankel_shape": attempt.hankel_shape,
                    "singular_values": attempt.singular_values,
                    "effective_rank": attempt.effective_rank,
                    "alignment_error_c": attempt.alignment_error_c,
                    "rejection_reasons": tuple(reason.value for reason in attempt.rejection_reasons),
                    "elapsed_ms": attempt.elapsed_ms,
                }
                for attempt in latest.attempts
            )
            accepted = latest.accepted
            terminal_reason = None if latest.terminal_reason is None else latest.terminal_reason.value
            selected_order = latest.selected_order
            selected_delay = latest.selected_delay
        evidence = {
            "accepted": accepted,
            "terminal_reason": terminal_reason,
            "attempts": attempts,
            "refresh_duration_ms": sum(attempt["elapsed_ms"] for attempt in attempts),
            "state_space_digest": OnlineAdaptation.model_digest(model),
        }
        if not accepted:
            return evidence
        selected = next(
            (
                attempt
                for attempt in attempts
                if (attempt["order"], attempt["delay"]) == (selected_order, selected_delay)
            ),
            None,
        )
        model_data = snapshot.get("model")
        if selected is None or not isinstance(model_data, Mapping):
            return None
        process = np.asarray(model_data.get("process_covariance"), dtype=float)
        poles = np.asarray(model_data.get("poles"), dtype=float)
        evidence.update(
            {
                "order": selected["order"],
                "delay": selected["delay"],
                "singular_values": selected["singular_values"],
                "effective_rank": selected["effective_rank"],
                "alignment_error_c": selected["alignment_error_c"],
                "max_pole_magnitude": float(np.max(poles)),
                "process_covariance_trace": float(np.trace(process)),
                "measurement_covariance": model_data.get("measurement_covariance"),
            }
        )
        return evidence

    def _compact_refresh_evidence(self, decision, *, production_prospective: bool = False):
        """Freeze selected state-space diagnostics for the compact ledger."""
        if self._online is None or not self._is_state_space_model(self._online.challenger):
            return None
        model = self._online.challenger
        refresh = self._state_space_refresh_evidence(model)
        snapshot = model.snapshot()
        model_data = snapshot.get("model")
        if not isinstance(refresh, Mapping) or not isinstance(model_data, Mapping):
            return None
        poles = np.asarray(model_data.get("poles"), dtype=float)
        covariance = np.asarray(model_data.get("process_covariance"), dtype=float)
        gain = model_data.get("steady_gain")
        delay = refresh.get("delay")
        alignment = refresh.get("alignment_error_c")
        order = refresh.get("order")
        finite = (
            poles.size > 0
            and covariance.size > 0
            and np.isfinite(poles).all()
            and np.isfinite(covariance).all()
            and isinstance(gain, (int, float))
            and math.isfinite(float(gain))
        )
        round_trip = False
        if isinstance(model, InnovationStateSpace):
            try:
                restored = InnovationStateSpace.from_snapshot(snapshot)
                round_trip = restored.snapshot() == snapshot
            except ValueError, FloatingPointError, RuntimeError:
                round_trip = False
        braking = decision.candidate_braking_score
        incumbent_braking = decision.incumbent_braking_score
        return RefreshDiagnosticsEvidence(
            accepted=refresh.get("accepted") is True,
            reason=refresh.get("terminal_reason") if isinstance(refresh.get("terminal_reason"), str) else None,
            full_rank=isinstance(order, int) and refresh.get("effective_rank") == order,
            finite_diagnostics=finite,
            pole_magnitude=float(np.max(poles)) if finite else None,
            gain=float(gain) if isinstance(gain, (int, float)) else None,
            delay_steps=delay if isinstance(delay, int) else None,
            covariance_finite=finite,
            alignment_error_c=float(alignment) if isinstance(alignment, (int, float)) else None,
            snapshot_round_trip=round_trip,
            sequential_wins=decision.consecutive_wins,
            generation_continuity=not any(
                reason.value in {"continuity", "stale-generation"} for reason in decision.reasons
            ),
            atomic_persistence=False,
            production_prospective=production_prospective,
            braking_error_c=braking,
            incumbent_braking_error_c=incumbent_braking,
        )

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
        active_kind = (
            "innovation-state-space"
            if isinstance(incumbent, InnovationStateSpace)
            else "scheduled-arx"
            if isinstance(incumbent, ScheduledARX)
            else "grey-box"
        )
        return {
            "enabled": True,
            "active_model_kind": active_kind,
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

    @staticmethod
    def _completed_origin_payloads(origin) -> tuple[CompletedOriginEvidence, ForecastOriginEvidence]:
        """Serialize one completed causal event into raw and compact forms."""
        raw = CompletedOriginEvidence(
            origin_time_ms=int(origin.origin_time_s * 1_000),
            completion_time_ms=int(origin.completion_time_s * 1_000),
            horizon_steps=origin.horizon_steps,
            generation=origin.generation,
            observed_temperature_c=origin.observed_temperature_c,
            incumbent_error_c=origin.incumbent_error_c,
            challenger_error_c=origin.challenger_error_c,
            braking=origin.braking,
            observation_sequence=origin.observation_sequence,
            incumbent_digest=origin.incumbent_digest,
            challenger_digest=origin.challenger_digest,
            incumbent_prediction_c=origin.incumbent_prediction_c,
            challenger_prediction_c=origin.challenger_prediction_c,
            temperature_band=origin.temperature_band,
            ambient_source=origin.ambient_source,
        )
        compact = ForecastOriginEvidence(
            origin_sequence=origin.observation_sequence,
            origin_time_ms=raw.origin_time_ms,
            completion_time_ms=raw.completion_time_ms,
            horizon_steps=raw.horizon_steps,
            incumbent_digest=raw.incumbent_digest,
            challenger_digest=raw.challenger_digest,
            incumbent_prediction_c=raw.incumbent_prediction_c,
            challenger_prediction_c=raw.challenger_prediction_c,
            observed_temperature_c=raw.observed_temperature_c,
            incumbent_error_c=raw.incumbent_error_c,
            challenger_error_c=raw.challenger_error_c,
            temperature_band=raw.temperature_band,
            phase="coasting" if raw.braking else "heating",
            ambient_source=raw.ambient_source,
            calibration_fit=False,
        )
        return raw, compact

    def _evaluation_payloads(
        self, decision: EvaluationDecision
    ) -> tuple[ModelEvaluationPayload, tuple[ForecastOriginEvidence, ...]]:
        events = tuple(self._completed_origin_payloads(origin) for origin in decision.completed_origins)
        raw_origins = tuple(event[0] for event in events)
        return (
            ModelEvaluationPayload(
                decision_id=decision.decision_id,
                evaluated_at_ms=int(decision.evaluated_at_s * 1_000),
                role_generation=decision.generation,
                promoted=decision.promoted,
                committed=decision.committed,
                consecutive_wins=decision.consecutive_wins,
                rejection_reasons=tuple(reason.value for reason in decision.reasons),
                incumbent_prediction_score=decision.incumbent_prediction_score,
                challenger_prediction_score=decision.candidate_prediction_score,
                incumbent_braking_score=decision.incumbent_braking_score,
                challenger_braking_score=decision.candidate_braking_score,
                sample_count=decision.sample_count,
                prospective_digest=decision.prospective_digest,
                window_start_ms=int(decision.window_start_s * 1_000),
                window_end_ms=int(decision.window_end_s * 1_000),
                incumbent_digest=decision.incumbent_digest,
                challenger_digest=decision.challenger_digest,
                completed_origins=raw_origins,
                horizon_scores=tuple(
                    HorizonScoreEvidence(
                        horizon_steps=score.horizon_steps,
                        incumbent_rmse_c=score.incumbent_rmse_c,
                        challenger_rmse_c=score.challenger_rmse_c,
                        sample_count=score.sample_count,
                    )
                    for score in decision.horizon_scores
                ),
                evaluation_duration_ms=decision.evaluation_duration_ms,
                challenger_model_kind=(
                    "innovation-state-space" if self._is_state_space_model(self._online.challenger) else "scheduled-arx"
                ),
                state_space_refresh=(
                    self._state_space_refresh_evidence(self._online.challenger)
                    if self._is_state_space_model(self._online.challenger)
                    else None
                ),
            ),
            tuple(event[1] for event in events),
        )

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
        challenger = self._online.challenger
        if self._is_state_space_model(challenger):
            evidence = self._state_space_refresh_evidence(challenger)
            if evidence is not None:
                self._online_last_evaluation.update(
                    challenger_model_kind="innovation-state-space",
                    state_space_refresh=evidence,
                )
        if isinstance(decision, EvaluationDecision):
            self._online_last_evaluation.update(
                {
                    "window_start_s": decision.window_start_s,
                    "window_end_s": decision.window_end_s,
                    "incumbent_digest": decision.incumbent_digest,
                    "challenger_digest": decision.challenger_digest,
                    "completed_origins": tuple(asdict(origin) for origin in decision.completed_origins),
                    "horizon_scores": tuple(asdict(score) for score in decision.horizon_scores),
                    "evaluation_duration_ms": decision.evaluation_duration_ms,
                }
            )

    def _online_lifecycle(self, event, detail):
        model = self._online.incumbent
        snapshot = model.snapshot()
        state_space = self._is_state_space_model(model)
        lifecycle = {
            "event": event,
            "model_revision": self._model_revision,
            "provenance": "online-adaptation",
            "detail": detail,
            "model_kind": (
                "innovation-state-space"
                if state_space
                else "scheduled-arx"
                if isinstance(model, ScheduledARX)
                else "grey-box"
            ),
            "model_schema": snapshot.get("schema"),
            "role_generation": self._online.role_generation,
            "snapshot_digest": OnlineAdaptation.model_digest(model),
            "parameters": (),
        }
        if state_space:
            lifecycle["state_space_refresh"] = self._state_space_refresh_evidence(model)
        return lifecycle

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
        if isinstance(decision, EvaluationDecision):
            decision = replace(decision, evaluation_duration_ms=self._online_evaluation_duration * 1_000)
        self._record_evaluation(decision)
        evaluation_payload, forecast_origin_evidence = (
            self._evaluation_payloads(decision) if isinstance(decision, EvaluationDecision) else (None, ())
        )
        refresh_diagnostics_evidence = (
            self._compact_refresh_evidence(decision, production_prospective=False)
            if isinstance(decision, EvaluationDecision)
            else None
        )
        self._model_revision += 1
        if not decision.promoted:
            return {
                "evaluation": self._online_last_evaluation,
                "evaluation_payload": evaluation_payload,
                "forecast_origin_evidence": forecast_origin_evidence,
                "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
            }
        if (
            self._is_state_space_model(self._online.challenger)
            and not self._online_experiment_active
            and not self._active_state_space()
        ):
            self._online.reject_prospective(decision.decision_id, "experiment-activation-gate")
            self._online_last_lifecycle_reason = "experiment-activation-gate"
            return {
                "evaluation": self._online_last_evaluation,
                "evaluation_payload": evaluation_payload,
                "forecast_origin_evidence": forecast_origin_evidence,
                "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
            }
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
            refresh_diagnostics_evidence = self._compact_refresh_evidence(decision, production_prospective=True)
            if self._active_state_space():
                self._online_pending_parameter_promotion = (decision.decision_id, solve)
                self._online_last_lifecycle_reason = "confidence-transaction-required"
                return {
                    "evaluation": self._online_last_evaluation,
                    "evaluation_payload": evaluation_payload,
                    "forecast_origin_evidence": forecast_origin_evidence,
                    "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
                }
        except Exception as error:
            detail = str(error)
            self._online.reject_prospective(decision.decision_id, detail)
            self._online_last_lifecycle_reason = detail
            lifecycle = self._online_lifecycle("reject", detail)
            self._online_last_rejection_reason = detail
            self._online_last_lifecycle = lifecycle
            return {
                "evaluation": self._online_last_evaluation,
                "evaluation_payload": evaluation_payload,
                "forecast_origin_evidence": forecast_origin_evidence,
                "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
                "lifecycle": lifecycle,
            }
        if not self._online.commit_promotion(decision.decision_id, solve):
            return {
                "evaluation": self._online_last_evaluation,
                "evaluation_payload": evaluation_payload,
                "forecast_origin_evidence": forecast_origin_evidence,
                "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
            }
        self._online_promotion_count += 1
        self._online_last_lifecycle_reason = "promotion"
        self._model_revision += 1
        self._record_evaluation(decision, committed=True)
        evaluation_payload = replace(evaluation_payload, committed=True)
        lifecycle = self._online_lifecycle("adopt", "promotion")
        self._online_last_lifecycle = lifecycle
        return {
            "evaluation": self._online_last_evaluation,
            "evaluation_payload": evaluation_payload,
            "forecast_origin_evidence": forecast_origin_evidence,
            "refresh_diagnostics_evidence": refresh_diagnostics_evidence,
            "lifecycle": lifecycle,
        }

    def observe_frame(self, observation):
        """Dispatch passive and completed operator frames to Task 7."""
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        if self._learning is None:
            return None
        result = self._learning.observe_completed_frame(
            observation,
            identifiability=1.0,
        )
        reasons = tuple(result.history.reasons)
        return {
            "role_generation": observation.role_generation,
            "eligible": bool(result.history.accepted),
            "rejection_reasons": reasons,
            "input_variance": 0.0,
            "input_levels": 0,
            "incumbent_innovation_c": None,
            "challenger_innovation_c": None,
            "effective_updates": len(self._learning.passive_history.observations)
            if hasattr(self._learning, "passive_history")
            else 0,
            "model_digest": grey_config_digest(self.mpc.config),
            "forecast_origin_evidence": tuple(result.completed_forecasts),
        }

    def observation_failure(self, observation, error):
        """Turn an isolated learning-hook failure into explicit frame evidence."""
        if self._learning is None or not isinstance(observation, FrameObservation):
            return None
        return {
            "role_generation": observation.role_generation,
            "eligible": False,
            "rejection_reasons": ("learner-exception",),
            "input_variance": 0.0,
            "input_levels": 0,
            "incumbent_innovation_c": None,
            "challenger_innovation_c": None,
            "effective_updates": 0,
            "model_digest": grey_config_digest(self.mpc.config),
            "learner_error": f"{type(error).__name__}: {error}",
            "forecast_origin_evidence": (),
        }

    def bind_learning_identity(self, session_id, cook_id, role_generation):
        """Fence Task 7 work to the runner's current cook/configuration identity."""
        self._learning_session_id = session_id
        self._learning_cook_id = cook_id
        self._learning_role_generation = int(role_generation)
        if self._learning is not None:
            self._learning.update_identity(
                self._learning_identity(),
                config=self.mpc.config,
                incumbent_pair=CandidatePair(self.estimator, self.mpc),
            )

    def poll_learning_off_path(self, *, live_origin):
        """Build/evaluate candidates only when called by the lifecycle worker."""
        if self._learning is None:
            return None, None
        origin = live_origin if isinstance(live_origin, CandidateOrigin) else CandidateOrigin(live_origin)
        delivery = self._learning.poll_fit_off_path(
            live_identity=self._learning_identity(),
            live_origin=origin,
        )
        evaluation = self._learning.evaluate_ready_off_path()
        return delivery, evaluation

    def get_status(self):
        return {
            "set_point": _finite_float(getattr(self, "set_point", self._set_point_c)),
            "set_point_c": _finite_float(self._set_point_c),
            "last_combustion_load": _finite_float(self._last_combustion_load),
            "last_raw_combustion_load": _optional_float(self._last_raw_combustion_load),
            "last_equilibrium_load": _optional_float(self._last_equilibrium_load),
            "last_residual_load": _optional_float(self._last_residual_load),
            "applied_combustion_load": _finite_float(self._applied_combustion_load),
            "policy": "acados-grey",
            "policy_kind": "acados-grey",
            "n_horizon": int(self.cfg["n_horizon"]),
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
            "activation": (
                {
                    "active_kind": GREY_BOX_KIND,
                    "active_digest": None,
                    "decision_id": None,
                    "role_generation": 0,
                    "failed_digest": None,
                    "failed_generation": None,
                    "last_safe_command": None,
                    "fallback_kind": None,
                    "fallback_reason": None,
                }
                if self._activation_manager is None
                else asdict(self._activation_manager.state)
            ),
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
        active_kind = (
            "innovation-state-space"
            if isinstance(self._online.incumbent, InnovationStateSpace)
            else "scheduled-arx"
            if self._active_arx()
            else "grey-box"
        )
        return {
            **self._online.snapshot(),
            "active_model_kind": active_kind,
            "eligible_updates": self._online_eligible_updates,
            "rejected_updates": self._online_rejected_updates,
            "promotion_count": self._online_promotion_count,
            "rollback_count": self._online_rollback_count,
            "last_lifecycle_reason": self._online_last_lifecycle_reason,
            "last_evaluation": copy.deepcopy(self._online_last_evaluation),
            "last_lifecycle": copy.deepcopy(self._online_last_lifecycle),
        }

    def get_model_snapshot(self):
        """Return only the active grey physical model; Task 11 owns reports."""
        if self._model_meta is None:
            return None
        snapshot = {
            "version": self._MODEL_SCHEMA,
            "revision": int(self._model_revision),
            "params": {key: float(self.cfg[key]) for key in self._MODEL_PARAM_KEYS},
            **self._model_meta,
            "band_c": list(self._model_meta["band_c"]),
        }
        try:
            encoded = json.dumps(snapshot, allow_nan=False).encode()
        except (TypeError, ValueError, OverflowError):
            return None
        return snapshot if len(encoded) <= MAX_SNAPSHOT_BYTES else None

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
        old_estimator = self.estimator
        old_solver = self.mpc
        old_learning = self._learning
        self.cfg.update(merged)
        self.estimator, self._net, self.model, self.mpc = rebuilt
        self._learning = None
        self._close_component(old_learning)
        self._close_component(old_solver)
        self._close_component(old_estimator)
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
            self._learning = self._build_learning()
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

    def set_safety_ceiling_c(self, ceiling_c) -> None:
        """Track the grill's configured maximum, which probing must stay under.

        Read from settings on every tick rather than carried on a calibration
        command, so lowering the grill maximum mid-cook binds the next probe
        instead of the next operator action.
        """
        value = float(ceiling_c)
        if not math.isfinite(value):
            raise ValueError("safety ceiling must be finite Celsius")
        self._calibration_safety_ceiling_c = value

    def request_calibration(self, command: CalibrationCommand) -> None:
        """Queue each strictly newer operator command for ordered consumption."""
        if not isinstance(command, CalibrationCommand):
            raise TypeError("command must be CalibrationCommand")
        if command.command_revision < self._calibration_last_revision:
            raise ValueError("calibration command revision must be monotonic")
        if command.command_revision == self._calibration_last_revision:
            return
        self._calibration_operations.append(("command", command))
        self._calibration_last_revision = command.command_revision

    def cancel_calibration(self, reason: str) -> None:
        """Queue a safety abort without consuming an operator revision."""
        if not isinstance(reason, str) or not reason:
            raise ValueError("calibration cancellation reason must be a non-empty string")
        self._calibration_operations.append(("cancel", reason))

    def _predict_calibration_max(self, baseline_q: float, probe_q: float, runtime: CalibrationRuntimeContext) -> float:
        """Forecast the incumbent grey-box horizon for one requested probe."""
        horizon = max(1, int(self.cfg["n_horizon"]))
        requested_q = float(np.clip(baseline_q + probe_q, 0.0, 1.0))
        adapter = GreyBoxPredictionAdapter.from_controller(self)
        forecast = adapter.forecast(
            np.full(horizon, requested_q, dtype=np.float64),
            np.full(horizon, self._calibration_ambient_c, dtype=np.float64),
        )
        if forecast.size != horizon or not np.isfinite(forecast).all():
            raise FloatingPointError("grey-box calibration forecast is non-finite")
        return float(np.max(forecast))

    def _calibration_runtime(
        self,
        baseline_q: float,
        temperature_c: float,
        *,
        realized_q: float | None = None,
        continuous: bool = True,
        actuation_known: bool = True,
    ) -> CalibrationRuntimeContext:
        return CalibrationRuntimeContext(
            now_s=time.monotonic(),
            temp_c=temperature_c,
            target_c=self._set_point_c,
            baseline_q=baseline_q,
            realized_q=self._applied_combustion_load if realized_q is None else realized_q,
            safety_ceiling_c=self._calibration_safety_ceiling_c,
            allocator_headroom=1.0,
            error_rate_headroom=1.0,
            capability_headroom=1.0,
            saturation_headroom=1.0,
            rank_progress=1.0,
            coverage_progress=1.0,
            continuous=continuous,
            actuation_known=actuation_known,
        )

    @staticmethod
    def _command_decision(
        decision: CalibrationDecision, command: CalibrationCommand, command_generation: int
    ) -> CalibrationDecision:
        return replace(
            decision,
            command_revision=command.command_revision,
            command_action=command.action,
            command_generation=command_generation,
        )

    def _advance_calibration(self, baseline_q: float, temperature_c: float) -> CalibrationDecision:
        decision: CalibrationDecision | None = None
        while self._calibration_feedback:
            (
                feedback_baseline_q,
                realized_q,
                continuous,
                actuation_known,
                disposition,
                _revision,
                command_revision,
                command_action,
                command_generation,
            ) = self._calibration_feedback.popleft()
            provenance = self._trace_calibration
            if not provenance.active or (
                provenance.command_revision,
                provenance.command_action,
                provenance.command_generation,
            ) != (command_revision, command_action, command_generation):
                continue
            if disposition is FrameFeedbackDisposition.DISCARDED:
                decision = self._calibration.cancel_probe("discarded_frame")
            elif disposition is FrameFeedbackDisposition.COMPLETE:
                decision = replace(
                    self._calibration.advance(
                        self._calibration_runtime(
                            feedback_baseline_q,
                            temperature_c,
                            realized_q=realized_q,
                            continuous=continuous,
                            actuation_known=actuation_known,
                        )
                    ),
                    command_revision=provenance.command_revision,
                    command_action=provenance.command_action,
                    command_generation=provenance.command_generation,
                )
            else:
                continue
            self._trace_calibration = decision
        while self._calibration_operations:
            operation, payload = self._calibration_operations.popleft()
            if operation == "cancel":
                decision = self._calibration.cancel_probe(payload)
            else:
                command = payload
                assert isinstance(command, CalibrationCommand)
                self._calibration_ambient_c = command.ambient_c
                runtime = self._calibration_runtime(baseline_q, temperature_c)
                if command.action == "start":
                    self._calibration_generation += 1
                    command_generation = self._calibration_generation
                else:
                    command_generation = self._trace_calibration.command_generation
                if command.action == "start":
                    decision = self._calibration.start(
                        _CoordinatorCalibrationCommand(command.command_revision, command.seed),
                        runtime,
                    )
                elif command.action == "pause":
                    decision = self._calibration.pause()
                elif command.action == "resume":
                    decision = self._calibration.resume(runtime)
                elif command.action == "stop":
                    decision = self._calibration.stop(runtime)
                else:
                    decision = self._calibration.reset_progress(runtime)
                decision = self._command_decision(decision, command, command_generation)
            self._trace_calibration = decision
        if decision is None:
            decision = replace(
                self._trace_calibration,
                events=(),
            )
        self._trace_calibration = decision
        return decision

    def register_calibration_result(self, result) -> None:
        """Associate a completed runner result with the frame that may latch it."""
        calibration = result.calibration
        baseline = result.baseline_allocation
        if calibration is None or baseline is None or result.revision <= 0:
            return
        self._calibration_frame_results[result.revision] = (
            baseline.normalized_combustion_load,
            calibration,
        )

    def set_output(self, applied):
        """Record physical output and terminalize only explicit frame feedback."""
        realized_q = normalized_load_from_auger_duty(applied.ratio, u_max=self.u_max)
        self._applied_combustion_load = realized_q
        if applied.feedback_disposition is FrameFeedbackDisposition.PROGRESS:
            return
        revision = applied.producing_result_revision
        produced = self._calibration_frame_results.pop(revision, None) if revision > 0 else None
        if produced is None:
            return
        for stale_revision in tuple(self._calibration_frame_results):
            if stale_revision < revision:
                del self._calibration_frame_results[stale_revision]
        baseline_q, decision = produced
        if not decision.active or (
            decision.command_revision,
            decision.command_action,
            decision.command_generation,
        ) != (
            applied.producing_calibration_revision,
            applied.producing_calibration_action,
            applied.producing_calibration_generation,
        ):
            return
        previous = self._calibration_last_feedback_timestamp
        continuous = previous is None or applied.timestamp > previous
        self._calibration_last_feedback_timestamp = applied.timestamp
        disposition = applied.feedback_disposition
        if disposition is FrameFeedbackDisposition.COMPLETE and not applied.sample_complete:
            disposition = FrameFeedbackDisposition.DISCARDED
        self._calibration_feedback.append(
            (
                baseline_q,
                realized_q,
                continuous,
                applied.controller_commanded,
                disposition,
                revision,
                decision.command_revision,
                decision.command_action,
                decision.command_generation,
            )
        )

    def _equilibrium_load(self, target, disturbance):
        """Return the bounded equilibrium passed to the native residual model."""
        if not _model_is_identified(self.cfg, self._model_meta):
            return 0.0
        return float(
            np.clip(
                steady_combustion_load(self.cfg, target, disturbance),
                0.0,
                1.0,
            )
        )

    def _validated_native_command(self, solve):
        horizon = int(self.cfg["n_horizon"])
        sequence = np.asarray(solve.sequence_q, dtype=float)
        residual = np.asarray(solve.sequence_residual, dtype=float)
        objective = float(solve.objective)
        diagnostics = solve.diagnostics
        diagnostic_values = (
            diagnostics.solve_time_s,
            diagnostics.objective,
            diagnostics.kkt_residual,
            diagnostics.constraint_residual,
        )
        if (
            sequence.shape != (horizon,)
            or residual.shape != (horizon,)
            or not np.isfinite(sequence).all()
            or not np.isfinite(residual).all()
            or not np.all((0.0 <= sequence) & (sequence <= 1.0))
            or not math.isfinite(objective)
            or diagnostics.status != 0
            or diagnostics.backend_status != 0
            or isinstance(diagnostics.iterations, bool)
            or not isinstance(diagnostics.iterations, int)
            or diagnostics.iterations < 0
            or not all(math.isfinite(float(value)) and float(value) >= 0.0 for value in diagnostic_values)
            or not isinstance(diagnostics.warm_started, bool)
        ):
            raise ValueError("native grey-box result is malformed")
        self._native_failure_diagnostics = None
        return float(sequence[0])

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
        try:
            solve = self.mpc.solve(
                x_hat,
                setpoint_c=self._set_point_c,
                q_previous=applied_combustion_load,
                equilibrium_q=equilibrium,
            )
            combustion_load = self._validated_native_command(solve)
            raw_firing_load = combustion_load
            residual_move = combustion_load - equilibrium
        except Exception as error:
            failure_state = MpcFailureState.POLICY_EXCEPTION
            failure_error = error
            if isinstance(error, SolverError):
                self._native_failure_diagnostics = error.diagnostics
            combustion_load = self._last_combustion_load
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
            # The runner owns stale/deadline fallback; the controller keeps the
            # last physically safe command until that ownership boundary acts.

        self._last_equilibrium_load = equilibrium
        self._last_residual_load = residual_move
        self._last_raw_combustion_load = raw_firing_load
        self._last_combustion_load = combustion_load
        allocation_kwargs = {
            "u_max": self.u_max,
            "fan_min_pct": self.cfg["fan_min_pct"],
            "fan_max_pct": self.cfg["fan_max_pct"],
            "enable_fan": bool(self.cfg["enable_fan_input"]),
        }
        baseline_allocation = allocate(combustion_load, **allocation_kwargs)
        calibration = self._advance_calibration(combustion_load, y)
        requested_load = float(np.clip(combustion_load + calibration.probe_q, 0.0, 1.0))
        allocation = allocate(requested_load, **allocation_kwargs)
        auger = allocation.auger_duty
        self._trace_baseline_allocation = baseline_allocation
        self._trace_allocation = allocation
        # The runner registers this immutable trace under its own completion
        # revision after it atomically captures the result.
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
            policy_kind="acados-grey",
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

    def trace_baseline_allocation(self) -> AllocationResult | None:
        return self._trace_baseline_allocation

    def trace_calibration(self) -> CalibrationDecision:
        return self._trace_calibration

    def native_failure_diagnostics(self) -> SolverDiagnostics | None:
        return self._native_failure_diagnostics

    def close(self):
        """Release learning, native solver, and estimator exactly once."""
        if self._closed:
            return
        self._closed = True
        learning = self._learning
        self._learning = None
        self._close_component(learning)
        self._close_component(self.mpc)
        self._close_component(self.estimator)
