"""Regularized Laguerre dynamic-matrix control identification model.

The model represents an input step response with a compact, discrete Laguerre
basis. It is an experiment arm, not a production controller implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import numpy.typing as npt

from .contracts import AffinePrediction, FloatArray, Observation, SignalRecord, UpdateOutcome

_STEP_RESPONSE_SECONDS = 60 * 60
_REFRESH_SECONDS = 5 * 60
_EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class DMCConfig:
    """Regularization, basis-bank, and physical-gain settings for ``LaguerreDMC``."""

    terms: tuple[int, ...]
    poles: tuple[float, ...]
    delay_seconds: tuple[int, ...] = tuple(range(0, 301, 20))
    lambda_curve: float = 1e-2
    forgetting_factor: float = 0.995
    initial_covariance: float = 1_000.0
    final_gain_bounds: tuple[float, float] = (1e-6, 100.0)
    challenger_margin: float = 1e-9

    def __post_init__(self) -> None:
        if not self.terms or any(term < 1 for term in self.terms):
            raise ValueError("terms must contain positive basis counts")
        if len(set(self.terms)) != len(self.terms):
            raise ValueError("terms must not contain duplicates")
        if not self.poles or any(not 0.0 <= pole < 1.0 for pole in self.poles):
            raise ValueError("poles must be in [0, 1)")
        if len(set(self.poles)) != len(self.poles):
            raise ValueError("poles must not contain duplicates")
        if not self.delay_seconds or any(delay < 0 for delay in self.delay_seconds):
            raise ValueError("delay_seconds must contain non-negative delays")
        if len(set(self.delay_seconds)) != len(self.delay_seconds):
            raise ValueError("delay_seconds must not contain duplicates")
        if self.lambda_curve < 0.0:
            raise ValueError("lambda_curve must be non-negative")
        if not 0.0 < self.forgetting_factor <= 1.0:
            raise ValueError("forgetting_factor must be in (0, 1]")
        if self.initial_covariance <= 0.0:
            raise ValueError("initial_covariance must be positive")
        lower, upper = self.final_gain_bounds
        if not 0.0 < lower <= upper:
            raise ValueError("final_gain_bounds must be positive and ordered")
        if self.challenger_margin < 0.0:
            raise ValueError("challenger_margin must be non-negative")


@dataclass(slots=True)
class _Candidate:
    terms: int
    pole: float
    delay_steps: int
    basis: FloatArray
    coefficients: FloatArray
    information_factor: FloatArray
    normal_rhs: FloatArray
    projected_gain: bool = False
    validation_error: float = 0.0
    validation_samples: int = 0
    consecutive_wins: int = 0

    @property
    def step_response(self) -> FloatArray:
        return self.basis @ self.coefficients[: self.terms]

    @property
    def promotion_eligible(self) -> bool:
        return not self.projected_gain


class LaguerreDMC:
    """Adaptive positive-gain DMC model with a Laguerre step response.

    Candidate term counts, poles, and the 0--300-second delay grid are fitted
    on an earlier chronological partition and selected by the later partition.
    Online frames update every candidate through QR square-root RLS. A new
    candidate becomes active only after two five-minute challenger wins.
    """

    def __init__(self, config: DMCConfig) -> None:
        self._config = config
        self._candidates: list[_Candidate] = []
        self._active_index = 0
        self._time_history: list[float] = []
        self._temperature_history: list[float] = []
        self._input_history: list[float] = []
        self._ambient_history: list[float] = []
        self._frame_seconds = 20.0
        self._last_refresh_time_s: float | None = None
        self._refreshes = 0

        self._max_plausible_gain: float | None = None

    @property
    def _active(self) -> _Candidate:
        if not self._candidates:
            raise RuntimeError("fit must be called before accessing the active candidate")
        return self._candidates[self._active_index]

    @property
    def promotion_eligible(self) -> bool:
        """Whether the active response needed no physical gain projection."""
        return self._active.promotion_eligible

    def fit(self, record: SignalRecord) -> None:
        """Fit and select a response on chronological validation data."""
        _validate_record(record)
        self._frame_seconds = _frame_seconds(record.time_s)
        response_length = max(1, round(_STEP_RESPONSE_SECONDS / self._frame_seconds))
        candidates = self._new_candidates(response_length)
        validation_start = max(1, int(record.time_s.size * 0.75))
        train = slice(0, validation_start)
        input_span = max(float(np.ptp(record.q[:validation_start])), np.finfo(np.float64).eps)
        output_span = max(
            float(np.ptp(record.temp_c[:validation_start])),
            np.finfo(np.float64).eps,
        )
        self._max_plausible_gain = 16.0 * output_span / input_span

        for candidate in candidates:
            self._fit_candidate(candidate, record, train)
            predicted = self._predict_indices(candidate, record.q, record.ambient_c)
            error = predicted[validation_start:] - record.temp_c[validation_start:]
            candidate.validation_error = float(error @ error)
            candidate.validation_samples = int(error.size)

        self._active_index = min(
            range(len(candidates)),
            key=lambda index: candidates[index].validation_error / max(1, candidates[index].validation_samples),
        )
        for candidate in candidates:
            self._fit_candidate(candidate, record, slice(0, record.time_s.size))
            candidate.validation_error = 0.0
            candidate.validation_samples = 0
            candidate.consecutive_wins = 0

        self._candidates = candidates
        self._time_history = record.time_s.astype(np.float64, copy=False).tolist()
        self._temperature_history = record.temp_c.astype(np.float64, copy=False).tolist()
        self._input_history = record.q.astype(np.float64, copy=False).tolist()
        self._ambient_history = record.ambient_c.astype(np.float64, copy=False).tolist()
        self._last_refresh_time_s = float(record.time_s[-1])
        self._refreshes = 0

    def forecast(
        self,
        prefix: SignalRecord,
        q_future: FloatArray,
        ambient_future: FloatArray,
    ) -> FloatArray:
        """Predict a free run from the supplied prefix and planned input."""
        _validate_record(prefix)
        future_q = _as_vector(q_future, "q_future")
        future_ambient = _as_vector(ambient_future, "ambient_future")
        if future_q.size != future_ambient.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        free, response = self._affine_components(
            self._active,
            prefix.q,
            prefix.temp_c,
            prefix.ambient_c,
            float(prefix.q[-1]),
            future_ambient,
        )
        prediction = free + response @ future_q
        prediction.setflags(write=False)
        return prediction

    def observe(self, observation: Observation) -> UpdateOutcome:
        """Score and learn one informative observation."""
        prediction = self._next_prediction(observation)
        prefix_q = np.asarray(self._input_history, dtype=np.float64)
        updated = False
        for candidate in self._candidates:
            feature = self._feature(candidate, prefix_q, observation.ambient_c)
            candidate_prediction = float(candidate.coefficients @ feature)
            error = observation.temp_c - candidate_prediction
            candidate.validation_error += error * error
            candidate.validation_samples += 1
            if np.linalg.norm(feature[: candidate.terms]) > _EPSILON:
                self._rls_update(candidate, feature, observation.temp_c)
                updated = True

        self._append_observation(observation)
        if self._last_refresh_time_s is None or observation.time_s - self._last_refresh_time_s >= _REFRESH_SECONDS:
            self._refresh_candidate(observation.time_s)
        return UpdateOutcome(
            predicted_temp_c=prediction,
            observed_temp_c=observation.temp_c,
            innovation_c=observation.temp_c - prediction,
            updated=updated,
        )

    def track(self, observation: Observation) -> UpdateOutcome:
        """Assimilate runtime history without updating DMC coefficients."""
        prediction = self._next_prediction(observation)
        self._append_observation(observation)
        return UpdateOutcome(
            predicted_temp_c=prediction,
            observed_temp_c=observation.temp_c,
            innovation_c=observation.temp_c - prediction,
            updated=False,
        )

    def _next_prediction(self, observation: Observation) -> float:
        if not self._input_history:
            raise RuntimeError("fit must be called before observe")
        prefix_q = np.asarray(self._input_history, dtype=np.float64)
        free, response = self._affine_components(
            self._active,
            prefix_q,
            np.asarray(self._temperature_history, dtype=np.float64),
            np.asarray(self._ambient_history, dtype=np.float64),
            float(prefix_q[-1]),
            np.asarray([observation.ambient_c], dtype=np.float64),
        )
        return float(free[0] + response[0, 0] * observation.q)

    def _append_observation(self, observation: Observation) -> None:
        self._time_history.append(observation.time_s)
        self._temperature_history.append(observation.temp_c)
        self._input_history.append(observation.q)
        self._ambient_history.append(observation.ambient_c)

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: FloatArray,
    ) -> AffinePrediction:
        """Return the exact shifted-step-response DMC horizon map."""
        if not self._input_history:
            raise RuntimeError("fit must be called before affine_prediction")
        if horizon_steps < 0:
            raise ValueError("horizon_steps must be non-negative")
        future_ambient = _as_vector(ambient_future, "ambient_future")
        if future_ambient.size != horizon_steps:
            raise ValueError("ambient_future length must equal horizon_steps")
        inputs = np.asarray(self._input_history, dtype=np.float64).copy()
        inputs[-1] = float(q_previous)
        free, response = self._affine_components(
            self._active,
            inputs,
            np.asarray(self._temperature_history, dtype=np.float64),
            np.asarray(self._ambient_history, dtype=np.float64),
            float(q_previous),
            future_ambient,
        )
        return AffinePrediction(free, response)

    def snapshot(self) -> Mapping[str, object]:
        """Return an immutable 60-minute response and active identification state."""
        active = self._active
        response = np.zeros(active.basis.shape[0], dtype=np.float64)
        usable = response.size - active.delay_steps
        if usable > 0:
            response[active.delay_steps :] = active.step_response[:usable]
        response.setflags(write=False)
        return _freeze_snapshot(
            {
                "schema": "laguerre-dmc/v1",
                "terms": active.terms,
                "pole": active.pole,
                "delay_steps": active.delay_steps,
                "delay_seconds": active.delay_steps * self._frame_seconds,
                "step_response": response,
                "final_gain": float(response[-1]),
                "steady_gain": float(response[-1]),
                "plausibility_bounds": {
                    "max_steady_gain_c_per_q": self._max_plausible_gain,
                },
                "gain_projected": active.projected_gain,
                "promotion_eligible": active.promotion_eligible,
                "update_timing": {
                    "last_refresh_time_s": self._last_refresh_time_s,
                    "refreshes": self._refreshes,
                },
            }
        )

    def _new_candidates(self, response_length: int) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for terms in self._config.terms:
            for pole in self._config.poles:
                basis = np.cumsum(laguerre_basis(response_length, terms, pole), axis=0)
                for delay_seconds in self._config.delay_seconds:
                    delay_steps = round(delay_seconds / self._frame_seconds)
                    feature_count = terms + 2
                    information_scale = 1.0 / np.sqrt(self._config.initial_covariance)
                    candidates.append(
                        _Candidate(
                            terms=terms,
                            pole=pole,
                            delay_steps=delay_steps,
                            basis=basis,
                            coefficients=np.zeros(feature_count, dtype=np.float64),
                            information_factor=np.eye(feature_count) * information_scale,
                            normal_rhs=np.zeros(feature_count, dtype=np.float64),
                        )
                    )
        return candidates

    def _fit_candidate(self, candidate: _Candidate, record: SignalRecord, rows: slice) -> None:
        indexes = np.arange(record.time_s.size)[rows]
        input_features = self._input_features(candidate, record.q)[indexes]
        Phi = np.column_stack((input_features, record.ambient_c[indexes], np.ones(indexes.size)))
        target = record.temp_c[indexes]
        D2 = _second_difference(candidate.basis.shape[0])
        basis = candidate.basis
        normal = input_features.T @ input_features + self._config.lambda_curve * (D2 @ basis).T @ (D2 @ basis)
        rhs = input_features.T @ target
        full_normal = Phi.T @ Phi
        full_normal[: candidate.terms, : candidate.terms] = normal
        full_rhs = Phi.T @ target
        full_rhs[: candidate.terms] = rhs
        full_normal += np.eye(full_normal.shape[0]) / self._config.initial_covariance
        factor = np.linalg.cholesky(full_normal).T
        candidate.information_factor = factor
        candidate.normal_rhs = full_rhs
        candidate.coefficients = _solve_information(factor, full_rhs)
        self._project_gain(candidate)

    def _feature(self, candidate: _Candidate, inputs: FloatArray, ambient_c: float) -> FloatArray:
        feature = np.empty(candidate.terms + 2, dtype=np.float64)
        feature[: candidate.terms] = self._input_features(candidate, inputs)[-1]
        feature[-2] = ambient_c
        feature[-1] = 1.0
        return feature

    def _predict_indices(self, candidate: _Candidate, inputs: FloatArray, ambient: FloatArray) -> FloatArray:
        input_features = self._input_features(candidate, inputs)
        design = np.column_stack((input_features, ambient, np.ones(inputs.size)))
        return design @ candidate.coefficients

    def _input_features(self, candidate: _Candidate, inputs: FloatArray) -> FloatArray:
        delta = np.diff(np.concatenate((np.zeros(1, dtype=np.float64), inputs)))
        output = np.zeros((inputs.size, candidate.terms), dtype=np.float64)
        for term in range(candidate.terms):
            convolution = np.convolve(delta, candidate.basis[:, term])[: inputs.size]
            if candidate.delay_steps == 0:
                output[:, term] = convolution
            elif candidate.delay_steps < inputs.size:
                output[candidate.delay_steps :, term] = convolution[: -candidate.delay_steps]
            settled_start = candidate.delay_steps + candidate.basis.shape[0]
            if settled_start < inputs.size:
                output[settled_start:, term] += candidate.basis[-1, term] * inputs[:-settled_start]
        return output

    def _rls_update(self, candidate: _Candidate, feature: FloatArray, target: float) -> None:
        factor = np.vstack(
            (
                np.sqrt(self._config.forgetting_factor) * candidate.information_factor,
                feature,
            )
        )
        _, information_factor = np.linalg.qr(factor, mode="reduced")
        signs = np.where(np.diag(information_factor) < 0.0, -1.0, 1.0)
        information_factor *= signs[:, np.newaxis]
        candidate.information_factor = information_factor
        candidate.normal_rhs = self._config.forgetting_factor * candidate.normal_rhs + feature * target
        candidate.coefficients = _solve_information(candidate.information_factor, candidate.normal_rhs)
        self._project_gain(candidate)

    def _project_gain(self, candidate: _Candidate) -> None:
        """Project final gain through the same path after batch and online fitting."""
        exposed_index = max(0, candidate.basis.shape[0] - candidate.delay_steps - 1)
        response_row = candidate.basis[exposed_index]
        raw_gain = float(response_row @ candidate.coefficients[: candidate.terms])
        lower, upper = self._config.final_gain_bounds
        clipped_gain = float(np.clip(raw_gain, lower, upper))
        candidate.projected_gain = not np.isclose(raw_gain, clipped_gain, atol=1e-10)
        if candidate.projected_gain:
            candidate.coefficients[: candidate.terms] += (
                (clipped_gain - raw_gain) * response_row / max(float(response_row @ response_row), _EPSILON)
            )

    def _refresh_candidate(self, refresh_time_s: float) -> None:
        losses = [candidate.validation_error / max(1, candidate.validation_samples) for candidate in self._candidates]
        challenger_index = int(np.argmin(losses))
        active_loss = losses[self._active_index]
        if (
            challenger_index != self._active_index
            and losses[challenger_index] + self._config.challenger_margin < active_loss
            and self._candidates[challenger_index].promotion_eligible
        ):
            challenger = self._candidates[challenger_index]
            challenger.consecutive_wins += 1
            for index, candidate in enumerate(self._candidates):
                if index != challenger_index:
                    candidate.consecutive_wins = 0
            if challenger.consecutive_wins >= 2:
                self._active_index = challenger_index
                for candidate in self._candidates:
                    candidate.consecutive_wins = 0
        else:
            for candidate in self._candidates:
                candidate.consecutive_wins = 0
        for candidate in self._candidates:
            candidate.validation_error = 0.0
            candidate.validation_samples = 0
        self._last_refresh_time_s = refresh_time_s
        self._refreshes += 1

    def _affine_components(
        self,
        candidate: _Candidate,
        prefix_q: FloatArray,
        prefix_temp: FloatArray,
        prefix_ambient: FloatArray,
        q_previous: float,
        ambient_future: FloatArray,
    ) -> tuple[FloatArray, FloatArray]:
        horizon = ambient_future.size
        step = candidate.step_response
        prefix = np.asarray(prefix_q, dtype=np.float64)
        if prefix.size == 0:
            raise ValueError("prefix must contain at least one input")
        prefix = prefix.copy()
        prefix[-1] = q_previous
        past_delta = np.diff(np.concatenate((np.zeros(1, dtype=np.float64), prefix)))
        free = np.empty(horizon, dtype=np.float64)
        for future_index in range(horizon):
            target = prefix.size + future_index
            value = candidate.coefficients[-2] * ambient_future[future_index]
            value += candidate.coefficients[-1]
            start = 0
            for source in range(start, prefix.size):
                lag = target - candidate.delay_steps - source
                if 0 <= lag < step.size:
                    value += step[lag] * past_delta[source]
                elif lag >= step.size:
                    value += step[-1] * past_delta[source]
            free[future_index] = value
        shifted_step = np.zeros((horizon, horizon), dtype=np.float64)
        for row in range(horizon):
            for column in range(row + 1):
                lag = row - column - candidate.delay_steps
                if 0 <= lag < step.size:
                    shifted_step[row, column] = step[lag]
                elif lag >= step.size:
                    shifted_step[row, column] = step[-1]
        difference = np.eye(horizon, dtype=np.float64)
        if horizon > 1:
            difference[np.arange(1, horizon), np.arange(horizon - 1)] = -1.0
        response = shifted_step @ difference
        if horizon:
            free -= shifted_step[:, 0] * q_previous

        # Cross-validation prefixes may begin after older input changes. Carry
        # the measured residual state through the identified response tail.
        if prefix_temp.size:
            observed_last = float(prefix_temp[-1])
            modeled_last = candidate.coefficients[-2] * float(prefix_ambient[-1])
            modeled_last += candidate.coefficients[-1]
            target = prefix.size - 1
            for source in range(prefix.size):
                lag = target - candidate.delay_steps - source
                if 0 <= lag < step.size:
                    modeled_last += step[lag] * past_delta[source]
                elif lag >= step.size:
                    modeled_last += step[-1] * past_delta[source]
            correction = observed_last - modeled_last
            final_gain = max(float(step[-1]), _EPSILON)
            tail = np.zeros(horizon, dtype=np.float64)
            available = min(horizon, step.size)
            tail[:available] = (final_gain - step[:available]) / final_gain
            free += correction * np.maximum(tail, 0.0)
        return free, response


def laguerre_basis(length: int, terms: int, pole: float) -> FloatArray:
    """Generate discrete Laguerre impulse responses by a stable recurrence."""
    if length < 1:
        raise ValueError("length must be positive")
    if terms < 1 or terms > length:
        raise ValueError("terms must be between one and length")
    if not 0.0 <= pole < 1.0:
        raise ValueError("pole must be in [0, 1)")
    basis = np.zeros((length, terms), dtype=np.float64)
    basis[0, 0] = np.sqrt(1.0 - pole * pole)
    for term in range(1, terms):
        basis[0, term] = -pole * basis[0, term - 1]
    for index in range(1, length):
        basis[index, 0] = pole * basis[index - 1, 0]
    for term in range(1, terms):
        for index in range(1, length):
            basis[index, term] = (
                pole * basis[index - 1, term] + basis[index - 1, term - 1] - pole * basis[index, term - 1]
            )
    basis.setflags(write=False)
    return basis


def _frame_seconds(time_s: FloatArray) -> float:
    if time_s.size < 2:
        return 20.0
    frame = float(np.median(np.diff(time_s)))
    if frame <= 0.0:
        raise ValueError("record time must be strictly increasing")
    return frame


def _second_difference(length: int) -> FloatArray:
    if length < 3:
        return np.empty((0, length), dtype=np.float64)
    matrix = np.zeros((length - 2, length), dtype=np.float64)
    rows = np.arange(length - 2)
    matrix[rows, rows] = 1.0
    matrix[rows, rows + 1] = -2.0
    matrix[rows, rows + 2] = 1.0
    return matrix


def _solve_information(factor: FloatArray, rhs: FloatArray) -> FloatArray:
    lower_solution = np.linalg.solve(factor.T, rhs)
    return np.linalg.solve(factor, lower_solution)


def _as_vector(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return array


def _validate_record(record: SignalRecord) -> None:
    if record.temp_c.size == 0:
        raise ValueError("record must contain at least one sample")
    if record.temp_c.size != record.q.size or record.q.size != record.ambient_c.size:
        raise ValueError("record signal arrays must have equal lengths")


def _freeze_snapshot(value: object) -> Mapping[str, object]:
    def freeze(nested: object) -> object:
        if isinstance(nested, dict):
            return MappingProxyType({key: freeze(item) for key, item in nested.items()})
        if isinstance(nested, list):
            return tuple(freeze(item) for item in nested)
        return nested

    frozen = freeze(value)
    assert isinstance(frozen, Mapping)
    return frozen
