"""Bounded deterministic innovation state-space identification for linear MPC."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from numbers import Integral, Real
from time import perf_counter
from typing import cast

import numpy as np
import numpy.typing as npt

from .contracts import AffinePrediction, FloatArray, FrameObservation, ModelUpdate

_SCHEMA = "innovation-state-space/v2"
_EPSILON = np.finfo(np.float64).eps
_FRAME_SECONDS = 20.0
_FRAME_TIME_TOLERANCE = 1e-9
_SNAPSHOT_MAX_BYTES = 65_536
_MAX_ORDER = 8
_MAX_DELAY = 30
_MAX_BLOCK_ROWS = 32
_MAX_CANDIDATES = 64
_MAX_AFFINE_PREDICTION_HORIZON = 180
_MAX_BUFFER_SAMPLES = 1_800


class RefreshRejectionReason(StrEnum):
    """Typed outcome of a bounded candidate realization attempt."""

    INSUFFICIENT_SAMPLES = "insufficient-samples"
    RANK_DEFICIENT = "rank-deficient"
    ILL_CONDITIONED = "ill-conditioned"
    UNSTABLE_AFTER_PROJECTION = "unstable-after-projection"
    IMPLAUSIBLE_GAIN = "implausible-gain"
    ALIGNMENT_FAILED = "alignment-failed"
    NONFINITE = "nonfinite"
    NO_VALID_CANDIDATE = "no-valid-candidate"


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """Evidence captured at one configured ``(order, delay)`` attempt."""

    order: int
    delay: int
    sample_count: int
    hankel_shape: tuple[int, int]
    singular_values: tuple[float, ...]
    effective_rank: int
    condition_number: float | None
    projection_applied: bool
    steady_gain: float | None
    alignment_error_c: float | None
    prediction_score: float | None
    braking_score: float | None
    rejection_reasons: tuple[RefreshRejectionReason, ...]
    elapsed_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "order", _integer(self.order, "order", minimum=1))
        object.__setattr__(self, "delay", _integer(self.delay, "delay", minimum=1))
        object.__setattr__(self, "sample_count", _integer(self.sample_count, "sample_count", minimum=0))
        if len(self.hankel_shape) != 2:
            raise ValueError("hankel_shape must contain two dimensions")
        object.__setattr__(
            self,
            "hankel_shape",
            tuple(_integer(value, "hankel_shape", minimum=0) for value in self.hankel_shape),
        )
        object.__setattr__(
            self,
            "singular_values",
            tuple(_finite(value, "singular_values") for value in self.singular_values),
        )
        object.__setattr__(self, "effective_rank", _integer(self.effective_rank, "effective_rank", minimum=0))
        for name in (
            "condition_number",
            "steady_gain",
            "alignment_error_c",
            "prediction_score",
            "braking_score",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, None if value is None else _finite(value, name))
        if not isinstance(self.projection_applied, bool):
            raise ValueError("projection_applied must be a bool")
        if not isinstance(self.rejection_reasons, tuple) or not all(
            isinstance(reason, RefreshRejectionReason) for reason in self.rejection_reasons
        ):
            raise ValueError("rejection_reasons must be typed immutable rejection reasons")
        elapsed = _finite(self.elapsed_ms, "elapsed_ms")
        if elapsed < 0.0:
            raise ValueError("elapsed_ms must be non-negative")
        object.__setattr__(self, "elapsed_ms", elapsed)


@dataclass(frozen=True, slots=True)
class RefreshDiagnostics:
    """Immutable complete evidence for a transactional fit or refresh."""

    accepted: bool
    terminal_reason: RefreshRejectionReason | None
    attempts: tuple[CandidateAttempt, ...]
    selected_order: int | None = None
    selected_delay: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ValueError("accepted must be a bool")
        if self.terminal_reason is not None and not isinstance(self.terminal_reason, RefreshRejectionReason):
            raise ValueError("terminal_reason must be a typed rejection reason")
        if not isinstance(self.attempts, tuple) or not all(
            isinstance(attempt, CandidateAttempt) for attempt in self.attempts
        ):
            raise ValueError("attempts must be immutable candidate evidence")
        if self.accepted != (self.terminal_reason is None):
            raise ValueError("accepted diagnostics must have no terminal rejection")
        if self.accepted != (self.selected_order is not None and self.selected_delay is not None):
            raise ValueError("accepted diagnostics must identify the selected candidate")
        if not self.accepted and self.terminal_reason is None:
            raise ValueError("rejected diagnostics require a terminal reason")
        for name in ("selected_order", "selected_delay"):
            value = getattr(self, name)
            if self.accepted:
                object.__setattr__(self, name, _integer(value, name, minimum=1))


@dataclass(frozen=True, slots=True)
class StateSpaceConfig:
    """Finite limits and candidate grid for innovation state-space fitting."""

    orders: tuple[int, ...]
    delays: tuple[int, ...]
    block_rows: int = 8
    validation_fraction: float = 0.2
    parameter_penalty: float = 1e-5
    max_buffer_samples: int = 1_800
    refresh_interval_s: float = 300.0
    max_pole_magnitude: float = 0.999
    max_condition_number: float = 1e10
    steady_gain_scale_limit: float = 16.0
    held_out_forecast_scale_limit: float = 8.0
    covariance_floor: float = 1e-8
    covariance_ceiling: float = 1e6

    def __post_init__(self) -> None:
        orders = _positive_tuple(self.orders, "orders")
        delays = _positive_tuple(self.delays, "delays")
        if len(set(orders)) != len(orders) or len(set(delays)) != len(delays):
            raise ValueError("orders and delays must not contain duplicates")
        if max(orders) > _MAX_ORDER:
            raise ValueError(f"orders must not exceed {_MAX_ORDER}")
        if max(delays) > _MAX_DELAY:
            raise ValueError(f"delays must not exceed {_MAX_DELAY}")
        if len(orders) * len(delays) > _MAX_CANDIDATES:
            raise ValueError(f"candidate grid must not exceed {_MAX_CANDIDATES}")
        object.__setattr__(self, "orders", orders)
        object.__setattr__(self, "delays", delays)
        block_rows = _integer(self.block_rows, "block_rows", minimum=2)
        if block_rows > _MAX_BLOCK_ROWS:
            raise ValueError(f"block_rows must not exceed {_MAX_BLOCK_ROWS}")
        if max(orders) > block_rows:
            raise ValueError("block_rows must be at least the largest order")
        object.__setattr__(self, "block_rows", block_rows)
        for name, minimum, maximum in (
            ("validation_fraction", 0.0, 0.5),
            ("parameter_penalty", 0.0, None),
            ("refresh_interval_s", 0.0, None),
            ("max_pole_magnitude", 0.0, 1.0),
            ("max_condition_number", 1.0, None),
            ("steady_gain_scale_limit", 0.0, None),
            ("held_out_forecast_scale_limit", 0.0, None),
            ("covariance_floor", 0.0, None),
            ("covariance_ceiling", 0.0, None),
        ):
            value = _finite(getattr(self, name), name)
            if value <= minimum or (maximum is not None and value >= maximum):
                raise ValueError(f"{name} is outside its permitted finite range")
            object.__setattr__(self, name, value)
        if self.covariance_ceiling < self.covariance_floor:
            raise ValueError("covariance_ceiling must be at least covariance_floor")
        max_buffer_samples = _integer(self.max_buffer_samples, "max_buffer_samples", minimum=8)
        if max_buffer_samples > _MAX_BUFFER_SAMPLES:
            raise ValueError(f"max_buffer_samples must not exceed {_MAX_BUFFER_SAMPLES}")
        object.__setattr__(self, "max_buffer_samples", max_buffer_samples)


@dataclass(frozen=True, slots=True)
class _Bounds:
    max_steady_gain_c_per_q: float
    max_forecast_deviation_c: float


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Owned candidate-to-incumbent coordinate mapping evidence."""

    transform: FloatArray
    aligned_state: FloatArray
    output_error_c: float

    def __post_init__(self) -> None:
        state = _vector(self.aligned_state, "aligned_state")
        transform = _matrix(self.transform, (state.size, state.size), "transform")
        object.__setattr__(self, "transform", _readonly(transform))
        object.__setattr__(self, "aligned_state", _readonly(state))
        object.__setattr__(self, "output_error_c", _finite(self.output_error_c, "output_error_c"))


