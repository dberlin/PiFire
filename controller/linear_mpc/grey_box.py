"""Immutable affine forecast origins frozen from production grey-box estimators."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral, Real
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.linalg import expm

from .contracts import AffinePrediction, FloatArray

_KELVIN = 273.15


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
        raise ValueError(f"{name} must be an integer at least {minimum}")
    return int(value)


def _owned_vector(values: npt.ArrayLike, name: str) -> FloatArray:
    vector = np.array(values, dtype=np.float64, copy=True)
    if vector.ndim != 1 or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite one-dimensional array")
    vector.setflags(write=False)
    return vector


def _owned_square(values: npt.ArrayLike, size: int, name: str) -> FloatArray:
    matrix = np.array(values, dtype=np.float64, copy=True)
    if matrix.shape != (size, size) or not np.isfinite(matrix).all():
        raise ValueError(f"{name} must be a finite ({size}, {size}) array")
    matrix.setflags(write=False)
    return matrix


def _future_vector(values: npt.ArrayLike, name: str) -> FloatArray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.ndim != 1 or not np.isfinite(vector).all():
        raise ValueError(f"{name} must be a finite one-dimensional array")
    return vector


def _continuous_system(
    *,
    capacitance: float,
    conductance: float,
    theta: float,
    delay_steps: int,
    input_gain: float,
    radiation_slope: float,
) -> FloatArray:
    size = delay_steps + 2
    temperature_index = delay_steps
    system = np.zeros((size, size), dtype=np.float64)
    if delay_steps:
        delay_tau = theta / delay_steps
        for index in range(delay_steps):
            system[index, index] = -1.0 / delay_tau
            if index:
                system[index, index - 1] = 1.0 / delay_tau
        system[temperature_index, delay_steps - 1] = input_gain / capacitance
    system[temperature_index, temperature_index] = -(conductance + radiation_slope) / capacitance
    system[temperature_index, temperature_index + 1] = 1.0 / capacitance
    return system


def _discrete_input_gains(
    system: FloatArray,
    *,
    timestep_s: float,
    capacitance: float,
    conductance: float,
    theta: float,
    delay_steps: int,
    input_gain: float,
    include_radiation_constant: bool,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray]:
    """Discretize duty, ambient, and optional radiative-offset input channels."""
    size = system.shape[0]
    temperature_index = delay_steps
    input_count = 3 if include_radiation_constant else 2
    inputs = np.zeros((size, input_count), dtype=np.float64)
    if delay_steps:
        inputs[0, 0] = 1.0 / (theta / delay_steps)
    else:
        inputs[temperature_index, 0] = input_gain / capacitance
    inputs[temperature_index, 1] = conductance / capacitance
    if include_radiation_constant:
        inputs[temperature_index, 2] = 1.0 / capacitance
    augmented = np.zeros((size + input_count, size + input_count), dtype=np.float64)
    augmented[:size, :size] = system
    augmented[:size, size:] = inputs
    discrete = expm(augmented * timestep_s)
    transition = np.asarray(discrete[:size, :size], dtype=np.float64)
    q_gain = np.asarray(discrete[:size, size], dtype=np.float64)
    ambient_gain = np.asarray(discrete[:size, size + 1], dtype=np.float64)
    constant_gain = (
        np.asarray(discrete[:size, size + 2], dtype=np.float64)
        if include_radiation_constant
        else np.zeros(size, dtype=np.float64)
    )
    return transition, q_gain, ambient_gain, constant_gain


@dataclass(frozen=True, slots=True)
class GreyBoxPredictionAdapter:
    """A read-only local thermal origin retaining no live controller or estimator."""

    state: FloatArray
    transition: FloatArray
    q_gain: FloatArray
    ambient_gain: FloatArray
    affine_offset: FloatArray
    radiation_constant_gain: FloatArray
    temperature_index: int
    radiation_sigma: float
    radiation_slope: float
    chamber_origin_c: float

    def __post_init__(self) -> None:
        state = _owned_vector(self.state, "state")
        size = state.size
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "transition", _owned_square(self.transition, size, "transition"))
        for name in ("q_gain", "ambient_gain", "affine_offset", "radiation_constant_gain"):
            vector = _owned_vector(getattr(self, name), name)
            if vector.shape != state.shape:
                raise ValueError(f"{name} must have shape {state.shape}")
            object.__setattr__(self, name, vector)
        temperature_index = _integer(self.temperature_index, "temperature_index", minimum=0)
        if temperature_index >= size:
            raise ValueError("temperature_index must index state")
        object.__setattr__(self, "temperature_index", temperature_index)
        radiation_sigma = _finite(self.radiation_sigma, "radiation_sigma")
        radiation_slope = _finite(self.radiation_slope, "radiation_slope")
        if radiation_sigma < 0.0 or radiation_slope < 0.0:
            raise ValueError("radiation values must be non-negative")
        object.__setattr__(self, "radiation_sigma", radiation_sigma)
        object.__setattr__(self, "radiation_slope", radiation_slope)
        object.__setattr__(self, "chamber_origin_c", _finite(self.chamber_origin_c, "chamber_origin_c"))

    @classmethod
    def from_controller(cls, controller: Any) -> GreyBoxPredictionAdapter:
        """Freeze one production controller origin without retaining the controller."""
        try:
            config = controller.cfg
            estimator = controller.estimator
        except AttributeError as error:
            raise ValueError("controller must expose cfg and estimator") from error
        if not isinstance(config, Mapping):
            raise ValueError("controller cfg must be a mapping")
        return cls.from_estimator(estimator, config=config)

    @classmethod
    def from_estimator(
        cls, estimator: Any, *, config: Mapping[str, object]
    ) -> GreyBoxPredictionAdapter:
        """Capture owned estimator dynamics and locally linearized physical inputs."""
        required = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")
        try:
            parameters = {name: config[name] for name in required}
            state = estimator.x
            timestep_s = getattr(estimator, "t_step", None)
            if timestep_s is None:
                timestep_s = config["control_period"]
        except (AttributeError, KeyError) as error:
            raise ValueError("estimator and config lack required grey-box origin values") from error
        capacitance = _finite(parameters["C_c"], "C_c")
        conductance = _finite(parameters["h_amb"], "h_amb")
        ambient_reference = _finite(parameters["T_amb"], "T_amb")
        theta = _finite(parameters["theta"], "theta")
        input_gain = _finite(parameters["K_Q"], "K_Q")
        configured_sigma = _finite(parameters["sigma"], "sigma")
        timestep = _finite(timestep_s, "estimator.t_step")
        if (
            capacitance <= 0.0
            or conductance < 0.0
            or theta < 0.0
            or input_gain < 0.0
            or configured_sigma < 0.0
            or timestep <= 0.0
        ):
            raise ValueError("grey-box physical parameters must be non-negative with positive C_c and t_step")
        delay_steps = _integer(parameters["n_delay"], "n_delay")
        if delay_steps and theta <= 0.0:
            raise ValueError("theta must be positive when n_delay is positive")
        frozen_state = _owned_vector(state, "estimator.x")
        expected_size = delay_steps + 2
        if frozen_state.size != expected_size:
            raise ValueError("estimator state does not match n_delay")
        temperature_index = delay_steps
        chamber_origin = float(frozen_state[temperature_index])

        # GreyBoxKF has already discretized an exactly linear, non-radiative
        # model. Preserve those matrices even when a controller configuration
        # carries a sigma intended for a different estimator kind.
        if all(hasattr(estimator, name) for name in ("Ad", "Bd", "bd")):
            system = _continuous_system(
                capacitance=capacitance,
                conductance=conductance,
                theta=theta,
                delay_steps=delay_steps,
                input_gain=input_gain,
                radiation_slope=0.0,
            )
            _, _, ambient_gain, _ = _discrete_input_gains(
                system,
                timestep_s=timestep,
                capacitance=capacitance,
                conductance=conductance,
                theta=theta,
                delay_steps=delay_steps,
                input_gain=input_gain,
                include_radiation_constant=False,
            )
            affine_offset = np.asarray(estimator.bd, dtype=np.float64).reshape(-1)
            affine_offset = affine_offset - ambient_gain * ambient_reference
            return cls(
                frozen_state,
                np.asarray(estimator.Ad, dtype=np.float64),
                np.asarray(estimator.Bd, dtype=np.float64).reshape(-1),
                ambient_gain,
                affine_offset,
                np.zeros(expected_size, dtype=np.float64),
                temperature_index,
                0.0,
                0.0,
                chamber_origin,
            )

        radiation_sigma = _finite(getattr(estimator, "sigma", configured_sigma), "estimator.sigma")
        radiation_slope = 4.0 * radiation_sigma * (chamber_origin + _KELVIN) ** 3
        system = _continuous_system(
            capacitance=capacitance,
            conductance=conductance,
            theta=theta,
            delay_steps=delay_steps,
            input_gain=input_gain,
            radiation_slope=radiation_slope,
        )
        transition, q_gain, ambient_gain, radiation_constant_gain = _discrete_input_gains(
            system,
            timestep_s=timestep,
            capacitance=capacitance,
            conductance=conductance,
            theta=theta,
            delay_steps=delay_steps,
            input_gain=input_gain,
            include_radiation_constant=True,
        )
        return cls(
            frozen_state,
            transition,
            q_gain,
            ambient_gain,
            np.zeros(expected_size, dtype=np.float64),
            radiation_constant_gain,
            temperature_index,
            radiation_sigma,
            radiation_slope,
            chamber_origin,
        )

    def forecast(self, q_future: npt.ArrayLike, ambient_future: npt.ArrayLike) -> FloatArray:
        """Forecast the frozen origin under realized duty and ambient sequences."""
        q = _future_vector(q_future, "q_future")
        ambient = _future_vector(ambient_future, "ambient_future")
        if q.size != ambient.size:
            raise ValueError("q_future and ambient_future must have equal lengths")
        if np.any(q < 0.0) or np.any(q > 1.0):
            raise ValueError("q_future must be within [0, 1]")
        state = self.state.copy()
        output = np.empty(q.size, dtype=np.float64)
        for index, (duty, ambient_c) in enumerate(zip(q, ambient, strict=True)):
            radiation_constant = (
                -self.radiation_sigma
                * ((self.chamber_origin_c + _KELVIN) ** 4 - (ambient_c + _KELVIN) ** 4)
                + self.radiation_slope * self.chamber_origin_c
            )
            state = (
                self.transition @ state
                + self.q_gain * duty
                + self.ambient_gain * ambient_c
                + self.affine_offset
                + self.radiation_constant_gain * radiation_constant
            )
            output[index] = state[self.temperature_index]
        if not np.isfinite(output).all():
            raise FloatingPointError("grey-box forecast is non-finite")
        output.setflags(write=False)
        return output

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: npt.ArrayLike,
    ) -> AffinePrediction:
        """Build the exact frozen-origin affine duty map by basis forecasts."""
        steps = _integer(horizon_steps, "horizon_steps")
        previous = _finite(q_previous, "q_previous")
        if not 0.0 <= previous <= 1.0:
            raise ValueError("q_previous must be within [0, 1]")
        ambient = _future_vector(ambient_future, "ambient_future")
        if ambient.size != steps:
            raise ValueError("ambient_future length must equal horizon_steps")
        free_output = self.forecast(np.zeros(steps, dtype=np.float64), ambient)
        response = np.empty((steps, steps), dtype=np.float64)
        for column in range(steps):
            basis = np.zeros(steps, dtype=np.float64)
            basis[column] = 1.0
            response[:, column] = self.forecast(basis, ambient) - free_output
        return AffinePrediction(free_output, response)
