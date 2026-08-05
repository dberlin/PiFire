"""Immutable signal contracts shared by the linear-MPC bake-off experiments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, TypeAlias

import numpy as np
import numpy.typing as npt
from controller.linear_mpc.contracts import AffinePrediction

JSONPrimitive: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = (
    JSONPrimitive | tuple["JSONValue", ...] | Mapping[str, "JSONValue"]
)
JSONInput: TypeAlias = (
    JSONPrimitive | Sequence["JSONInput"] | Mapping[str, "JSONInput"]
)
Metadata: TypeAlias = Mapping[str, JSONValue]
FloatArray: TypeAlias = npt.NDArray[np.float64]


def _normalized_float_array(values: npt.ArrayLike) -> FloatArray:
    """Copy once into a read-only float64 array for a record boundary."""
    array = np.array(values, dtype=np.float64, copy=True)
    array.setflags(write=False)
    return array

def _frozen_json_value(value: JSONInput) -> JSONValue:
    """Make a defensive, recursively immutable JSON-compatible value."""
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("metadata keys must be strings")
        return MappingProxyType(
            {key: _frozen_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, Sequence) and not isinstance(value, str):
        return tuple(_frozen_json_value(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("metadata values must be JSON-compatible")


@dataclass(frozen=True, slots=True)
class Sample:
    """One synchronized measurement and normalized auger duty.

    Simulator records use realized mean duty. Requested-input-reconstructed
    fixtures retain their requested-duty reconstruction, identified by record
    provenance rather than by a different field or scale.
    """

    time_s: float
    temp_c: float
    q: float
    ambient_c: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", float(self.time_s))
        object.__setattr__(self, "temp_c", float(self.temp_c))
        object.__setattr__(self, "q", float(self.q))
        object.__setattr__(self, "ambient_c", float(self.ambient_c))


@dataclass(frozen=True, slots=True)
class SignalRecord:
    """A time-ordered signal whose q meaning is declared by provenance.

    Simulator evidence records realized mean auger duty. Real-fixture records
    may carry requested-input-reconstructed duty; values remain normalized in
    both cases and consumers must not reinterpret them.
    """

    time_s: FloatArray
    temp_c: FloatArray
    q: FloatArray
    ambient_c: FloatArray
    provenance: str
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", _normalized_float_array(self.time_s))
        object.__setattr__(self, "temp_c", _normalized_float_array(self.temp_c))
        object.__setattr__(self, "q", _normalized_float_array(self.q))
        object.__setattr__(self, "ambient_c", _normalized_float_array(self.ambient_c))
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(
                {key: _frozen_json_value(value) for key, value in self.metadata.items()}
            ),
        )


@dataclass(frozen=True, slots=True)
class DatasetSplit:
    """Disjoint, time-ordered partitions for fitting and honest evaluation."""

    fit: SignalRecord
    validation: SignalRecord
    test: SignalRecord

@dataclass(frozen=True, slots=True)
class Observation:
    """One online measurement with normalized duty feedback.

    Simulator observations provide realized mean duty; reconstructed fixtures
    retain requested-duty provenance on their enclosing record.
    """

    time_s: float
    temp_c: float
    q: float
    ambient_c: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", float(self.time_s))
        object.__setattr__(self, "temp_c", float(self.temp_c))
        object.__setattr__(self, "q", float(self.q))
        object.__setattr__(self, "ambient_c", float(self.ambient_c))


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    """The pre-update prediction and the innovation it was scored against."""

    predicted_temp_c: float
    observed_temp_c: float
    innovation_c: float
    updated: bool




class AdaptiveLinearModel(Protocol):
    """The common online-model contract consumed by scoring and control."""

    def fit(self, record: SignalRecord) -> None: ...

    def forecast(
        self,
        prefix: SignalRecord,
        q_future: FloatArray,
        ambient_future: FloatArray,
    ) -> FloatArray: ...

    def observe(self, observation: Observation) -> UpdateOutcome: ...

    def track(self, observation: Observation) -> UpdateOutcome: ...

    def affine_prediction(
        self,
        horizon_steps: int,
        q_previous: float,
        ambient_future: FloatArray,
    ) -> AffinePrediction: ...

    def snapshot(self) -> Mapping[str, object]: ...