@dataclass(frozen=True, slots=True)
class _Realization:
    order: int
    delay: int
    A: FloatArray
    B: FloatArray
    C: FloatArray
    D: FloatArray
    E: FloatArray
    K: FloatArray
    process_covariance: FloatArray
    measurement_covariance: float
    input_mean: float
    steady_gain: float


class InnovationStateSpace:
    """Atomic, bounded SISO innovation realization over complete production frames."""

    def __init__(self, config: StateSpaceConfig) -> None:
        self._config = config
        self._model: _Realization | None = None
        self._state = np.empty(0, dtype=np.float64)
        self._state_covariance = np.empty((0, 0), dtype=np.float64)
        self._times: list[float] = []
        self._temperatures: list[float] = []
        self._inputs: list[float] = []
        self._ambients: list[float] = []
        self._buffer_samples = 0
        self._last_refresh_time_s: float | None = None
        self._refreshes = 0
        self._last_diagnostics = RefreshDiagnostics(False, RefreshRejectionReason.INSUFFICIENT_SAMPLES, ())
        self._alignment_error_c: float | None = None
        self._bounds: _Bounds | None = None

    @property
    def config(self) -> StateSpaceConfig:
        """Return this immutable model configuration."""
        return self._config

    @property
    def diagnostics(self) -> RefreshDiagnostics:
        """Return the evidence associated with the latest fit/refresh attempt."""
        return self._last_diagnostics

    def fit(self, observations: Sequence[FrameObservation]) -> RefreshDiagnostics:
        """Identify and install a realization only after full candidate validation."""
        frames = _bounded_frames(_frames(observations), self._config.max_buffer_samples)
        diagnostics, candidate, bounds = self._identify(frames)
        self._last_diagnostics = diagnostics
        if candidate is None:
            return diagnostics
        state = _state_from_frames(frames, candidate)
        covariance = candidate.process_covariance.copy()
        self._install(candidate, state, covariance, frames, bounds, reset_refreshes=True)
        self._alignment_error_c = None
        return diagnostics

    def refresh(self, observations: Sequence[FrameObservation]) -> RefreshDiagnostics:
        """Try a replacement realization without mutating an incumbent on rejection."""
        frames = _bounded_frames(_frames(observations), self._config.max_buffer_samples)
        diagnostics, candidate, bounds = self._identify(frames)
        if candidate is None:
            return diagnostics
        state = _state_from_frames(frames, candidate)
        covariance = candidate.process_covariance.copy()
        if self._model is not None:
            alignment = _align_refresh_realization(
                candidate,
                state,
                covariance,
                frames[-1].temp_c,
                frames[-1].frame_end_s,
                [frame.realized_q for frame in frames],
                [frame.ambient_c for frame in frames],
                self._model,
                self._state,
                self._state_covariance,
                self._temperatures[-1],
                self._times[-1],
                self._inputs,
                self._ambients,
                self._config,
            )
            if alignment is None:
                attempts = tuple(
                    _reject(attempt, RefreshRejectionReason.ALIGNMENT_FAILED)
                    if (attempt.order, attempt.delay) == (diagnostics.selected_order, diagnostics.selected_delay)
                    else attempt
                    for attempt in diagnostics.attempts
                )
                return RefreshDiagnostics(False, RefreshRejectionReason.ALIGNMENT_FAILED, attempts)
            candidate, state, covariance, output_error_c = alignment
            attempts = tuple(
                replace(attempt, alignment_error_c=output_error_c)
                if (attempt.order, attempt.delay) == (diagnostics.selected_order, diagnostics.selected_delay)
                else attempt
                for attempt in diagnostics.attempts
            )
            diagnostics = RefreshDiagnostics(
                True, None, attempts, diagnostics.selected_order, diagnostics.selected_delay
            )
            self._alignment_error_c = output_error_c
        self._install(candidate, state, covariance, frames, bounds, reset_refreshes=False)
        self._last_diagnostics = diagnostics
        return diagnostics

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        """Assimilate a complete frame and trigger a bounded periodic refresh."""
        update = self._assimilate(observation, updated=True)
        if (
            self._last_refresh_time_s is not None
            and observation.frame_end_s - self._last_refresh_time_s >= self._config.refresh_interval_s
        ):
            self._last_diagnostics = self.refresh(self._history_frames())
            self._last_refresh_time_s = observation.frame_end_s
        return update

    def track(self, observation: FrameObservation) -> ModelUpdate:
        """Assimilate a frame without claiming a parameter-model update."""
        return self._assimilate(observation, updated=False)

    def forecast(
        self,
        prefix: Sequence[FrameObservation],
        q_future: npt.ArrayLike,
        ambient_future: npt.ArrayLike,
    ) -> FloatArray:
        """Produce a finite open-loop forecast from an independent frame prefix."""
        model = self._require_model()
        frames = _bounded_frames(_frames(prefix), self._config.max_buffer_samples)
        if not frames:
            raise ValueError("prefix must contain at least one frame")
        inputs = _vector(q_future, "q_future")
        ambients = _vector(ambient_future, "ambient_future")
        if inputs.size != ambients.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        state = _state_from_frames(frames, model)
        history = [frame.realized_q for frame in frames]
        prediction = np.empty(inputs.size, dtype=np.float64)
        for index, ambient in enumerate(ambients):
            target = len(history)
            state = _advance(model, state, _delayed(history, target - 1, model.delay))
            prediction[index] = _output(model, state, _delayed(history, target, model.delay), float(ambient))
            history.append(float(inputs[index]))
        if not np.isfinite(prediction).all():
            raise RuntimeError("state-space forecast is non-finite")
        prediction.setflags(write=False)
        return prediction

    def affine_prediction(
        self, horizon_steps: int, q_previous: float, ambient_future: npt.ArrayLike
    ) -> AffinePrediction:
        """Return the finite lower-triangular open-loop input response."""
        horizon = _integer(horizon_steps, "horizon_steps", minimum=0)
        if horizon > _MAX_AFFINE_PREDICTION_HORIZON:
            raise ValueError(f"horizon_steps must not exceed {_MAX_AFFINE_PREDICTION_HORIZON}")
        model = self._require_model()
        previous = _finite(q_previous, "q_previous")
        if not 0.0 <= previous <= 1.0:
            raise ValueError("q_previous must be in [0, 1]")
        ambient = _vector(ambient_future, "ambient_future")
        if ambient.size != horizon:
            raise ValueError("ambient_future length must equal horizon_steps")
        inputs = list(self._inputs)
        if not inputs:
            raise RuntimeError("fit must be called before prediction")
        inputs[-1] = previous
        history_length = len(inputs)

        state = self._state.copy()
        response_state = np.zeros((model.order, horizon), dtype=np.float64)
        free = np.empty(horizon, dtype=np.float64)
        response = np.zeros((horizon, horizon), dtype=np.float64)
        for step in range(horizon):
            index = history_length + step
            transition_constant, transition_response = _affine_input(
                inputs, history_length, index - 1, model.delay, horizon
            )
            output_constant, output_response = _affine_input(inputs, history_length, index, model.delay, horizon)
            state = _advance(model, state, transition_constant)
            response_state = model.A @ response_state + np.outer(model.B, transition_response)
            free[step] = _output(model, state, output_constant, float(ambient[step]))
            response[step] = model.C @ response_state + model.D[0] * output_response
        if not np.isfinite(free).all() or not np.isfinite(response).all():
            raise RuntimeError("state-space affine prediction is non-finite")
        return AffinePrediction(free, response)

    def align_to(self, incumbent: InnovationStateSpace) -> AlignmentResult | None:
        """Return a transactional candidate-to-incumbent similarity alignment."""
        if not isinstance(incumbent, InnovationStateSpace):
            return None
        candidate = self._require_model()
        incumbent_model = incumbent._require_model()
        aligned = _align_realization(
            candidate,
            self._state,
            self._state_covariance,
            self._inputs,
            self._ambients,
            incumbent_model,
            incumbent._state,
            incumbent._inputs,
            incumbent._ambients,
            self._config,
        )
        return None if aligned is None else aligned[0]

    def snapshot(self) -> dict[str, object]:
        """Return a finite, bounded, JSON-safe snapshot without numerical workspaces."""
        model = self._require_model()
        bounds = self._bounds
        if bounds is None:
            raise RuntimeError("model bounds are missing")
        snapshot: dict[str, object] = {
            "schema": _SCHEMA,
            "config": _config_snapshot(self._config),
            "model": {
                "order": model.order,
                "delay": model.delay,
                "A": model.A.tolist(),
                "B": model.B.tolist(),
                "C": model.C.tolist(),
                "D": model.D.tolist(),
                "E": model.E.tolist(),
                "K": model.K.tolist(),
                "process_covariance": model.process_covariance.tolist(),
                "measurement_covariance": model.measurement_covariance,
                "input_mean": model.input_mean,
                "steady_gain": model.steady_gain,
                "poles": [float(abs(value)) for value in np.linalg.eigvals(model.A)],
            },
            "delay_steps": model.delay,
            "delay_seconds": float(model.delay * _FRAME_SECONDS),
            "steady_gain": model.steady_gain,
            "state": self._state.tolist(),
            "state_covariance": self._state_covariance.tolist(),
            "record": {
                "buffer_samples": self._buffer_samples,
                "lag": {
                    "time_s": self._times[-(model.delay + 1) :],
                    "temperature_c": self._temperatures[-(model.delay + 1) :],
                    "realized_q": self._inputs[-(model.delay + 1) :],
                    "ambient_c": self._ambients[-(model.delay + 1) :],
                },
            },
            "bounds": {
                "max_steady_gain_c_per_q": bounds.max_steady_gain_c_per_q,
                "max_forecast_deviation_c": bounds.max_forecast_deviation_c,
            },
            "plausibility_bounds": {
                "max_steady_gain_c_per_q": bounds.max_steady_gain_c_per_q,
                "max_forecast_deviation_c": bounds.max_forecast_deviation_c,
            },
            "diagnostics": _diagnostics_snapshot(self._last_diagnostics),
            "refreshes": self._refreshes,
            "update_timing": {
                "last_attempt_time_s": self._last_refresh_time_s,
                "refreshes": self._refreshes,
            },
            "status": {
                "last_refresh_time_s": self._last_refresh_time_s,
                "refreshes": self._refreshes,
                "alignment_error_c": self._alignment_error_c,
                "alignment_evidence": "measured",
                "state_output_c": _current_output(model, self._state, self._inputs, self._ambients),
            },
        }
        _assert_snapshot_size(snapshot)
        return snapshot

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object]) -> InnovationStateSpace:
        """Strictly validate and restore a finite bounded model snapshot."""
        if not isinstance(snapshot, Mapping):
            raise ValueError(f"snapshot schema must be {_SCHEMA!r}")
        _assert_snapshot_size(snapshot)
        if snapshot.get("schema") != _SCHEMA:
            raise ValueError(f"snapshot schema must be {_SCHEMA!r}")
        config = _config_from_snapshot(_mapping(snapshot.get("config"), "config"))
        model_data = _mapping(snapshot.get("model"), "model")
        expected_fields = {
            "schema",
            "config",
            "model",
            "delay_steps",
            "delay_seconds",
            "steady_gain",
            "state",
            "state_covariance",
            "record",
            "bounds",
            "plausibility_bounds",
            "diagnostics",
            "refreshes",
            "update_timing",
            "status",
        }
        if set(snapshot) != expected_fields:
            raise ValueError("snapshot fields must exactly match the innovation state-space schema")
        if set(model_data) != {
            "order",
            "delay",
            "A",
            "B",
            "C",
            "D",
            "E",
            "K",
            "process_covariance",
            "measurement_covariance",
            "input_mean",
            "steady_gain",
            "poles",
        }:
            raise ValueError("model fields must exactly match the innovation state-space schema")
        order = _integer(model_data.get("order"), "model.order", minimum=1)
        delay = _integer(model_data.get("delay"), "model.delay", minimum=1)
        if order not in config.orders or delay not in config.delays:
            raise ValueError("model order and delay must belong to config")
        A = _matrix(model_data.get("A"), (order, order), "model.A")
        B = _vector_length(model_data.get("B"), order, "model.B")
        C = _vector_length(model_data.get("C"), order, "model.C")
        D = _vector_length(model_data.get("D"), 1, "model.D")
        E = _vector_length(model_data.get("E"), order, "model.E")
        K = _vector_length(model_data.get("K"), order, "model.K")
        process_covariance = _matrix(model_data.get("process_covariance"), (order, order), "model.process_covariance")
        measurement_covariance = _finite(model_data.get("measurement_covariance"), "model.measurement_covariance")
        input_mean = _finite(model_data.get("input_mean"), "model.input_mean")
        poles = _finite_list(model_data.get("poles"), "model.poles")
        if len(poles) != order or not np.allclose(
            sorted(poles),
            sorted(float(abs(value)) for value in np.linalg.eigvals(A)),
            atol=1e-12,
            rtol=1e-12,
        ):
            raise ValueError("model.poles must match the restored transition matrix")
        steady_gain = _finite(model_data.get("steady_gain"), "model.steady_gain")
        try:
            recovered_gain = float(C @ np.linalg.solve(np.eye(order) - A, B) + D[0])
        except np.linalg.LinAlgError as error:
            raise ValueError("model transition cannot have a finite steady gain") from error
        if not np.isclose(steady_gain, recovered_gain, atol=1e-12, rtol=1e-12):
            raise ValueError("model.steady_gain must match restored matrices")
        _validate_realization(A, B, C, D, E, K, process_covariance, measurement_covariance, steady_gain, config)
        state = _vector_length(snapshot.get("state"), order, "state")
        state_covariance = _matrix(snapshot.get("state_covariance"), (order, order), "state_covariance")
        _validate_covariance(state_covariance, config, "state_covariance")
        record = _mapping(snapshot.get("record"), "record")
        buffer_samples = _integer(record.get("buffer_samples"), "record.buffer_samples", minimum=1)
        if set(record) != {"buffer_samples", "lag"}:
            raise ValueError("record fields must exactly match the bounded snapshot schema")
        lag = _mapping(record.get("lag"), "record.lag")
        if set(lag) != {"time_s", "temperature_c", "realized_q", "ambient_c"}:
            raise ValueError("record lag fields must exactly match the bounded snapshot schema")
        times = _finite_list(lag.get("time_s"), "record.lag.time_s")
        temperatures = _finite_list(lag.get("temperature_c"), "record.lag.temperature_c")
        inputs = _finite_list(lag.get("realized_q"), "record.lag.realized_q")
        ambients = _finite_list(lag.get("ambient_c"), "record.lag.ambient_c")
        if not times or not (len(times) == len(temperatures) == len(inputs) == len(ambients)):
            raise ValueError("record lag must contain equal non-empty vectors")
        if buffer_samples > config.max_buffer_samples or len(times) != min(buffer_samples, delay + 1):
            raise ValueError("record lag must exactly match bounded synchronized history")
        if any(not 0.0 <= value <= 1.0 for value in inputs):
            raise ValueError("record lag realized_q must be in [0, 1]")
        if any(
            not np.isclose(right - left, _FRAME_SECONDS, rtol=0.0, atol=_FRAME_TIME_TOLERANCE)
            for left, right in zip(times, times[1:])
        ):
            raise ValueError("record lag times must be contiguous 20-second frames")
        bounds_data = _mapping(snapshot.get("bounds"), "bounds")
        bounds = _Bounds(
            _finite(bounds_data.get("max_steady_gain_c_per_q"), "bounds.max_steady_gain_c_per_q"),
            _finite(bounds_data.get("max_forecast_deviation_c"), "bounds.max_forecast_deviation_c"),
        )
        if bounds.max_steady_gain_c_per_q < steady_gain or bounds.max_forecast_deviation_c <= 0.0:
            raise ValueError("snapshot bounds are incompatible with model")
        diagnostics = _diagnostics_from_snapshot(_mapping(snapshot.get("diagnostics"), "diagnostics"))
        status = _mapping(snapshot.get("status"), "status")
        if set(status) != {
            "last_refresh_time_s",
            "refreshes",
            "alignment_error_c",
            "alignment_evidence",
            "state_output_c",
        }:
            raise ValueError("status fields must exactly match the innovation state-space schema")
        last_refresh = status.get("last_refresh_time_s")
        if last_refresh is not None:
            last_refresh = _finite(last_refresh, "status.last_refresh_time_s")
        refreshes = _integer(status.get("refreshes"), "status.refreshes", minimum=0)
        if status.get("alignment_evidence") != "measured":
            raise ValueError("status.alignment_evidence must be measured")
        alignment_error = status.get("alignment_error_c")
        if alignment_error is not None:
            alignment_error = _finite(alignment_error, "status.alignment_error_c")
            if alignment_error > 2.0:
                raise ValueError("status.alignment_error_c exceeds the state-alignment gate")
        state_output = _finite(status.get("state_output_c"), "status.state_output_c")
        restored_output = _current_output(
            _Realization(
                order, delay, A, B, C, D, E, K, process_covariance, measurement_covariance, input_mean, steady_gain
            ),
            state,
            inputs,
            ambients,
        )
        if not np.isclose(state_output, restored_output, atol=1e-12, rtol=1e-12):
            raise ValueError("status.state_output_c must match restored state")
        legacy_delay = _integer(snapshot.get("delay_steps"), "delay_steps", minimum=1)
        if legacy_delay != delay:
            raise ValueError("delay_steps must match model.delay")
        legacy_delay_seconds = _finite(snapshot.get("delay_seconds"), "delay_seconds")
        if not np.isclose(legacy_delay_seconds, delay * _FRAME_SECONDS, atol=1e-12, rtol=1e-12):
            raise ValueError("delay_seconds must match delay_steps")
        legacy_gain = _finite(snapshot.get("steady_gain"), "steady_gain")
        if not np.isclose(legacy_gain, steady_gain, atol=1e-12, rtol=1e-12):
            raise ValueError("steady_gain must match model.steady_gain")
        plausibility = _mapping(snapshot.get("plausibility_bounds"), "plausibility_bounds")
        if set(plausibility) != {"max_steady_gain_c_per_q", "max_forecast_deviation_c"}:
            raise ValueError("plausibility_bounds fields must exactly match model bounds")
        if not np.isclose(
            _finite(plausibility.get("max_steady_gain_c_per_q"), "plausibility_bounds.max_steady_gain_c_per_q"),
            bounds.max_steady_gain_c_per_q,
            atol=1e-12,
            rtol=1e-12,
        ) or not np.isclose(
            _finite(plausibility.get("max_forecast_deviation_c"), "plausibility_bounds.max_forecast_deviation_c"),
            bounds.max_forecast_deviation_c,
            atol=1e-12,
            rtol=1e-12,
        ):
            raise ValueError("plausibility_bounds must match model bounds")
        if _integer(snapshot.get("refreshes"), "refreshes", minimum=0) != refreshes:
            raise ValueError("refreshes must match status.refreshes")
        update_timing = _mapping(snapshot.get("update_timing"), "update_timing")
        if set(update_timing) != {"last_attempt_time_s", "refreshes"}:
            raise ValueError("update_timing fields must exactly match refresh evidence")
        attempt_time = update_timing.get("last_attempt_time_s")
        if attempt_time is not None:
            attempt_time = _finite(attempt_time, "update_timing.last_attempt_time_s")
        if (
            attempt_time != last_refresh
            or _integer(update_timing.get("refreshes"), "update_timing.refreshes", minimum=0) != refreshes
        ):
            raise ValueError("update_timing must match status refresh evidence")
        restored = cls(config)
        restored._model = _Realization(
            order, delay, A, B, C, D, E, K, process_covariance, measurement_covariance, input_mean, steady_gain
        )
        restored._state = state
        restored._state_covariance = state_covariance
        restored._times, restored._temperatures = times, temperatures
        restored._inputs, restored._ambients = inputs, ambients
        restored._buffer_samples = buffer_samples
        restored._bounds, restored._last_diagnostics = bounds, diagnostics
        restored._last_refresh_time_s, restored._refreshes = last_refresh, refreshes
        restored._alignment_error_c = alignment_error
        if restored.snapshot() != dict(snapshot):
            raise ValueError("snapshot must exactly match restored finite model state")
        return restored

    def _identify(
        self, frames: tuple[FrameObservation, ...]
    ) -> tuple[RefreshDiagnostics, _Realization | None, _Bounds | None]:
        if not frames:
            return RefreshDiagnostics(False, RefreshRejectionReason.INSUFFICIENT_SAMPLES, ()), None, None
        minimum = max(
            self._config.max_buffer_samples // 100,
            max(self._config.orders) + max(self._config.delays) + 6,
            2 * self._config.block_rows + 3,
        )
        if len(frames) < minimum:
            return RefreshDiagnostics(False, RefreshRejectionReason.INSUFFICIENT_SAMPLES, ()), None, None
        split = max(minimum, int(len(frames) * (1.0 - self._config.validation_fraction)))
        split = min(split, len(frames) - 2)
        training, validation = frames[:split], frames[split:]
        bounds = _bounds(training, self._config)
        attempts: list[CandidateAttempt] = []
        survivors: list[tuple[_Realization, CandidateAttempt]] = []
        for order in sorted(self._config.orders):
            for delay in sorted(self._config.delays):
                started = perf_counter()
                candidate, attempt = _candidate(training, validation, order, delay, self._config, bounds)
                attempts.append(_with_elapsed(attempt, (perf_counter() - started) * 1000.0))
                if candidate is not None:
                    survivors.append((candidate, attempts[-1]))
        if not survivors:
            return RefreshDiagnostics(False, RefreshRejectionReason.NO_VALID_CANDIDATE, tuple(attempts)), None, None
        selected, selected_attempt = min(
            survivors,
            key=lambda item: (
                _score(item[1].prediction_score),
                _score(item[1].braking_score),
                item[0].order,
                item[0].delay,
            ),
        )
        diagnostics = RefreshDiagnostics(True, None, tuple(attempts), selected.order, selected.delay)
        return diagnostics, selected, bounds

    def _install(
        self,
        model: _Realization,
        state: FloatArray,
        covariance: FloatArray,
        frames: tuple[FrameObservation, ...],
        bounds: _Bounds | None,
        *,
        reset_refreshes: bool,
    ) -> None:
        if bounds is None:
            raise RuntimeError("accepted model missing bounds")
        self._model, self._state, self._state_covariance = model, state, covariance
        self._times = [frame.frame_end_s for frame in frames[-self._config.max_buffer_samples :]]
        self._temperatures = [frame.temp_c for frame in frames[-self._config.max_buffer_samples :]]
        self._inputs = [frame.realized_q for frame in frames[-self._config.max_buffer_samples :]]
        self._ambients = [frame.ambient_c for frame in frames[-self._config.max_buffer_samples :]]
        self._bounds = bounds
        self._buffer_samples = len(self._times)
        self._last_refresh_time_s = self._times[-1]
        self._refreshes = 0 if reset_refreshes else self._refreshes + 1

    def _assimilate(self, observation: FrameObservation, *, updated: bool) -> ModelUpdate:
        model = self._require_model()
        _frames((observation,))
        if self._times and not np.isclose(
            observation.frame_start_s,
            self._times[-1],
            rtol=0.0,
            atol=_FRAME_TIME_TOLERANCE,
        ):
            raise ValueError("observations must be contiguous 20-second frames")
        if self._times and observation.frame_end_s <= self._times[-1]:
            raise ValueError("observations must be strictly chronological")
        transition_q = _delayed(self._inputs, len(self._inputs) - 1, model.delay)
        output_q = _delayed(self._inputs, len(self._inputs), model.delay)
        predicted_state = _advance(model, self._state, transition_q)
        predicted_covariance = model.A @ self._state_covariance @ model.A.T + model.process_covariance
        predicted_temperature = _output(model, predicted_state, output_q, observation.ambient_c)
        innovation = observation.temp_c - predicted_temperature
        innovation_variance = float(model.C @ predicted_covariance @ model.C.T + model.measurement_covariance)
        gain = predicted_covariance @ model.C / max(innovation_variance, self._config.covariance_floor)
        self._state = predicted_state + gain * innovation
        covariance = (np.eye(model.order) - np.outer(gain, model.C)) @ predicted_covariance
        self._state_covariance = _positive_semidefinite(covariance, self._config)
        self._times.append(observation.frame_end_s)
        self._temperatures.append(observation.temp_c)
        self._inputs.append(observation.realized_q)
        self._buffer_samples = min(self._config.max_buffer_samples, self._buffer_samples + 1)
        self._ambients.append(observation.ambient_c)
        excess = len(self._times) - self._config.max_buffer_samples
        if excess > 0:
            del self._times[:excess]
            del self._temperatures[:excess]
            del self._inputs[:excess]
            del self._ambients[:excess]
        return ModelUpdate(predicted_temperature, observation.temp_c, innovation, updated)

    def _history_frames(self) -> tuple[FrameObservation, ...]:
        return tuple(
            FrameObservation(
                time - 20.0,
                time,
                temp,
                0.0,
                ambient,
                q,
                q,
                q,
                q * 20.0,
                None,
                None,
                0,
                "state-space",
                False,
                False,
                False,
                False,
                False,
                False,
                True,
                0,
            )
            for time, temp, q, ambient in zip(
                self._times, self._temperatures, self._inputs, self._ambients, strict=True
            )
        )

    def _require_model(self) -> _Realization:
        if self._model is None:
            raise RuntimeError("fit must produce an accepted realization before use")
        return self._model


