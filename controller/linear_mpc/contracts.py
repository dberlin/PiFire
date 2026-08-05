"""Immutable production boundaries for online linear-model learning."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def _finite_float(value: Real, name: str) -> float:
    """Normalize one finite real scalar without admitting booleans."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    normalized = float(value)
    if not np.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _bounded_duty(value: Real, name: str) -> float:
    normalized = _finite_float(value, name)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return normalized


def _nonnegative_int(value: Integral, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def _owned_array(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.array(values, dtype=np.float64, copy=True)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class FrameObservation:
    """One complete actuator frame and its synchronized thermal measurement."""

    frame_start_s: float
    frame_end_s: float
    temp_c: float
    setpoint_c: float
    ambient_c: float
    requested_q: float
    realized_q: float
    requested_auger_duty: float
    delivered_on_s: float
    requested_fan_duty: float | None
    actual_fan_duty: float | None
    result_revision: int
    output_source: str
    lid_open: bool
    safety_inhibited: bool
    manual_override: bool
    stale: bool
    skipped: bool
    reset: bool
    continuous: bool
    role_generation: int

    def __post_init__(self) -> None:
        start = _finite_float(self.frame_start_s, "frame_start_s")
        end = _finite_float(self.frame_end_s, "frame_end_s")
        if end <= start:
            raise ValueError("frame_end_s must be greater than frame_start_s")
        duration = end - start
        object.__setattr__(self, "frame_start_s", start)
        object.__setattr__(self, "frame_end_s", end)
        for name in ("temp_c", "setpoint_c", "ambient_c"):
            object.__setattr__(self, name, _finite_float(getattr(self, name), name))
        for name in ("requested_q", "realized_q", "requested_auger_duty"):
            object.__setattr__(self, name, _bounded_duty(getattr(self, name), name))
        delivered_on_s = _finite_float(self.delivered_on_s, "delivered_on_s")
        if not 0.0 <= delivered_on_s <= duration:
            raise ValueError("delivered_on_s must be within the frame duration")
        object.__setattr__(self, "delivered_on_s", delivered_on_s)
        for name in ("requested_fan_duty", "actual_fan_duty"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _bounded_duty(value, name))
        object.__setattr__(self, "result_revision", _nonnegative_int(self.result_revision, "result_revision"))
        object.__setattr__(self, "role_generation", _nonnegative_int(self.role_generation, "role_generation"))
        if not isinstance(self.output_source, str) or not self.output_source:
            raise ValueError("output_source must be a non-empty string")
        for name in (
            "lid_open",
            "safety_inhibited",
            "manual_override",
            "stale",
            "skipped",
            "reset",
            "continuous",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a bool")

    @property
    def time_s(self) -> float:
        """Compatibility timestamp: the completed frame's end time."""
        return self.frame_end_s

    @property
    def q(self) -> float:
        """Compatibility control input: the realized normalized heat input."""
        return self.realized_q


@dataclass(frozen=True, slots=True)
class AffinePrediction:
    """An exact horizon prediction expressed as an affine input response."""

    free_output_c: FloatArray
    input_response_c: FloatArray

    def __post_init__(self) -> None:
        free_output_c = _owned_array(self.free_output_c, "free_output_c")
        input_response_c = _owned_array(self.input_response_c, "input_response_c")
        if free_output_c.ndim != 1:
            raise ValueError("free_output_c must have shape (N,)")
        if input_response_c.shape != (free_output_c.size, free_output_c.size):
            raise ValueError("input_response_c must have shape (N, N)")
        object.__setattr__(self, "free_output_c", free_output_c)
        object.__setattr__(self, "input_response_c", input_response_c)


@dataclass(frozen=True, slots=True)
class ModelUpdate:
    """The pre-update prediction and innovation produced for one frame."""

    predicted_temp_c: float
    observed_temp_c: float
    innovation_c: float
    updated: bool

    def __post_init__(self) -> None:
        for name in ("predicted_temp_c", "observed_temp_c", "innovation_c"):
            object.__setattr__(self, name, _finite_float(getattr(self, name), name))
        if not isinstance(self.updated, bool):
            raise ValueError("updated must be a bool")
