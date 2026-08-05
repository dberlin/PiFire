"""Temperature-scheduled online ARX identification with complete restoration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from .contracts import AffinePrediction, FloatArray, FrameObservation, ModelUpdate

_MAX_AR_POLE: Final = 0.999
_DC_GAIN_SCALE: Final = 16.0
_TEMPERATURE_KNOTS_C: Final = (82.2, 162.8, 232.2, 315.6)
_SCHEMA: Final = "scheduled-arx/v2"


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return int(value)


def _vector(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or not np.isfinite(array).all():
        raise ValueError(f"{name} must be a finite one-dimensional vector")
    return array


@dataclass(frozen=True, slots=True)
class ScheduledARXConfig:
    """Identification and delay-selection configuration for :class:`ScheduledARX`."""

    na: int
    nb: int
    delays: tuple[int, ...]
    forgetting_factor: float = 0.995
    initial_covariance: float = 1_000.0
    validation_window: int = 32
    challenger_margin: float = 1e-9

    def __post_init__(self) -> None:
        object.__setattr__(self, "na", _integer(self.na, "na", minimum=1))
        object.__setattr__(self, "nb", _integer(self.nb, "nb", minimum=1))
        if not isinstance(self.delays, tuple) or not self.delays:
            raise ValueError("delays must be a non-empty tuple")
        delays = tuple(_integer(delay, "delay") for delay in self.delays)
        if len(set(delays)) != len(delays):
            raise ValueError("delays must not contain duplicates")
        object.__setattr__(self, "delays", delays)
        forgetting_factor = _finite(self.forgetting_factor, "forgetting_factor")
        if not 0.0 < forgetting_factor <= 1.0:
            raise ValueError("forgetting_factor must be in (0, 1]")
        object.__setattr__(self, "forgetting_factor", forgetting_factor)
        initial_covariance = _finite(self.initial_covariance, "initial_covariance")
        if initial_covariance <= 0.0:
            raise ValueError("initial_covariance must be positive")
        object.__setattr__(self, "initial_covariance", initial_covariance)
        object.__setattr__(
            self, "validation_window", _integer(self.validation_window, "validation_window", minimum=1)
        )
        challenger_margin = _finite(self.challenger_margin, "challenger_margin")
        if challenger_margin < 0.0:
            raise ValueError("challenger_margin must be non-negative")
        object.__setattr__(self, "challenger_margin", challenger_margin)


@dataclass(slots=True)
class _Region:
    theta: FloatArray
    information_factor: FloatArray
    normal_rhs: FloatArray
    effective_samples: float = 0.0


@dataclass(slots=True)
class _Candidate:
    delay_steps: int
    regions: list[_Region]
    validation_error: float = 0.0
    validation_samples: int = 0
    consecutive_wins: int = 0


class ScheduledARX:
    """A four-knot, delay-bank ARX learner for complete control frames."""

    def __init__(self, config: ScheduledARXConfig) -> None:
        self._config = config
        self._feature_count = config.na + config.nb + 2
        self._history_limit = max(config.na + 1, max(config.delays) + config.nb + 1)
        self._candidates = self._new_candidates()
        self._active_delay = config.delays[0]
        self._temperature_history: list[float] = []
        self._input_history: list[float] = []
        self._ambient_history: list[float] = []
        self._last_observation_time_s: float | None = None
        self._refreshes = 0
        self._last_refresh_sample: int | None = None
        self._max_dc_gain: float | None = None
        self._max_forecast_deviation: float | None = None

    @property
    def config(self) -> ScheduledARXConfig:
        """Return the immutable learner configuration."""
        return self._config

    def fit(self, observations: Sequence[FrameObservation]) -> None:
        """Reset and identify every delay candidate from complete frame history."""
        frames = tuple(observations)
        if not frames:
            raise ValueError("observations must contain at least one frame")
        self._validate_frames(frames)
        temperatures = np.asarray([frame.temp_c for frame in frames], dtype=np.float64)
        inputs = np.asarray([frame.realized_q for frame in frames], dtype=np.float64)
        self._candidates = self._new_candidates()
        self._active_delay = self._config.delays[0]
        self._temperature_history = []
        self._input_history = []
        self._ambient_history = []
        self._refreshes = 0
        self._last_refresh_sample = None
        self._max_dc_gain = _DC_GAIN_SCALE * max(float(np.ptp(temperatures)), np.finfo(np.float64).eps) / max(float(np.ptp(inputs)), np.finfo(np.float64).eps)
        self._max_forecast_deviation = _DC_GAIN_SCALE * max(float(np.ptp(temperatures)), np.finfo(np.float64).eps)
        for sample, frame in enumerate(frames):
            if len(self._temperature_history) >= self._history_limit:
                self._assimilate(frame.ambient_c, frame.temp_c, sample)
            self._append_observation(frame)

    def observe(self, observation: FrameObservation) -> ModelUpdate:
        """Score then assimilate one frame, preserving its pre-update innovation."""
        prediction = self._next_prediction(observation)
        self._assimilate(
            observation.ambient_c,
            observation.temp_c,
            len(self._temperature_history),
        )
        self._append_observation(observation)
        return ModelUpdate(prediction, observation.temp_c, observation.temp_c - prediction, True)

    def track(self, observation: FrameObservation) -> ModelUpdate:
        """Advance bounded history without changing ARX sufficient statistics."""
        prediction = self._next_prediction(observation)
        self._append_observation(observation)
        return ModelUpdate(prediction, observation.temp_c, observation.temp_c - prediction, False)

    def forecast(
        self,
        prefix: Sequence[FrameObservation],
        q_future: npt.ArrayLike,
        ambient_future: npt.ArrayLike,
    ) -> FloatArray:
        """Predict a free run from a supplied frame prefix and future inputs."""
        frames = tuple(prefix)
        if not frames:
            raise ValueError("prefix must contain at least one frame")
        self._validate_frames(frames)
        future_q = _vector(q_future, "q_future")
        future_ambient = _vector(ambient_future, "ambient_future")
        if future_q.size != future_ambient.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        temperatures = [frame.temp_c for frame in frames[-self._history_limit :]]
        inputs = [frame.realized_q for frame in frames[-self._history_limit :]]
        if len(temperatures) < self._history_limit:
            raise ValueError("prefix lacks the ARX lag history")
        theta = self._scheduled_theta(self._candidates[self._active_delay], temperatures[-1])
        output = self._forecast_with_theta(theta, temperatures, inputs, future_q, future_ambient)
        output.setflags(write=False)
        return output

    def affine_prediction(
        self, horizon_steps: int, q_previous: float, ambient_future: npt.ArrayLike
    ) -> AffinePrediction:
        """Express the active incremental recursion as a horizon affine map."""
        if horizon_steps < 0:
            raise ValueError("horizon_steps must be non-negative")
        future_ambient = _vector(ambient_future, "ambient_future")
        if future_ambient.size != horizon_steps:
            raise ValueError("ambient_future length must equal horizon_steps")
        self._require_history()
        q_previous = _finite(q_previous, "q_previous")
        if not 0.0 <= q_previous <= 1.0:
            raise ValueError("q_previous must be in [0, 1]")
        candidate = self._candidates[self._active_delay]
        theta = self._scheduled_theta(candidate, self._temperature_history[-1])
        inputs = list(self._input_history)
        inputs[-1] = q_previous
        free_output = np.empty(horizon_steps, dtype=np.float64)
        input_response = np.zeros((horizon_steps, horizon_steps), dtype=np.float64)
        temperatures: list[tuple[float, FloatArray]] = [
            (value, np.zeros(horizon_steps, dtype=np.float64)) for value in self._temperature_history
        ]
        input_terms: list[tuple[float, FloatArray]] = [
            (value, np.zeros(horizon_steps, dtype=np.float64)) for value in inputs
        ]
        input_terms.extend((0.0, _basis(horizon_steps, index)) for index in range(horizon_steps))
        for step in range(horizon_steps):
            target = len(self._temperature_history) + step
            prior_value, prior_response = temperatures[target - 1]
            delta_constant = theta[-1] + theta[-2] * (future_ambient[step] - prior_value)
            delta_response = -theta[-2] * prior_response
            for lag in range(self._config.na):
                value, coefficients = temperatures[target - 1 - lag]
                previous_value, previous_coefficients = temperatures[target - 2 - lag]
                delta_constant += theta[lag] * (value - previous_value)
                delta_response += theta[lag] * (coefficients - previous_coefficients)
            for lag in range(self._config.nb):
                value, coefficients = input_terms[target - 1 - candidate.delay_steps - lag]
                previous_value, previous_coefficients = input_terms[target - 2 - candidate.delay_steps - lag]
                coefficient = theta[self._config.na + lag]
                delta_constant += coefficient * (value - previous_value)
                delta_response += coefficient * (coefficients - previous_coefficients)
            denominator = 1.0 + theta[-2]
            if abs(denominator) < 1e-9:
                raise RuntimeError("ambient-error coefficient makes the ARX recursion singular")
            next_value = prior_value + delta_constant / denominator
            next_response = prior_response + delta_response / denominator
            free_output[step] = next_value
            input_response[step] = next_response
            temperatures.append((next_value, next_response))
        response_peak = float(np.max(np.abs(input_response))) if input_response.size else 0.0
        if self._max_forecast_deviation is not None and response_peak > self._max_forecast_deviation:
            scale = self._max_forecast_deviation / response_peak
            baseline = self._temperature_history[-1]
            free_output = baseline + (free_output - baseline) * scale
            input_response *= scale
        if not np.isfinite(free_output).all() or not np.isfinite(input_response).all():
            raise RuntimeError("ARX horizon forecast is non-finite")
        return AffinePrediction(free_output, input_response)

    def snapshot(self) -> dict[str, object]:
        """Serialize all learning state into independently owned JSON data."""
        return {
            "schema": _SCHEMA,
            "config": {
                "na": self._config.na,
                "nb": self._config.nb,
                "delays": list(self._config.delays),
                "forgetting_factor": self._config.forgetting_factor,
                "initial_covariance": self._config.initial_covariance,
                "validation_window": self._config.validation_window,
                "challenger_margin": self._config.challenger_margin,
            },
            "active_delay": self._active_delay,
            "history": {
                "temperature_c": list(self._temperature_history),
                "realized_q": list(self._input_history),
                "ambient_c": list(self._ambient_history),
            },
            "candidates": [
                self._candidate_snapshot(candidate)
                for candidate in self._candidates.values()
            ],
            "status": self._status_snapshot(),
        }

    def _status_snapshot(self) -> dict[str, object]:
        """Derive evidence-only status from the authoritative learner state."""
        active = self._candidates[self._active_delay]
        active_regions = [
            self._region_status(region, knot)
            for knot, region in zip(
                _TEMPERATURE_KNOTS_C, active.regions, strict=True
            )
        ]
        return {
            "knots_c": list(_TEMPERATURE_KNOTS_C),
            "regions": active_regions,
            "steady_gain": sum(
                float(region["dc_gain"]) for region in active_regions
            )
            / len(active_regions),
            "max_dc_gain_c_per_q": self._max_dc_gain,
            "max_ar_pole": _MAX_AR_POLE,
            "last_observation_time_s": self._last_observation_time_s,
            "refreshes": self._refreshes,
            "last_refresh_sample": self._last_refresh_sample,
            "max_forecast_deviation_c": self._max_forecast_deviation,
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object]) -> ScheduledARX:
        """Validate and restore an owned learner from a ``scheduled-arx/v2`` snapshot."""
        if not isinstance(snapshot, Mapping) or snapshot.get("schema") != _SCHEMA:
            raise ValueError(f"snapshot schema must be {_SCHEMA!r}")
        config_data = _mapping(snapshot.get("config"), "config")
        config = ScheduledARXConfig(
            na=_integer(config_data.get("na"), "config.na", minimum=1),
            nb=_integer(config_data.get("nb"), "config.nb", minimum=1),
            delays=tuple(_integer(value, "config.delays item") for value in _sequence(config_data.get("delays"), "config.delays")),
            forgetting_factor=_finite(config_data.get("forgetting_factor"), "config.forgetting_factor"),
            initial_covariance=_finite(config_data.get("initial_covariance"), "config.initial_covariance"),
            validation_window=_integer(config_data.get("validation_window"), "config.validation_window", minimum=1),
            challenger_margin=_finite(config_data.get("challenger_margin"), "config.challenger_margin"),
        )
        model = cls(config)
        active_delay = _integer(snapshot.get("active_delay"), "active_delay")
        if active_delay not in config.delays:
            raise ValueError("active_delay must belong to config.delays")
        candidate_data = _sequence(snapshot.get("candidates"), "candidates")
        if len(candidate_data) != len(config.delays):
            raise ValueError("candidates must contain every configured delay exactly once")
        candidates: dict[int, _Candidate] = {}
        for payload in candidate_data:
            candidate = model._candidate_from_snapshot(_mapping(payload, "candidate"))
            if candidate.delay_steps not in config.delays or candidate.delay_steps in candidates:
                raise ValueError("candidate delays must exactly match config.delays")
            candidates[candidate.delay_steps] = candidate
        if set(candidates) != set(config.delays):
            raise ValueError("candidate delays must exactly match config.delays")
        history = _mapping(snapshot.get("history"), "history")
        temperatures = _finite_list(history.get("temperature_c"), "history.temperature_c")
        inputs = _finite_list(history.get("realized_q"), "history.realized_q")
        ambients = _finite_list(history.get("ambient_c"), "history.ambient_c")
        if len(temperatures) != len(inputs) or len(inputs) != len(ambients):
            raise ValueError("history vectors must have equal lengths")
        if len(temperatures) > model._history_limit:
            raise ValueError("history exceeds the configured bounded lag length")
        if any(not 0.0 <= value <= 1.0 for value in inputs):
            raise ValueError("history.realized_q must be in [0, 1]")
        status = _mapping(snapshot.get("status"), "status")
        max_dc_gain = _optional_nonnegative(status.get("max_dc_gain_c_per_q"), "status.max_dc_gain_c_per_q")
        max_forecast_deviation = _optional_nonnegative(status.get("max_forecast_deviation_c"), "status.max_forecast_deviation_c")
        last_observation = _optional_finite(status.get("last_observation_time_s"), "status.last_observation_time_s")
        refreshes = _integer(status.get("refreshes"), "status.refreshes")
        last_refresh = status.get("last_refresh_sample")
        if last_refresh is not None:
            last_refresh = _integer(last_refresh, "status.last_refresh_sample")
        model._candidates = candidates
        model._active_delay = active_delay
        model._temperature_history = temperatures
        model._input_history = inputs
        model._ambient_history = ambients
        model._max_dc_gain = max_dc_gain
        model._max_forecast_deviation = max_forecast_deviation
        model._last_observation_time_s = last_observation
        model._refreshes = refreshes
        model._last_refresh_sample = last_refresh
        if status != model._status_snapshot():
            raise ValueError("snapshot status must exactly match restored learner state")
        return model

    def _new_candidates(self) -> dict[int, _Candidate]:
        return {delay: _Candidate(delay, self._new_regions()) for delay in self._config.delays}

    def _new_regions(self) -> list[_Region]:
        information_scale = 1.0 / np.sqrt(self._config.initial_covariance)
        regions = []
        for _ in _TEMPERATURE_KNOTS_C:
            theta = np.zeros(self._feature_count, dtype=np.float64)
            theta[self._config.na] = 1e-9
            regions.append(_Region(theta, np.eye(self._feature_count) * information_scale, np.zeros(self._feature_count, dtype=np.float64)))
        return regions

    def _validate_frames(self, frames: Sequence[FrameObservation]) -> None:
        previous_end: float | None = None
        for frame in frames:
            if not isinstance(frame, FrameObservation):
                raise ValueError("observations must contain FrameObservation values")
            if previous_end is not None and frame.frame_start_s < previous_end:
                raise ValueError("observations must be time ordered without overlap")
            previous_end = frame.frame_end_s

    def _require_history(self) -> None:
        if len(self._temperature_history) < self._history_limit:
            raise RuntimeError("fit must provide enough lag history before prediction")

    def _append_observation(self, observation: FrameObservation) -> None:
        self._temperature_history.append(observation.temp_c)
        self._input_history.append(observation.realized_q)
        self._ambient_history.append(observation.ambient_c)
        del self._temperature_history[: -self._history_limit]
        del self._input_history[: -self._history_limit]
        del self._ambient_history[: -self._history_limit]
        self._last_observation_time_s = observation.frame_end_s

    def _next_prediction(self, observation: FrameObservation) -> float:
        self._require_history()
        candidate = self._candidates[self._active_delay]
        theta = self._scheduled_theta(candidate, self._temperature_history[-1])
        return self._predict_next(theta, self._temperature_history, self._input_history, observation.ambient_c, candidate.delay_steps)

    def _assimilate(self, ambient_c: float, target_temp_c: float, refresh_sample: int) -> None:
        for candidate in self._candidates.values():
            theta = self._scheduled_theta(candidate, self._temperature_history[-1])
            feature = self._feature(self._temperature_history, self._input_history, ambient_c, target_temp_c, candidate.delay_steps)
            prediction = self._predict_next(theta, self._temperature_history, self._input_history, ambient_c, candidate.delay_steps)
            error = target_temp_c - prediction
            candidate.validation_error += error * error
            candidate.validation_samples += 1
            target_increment = target_temp_c - self._temperature_history[-1]
            for index, weight in self._region_weights(self._temperature_history[-1]):
                self._update_region(candidate.regions[index], feature, target_increment, weight)
        if self._candidates[self._active_delay].validation_samples >= self._config.validation_window:
            self._refresh_delay(refresh_sample)

    def _refresh_delay(self, refresh_sample: int) -> None:
        losses = {delay: candidate.validation_error / candidate.validation_samples for delay, candidate in self._candidates.items()}
        challenger_delay = min(losses, key=losses.__getitem__)
        if challenger_delay != self._active_delay and losses[challenger_delay] + self._config.challenger_margin < losses[self._active_delay]:
            challenger = self._candidates[challenger_delay]
            challenger.consecutive_wins += 1
            for delay, candidate in self._candidates.items():
                if delay != challenger_delay:
                    candidate.consecutive_wins = 0
            if challenger.consecutive_wins >= 2:
                self._active_delay = challenger_delay
                for candidate in self._candidates.values():
                    candidate.consecutive_wins = 0
        else:
            for candidate in self._candidates.values():
                candidate.consecutive_wins = 0
        for candidate in self._candidates.values():
            candidate.validation_error = 0.0
            candidate.validation_samples = 0
        self._refreshes += 1
        self._last_refresh_sample = refresh_sample

    def _update_region(self, region: _Region, feature: FloatArray, target: float, weight: float) -> None:
        if weight == 0.0:
            return
        weighted_feature = np.sqrt(weight) * feature
        factor = np.vstack((np.sqrt(self._config.forgetting_factor) * region.information_factor, weighted_feature))
        _, information_factor = np.linalg.qr(factor, mode="reduced")
        information_factor *= np.where(np.diag(information_factor) < 0.0, -1.0, 1.0)[:, np.newaxis]
        region.information_factor = information_factor
        region.normal_rhs = self._config.forgetting_factor * region.normal_rhs + weight * feature * target
        lower_solution = np.linalg.solve(information_factor.T, region.normal_rhs)
        region.theta = np.linalg.solve(information_factor, lower_solution)
        self._project_physical_parameters(region.theta)
        region.effective_samples = self._config.forgetting_factor * region.effective_samples + weight

    def _project_physical_parameters(self, theta: FloatArray) -> None:
        theta[-2] = max(0.0, float(theta[-2]))
        ar = theta[: self._config.na]
        roots = np.roots(np.concatenate(([1.0], -ar)))
        if roots.size and np.max(np.abs(roots)) > _MAX_AR_POLE:
            roots = np.where(np.abs(roots) > _MAX_AR_POLE, roots / np.abs(roots) * _MAX_AR_POLE, roots)
            theta[: self._config.na] = -np.real_if_close(np.poly(roots)[1:])
        input_slice = slice(self._config.na, self._config.na + self._config.nb)
        denominator = 1.0 - float(np.sum(theta[: self._config.na]))
        if denominator <= 1e-9:
            theta[: self._config.na] *= 0.998 / max(1e-12, abs(float(np.sum(theta[: self._config.na]))))
            denominator = 1.0 - float(np.sum(theta[: self._config.na]))
        gain = float(np.sum(theta[input_slice])) / denominator
        if gain <= 0.0:
            theta[input_slice] = 0.0
            theta[self._config.na] = max(1e-9, denominator * 1e-6)
        elif self._max_dc_gain is not None and gain > self._max_dc_gain:
            theta[input_slice] *= self._max_dc_gain / gain

    def _scheduled_theta(self, candidate: _Candidate, temp_c: float) -> FloatArray:
        theta = np.zeros(self._feature_count, dtype=np.float64)
        for index, weight in self._region_weights(temp_c):
            theta += weight * candidate.regions[index].theta
        self._project_physical_parameters(theta)
        return theta

    def _region_weights(self, temp_c: float) -> tuple[tuple[int, float], ...]:
        if temp_c <= _TEMPERATURE_KNOTS_C[0]:
            return ((0, 1.0),)
        if temp_c >= _TEMPERATURE_KNOTS_C[-1]:
            return ((len(_TEMPERATURE_KNOTS_C) - 1, 1.0),)
        for index, upper in enumerate(_TEMPERATURE_KNOTS_C[1:], start=1):
            if temp_c <= upper:
                lower = _TEMPERATURE_KNOTS_C[index - 1]
                upper_weight = (temp_c - lower) / (upper - lower)
                return ((index - 1, 1.0 - upper_weight), (index, upper_weight))
        raise AssertionError("temperature knot bounds are exhaustive")

    def _feature(self, temperatures: Sequence[float], inputs: Sequence[float], ambient_c: float, target_temp_c: float, delay_steps: int) -> FloatArray:
        feature = np.empty(self._feature_count, dtype=np.float64)
        for lag in range(self._config.na):
            feature[lag] = temperatures[-1 - lag] - temperatures[-2 - lag]
        for lag in range(self._config.nb):
            delayed_index = -1 - delay_steps - lag
            feature[self._config.na + lag] = inputs[delayed_index] - inputs[delayed_index - 1]
        feature[-2] = ambient_c - target_temp_c
        feature[-1] = 1.0
        return feature

    def _predict_next(self, theta: FloatArray, temperatures: Sequence[float], inputs: Sequence[float], ambient_c: float, delay_steps: int) -> float:
        increment = float(theta[-1])
        for lag in range(self._config.na):
            increment += theta[lag] * (temperatures[-1 - lag] - temperatures[-2 - lag])
        for lag in range(self._config.nb):
            delayed_index = -1 - delay_steps - lag
            increment += theta[self._config.na + lag] * (inputs[delayed_index] - inputs[delayed_index - 1])
        denominator = 1.0 + theta[-2]
        if abs(denominator) < 1e-9:
            raise RuntimeError("ambient-error coefficient makes the ARX recursion singular")
        increment += theta[-2] * (ambient_c - temperatures[-1])
        return float(temperatures[-1] + increment / denominator)

    def _forecast_with_theta(self, theta: FloatArray, temperatures: list[float], inputs: list[float], future_q: FloatArray, future_ambient: FloatArray) -> FloatArray:
        candidate = self._candidates[self._active_delay]
        output = np.empty(future_ambient.size, dtype=np.float64)
        for step, (q, ambient_c) in enumerate(zip(future_q, future_ambient, strict=True)):
            prediction = self._predict_next(theta, temperatures, inputs, float(ambient_c), candidate.delay_steps)
            output[step] = prediction
            temperatures.append(prediction)
            inputs.append(float(q))
        return output

    def _candidate_snapshot(self, candidate: _Candidate) -> dict[str, object]:
        return {
            "delay_steps": candidate.delay_steps,
            "validation_error": candidate.validation_error,
            "validation_samples": candidate.validation_samples,
            "consecutive_wins": candidate.consecutive_wins,
            "regions": [
                {
                    "theta": region.theta.tolist(),
                    "information_factor": region.information_factor.tolist(),
                    "normal_rhs": region.normal_rhs.tolist(),
                    "effective_samples": region.effective_samples,
                }
                for region in candidate.regions
            ],
        }

    def _region_status(self, region: _Region, knot: float) -> dict[str, object]:
        ar = region.theta[: self._config.na]
        inputs = region.theta[self._config.na : self._config.na + self._config.nb]
        inverse_information = np.linalg.solve(region.information_factor, np.eye(self._feature_count))
        covariance_diagonal = np.diag(inverse_information @ inverse_information.T)
        return {
            "knot_c": knot,
            "coefficients": {"ar": ar.tolist(), "input": inputs.tolist(), "ambient": float(region.theta[-2]), "intercept": float(region.theta[-1])},
            "poles": _poles(ar),
            "dc_gain": _dc_gain(ar, inputs),
            "covariance_diagonal": covariance_diagonal.tolist(),
            "effective_samples": region.effective_samples,
        }

    def _candidate_from_snapshot(self, payload: Mapping[str, object]) -> _Candidate:
        delay = _integer(payload.get("delay_steps"), "candidate.delay_steps")
        validation_error = _finite(payload.get("validation_error"), "candidate.validation_error")
        if validation_error < 0.0:
            raise ValueError("candidate.validation_error must be non-negative")
        validation_samples = _integer(payload.get("validation_samples"), "candidate.validation_samples")
        consecutive_wins = _integer(payload.get("consecutive_wins"), "candidate.consecutive_wins")
        region_data = _sequence(payload.get("regions"), "candidate.regions")
        if len(region_data) != len(_TEMPERATURE_KNOTS_C):
            raise ValueError("candidate.regions must match the four scheduling knots")
        regions = [self._region_from_snapshot(_mapping(item, "region")) for item in region_data]
        return _Candidate(delay, regions, validation_error, validation_samples, consecutive_wins)

    def _region_from_snapshot(self, payload: Mapping[str, object]) -> _Region:
        theta = np.asarray(_finite_list(payload.get("theta"), "region.theta"), dtype=np.float64)
        rhs = np.asarray(_finite_list(payload.get("normal_rhs"), "region.normal_rhs"), dtype=np.float64)
        factor_rows = _sequence(payload.get("information_factor"), "region.information_factor")
        factor = np.asarray([_finite_list(row, "region.information_factor row") for row in factor_rows], dtype=np.float64)
        if theta.shape != (self._feature_count,) or rhs.shape != (self._feature_count,) or factor.shape != (self._feature_count, self._feature_count):
            raise ValueError("region RLS dimensions do not match the configured ARX order")
        if not np.allclose(factor, np.triu(factor), atol=1e-12) or np.any(np.diag(factor) <= 0.0):
            raise ValueError("region.information_factor must be upper triangular with positive diagonal")
        self._validate_physical_theta(theta)
        effective_samples = _finite(payload.get("effective_samples"), "region.effective_samples")
        if effective_samples < 0.0:
            raise ValueError("region.effective_samples must be non-negative")
        return _Region(theta.copy(), factor.copy(), rhs.copy(), effective_samples)

    def _validate_physical_theta(self, theta: FloatArray) -> None:
        ar = theta[: self._config.na]
        roots = np.roots(np.concatenate(([1.0], -ar)))
        if roots.size and np.max(np.abs(roots)) > _MAX_AR_POLE + 1e-12:
            raise ValueError("AR pole exceeds the configured stability bound")
        gain = _dc_gain(ar, theta[self._config.na : self._config.na + self._config.nb])
        if not np.isfinite(gain) or gain <= 0.0:
            raise ValueError("ARX direct-current gain must be positive")
        if theta[-2] < 0.0:
            raise ValueError("ambient-error coefficient must be non-negative")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _finite_list(value: object, name: str) -> list[float]:
    return [_finite(item, name) for item in _sequence(value, name)]


def _optional_finite(value: object, name: str) -> float | None:
    return None if value is None else _finite(value, name)


def _optional_nonnegative(value: object, name: str) -> float | None:
    result = _optional_finite(value, name)
    if result is not None and result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _basis(size: int, index: int) -> FloatArray:
    vector = np.zeros(size, dtype=np.float64)
    vector[index] = 1.0
    return vector


def _poles(ar: FloatArray) -> list[float | dict[str, float]]:
    roots = np.roots(np.concatenate(([1.0], -ar)))
    return [float(np.round(root.real, 12)) if abs(root.imag) < 1e-12 else {"real": float(np.round(root.real, 12)), "imag": float(np.round(root.imag, 12))} for root in roots]


def _dc_gain(ar: FloatArray, inputs: FloatArray) -> float:
    denominator = 1.0 - float(np.sum(ar))
    return float(np.sum(inputs)) / denominator if denominator > 0.0 else 0.0