def _candidate(
    training: tuple[FrameObservation, ...],
    validation: tuple[FrameObservation, ...],
    order: int,
    delay: int,
    config: StateSpaceConfig,
    bounds: _Bounds,
) -> tuple[_Realization | None, CandidateAttempt]:
    count = len(training)
    hankel_shape = (config.block_rows, config.block_rows)
    empty = CandidateAttempt(order, delay, count, hankel_shape, (), 0, None, False, None, None, None, None, (), 0.0)
    if count <= max(order + delay + 2, 2 * config.block_rows):
        return None, _reject(empty, RefreshRejectionReason.INSUFFICIENT_SAMPLES)
    output = np.asarray([frame.temp_c - frame.ambient_c for frame in training], dtype=np.float64)
    input_ = np.asarray([frame.realized_q for frame in training], dtype=np.float64)
    input_mean = float(np.mean(input_))
    centered_input = input_ - input_mean
    if not np.isfinite(output).all() or not np.isfinite(input_).all():
        return None, _reject(empty, RefreshRejectionReason.NONFINITE)
    start = order + delay
    target = output[start:]
    design = np.column_stack(
        (
            *(output[start - lag : count - lag] for lag in range(1, order + 1)),
            centered_input[start - delay : count - delay],
            *(centered_input[start - delay - lag : count - delay - lag] for lag in range(1, order + 1)),
            np.ones(target.size, dtype=np.float64),
        )
    )
    left, singular, right = np.linalg.svd(design, full_matrices=False)
    rank_cutoff = _EPSILON * max(design.shape) * singular[0] if singular.size else np.inf
    rank = int(np.count_nonzero(singular > rank_cutoff))
    condition = float(singular[0] / singular[-1]) if singular.size and singular[-1] > 0.0 else None
    attempt = CandidateAttempt(
        order,
        delay,
        count,
        hankel_shape,
        tuple(float(value) for value in singular),
        rank,
        condition,
        False,
        None,
        None,
        None,
        None,
        (),
        0.0,
    )
    if rank < design.shape[1]:
        return None, _reject(attempt, RefreshRejectionReason.RANK_DEFICIENT)
    if condition is None or condition > config.max_condition_number:
        return None, _reject(attempt, RefreshRejectionReason.ILL_CONDITIONED)
    damping = singular / (singular * singular + config.parameter_penalty)
    coefficients = right.T @ (damping * (left.T @ target))
    residuals = target - design @ coefficients
    if not np.isfinite(coefficients).all() or not np.isfinite(residuals).all():
        return None, _reject(attempt, RefreshRejectionReason.NONFINITE)
    ar, direct = coefficients[:order], float(coefficients[order])
    numerator = coefficients[order + 1 : 2 * order + 1]
    impulse = np.empty(2 * config.block_rows, dtype=np.float64)
    impulse[0] = direct
    for index in range(1, impulse.size):
        impulse[index] = ar[: min(order, index)] @ impulse[index - min(order, index) : index][::-1] + (
            numerator[index - 1] if index <= order else 0.0
        )
    hankel = _block_hankel(impulse[1:], config.block_rows)
    h_left, h_singular, h_right = np.linalg.svd(hankel, full_matrices=False)
    h_rank = int(np.count_nonzero(h_singular > _EPSILON * max(hankel.shape) * h_singular[0])) if h_singular.size else 0
    h_condition = (
        float(h_singular[0] / h_singular[order - 1])
        if h_singular.size >= order and h_singular[order - 1] > 0.0
        else None
    )
    attempt = CandidateAttempt(
        order,
        delay,
        count,
        tuple(hankel.shape),
        tuple(float(value) for value in h_singular),
        h_rank,
        h_condition,
        False,
        None,
        None,
        None,
        None,
        (),
        0.0,
    )
    if h_rank < order:
        return None, _reject(attempt, RefreshRejectionReason.RANK_DEFICIENT)
    if h_condition is None or h_condition > config.max_condition_number:
        return None, _reject(attempt, RefreshRejectionReason.ILL_CONDITIONED)
    roots = np.sqrt(h_singular[:order])
    observability = h_left[:, :order] * roots
    reachability = roots[:, np.newaxis] * h_right[:order]
    transition = _ridge(observability[:-1], observability[1:], config.parameter_penalty)
    input_vector = reachability[:, 0]
    output_vector = observability[0]
    direct = np.array([impulse[0]], dtype=np.float64)
    if not all(np.isfinite(value).all() for value in (transition, input_vector, output_vector, direct)):
        return None, _reject(attempt, RefreshRejectionReason.NONFINITE)
    transition, projected = _project_stable(transition, config.max_pole_magnitude)
    if (
        not np.isfinite(transition).all()
        or max(np.abs(np.linalg.eigvals(transition)), default=0.0) >= config.max_pole_magnitude
    ):
        return None, _reject(attempt, RefreshRejectionReason.UNSTABLE_AFTER_PROJECTION)
    try:
        denominator = 1.0 - float(np.sum(ar))
        if not np.isfinite(denominator) or abs(denominator) <= _EPSILON:
            return None, _reject(attempt, RefreshRejectionReason.NONFINITE)
        intercept = float(coefficients[-1] / denominator)
        constant_direction = np.linalg.solve((np.eye(order) - transition).T, output_vector)
        ambient_vector = constant_direction * intercept / max(float(constant_direction @ constant_direction), _EPSILON)
        gain = float(output_vector @ np.linalg.solve(np.eye(order) - transition, input_vector) + direct[0])
    except np.linalg.LinAlgError:
        return None, _reject(attempt, RefreshRejectionReason.UNSTABLE_AFTER_PROJECTION)
    if not np.isfinite(intercept) or not np.isfinite(ambient_vector).all():
        return None, _reject(attempt, RefreshRejectionReason.NONFINITE)
    attempt = CandidateAttempt(
        order,
        delay,
        count,
        tuple(hankel.shape),
        tuple(float(value) for value in h_singular),
        h_rank,
        h_condition,
        projected,
        gain,
        None,
        None,
        None,
        (),
        0.0,
    )
    if not np.isfinite(gain) or gain <= 0.0 or gain > bounds.max_steady_gain_c_per_q:
        return None, _reject(attempt, RefreshRejectionReason.IMPLAUSIBLE_GAIN)
    covariance = float(np.clip(np.mean(residuals * residuals), config.covariance_floor, config.covariance_ceiling))
    process = np.eye(order, dtype=np.float64) * covariance
    measurement = covariance
    kalman_gain = process @ output_vector / float(output_vector @ process @ output_vector + measurement)
    model = _Realization(
        order,
        delay,
        _readonly(transition),
        _readonly(input_vector),
        _readonly(output_vector),
        _readonly(direct),
        _readonly(ambient_vector),
        _readonly(kalman_gain),
        _readonly(process),
        measurement,
        input_mean,
        gain,
    )
    prediction_score = _validation_score(model, training, validation)
    braking_score = _braking_score(model, training, validation)
    attempt = CandidateAttempt(
        order,
        delay,
        count,
        tuple(hankel.shape),
        tuple(float(value) for value in h_singular),
        h_rank,
        h_condition,
        projected,
        gain,
        None,
        prediction_score,
        braking_score,
        (),
        0.0,
    )
    if (
        not np.isfinite(prediction_score)
        or not np.isfinite(braking_score)
        or braking_score > bounds.max_forecast_deviation_c
    ):
        return None, _reject(
            attempt,
            RefreshRejectionReason.NONFINITE
            if not np.isfinite(prediction_score)
            else RefreshRejectionReason.IMPLAUSIBLE_GAIN,
        )
    return model, attempt


