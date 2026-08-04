"""Deterministic innovation state-space identification for the linear-MPC bake-off."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from types import MappingProxyType

import numpy as np
import numpy.typing as npt

from .contracts import AffinePrediction, FloatArray, Observation, SignalRecord, UpdateOutcome


@dataclass(frozen=True, slots=True)
class StateSpaceConfig:
    """Candidate orders, delays, and bounded online-refit settings."""

    orders: tuple[int, ...]
    delays: tuple[int, ...]
    block_rows: int = 8
    validation_fraction: float = 0.2
    parameter_penalty: float = 1e-5
    max_buffer_samples: int = 1_800
    refresh_interval_s: float = 300.0
    alignment_tolerance_c: float = 0.05

    def __post_init__(self) -> None:
        if not self.orders or any(order < 1 for order in self.orders):
            raise ValueError("orders must contain positive values")
        if len(set(self.orders)) != len(self.orders):
            raise ValueError("orders must not contain duplicates")
        if not self.delays or any(delay < 1 for delay in self.delays):
            raise ValueError("delays must contain positive values")
        if len(set(self.delays)) != len(self.delays):
            raise ValueError("delays must not contain duplicates")
        if self.block_rows < 2:
            raise ValueError("block_rows must be at least two")
        if not 0.0 < self.validation_fraction < 0.5:
            raise ValueError("validation_fraction must be in (0, 0.5)")
        if self.parameter_penalty < 0.0:
            raise ValueError("parameter_penalty must be non-negative")
        if self.max_buffer_samples < 8:
            raise ValueError("max_buffer_samples must be at least eight")
        if self.refresh_interval_s <= 0.0:
            raise ValueError("refresh_interval_s must be positive")
        if self.alignment_tolerance_c < 0.0:
            raise ValueError("alignment_tolerance_c must be non-negative")


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    """Result of an atomic rolling-realization replacement attempt."""

    accepted: bool
    alignment_error_c: float
    duration_s: float


@dataclass(frozen=True, slots=True)
class SubspaceFit:
    """One stable delayed SISO realization recovered from a signal record."""

    order: int
    delay: int
    A: FloatArray
    B: FloatArray
    C: FloatArray
    D: FloatArray
    intercept: float
    input_mean: float
    covariance: float
    validation_error: float
    state_offset: FloatArray = field(
        default_factory=lambda: np.empty(0, dtype=np.float64), repr=False
    )
    def __post_init__(self) -> None:
        for name in ("A", "B", "C", "D", "state_offset"):
            array = np.array(getattr(self, name), dtype=np.float64, copy=True)
            if name == "state_offset" and array.size == 0:
                array = np.zeros(self.order, dtype=np.float64)
            if name == "state_offset" and array.shape != (self.order,):
                raise ValueError("state_offset must have shape (order,)")
            array.setflags(write=False)
            object.__setattr__(self, name, array)


class InnovationStateSpace:
    """Stable delayed-input realization with innovation filtering and atomic refresh."""

    def __init__(self, config: StateSpaceConfig) -> None:
        self._config = config
        self._fit: SubspaceFit | None = None
        self._state = np.empty(0, dtype=np.float64)
        self._covariance = np.empty((0, 0), dtype=np.float64)
        self._temperatures: list[float] = []
        self._inputs: list[float] = []
        self._ambients: list[float] = []
        self._times: list[float] = []
        self._last_refresh_time_s: float | None = None
        self._last_alignment_error_c: float | None = None
        self._last_refresh_duration_s = 0.0
        self._refreshes = 0

    @property
    def current_output_c(self) -> float:
        """The filtered current temperature estimate in degrees Celsius."""
        fit = self._require_fit()
        delayed = _delayed_input(
            self._inputs, np.empty(0), len(self._inputs) - 1, fit.delay
        )
        return _output(fit, self._state, delayed, self._ambients[-1])
    @property
    def input_history(self) -> tuple[float, ...]:
        """An immutable view of requested-input history for controller callers."""
        return tuple(self._inputs)

    @property
    def history_record(self) -> SignalRecord:
        """A defensive record snapshot of the bounded online history."""
        return SignalRecord(
            np.asarray(self._times, dtype=np.float64),
            np.asarray(self._temperatures, dtype=np.float64),
            np.asarray(self._inputs, dtype=np.float64),
            np.asarray(self._ambients, dtype=np.float64),
            "innovation-state-space-history",
        )

    def fit(self, record: SignalRecord) -> None:
        """Select and initialize a stable realization from a complete record."""
        _validate_record(record)
        fit = _select_fit(record, self._config)
        self._fit = fit
        self._temperatures = record.temp_c.astype(float).tolist()
        self._inputs = record.q.astype(float).tolist()
        self._ambients = record.ambient_c.astype(float).tolist()
        self._times = record.time_s.astype(float).tolist()
        self._state = _state_from_record(record, fit)
        self._covariance = np.eye(fit.order, dtype=np.float64) * fit.covariance
        self._trim_history()
        self._last_refresh_time_s = self._times[-1]
        self._last_alignment_error_c = None
        self._last_refresh_duration_s = 0.0
        self._refreshes = 0

    def forecast(self, prefix: SignalRecord, q_future: FloatArray, ambient_future: FloatArray) -> FloatArray:
        """Produce an open-loop temperature forecast without observing target values."""
        fit = self._require_fit()
        _validate_record(prefix)
        future_q = _as_vector(q_future, "q_future")
        future_ambient = _as_vector(ambient_future, "ambient_future")
        if future_q.size != future_ambient.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        state = _state_from_record(prefix, fit)
        inputs = prefix.q.astype(float).tolist()
        prediction = np.empty(future_q.size, dtype=np.float64)
        for step, ambient_c in enumerate(future_ambient):
            target = len(inputs)
            transition_delayed = _delayed_input(inputs, future_q, target - 1, fit.delay)
            output_delayed = _delayed_input(inputs, future_q, target, fit.delay)
            state = _advance(fit, state, transition_delayed)
            prediction[step] = _output(fit, state, output_delayed, float(ambient_c))
            inputs.append(float(future_q[step]))
        prediction.setflags(write=False)
        return prediction

    def observe(self, observation: Observation) -> UpdateOutcome:
        """Score one 20-second sample, then apply its scalar Kalman innovation."""
        fit = self._require_fit()
        target = len(self._inputs)
        transition_delayed = _delayed_input(self._inputs, np.empty(0), target - 1, fit.delay)
        output_delayed = _delayed_input(self._inputs, np.empty(0), target, fit.delay)
        predicted_state = _advance(fit, self._state, transition_delayed)
        predicted_covariance = fit.A @ self._covariance @ fit.A.T + np.eye(fit.order) * fit.covariance
        predicted_temp = _output(fit, predicted_state, output_delayed, observation.ambient_c)
        innovation = float(observation.temp_c - predicted_temp)
        innovation_variance = float(fit.C @ predicted_covariance @ fit.C.T + fit.covariance)
        gain = (predicted_covariance @ fit.C) / max(innovation_variance, 1e-12)
        self._state = predicted_state + gain * innovation
        self._covariance = (np.eye(fit.order) - np.outer(gain, fit.C)) @ predicted_covariance
        self._covariance = 0.5 * (self._covariance + self._covariance.T)
        self._times.append(observation.time_s)
        self._temperatures.append(observation.temp_c)
        self._inputs.append(observation.q)
        self._ambients.append(observation.ambient_c)
        self._trim_history()
        if self._last_refresh_time_s is not None and observation.time_s - self._last_refresh_time_s >= self._config.refresh_interval_s:
            self.refresh(self.history_record)
        return UpdateOutcome(predicted_temp, observation.temp_c, innovation, True)

    def refresh(self, record: SignalRecord) -> RefreshOutcome:
        """Atomically replace the realization only when aligned prediction is continuous."""
        self._require_fit()
        _validate_record(record)
        started = perf_counter()
        combined = _join_records(self.history_record, record, self._config.max_buffer_samples)
        candidate = _select_fit(combined, self._config)
        old = self._require_fit()
        candidate_state = _state_from_values(
            self._temperatures, self._ambients, self._inputs, candidate
        )
        old_transition = _delayed_input(self._inputs, np.empty(0), len(self._inputs) - 1, old.delay)
        old_output = _delayed_input(self._inputs, np.empty(0), len(self._inputs), old.delay)
        new_transition = _delayed_input(
            self._inputs, np.empty(0), len(self._inputs) - 1, candidate.delay
        )
        new_output = _delayed_input(self._inputs, np.empty(0), len(self._inputs), candidate.delay)
        old_next = _output(
            old, _advance(old, self._state, old_transition), old_output, self._ambients[-1]
        )
        new_next = _output(
            candidate,
            _advance(candidate, candidate_state, new_transition),
            new_output,
            self._ambients[-1],
        )
        alignment_error = abs(new_next - old_next)
        duration = perf_counter() - started
        if alignment_error > self._config.alignment_tolerance_c:
            self._last_refresh_time_s = float(record.time_s[-1])
            return RefreshOutcome(False, alignment_error, duration)
        self._fit = candidate
        self._state = candidate_state
        self._covariance = np.eye(candidate.order, dtype=np.float64) * candidate.covariance
        self._last_alignment_error_c = alignment_error
        self._last_refresh_duration_s = duration
        self._last_refresh_time_s = self._times[-1]
        self._refreshes += 1
        return RefreshOutcome(True, alignment_error, duration)

    def affine_prediction(self, horizon_steps: int, q_previous: float, ambient_future: FloatArray) -> AffinePrediction:
        """Return the lower-triangular affine open-loop map using powers of ``A``."""
        fit = self._require_fit()
        if horizon_steps < 0:
            raise ValueError("horizon_steps must be non-negative")
        ambient = _as_vector(ambient_future, "ambient_future")
        if ambient.size != horizon_steps:
            raise ValueError("ambient_future length must equal horizon_steps")
        inputs = list(self._inputs)
        inputs[-1] = float(q_previous)
        history_length = len(inputs)
        free_state = self._state.copy()
        response_state = np.zeros((fit.order, horizon_steps), dtype=np.float64)
        free_output = np.empty(horizon_steps, dtype=np.float64)
        response = np.zeros((horizon_steps, horizon_steps), dtype=np.float64)
        for step in range(horizon_steps):
            target = history_length + step
            transition_constant, transition_response = _affine_delayed_input(
                inputs, history_length, target - 1, fit.delay, horizon_steps
            )
            output_constant, output_response = _affine_delayed_input(
                inputs, history_length, target, fit.delay, horizon_steps
            )
            free_state = _advance(fit, free_state, transition_constant)
            response_state = fit.A @ response_state + np.outer(fit.B, transition_response)
            free_output[step] = _output(fit, free_state, output_constant, float(ambient[step]))
            response[step] = fit.C @ response_state + fit.D[0] * output_response
        return AffinePrediction(free_output, response)

    def snapshot(self) -> Mapping[str, object]:
        """Serialize only immutable plain values suitable for deterministic comparison."""
        fit = self._require_fit()
        poles = np.linalg.eigvals(fit.A)
        return _freeze({
            "schema": "innovation-state-space/v1", "order": fit.order, "delay_steps": fit.delay,
            "matrices": {
                "A": fit.A.tolist(), "B": fit.B.tolist(), "C": fit.C.tolist(),
                "D": fit.D.tolist(), "state_offset": fit.state_offset.tolist(),
            },
            "poles": [float(abs(pole)) for pole in poles], "steady_gain": _steady_gain(fit),
            "innovation_covariance": fit.covariance, "state_covariance": self._covariance.tolist(),
            "alignment_error_c": self._last_alignment_error_c, "buffer_samples": len(self._times),
            "refresh_duration_s": self._last_refresh_duration_s, "refreshes": self._refreshes,
            "update_timing": {"last_attempt_time_s": self._last_refresh_time_s},
        })

    def _trim_history(self) -> None:
        excess = len(self._times) - self._config.max_buffer_samples
        if excess > 0:
            del self._times[:excess]
            del self._temperatures[:excess]
            del self._inputs[:excess]
            del self._ambients[:excess]

    def _require_fit(self) -> SubspaceFit:
        if self._fit is None:
            raise RuntimeError("fit must be called before using the model")
        return self._fit


def subspace_fit(record: SignalRecord, order: int, block_rows: int) -> SubspaceFit:
    """Recover a stable order-specific realization with deterministic full-SVD least squares."""
    _validate_record(record)
    if order < 1 or block_rows < 2:
        raise ValueError("order must be positive and block_rows must be at least two")
    return _fit_candidate(record, order, 1, block_rows)


def _select_fit(record: SignalRecord, config: StateSpaceConfig) -> SubspaceFit:
    n = record.temp_c.size
    candidates: list[SubspaceFit] = []
    split = max(
        max(config.orders) + max(config.delays) + 4,
        int(n * (1.0 - config.validation_fraction)),
    )
    split = min(split, n - 2)
    training = _slice_record(record, 0, split)
    validation = _slice_record(record, split, n)
    for order in config.orders:
        for delay in config.delays:
            try:
                candidate = _fit_candidate(training, order, delay, config.block_rows)
                error = _one_step_error(candidate, training, validation)
                candidates.append(SubspaceFit(
                    candidate.order, candidate.delay, candidate.A, candidate.B,
                    candidate.C, candidate.D, candidate.intercept, candidate.input_mean,
                    candidate.covariance,
                    error + config.parameter_penalty * (order * order + 2 * order + 2),
                    candidate.state_offset,
                ))
            except ValueError:
                continue
    if not candidates:
        raise ValueError("record is too short for configured state-space candidates")
    return min(candidates, key=lambda candidate: (candidate.validation_error, candidate.order, candidate.delay))


def _fit_candidate(record: SignalRecord, order: int, delay: int, block_rows: int) -> SubspaceFit:
    z = np.asarray(record.temp_c - record.ambient_c, dtype=np.float64)
    q = np.asarray(record.q, dtype=np.float64)
    if z.size <= max(order + delay, 2 * block_rows):
        raise ValueError("record is too short for selected order, delay, and block rows")
    input_mean = float(np.mean(q))
    A, B, C, D, state_offset, intercept, residuals = _recover_projected_realization(
        z, q - input_mean, order, delay, block_rows
    )
    provisional = SubspaceFit(
        order, delay, A, B, C, D, intercept, input_mean, 1e-6, 0.0, state_offset
    )
    if not 0.0 < _steady_gain(provisional) < 1_000.0:
        raise ValueError("identified steady gain is not physically plausible")
    covariance = max(float(np.mean(residuals * residuals)), 1e-8)
    return SubspaceFit(
        order, delay, A, B, C, D, intercept, input_mean, covariance, 0.0, state_offset
    )


def _recover_projected_realization(
    output: FloatArray,
    centered_input: FloatArray,
    order: int,
    delay: int,
    block_rows: int,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray, float, FloatArray]:
    """Recover a projected realization from deterministic ARX Markov parameters."""
    start = order + delay
    targets = output[start:]
    design = np.column_stack((
        *(output[start - lag : output.size - lag] for lag in range(1, order + 1)),
        *(centered_input[start - delay - lag : output.size - delay - lag] for lag in range(order + 1)),
        np.ones(targets.size, dtype=np.float64),
    ))
    coefficients = _svd_least_squares(design, targets)
    ar = coefficients[:order]
    numerator = coefficients[order : 2 * order + 1]
    residuals = targets - design @ coefficients
    response = np.empty(2 * block_rows, dtype=np.float64)
    response[0] = numerator[0]
    for step in range(1, response.size):
        response[step] = (
            ar[: min(order, step)] @ response[step - min(order, step) : step][::-1]
            + (numerator[step] if step <= order else 0.0)
        )
    hankel = _block_hankel(response[1:], block_rows)
    left, singular_values, right = np.linalg.svd(hankel, full_matrices=False)
    if singular_values[order - 1] <= np.finfo(np.float64).eps:
        raise ValueError("subspace Markov projection does not support requested order")
    roots = np.sqrt(singular_values[:order])
    observability = left[:, :order] * roots
    reachability = roots[:, np.newaxis] * right[:order]
    A = _project_matrix(_svd_least_squares(observability[:-1], observability[1:]))
    B = reachability[:, 0]
    C = observability[0]
    D = np.array([response[0]], dtype=np.float64)
    constant_output = float(coefficients[-1] / (1.0 - np.sum(ar)))
    constant_direction = np.linalg.solve((np.eye(order) - A).T, C)
    state_offset = constant_output * constant_direction / (constant_direction @ constant_direction)
    return A, B, C, D, state_offset, 0.0, residuals






def _svd_least_squares(features: FloatArray, targets: FloatArray) -> FloatArray:
    left, singular_values, right = np.linalg.svd(features, full_matrices=True)
    cutoff = np.finfo(np.float64).eps * max(features.shape) * singular_values[0]
    inverse = np.zeros_like(singular_values)
    inverse[singular_values > cutoff] = 1.0 / singular_values[singular_values > cutoff]
    projected_targets = left[:, : singular_values.size].T @ targets
    scale = inverse if projected_targets.ndim == 1 else inverse[:, np.newaxis]
    return right[: singular_values.size].T @ (scale * projected_targets)
def _one_step_error(fit: SubspaceFit, training: SignalRecord, validation: SignalRecord) -> float:
    temperatures = training.temp_c.astype(float).tolist()
    ambients = training.ambient_c.astype(float).tolist()
    inputs = training.q.astype(float).tolist()
    errors: list[float] = []
    for temp_c, input_q, ambient_c in zip(validation.temp_c, validation.q, validation.ambient_c, strict=True):
        state = _state_from_values(temperatures, ambients, inputs, fit)
        transition_delayed = _delayed_input(inputs, np.empty(0), len(inputs) - 1, fit.delay)
        output_delayed = _delayed_input(inputs, np.empty(0), len(inputs), fit.delay)
        prediction = _output(
            fit, _advance(fit, state, transition_delayed), output_delayed, float(ambient_c)
        )
        errors.append((prediction - float(temp_c)) ** 2)
        temperatures.append(float(temp_c))
        ambients.append(float(ambient_c))
        inputs.append(float(input_q))
    return float(np.mean(errors))


def _state_from_record(record: SignalRecord, fit: SubspaceFit) -> FloatArray:
    return _state_from_values(
        record.temp_c.astype(float).tolist(),
        record.ambient_c.astype(float).tolist(),
        record.q.astype(float).tolist(),
        fit,
    )


def _state_from_values(
    temperatures: list[float], ambients: list[float], inputs: list[float], fit: SubspaceFit
) -> FloatArray:
    if len(temperatures) != len(ambients) or len(temperatures) != len(inputs):
        raise ValueError("state initialization requires synchronized history")
    rows = min(len(temperatures), max(2 * fit.order, 2))
    if rows < fit.order:
        raise ValueError("record does not contain enough observations for model order")
    start = len(temperatures) - rows
    observability = _observability(fit.A, fit.C, rows)
    residuals = np.empty(rows, dtype=np.float64)
    forced_state = np.zeros(fit.order, dtype=np.float64)
    for row, target in enumerate(range(start, len(temperatures))):
        delayed = _delayed_input(inputs, np.empty(0), target, fit.delay)
        residuals[row] = (
            temperatures[target] - ambients[target]
            - (fit.C @ forced_state)
            - fit.D[0] * (delayed - fit.input_mean)
            - fit.intercept
        )
        forced_state = _advance(fit, forced_state, delayed)
    state = np.asarray(_svd_least_squares(observability, residuals), dtype=np.float64)
    for target in range(start, len(temperatures) - 1):
        state = _advance(fit, state, _delayed_input(inputs, np.empty(0), target, fit.delay))
    return state


def _advance(fit: SubspaceFit, state: FloatArray, delayed_q: float) -> FloatArray:
    return fit.A @ state + fit.B * (delayed_q - fit.input_mean) + fit.state_offset


def _output(fit: SubspaceFit, state: FloatArray, delayed_q: float, ambient_c: float) -> float:
    return float(
        ambient_c + fit.C @ state + fit.D[0] * (delayed_q - fit.input_mean) + fit.intercept
    )


def _delayed_input(history: list[float], future: FloatArray, target: int, delay: int) -> float:
    index = target - delay
    if index < 0:
        return 0.0
    return float(history[index]) if index < len(history) else float(future[index - len(history)])


def _affine_delayed_input(
    history: list[float], history_length: int, target: int, delay: int, horizon: int
) -> tuple[float, FloatArray]:
    index = target - delay
    response = np.zeros(horizon, dtype=np.float64)
    if index < history_length:
        return float(history[index]), response
    response[index - history_length] = 1.0
    return 0.0, response


def _block_hankel(values: FloatArray, rows: int) -> FloatArray:
    frames = np.asarray(values, dtype=np.float64)
    if frames.ndim == 1:
        frames = frames[:, np.newaxis]
    columns = frames.shape[0] - rows + 1
    if columns < 1:
        raise ValueError("not enough samples for block Hankel matrix")
    return np.concatenate(
        [frames[offset : offset + columns].T for offset in range(rows)], axis=0
    )



def _project_matrix(A: FloatArray) -> FloatArray:
    values, vectors = np.linalg.eig(A)
    values = np.array(
        [value * min(1.0, 0.999 / abs(value)) if abs(value) else value for value in values]
    )
    return np.real_if_close(vectors @ np.diag(values) @ np.linalg.inv(vectors)).astype(np.float64)


def _steady_gain(fit: SubspaceFit) -> float:
    try:
        return float(fit.C @ np.linalg.solve(np.eye(fit.order) - fit.A, fit.B) + fit.D[0])
    except np.linalg.LinAlgError:
        return 0.0


def _observability(A: FloatArray, C: FloatArray, rows: int) -> FloatArray:
    result = np.empty((rows, A.shape[0]), dtype=np.float64); power = np.eye(A.shape[0], dtype=np.float64)
    for row in range(rows):
        result[row] = C @ power; power = power @ A
    return result


def _slice_record(record: SignalRecord, start: int, stop: int) -> SignalRecord:
    return SignalRecord(record.time_s[start:stop], record.temp_c[start:stop], record.q[start:stop], record.ambient_c[start:stop], record.provenance)


def _join_records(left: SignalRecord, right: SignalRecord, max_samples: int) -> SignalRecord:
    if right.time_s[0] <= left.time_s[-1]:
        right = _slice_record(right, int(np.searchsorted(right.time_s, left.time_s[-1], side="right")), right.time_s.size)
    if right.time_s.size == 0:
        return _slice_record(left, max(0, left.time_s.size - max_samples), left.time_s.size)
    return SignalRecord(np.concatenate((left.time_s, right.time_s))[-max_samples:], np.concatenate((left.temp_c, right.temp_c))[-max_samples:], np.concatenate((left.q, right.q))[-max_samples:], np.concatenate((left.ambient_c, right.ambient_c))[-max_samples:], left.provenance)


def _validate_record(record: SignalRecord) -> None:
    if not (record.time_s.size == record.temp_c.size == record.q.size == record.ambient_c.size):
        raise ValueError("record signal arrays must have equal lengths")
    if record.time_s.size == 0:
        raise ValueError("record must contain at least one sample")


def _as_vector(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return array


def _freeze(value: object) -> Mapping[str, object]:
    def freeze(nested: object) -> object:
        if isinstance(nested, dict):
            return MappingProxyType({key: freeze(item) for key, item in nested.items()})
        if isinstance(nested, list):
            return tuple(freeze(item) for item in nested)
        return nested
    frozen = freeze(value)
    assert isinstance(frozen, Mapping)
    return frozen
