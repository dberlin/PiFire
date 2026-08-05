"""Scheduled, online ARX models with stable square-root RLS identification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

import numpy as np
import numpy.typing as npt

from .contracts import (
    AffinePrediction,
    FloatArray,
    Observation,
    SignalRecord,
    UpdateOutcome,
)

_MAX_AR_POLE: Final = 0.999
_DC_GAIN_SCALE: Final = 16.0
TEMPERATURE_KNOTS_C: Final = (82.2, 162.8, 232.2, 315.6)


@dataclass(frozen=True, slots=True)
class ARXConfig:
    """Identification and delay-selection settings for :class:`ScheduledARX`."""

    na: int
    nb: int
    delays: tuple[int, ...]
    forgetting_factor: float = 0.995
    initial_covariance: float = 1_000.0
    validation_window: int = 32
    challenger_margin: float = 1e-9

    def __post_init__(self) -> None:
        if self.na < 1 or self.nb < 1:
            raise ValueError("na and nb must both be positive")
        if not self.delays or any(delay < 0 for delay in self.delays):
            raise ValueError("delays must contain non-negative delay steps")
        if len(set(self.delays)) != len(self.delays):
            raise ValueError("delays must not contain duplicates")
        if not 0.0 < self.forgetting_factor <= 1.0:
            raise ValueError("forgetting_factor must be in (0, 1]")
        if self.initial_covariance <= 0.0:
            raise ValueError("initial_covariance must be positive")
        if self.validation_window < 1:
            raise ValueError("validation_window must be positive")
        if self.challenger_margin < 0.0:
            raise ValueError("challenger_margin must be non-negative")


@dataclass(slots=True)
class _Region:
    theta: FloatArray
    information_factor: FloatArray
    covariance_factor: FloatArray
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
    """A temperature-scheduled incremental ARX arm with delay selection.

    Each local model regresses the chamber-temperature increment on lagged
    temperature increments, delayed auger-duty increments, contemporaneous
    ambient error, and an intercept. Every candidate delay is updated from the
    same observation; only the active delay is switched after two independent
    validation-window wins.
    """

    def __init__(self, config: ARXConfig) -> None:
        self._config = config
        self._feature_count = config.na + config.nb + 2
        self._candidates = {
            delay: _Candidate(delay, self._new_regions()) for delay in config.delays
        }
        self._active_delay = config.delays[0]
        self._temperature_history: list[float] = []
        self._input_history: list[float] = []
        self._ambient_history: list[float] = []
        self._last_observation_time_s: float | None = None
        self._refreshes = 0
        self._last_refresh_sample: int | None = None
        self._max_dc_gain: float | None = None
        self._max_forecast_deviation: float | None = None

    def fit(self, record: SignalRecord) -> None:
        """Reset and identify all delay candidates from a complete record."""
        if record.temp_c.size != record.q.size or record.q.size != record.ambient_c.size:
            raise ValueError("record signal arrays must have equal lengths")
        if record.temp_c.size == 0:
            raise ValueError("record must contain at least one sample")

        self._candidates = {
            delay: _Candidate(delay, self._new_regions())
            for delay in self._config.delays
        }
        self._active_delay = self._config.delays[0]
        self._refreshes = 0
        self._last_refresh_sample = None
        temperatures = np.asarray(record.temp_c, dtype=np.float64)
        inputs = np.asarray(record.q, dtype=np.float64)
        ambients = np.asarray(record.ambient_c, dtype=np.float64)
        input_span = float(np.ptp(inputs))
        observed_span = float(np.ptp(temperatures))
        self._max_dc_gain = _DC_GAIN_SCALE * max(
            observed_span, np.finfo(np.float64).eps
        ) / max(input_span, np.finfo(np.float64).eps)
        self._max_forecast_deviation = _DC_GAIN_SCALE * max(
            observed_span, np.finfo(np.float64).eps
        )
        first_target = max(
            self._config.na + 1,
            max(self._config.delays) + self._config.nb + 1,
        )

        for target_index in range(first_target, temperatures.size):
            self._assimilate(
                temperatures[:target_index],
                inputs[:target_index],
                float(ambients[target_index]),
                float(temperatures[target_index]),
                target_index,
            )

        self._temperature_history = temperatures.tolist()
        self._input_history = inputs.tolist()
        self._ambient_history = ambients.tolist()
        self._last_observation_time_s = float(record.time_s[-1])

    def forecast(
        self,
        prefix: SignalRecord,
        q_future: FloatArray,
        ambient_future: FloatArray,
    ) -> FloatArray:
        """Predict a free run from only the supplied prefix and future signals."""
        future_q = _as_vector(q_future, "q_future")
        future_ambient = _as_vector(ambient_future, "ambient_future")
        if future_q.size != future_ambient.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        if prefix.temp_c.size == 0:
            raise ValueError("prefix must contain at least one sample")
        if prefix.temp_c.size != prefix.q.size or prefix.q.size != prefix.ambient_c.size:
            raise ValueError("prefix signal arrays must have equal lengths")

        candidate = self._candidates[self._active_delay]
        theta = self._scheduled_theta(candidate, float(prefix.temp_c[-1]))
        temperatures = [float(value) for value in prefix.temp_c]
        inputs = [float(value) for value in prefix.q]
        output = self._forecast_with_theta(
            theta, temperatures, inputs, future_q, future_ambient
        )
        output.setflags(write=False)
        return output
    def observe(self, observation: Observation) -> UpdateOutcome:
        """Score and learn one observation after preserving its pre-update error."""
        prediction = self._next_prediction(observation)
        self._assimilate(
            self._temperature_history,
            self._input_history,
            observation.ambient_c,
            observation.temp_c,
            len(self._temperature_history),
        )
        self._append_observation(observation)
        return UpdateOutcome(
            predicted_temp_c=prediction,
            observed_temp_c=observation.temp_c,
            innovation_c=observation.temp_c - prediction,
            updated=True,
        )

    def track(self, observation: Observation) -> UpdateOutcome:
        """Assimilate runtime history without changing fitted ARX parameters."""
        prediction = self._next_prediction(observation)
        self._append_observation(observation)
        return UpdateOutcome(
            predicted_temp_c=prediction,
            observed_temp_c=observation.temp_c,
            innovation_c=observation.temp_c - prediction,
            updated=False,
        )

    def _next_prediction(self, observation: Observation) -> float:
        if not self._temperature_history:
            raise RuntimeError("fit must be called before observe")
        candidate = self._candidates[self._active_delay]
        theta = self._scheduled_theta(candidate, self._temperature_history[-1])
        return self._predict_next(
            theta,
            self._temperature_history,
            self._input_history,
            observation.ambient_c,
            candidate.delay_steps,
        )

    def _append_observation(self, observation: Observation) -> None:
        self._temperature_history.append(observation.temp_c)
        self._input_history.append(observation.q)
        self._ambient_history.append(observation.ambient_c)
        self._last_observation_time_s = observation.time_s

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: FloatArray,
    ) -> AffinePrediction:
        """Derive the active incremental recursion as an affine horizon map."""
        if not self._temperature_history:
            raise RuntimeError("fit must be called before affine_prediction")
        if horizon_steps < 0:
            raise ValueError("horizon_steps must be non-negative")
        future_ambient = _as_vector(ambient_future, "ambient_future")
        if future_ambient.size != horizon_steps:
            raise ValueError("ambient_future length must equal horizon_steps")

        candidate = self._candidates[self._active_delay]
        theta = self._scheduled_theta(candidate, self._temperature_history[-1])
        inputs = list(self._input_history)
        inputs[-1] = float(q_previous)
        free_output = np.empty(horizon_steps, dtype=np.float64)
        input_response = np.zeros((horizon_steps, horizon_steps), dtype=np.float64)
        temperatures: list[tuple[float, FloatArray]] = [
            (value, np.zeros(horizon_steps, dtype=np.float64))
            for value in self._temperature_history
        ]
        input_terms: list[tuple[float, FloatArray]] = [
            (value, np.zeros(horizon_steps, dtype=np.float64)) for value in inputs
        ]
        input_terms.extend(
            (0.0, _basis(horizon_steps, index)) for index in range(horizon_steps)
        )

        for step in range(horizon_steps):
            target = len(self._temperature_history) + step
            prior_value, prior_response = temperatures[target - 1]
            delta_constant = theta[-1] + theta[-2] * (
                future_ambient[step] - prior_value
            )
            delta_response = -theta[-2] * prior_response
            for lag in range(self._config.na):
                value, coefficients = temperatures[target - 1 - lag]
                previous_value, previous_coefficients = temperatures[target - 2 - lag]
                delta_constant += theta[lag] * (value - previous_value)
                delta_response += theta[lag] * (coefficients - previous_coefficients)
            input_offset = self._config.na
            for lag in range(self._config.nb):
                value, coefficients = input_terms[
                    target - 1 - candidate.delay_steps - lag
                ]
                previous_value, previous_coefficients = input_terms[
                    target - 2 - candidate.delay_steps - lag
                ]
                coefficient = theta[input_offset + lag]
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
        if (
            self._max_forecast_deviation is not None
            and response_peak > self._max_forecast_deviation
        ):
            scale = self._max_forecast_deviation / response_peak
            baseline = self._temperature_history[-1]
            free_output = baseline + (free_output - baseline) * scale
            input_response *= scale
        if not np.isfinite(free_output).all() or not np.isfinite(input_response).all():
            raise RuntimeError("ARX horizon forecast is non-finite")
        return AffinePrediction(free_output, input_response)

    def snapshot(self) -> Mapping[str, object]:
        """Return stable, inspectable model state without exposing mutable arrays."""
        active = self._candidates[self._active_delay]
        regions: list[dict[str, object]] = []
        for knot, region in zip(TEMPERATURE_KNOTS_C, active.regions, strict=True):
            theta = region.theta
            ar = theta[: self._config.na]
            input_coefficients = theta[self._config.na : self._config.na + self._config.nb]
            regions.append(
                {
                    "knot_c": knot,
                    "coefficients": {
                        "ar": ar.tolist(),
                        "input": input_coefficients.tolist(),
                        "ambient": float(theta[-2]),
                        "intercept": float(theta[-1]),
                    },
                    "poles": _poles(ar),
                    "dc_gain": _dc_gain(ar, input_coefficients),
                    "covariance_diagonal": np.diag(
                        region.covariance_factor.T @ region.covariance_factor
                    ).tolist(),
                    "effective_samples": region.effective_samples,
                }
            )
        steady_gain = float(np.mean([region["dc_gain"] for region in regions]))
        return _freeze_snapshot(
            {
                "schema": "scheduled-arx/v1",
                "order": {"na": self._config.na, "nb": self._config.nb},
                "delay_steps": self._active_delay,
                "delay_seconds": self._active_delay * 20.0,
                "steady_gain": steady_gain,
                "knots_c": list(TEMPERATURE_KNOTS_C),
                "regions": regions,
                "plausibility_bounds": {
                    "max_dc_gain_c_per_q": self._max_dc_gain,
                    "max_ar_pole": _MAX_AR_POLE,
                },
                "update_timing": {
                    "last_observation_time_s": self._last_observation_time_s,
                    "refreshes": self._refreshes,
                    "max_forecast_deviation_c": self._max_forecast_deviation,
                    "last_refresh_sample": self._last_refresh_sample,
                },
            }
        )

    def _new_regions(self) -> list[_Region]:
        information_scale = 1.0 / np.sqrt(self._config.initial_covariance)
        information_factor = np.eye(self._feature_count) * information_scale
        covariance_factor = np.eye(self._feature_count) * np.sqrt(
            self._config.initial_covariance
        )
        return [
            _Region(
                theta=np.zeros(self._feature_count, dtype=np.float64),
                information_factor=information_factor.copy(),
                covariance_factor=covariance_factor.copy(),
                normal_rhs=np.zeros(self._feature_count, dtype=np.float64),
            )
            for _ in TEMPERATURE_KNOTS_C
        ]

    def _assimilate(
        self,
        temperatures: npt.ArrayLike,
        inputs: npt.ArrayLike,
        ambient_c: float,
        target_temp_c: float,
        refresh_sample: int,
    ) -> None:
        temperature_values = np.asarray(temperatures, dtype=np.float64)
        target_increment = target_temp_c - float(temperature_values[-1])
        for candidate in self._candidates.values():
            theta = self._scheduled_theta(candidate, float(temperature_values[-1]))
            feature = self._feature(
                temperatures,
                inputs,
                ambient_c,
                target_temp_c,
                candidate.delay_steps,
            )
            prediction = self._predict_next(
                theta, temperatures, inputs, ambient_c, candidate.delay_steps
            )
            error = target_temp_c - prediction
            candidate.validation_error += error * error
            candidate.validation_samples += 1
            for index, weight in self._region_weights(float(temperature_values[-1])):
                self._update_region(
                    candidate.regions[index], feature, target_increment, weight
                )
        active_samples = self._candidates[self._active_delay].validation_samples
        if active_samples >= self._config.validation_window:
            self._refresh_delay(refresh_sample)

    def _refresh_delay(self, refresh_sample: int) -> None:
        losses = {
            delay: candidate.validation_error / candidate.validation_samples
            for delay, candidate in self._candidates.items()
        }
        challenger_delay = min(losses, key=losses.__getitem__)
        if (
            challenger_delay != self._active_delay
            and losses[challenger_delay] + self._config.challenger_margin
            < losses[self._active_delay]
        ):
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

    def _update_region(
        self, region: _Region, feature: FloatArray, target: float, weight: float
    ) -> None:
        if weight == 0.0:
            return
        weighted_feature = np.sqrt(weight) * feature
        factor = np.vstack(
            (
                np.sqrt(self._config.forgetting_factor) * region.information_factor,
                weighted_feature,
            )
        )
        _, information_factor = np.linalg.qr(factor, mode="reduced")
        signs = np.where(np.diag(information_factor) < 0.0, -1.0, 1.0)
        information_factor *= signs[:, np.newaxis]
        region.information_factor = information_factor
        region.normal_rhs = (
            self._config.forgetting_factor * region.normal_rhs
            + weight * feature * target
        )
        lower_solution = np.linalg.solve(information_factor.T, region.normal_rhs)
        region.theta = np.linalg.solve(information_factor, lower_solution)
        self._project_physical_parameters(region.theta)
        inverse_information = np.linalg.solve(
            information_factor, np.eye(self._feature_count)
        )
        _, covariance_factor = np.linalg.qr(inverse_information.T, mode="reduced")
        region.covariance_factor = covariance_factor
        region.effective_samples = (
            self._config.forgetting_factor * region.effective_samples + weight
        )

    def _project_physical_parameters(self, theta: FloatArray) -> None:
        theta[-2] = max(0.0, float(theta[-2]))
        ar = theta[: self._config.na]
        roots = np.roots(np.concatenate(([1.0], -ar)))
        if roots.size and np.max(np.abs(roots)) > _MAX_AR_POLE:
            roots = np.where(
                np.abs(roots) > _MAX_AR_POLE,
                roots / np.abs(roots) * _MAX_AR_POLE,
                roots,
            )
            theta[: self._config.na] = -np.real_if_close(np.poly(roots)[1:])
        input_slice = slice(self._config.na, self._config.na + self._config.nb)
        denominator = 1.0 - float(np.sum(theta[: self._config.na]))
        if denominator <= 1e-9:
            theta[: self._config.na] *= 0.998 / max(
                1e-12, abs(float(np.sum(theta[: self._config.na])))
            )
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
        if temp_c <= TEMPERATURE_KNOTS_C[0]:
            return ((0, 1.0),)
        if temp_c >= TEMPERATURE_KNOTS_C[-1]:
            return ((len(TEMPERATURE_KNOTS_C) - 1, 1.0),)
        for index, upper in enumerate(TEMPERATURE_KNOTS_C[1:], start=1):
            if temp_c <= upper:
                lower = TEMPERATURE_KNOTS_C[index - 1]
                upper_weight = (temp_c - lower) / (upper - lower)
                return ((index - 1, 1.0 - upper_weight), (index, upper_weight))
        raise AssertionError("temperature knot bounds are exhaustive")

    def _feature(
        self,
        temperatures: npt.ArrayLike,
        inputs: npt.ArrayLike,
        ambient_c: float,
        target_temp_c: float,
        delay_steps: int,
    ) -> FloatArray:
        temperature_values = np.asarray(temperatures, dtype=np.float64)
        input_values = np.asarray(inputs, dtype=np.float64)
        feature = np.empty(self._feature_count, dtype=np.float64)
        for lag in range(self._config.na):
            feature[lag] = (
                temperature_values[-1 - lag] - temperature_values[-2 - lag]
            )
        input_offset = self._config.na
        for lag in range(self._config.nb):
            delayed_index = -1 - delay_steps - lag
            feature[input_offset + lag] = (
                input_values[delayed_index] - input_values[delayed_index - 1]
            )
        feature[-2] = ambient_c - target_temp_c
        feature[-1] = 1.0
        return feature

    def _predict_next(
        self,
        theta: FloatArray,
        temperatures: npt.ArrayLike,
        inputs: npt.ArrayLike,
        ambient_c: float,
        delay_steps: int,
    ) -> float:
        temperature_values = np.asarray(temperatures, dtype=np.float64)
        input_values = np.asarray(inputs, dtype=np.float64)
        increment = float(theta[-1])
        for lag in range(self._config.na):
            increment += theta[lag] * (
                temperature_values[-1 - lag] - temperature_values[-2 - lag]
            )
        input_offset = self._config.na
        for lag in range(self._config.nb):
            delayed_index = -1 - delay_steps - lag
            increment += theta[input_offset + lag] * (
                input_values[delayed_index] - input_values[delayed_index - 1]
            )
        ambient_coefficient = theta[-2]
        denominator = 1.0 + ambient_coefficient
        if abs(denominator) < 1e-9:
            raise RuntimeError("ambient-error coefficient makes the ARX recursion singular")
        increment += ambient_coefficient * (ambient_c - temperature_values[-1])
        return float(temperature_values[-1] + increment / denominator)

    def _forecast_with_theta(
        self,
        theta: FloatArray,
        temperatures: list[float],
        inputs: list[float],
        future_q: FloatArray,
        future_ambient: FloatArray,
    ) -> FloatArray:
        candidate = self._candidates[self._active_delay]
        output = np.empty(future_ambient.size, dtype=np.float64)
        for step, (q, ambient_c) in enumerate(
            zip(future_q, future_ambient, strict=True)
        ):
            prediction = self._predict_next(
                theta, temperatures, inputs, float(ambient_c), candidate.delay_steps
            )
            output[step] = prediction
            temperatures.append(prediction)
            inputs.append(float(q))
        return output


def _as_vector(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return array


def _basis(size: int, index: int) -> FloatArray:
    vector = np.zeros(size, dtype=np.float64)
    vector[index] = 1.0
    return vector

def _poles(ar: FloatArray) -> list[float | dict[str, float]]:
    roots = np.roots(np.concatenate(([1.0], -ar)))
    return [
        float(np.round(root.real, 12))
        if abs(root.imag) < 1e-12
        else {
            "real": float(np.round(root.real, 12)),
            "imag": float(np.round(root.imag, 12)),
        }
        for root in roots
    ]


def _freeze_snapshot(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_snapshot(nested) for key, nested in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_snapshot(nested) for nested in value)
    return value


def _dc_gain(ar: FloatArray, inputs: FloatArray) -> float:
    denominator = 1.0 - float(np.sum(ar))
    return float(np.sum(inputs)) / denominator if denominator > 0.0 else 0.0