def _validation_score(
    model: _Realization, training: tuple[FrameObservation, ...], validation: tuple[FrameObservation, ...]
) -> float:
    state = _state_from_frames(training, model)
    inputs = [frame.realized_q for frame in training]
    errors: list[float] = []
    for frame in validation:
        transition_q = _delayed(inputs, len(inputs) - 1, model.delay)
        output_q = _delayed(inputs, len(inputs), model.delay)
        state = _advance(model, state, transition_q)
        errors.append((_output(model, state, output_q, frame.ambient_c) - frame.temp_c) ** 2)
        inputs.append(frame.realized_q)
    return float(np.mean(errors))


def _braking_score(
    model: _Realization, training: tuple[FrameObservation, ...], validation: tuple[FrameObservation, ...]
) -> float:
    state = _state_from_frames(training, model)
    inputs = [frame.realized_q for frame in training]
    maximum = 0.0
    for frame in validation:
        transition_q = _delayed(inputs, len(inputs) - 1, model.delay)
        output_q = _delayed(inputs, len(inputs), model.delay)
        state = _advance(model, state, transition_q)
        maximum = max(maximum, abs(_output(model, state, output_q, frame.ambient_c) - frame.ambient_c))
        inputs.append(0.0)
    return maximum


def _state_from_frames(frames: Sequence[FrameObservation], model: _Realization) -> FloatArray:
    state = np.zeros(model.order, dtype=np.float64)
    inputs = [frame.realized_q for frame in frames]
    for index, frame in enumerate(frames):
        state = _advance(model, state, _delayed(inputs, index - 1, model.delay))
        innovation = (frame.temp_c - frame.ambient_c) - float(
            model.C @ state + model.D[0] * (_delayed(inputs, index, model.delay) - model.input_mean)
        )
        state = state + model.K * innovation
    return state


def _advance(model: _Realization, state: FloatArray, delayed_input: float) -> FloatArray:
    return model.A @ state + model.B * (delayed_input - model.input_mean) + model.E


def _output(model: _Realization, state: FloatArray, delayed_input: float, ambient_c: float) -> float:
    return float(ambient_c + model.C @ state + model.D[0] * (delayed_input - model.input_mean))


_ALIGNMENT_OBSERVABILITY_HORIZON = 16
_ALIGNMENT_ATOL = 1e-10


def _current_output(
    model: _Realization,
    state: FloatArray,
    inputs: Sequence[float],
    ambients: Sequence[float],
) -> float:
    if not inputs or not ambients:
        raise ValueError("state-space record must contain current input and ambient")
    return _output(model, state, _delayed(inputs, len(inputs) - 1, model.delay), float(ambients[-1]))


def _observability(model: _Realization) -> FloatArray:
    rows = np.empty((_ALIGNMENT_OBSERVABILITY_HORIZON, model.order), dtype=np.float64)
    power = np.eye(model.order, dtype=np.float64)
    for row in range(_ALIGNMENT_OBSERVABILITY_HORIZON):
        rows[row] = model.C @ power
        power = power @ model.A
    return rows


def _align_realization(
    candidate: _Realization,
    candidate_state: FloatArray,
    candidate_covariance: FloatArray,
    candidate_inputs: Sequence[float],
    candidate_ambients: Sequence[float],
    incumbent: _Realization,
    incumbent_state: FloatArray,
    incumbent_inputs: Sequence[float],
    incumbent_ambients: Sequence[float],
    config: StateSpaceConfig,
) -> tuple[AlignmentResult, _Realization, FloatArray] | None:
    """Map a candidate realization into incumbent coordinates without mutation."""
    if candidate.order != incumbent.order or candidate.delay != incumbent.delay:
        return None
    values = (
        candidate.A,
        candidate.B,
        candidate.C,
        candidate.D,
        candidate.E,
        candidate.K,
        candidate.process_covariance,
        candidate_state,
        candidate_covariance,
        incumbent.A,
        incumbent.B,
        incumbent.C,
        incumbent.D,
        incumbent.E,
        incumbent.K,
        incumbent.process_covariance,
        incumbent_state,
    )
    if not all(np.isfinite(value).all() for value in values):
        return None
    if not all(
        np.isfinite(value)
        for value in (
            candidate.measurement_covariance,
            candidate.input_mean,
            candidate.steady_gain,
            incumbent.measurement_covariance,
            incumbent.input_mean,
            incumbent.steady_gain,
        )
    ):
        return None
    try:
        _validate_realization(
            candidate.A,
            candidate.B,
            candidate.C,
            candidate.D,
            candidate.E,
            candidate.K,
            candidate.process_covariance,
            candidate.measurement_covariance,
            candidate.steady_gain,
            config,
        )
        _validate_covariance(candidate_covariance, config, "candidate state covariance")
        _validate_realization(
            incumbent.A,
            incumbent.B,
            incumbent.C,
            incumbent.D,
            incumbent.E,
            incumbent.K,
            incumbent.process_covariance,
            incumbent.measurement_covariance,
            incumbent.steady_gain,
            config,
        )
    except ValueError:
        return None
    if not np.isclose(candidate.input_mean, incumbent.input_mean, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL):
        return None
    candidate_observability = _observability(candidate)
    incumbent_observability = _observability(incumbent)
    if (
        np.linalg.matrix_rank(candidate_observability, tol=_ALIGNMENT_ATOL) != candidate.order
        or np.linalg.matrix_rank(incumbent_observability, tol=_ALIGNMENT_ATOL) != incumbent.order
    ):
        return None
    try:
        transform, _, _, _ = np.linalg.lstsq(incumbent_observability, candidate_observability, rcond=None)
        inverse = np.linalg.inv(transform)
    except np.linalg.LinAlgError:
        return None
    if (
        not np.isfinite(transform).all()
        or not np.isfinite(inverse).all()
        or np.linalg.cond(transform) > config.max_condition_number
        or not np.allclose(
            incumbent_observability @ transform, candidate_observability, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL
        )
    ):
        return None
    mapped = _Realization(
        candidate.order,
        candidate.delay,
        transform @ candidate.A @ inverse,
        transform @ candidate.B,
        candidate.C @ inverse,
        candidate.D.copy(),
        transform @ candidate.E,
        transform @ candidate.K,
        transform @ candidate.process_covariance @ transform.T,
        candidate.measurement_covariance,
        candidate.input_mean,
        candidate.steady_gain,
    )
    mapped_covariance = transform @ candidate_covariance @ transform.T
    if not np.isfinite(mapped_covariance).all():
        return None
    if not (
        np.allclose(mapped.A, incumbent.A, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.allclose(mapped.B, incumbent.B, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.allclose(mapped.C, incumbent.C, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.allclose(mapped.D, incumbent.D, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.allclose(mapped.E, incumbent.E, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.allclose(mapped.K, incumbent.K, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL)
        and np.isclose(
            mapped.steady_gain,
            incumbent.steady_gain,
            atol=_ALIGNMENT_ATOL,
            rtol=_ALIGNMENT_ATOL,
        )
        and np.allclose(
            mapped.process_covariance, incumbent.process_covariance, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL
        )
        and np.isclose(
            mapped.measurement_covariance, incumbent.measurement_covariance, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL
        )
    ):
        return None
    try:
        candidate_output = _current_output(candidate, candidate_state, candidate_inputs, candidate_ambients)
        mapped_output = _current_output(mapped, transform @ candidate_state, candidate_inputs, candidate_ambients)
        incumbent_output = _current_output(incumbent, incumbent_state, incumbent_inputs, incumbent_ambients)
    except IndexError, ValueError:
        return None
    if not all(np.isfinite(value) for value in (candidate_output, mapped_output, incumbent_output)):
        return None
    if not np.isclose(candidate_output, mapped_output, atol=_ALIGNMENT_ATOL, rtol=_ALIGNMENT_ATOL):
        return None
    error = abs(incumbent_output - mapped_output)
    if error > 2.0:
        return None
    return AlignmentResult(transform, transform @ candidate_state, error), mapped, mapped_covariance


def _align_refresh_realization(
    candidate: _Realization,
    candidate_state: FloatArray,
    candidate_covariance: FloatArray,
    candidate_temperature_c: float,
    candidate_timestamp_s: float,
    candidate_inputs: Sequence[float],
    candidate_ambients: Sequence[float],
    incumbent: _Realization,
    incumbent_state: FloatArray,
    incumbent_covariance: FloatArray,
    incumbent_temperature_c: float,
    incumbent_timestamp_s: float,
    incumbent_inputs: Sequence[float],
    incumbent_ambients: Sequence[float],
    config: StateSpaceConfig,
) -> tuple[_Realization, FloatArray, FloatArray, float] | None:
    """Map only an equivalent replacement into the incumbent's coordinates."""
    try:
        if (
            candidate_timestamp_s != incumbent_timestamp_s
            or candidate_temperature_c != incumbent_temperature_c
            or candidate_inputs[-1] != incumbent_inputs[-1]
            or candidate_ambients[-1] != incumbent_ambients[-1]
        ):
            return None
        _validate_refresh_state(candidate, candidate_state, candidate_covariance, config, "candidate")
        _validate_refresh_state(incumbent, incumbent_state, incumbent_covariance, config, "incumbent")
    except IndexError, ValueError, np.linalg.LinAlgError:
        return None

    aligned = _align_realization(
        candidate,
        candidate_state,
        candidate_covariance,
        candidate_inputs,
        candidate_ambients,
        incumbent,
        incumbent_state,
        incumbent_inputs,
        incumbent_ambients,
        config,
    )
    if aligned is None:
        return None
    evidence, mapped_model, _ = aligned
    # The realization is now expressed in incumbent coordinates.  Its next
    # Kalman correction must therefore start from the incumbent posterior,
    # rather than from a refit's process covariance.
    mapped_state = incumbent_state.copy()
    mapped_covariance = incumbent_covariance.copy()
    try:
        _validate_refresh_state(mapped_model, mapped_state, mapped_covariance, config, "mapped candidate")
    except ValueError, np.linalg.LinAlgError:
        return None
    return mapped_model, mapped_state, mapped_covariance, evidence.output_error_c


def _validate_refresh_state(
    model: _Realization,
    state: FloatArray,
    covariance: FloatArray,
    config: StateSpaceConfig,
    name: str,
) -> None:
    """Reject malformed candidate/filter state before an install can mutate state."""
    order = _integer(model.order, f"{name}.order", minimum=1)
    _matrix(model.A, (order, order), f"{name}.A")
    _vector_length(model.B, order, f"{name}.B")
    _vector_length(model.C, order, f"{name}.C")
    _vector_length(model.E, order, f"{name}.E")
    _vector_length(model.K, order, f"{name}.K")
    _vector_length(model.D, 1, f"{name}.D")
    process = _matrix(model.process_covariance, (order, order), f"{name}.process_covariance")
    _vector_length(state, order, f"{name}.state")
    _matrix(covariance, (order, order), f"{name}.state_covariance")
    if not np.isfinite(model.input_mean):
        raise ValueError(f"{name}.input_mean must be finite")
    _validate_realization(
        model.A,
        model.B,
        model.C,
        model.D,
        model.E,
        model.K,
        process,
        model.measurement_covariance,
        model.steady_gain,
        config,
    )
    _validate_covariance(covariance, config, f"{name}.state_covariance")


def _bounds(frames: Sequence[FrameObservation], config: StateSpaceConfig) -> _Bounds:
    temperatures = np.asarray([frame.temp_c - frame.ambient_c for frame in frames], dtype=np.float64)
    inputs = np.asarray([frame.realized_q for frame in frames], dtype=np.float64)
    deviation = max(float(np.max(np.abs(temperatures))), 1.0)
    span = max(float(np.ptp(inputs)), 0.05)
    return _Bounds(config.steady_gain_scale_limit * deviation / span, config.held_out_forecast_scale_limit * deviation)


def _block_hankel(values: FloatArray, rows: int) -> FloatArray:
    columns = values.size - rows + 1
    if columns < rows:
        raise ValueError("insufficient Markov values for a square Hankel matrix")
    return np.asarray([values[row : row + columns] for row in range(rows)], dtype=np.float64)


def _ridge(features: FloatArray, targets: FloatArray, penalty: float) -> FloatArray:
    left, singular, right = np.linalg.svd(features, full_matrices=False)
    return right.T @ ((singular / (singular * singular + penalty)) * (left.T @ targets))


def _project_stable(matrix: FloatArray, bound: float) -> tuple[FloatArray, bool]:
    values, vectors = np.linalg.eig(matrix)
    magnitudes = np.abs(values)
    if not np.isfinite(values).all() or np.linalg.cond(vectors) > 1e12:
        return matrix, False
    projected_values = np.where(
        magnitudes >= bound, values * (bound * 0.999 / np.maximum(magnitudes, _EPSILON)), values
    )
    projected = np.real_if_close(vectors @ np.diag(projected_values) @ np.linalg.inv(vectors), tol=1_000)
    return np.asarray(projected, dtype=np.float64), bool(np.any(projected_values != values))


def _positive_semidefinite(matrix: FloatArray, config: StateSpaceConfig) -> FloatArray:
    symmetric = 0.5 * (matrix + matrix.T)
    values, vectors = np.linalg.eigh(symmetric)
    clipped = np.clip(values, config.covariance_floor, config.covariance_ceiling)
    return vectors @ np.diag(clipped) @ vectors.T


def _validate_realization(
    A: FloatArray,
    B: FloatArray,
    C: FloatArray,
    D: FloatArray,
    E: FloatArray,
    K: FloatArray,
    process: FloatArray,
    measurement: float,
    gain: float,
    config: StateSpaceConfig,
) -> None:
    if max(np.abs(np.linalg.eigvals(A)), default=np.inf) >= config.max_pole_magnitude or gain <= 0.0:
        raise ValueError("model violates stability or gain bounds")
    _validate_covariance(process, config, "model.process_covariance")
    floor_tolerance, ceiling_tolerance = _covariance_tolerances(config)
    if not config.covariance_floor - floor_tolerance <= measurement <= config.covariance_ceiling + ceiling_tolerance:
        raise ValueError("model.measurement_covariance violates bounds")
    expected_k = process @ C / float(C @ process @ C + measurement)
    if not np.allclose(K, expected_k, atol=1e-12, rtol=1e-12):
        raise ValueError("model.K must match Q*C/(C*Q*C+R)")


def _validate_covariance(matrix: FloatArray, config: StateSpaceConfig, name: str) -> None:
    floor_tolerance, ceiling_tolerance = _covariance_tolerances(config)
    if not np.allclose(matrix, matrix.T, atol=ceiling_tolerance, rtol=1e-12):
        raise ValueError(f"{name} must be symmetric")
    eigenvalues = np.linalg.eigvalsh(matrix)
    if eigenvalues.min() < config.covariance_floor - floor_tolerance:
        raise ValueError(f"{name} violates the configured covariance floor")
    if eigenvalues.max() > config.covariance_ceiling + ceiling_tolerance:
        raise ValueError(f"{name} exceeds configured covariance bound")


def _covariance_tolerances(config: StateSpaceConfig) -> tuple[float, float]:
    return (
        max(64.0 * _EPSILON * max(1.0, config.covariance_floor), config.covariance_floor * 1e-9),
        max(64.0 * _EPSILON * max(1.0, config.covariance_ceiling), config.covariance_ceiling * 1e-12),
    )


def _frames(observations: Sequence[FrameObservation]) -> tuple[FrameObservation, ...]:
    frames = tuple(observations)
    for previous, frame in zip(frames, frames[1:]):
        if not isinstance(frame, FrameObservation):
            raise ValueError("observations must be complete FrameObservation values")
        if not np.isclose(
            frame.frame_end_s - frame.frame_start_s, _FRAME_SECONDS, rtol=0.0, atol=_FRAME_TIME_TOLERANCE
        ) or not np.isclose(frame.frame_start_s, previous.frame_end_s, rtol=0.0, atol=_FRAME_TIME_TOLERANCE):
            raise ValueError("observations must be contiguous complete 20-second frames")
    if frames:
        first = frames[0]
        if not isinstance(first, FrameObservation) or not np.isclose(
            first.frame_end_s - first.frame_start_s, _FRAME_SECONDS, rtol=0.0, atol=_FRAME_TIME_TOLERANCE
        ):
            raise ValueError("observations must be complete 20-second FrameObservation values")
    return frames


def _bounded_frames(frames: tuple[FrameObservation, ...], max_samples: int) -> tuple[FrameObservation, ...]:
    return frames[-max_samples:]


def _delayed(inputs: Sequence[float], index: int, delay: int) -> float:
    source = index - delay
    return float(inputs[source]) if source >= 0 else 0.0


def _affine_input(
    inputs: Sequence[float], history_length: int, index: int, delay: int, horizon: int
) -> tuple[float, FloatArray]:
    source = index - delay
    response = np.zeros(horizon, dtype=np.float64)
    if source < history_length:
        return (float(inputs[source]) if source >= 0 else 0.0), response
    response[source - history_length] = 1.0
    return 0.0, response


def _reject(attempt: CandidateAttempt, reason: RefreshRejectionReason) -> CandidateAttempt:
    return CandidateAttempt(
        attempt.order,
        attempt.delay,
        attempt.sample_count,
        attempt.hankel_shape,
        attempt.singular_values,
        attempt.effective_rank,
        attempt.condition_number,
        attempt.projection_applied,
        attempt.steady_gain,
        attempt.alignment_error_c,
        attempt.prediction_score,
        attempt.braking_score,
        (reason,),
        attempt.elapsed_ms,
    )


def _with_elapsed(attempt: CandidateAttempt, elapsed_ms: float) -> CandidateAttempt:
    return CandidateAttempt(
        attempt.order,
        attempt.delay,
        attempt.sample_count,
        attempt.hankel_shape,
        attempt.singular_values,
        attempt.effective_rank,
        attempt.condition_number,
        attempt.projection_applied,
        attempt.steady_gain,
        attempt.alignment_error_c,
        attempt.prediction_score,
        attempt.braking_score,
        attempt.rejection_reasons,
        max(0.0, elapsed_ms),
    )


def _score(value: float | None) -> float:
    return float("inf") if value is None else value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return int(value)


def _positive_tuple(values: object, name: str) -> tuple[int, ...]:
    if not isinstance(values, tuple) or not values:
        raise ValueError(f"{name} must be a non-empty tuple")
    return tuple(_integer(value, name, minimum=1) for value in values)


def _vector(values: npt.ArrayLike, name: str) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if result.ndim != 1 or not np.isfinite(result).all():
        raise ValueError(f"{name} must be a finite vector")
    return result


def _vector_length(values: object, length: int, name: str) -> FloatArray:
    result = _vector(cast(npt.ArrayLike, values), name)
    if result.shape != (length,):
        raise ValueError(f"{name} has an invalid shape")
    return result


def _matrix(values: object, shape: tuple[int, int], name: str) -> FloatArray:
    result = np.asarray(values, dtype=np.float64)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"{name} has an invalid finite shape")
    return result


def _positive_sequence(values: object, name: str) -> tuple[int, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise ValueError(f"{name} must be a non-empty sequence")
    return tuple(_integer(value, name, minimum=1) for value in values)


def _finite_list(values: object, name: str) -> list[float]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence")
    return [_finite(value, name) for value in values]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _readonly(values: FloatArray) -> FloatArray:
    result = np.array(values, dtype=np.float64, copy=True)
    result.setflags(write=False)
    return result


def _snapshot_json_default(value: object) -> object:
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _assert_snapshot_size(snapshot: Mapping[str, object]) -> None:
    try:
        encoded = json.dumps(
            snapshot,
            allow_nan=False,
            separators=(",", ":"),
            default=_snapshot_json_default,
        ).encode()
    except (TypeError, ValueError) as error:
        raise ValueError("snapshot must be JSON-safe") from error
    if len(encoded) >= _SNAPSHOT_MAX_BYTES:
        raise ValueError(f"snapshot must be smaller than {_SNAPSHOT_MAX_BYTES} bytes")


def _config_snapshot(config: StateSpaceConfig) -> dict[str, object]:
    return {
        name: getattr(config, name) if name not in {"orders", "delays"} else list(getattr(config, name))
        for name in StateSpaceConfig.__dataclass_fields__
    }


def _config_from_snapshot(data: Mapping[str, object]) -> StateSpaceConfig:
    fields = StateSpaceConfig.__dataclass_fields__
    if set(data) != set(fields):
        raise ValueError("config must contain exactly the configured fields")
    return StateSpaceConfig(
        orders=_positive_sequence(data["orders"], "orders"),
        delays=_positive_sequence(data["delays"], "delays"),
        block_rows=_integer(data["block_rows"], "block_rows", minimum=2),
        validation_fraction=_finite(data["validation_fraction"], "validation_fraction"),
        parameter_penalty=_finite(data["parameter_penalty"], "parameter_penalty"),
        max_buffer_samples=_integer(data["max_buffer_samples"], "max_buffer_samples", minimum=8),
        refresh_interval_s=_finite(data["refresh_interval_s"], "refresh_interval_s"),
        max_pole_magnitude=_finite(data["max_pole_magnitude"], "max_pole_magnitude"),
        max_condition_number=_finite(data["max_condition_number"], "max_condition_number"),
        steady_gain_scale_limit=_finite(data["steady_gain_scale_limit"], "steady_gain_scale_limit"),
        held_out_forecast_scale_limit=_finite(data["held_out_forecast_scale_limit"], "held_out_forecast_scale_limit"),
        covariance_floor=_finite(data["covariance_floor"], "covariance_floor"),
        covariance_ceiling=_finite(data["covariance_ceiling"], "covariance_ceiling"),
    )


def _diagnostics_snapshot(value: RefreshDiagnostics) -> dict[str, object]:
    return {
        "accepted": value.accepted,
        "terminal_reason": None if value.terminal_reason is None else value.terminal_reason.value,
        "selected_order": value.selected_order,
        "selected_delay": value.selected_delay,
        "attempts": [
            {
                "order": attempt.order,
                "delay": attempt.delay,
                "sample_count": attempt.sample_count,
                "hankel_shape": list(attempt.hankel_shape),
                "singular_values": list(attempt.singular_values[:_MAX_ORDER]),
                "effective_rank": attempt.effective_rank,
                "condition_number": attempt.condition_number,
                "projection_applied": attempt.projection_applied,
                "steady_gain": attempt.steady_gain,
                "alignment_error_c": attempt.alignment_error_c,
                "prediction_score": attempt.prediction_score,
                "braking_score": attempt.braking_score,
                "rejection_reasons": [reason.value for reason in attempt.rejection_reasons],
                "elapsed_ms": attempt.elapsed_ms,
            }
            for attempt in value.attempts
        ],
    }


def _diagnostics_from_snapshot(data: Mapping[str, object]) -> RefreshDiagnostics:
    attempts: list[CandidateAttempt] = []
    for raw in _finite_list_or_mapping(data.get("attempts"), "diagnostics.attempts"):
        attempt = _mapping(raw, "diagnostics attempt")
        reasons = attempt.get("rejection_reasons")
        if not isinstance(reasons, Sequence):
            raise ValueError("diagnostics rejection_reasons must be a sequence")
        shape = attempt.get("hankel_shape")
        if not isinstance(shape, Sequence) or len(shape) != 2:
            raise ValueError("diagnostics hankel_shape must have two dimensions")
        singular = attempt.get("singular_values")
        if not isinstance(singular, Sequence):
            raise ValueError("diagnostics singular_values must be a sequence")
        attempts.append(
            CandidateAttempt(
                _integer(attempt.get("order"), "attempt.order", minimum=1),
                _integer(attempt.get("delay"), "attempt.delay", minimum=1),
                _integer(attempt.get("sample_count"), "attempt.sample_count", minimum=0),
                (
                    _integer(shape[0], "attempt.hankel_shape", minimum=0),
                    _integer(shape[1], "attempt.hankel_shape", minimum=0),
                ),
                tuple(_finite(value, "attempt.singular_values") for value in singular),
                _integer(attempt.get("effective_rank"), "attempt.effective_rank", minimum=0),
                None
                if attempt.get("condition_number") is None
                else _finite(attempt.get("condition_number"), "attempt.condition_number"),
                bool(attempt.get("projection_applied")),
                None
                if attempt.get("steady_gain") is None
                else _finite(attempt.get("steady_gain"), "attempt.steady_gain"),
                None
                if attempt.get("alignment_error_c") is None
                else _finite(attempt.get("alignment_error_c"), "attempt.alignment_error_c"),
                None
                if attempt.get("prediction_score") is None
                else _finite(attempt.get("prediction_score"), "attempt.prediction_score"),
                None
                if attempt.get("braking_score") is None
                else _finite(attempt.get("braking_score"), "attempt.braking_score"),
                tuple(RefreshRejectionReason(reason) for reason in reasons),
                _finite(attempt.get("elapsed_ms"), "attempt.elapsed_ms"),
            )
        )
    terminal = data.get("terminal_reason")
    return RefreshDiagnostics(
        bool(data.get("accepted")),
        None if terminal is None else RefreshRejectionReason(terminal),
        tuple(attempts),
        _optional_int(data.get("selected_order"), "diagnostics.selected_order"),
        _optional_int(data.get("selected_delay"), "diagnostics.selected_delay"),
    )


def _optional_int(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name, minimum=1)


def _finite_list_or_mapping(values: object, name: str) -> Sequence[object]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name} must be a sequence")
    return values
