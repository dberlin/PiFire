"""Normalized live-learning disclosure for the PID-SP controller."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Annotated, Literal, TypeAlias, cast

from pydantic import ConfigDict, Field
from pydantic.dataclasses import dataclass

from controller.fopdt_identifier import (
    CONFIRM_WINDOW,
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)

PidSpLearningStatus: TypeAlias = Literal[
    "collecting",
    "insufficient-excitation",
    "evaluating",
    "active",
    "fallback",
]
FiniteFloat: TypeAlias = Annotated[float, Field(allow_inf_nan=False, strict=True)]
GateValue: TypeAlias = int | FiniteFloat | bool
NonNegativeInt: TypeAlias = Annotated[int, Field(ge=0, strict=True)]

_DATACLASS_CONFIG = ConfigDict(extra="forbid", strict=True, validate_default=True)


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class PidSpLearningGate:
    """One backend-owned threshold and its current live observation."""

    name: str
    passed: bool
    observed: GateValue
    required: GateValue
    unit: str | None


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class PidSpConfirmationProgress:
    """Candidate confirmations observed against the backend-owned requirement."""

    observed: NonNegativeInt | None
    required: NonNegativeInt


@dataclass(frozen=True, slots=True, config=_DATACLASS_CONFIG)
class PidSpLiveLearning:
    """Validated immutable source for the JSON-safe live status projection."""

    schema_version: Literal[1]
    controller: Literal["pid_sp"]
    status: PidSpLearningStatus
    identifier: dict[str, object]
    predictor: dict[str, object]
    confirmation: PidSpConfirmationProgress
    gates: tuple[PidSpLearningGate, ...]

    def to_dict(self) -> dict[str, object]:
        """Return a caller-owned JSON projection of this immutable report."""

        return {
            "schema_version": self.schema_version,
            "controller": self.controller,
            "status": self.status,
            "identifier": _owned_json_mapping(self.identifier, "identifier"),
            "predictor": _owned_json_mapping(self.predictor, "predictor"),
            "confirmation": asdict(self.confirmation),
            "gates": [asdict(gate) for gate in self.gates],
        }


def _owned_json_value(value: object, path: str) -> object:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError(f"{path} must have string keys")
        return {key: _owned_json_value(item, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_owned_json_value(item, f"{path}[]") for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"{path} contains unsupported {type(value).__name__}")


def _owned_json_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    owned = _owned_json_value(value, name)
    return cast(dict[str, object], owned)


def _number(mapping: Mapping[str, object], field: str) -> int | float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be finite")
    return value


def _optional_nonnegative_int(mapping: Mapping[str, object], field: str) -> int | None:
    value = mapping.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _boolean(mapping: Mapping[str, object], field: str) -> bool:
    value = mapping.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _validate_known_numeric_fields(mapping: Mapping[str, object], fields: tuple[str, ...]) -> None:
    for field in fields:
        if field in mapping and mapping[field] is not None:
            _number(mapping, field)


def _validate_status_fields(identifier: Mapping[str, object], predictor: Mapping[str, object]) -> None:
    _validate_known_numeric_fields(
        identifier,
        (
            "accepted",
            "accepted_seconds",
            "duty_std",
            "temp_span",
            "duty_segments",
            "best_residual",
            "runner_up_residual",
            "candidates_passing",
            "distrust_count",
            "distrust_ratio",
        ),
    )
    _validate_known_numeric_fields(predictor, ("x0", "xd", "residual_streak", "truncated"))
    for model_name, model in (
        ("identifier.trusted", identifier.get("trusted")),
        ("predictor.model", predictor.get("model")),
    ):
        if model is not None and not isinstance(model, Mapping):
            raise ValueError(f"{model_name} must be a mapping or null")
        if isinstance(model, Mapping):
            _validate_known_numeric_fields(
                model,
                ("K", "tau", "theta", "K_i", "c0", "revision", "identified_at_f", "setpoint_f"),
            )


def build_pid_sp_live_learning(
    identifier: Mapping[str, object],
    predictor: Mapping[str, object],
) -> dict[str, object]:
    """Build one normalized projection from one identifier/predictor snapshot."""

    identifier_owned = _owned_json_mapping(identifier, "identifier")
    predictor_owned = _owned_json_mapping(predictor, "predictor")
    _validate_status_fields(identifier_owned, predictor_owned)

    accepted = _number(identifier_owned, "accepted")
    accepted_seconds = _number(identifier_owned, "accepted_seconds")
    duty_std = _number(identifier_owned, "duty_std")
    transition_seen = _boolean(identifier_owned, "transition_seen")
    temp_span = _number(identifier_owned, "temp_span")
    predictor_active = _boolean(predictor_owned, "active")
    predictor_disabled = _boolean(predictor_owned, "disabled")
    confirmation = PidSpConfirmationProgress(
        observed=_optional_nonnegative_int(identifier_owned, "confirming"),
        required=CONFIRM_WINDOW,
    )

    gates = (
        PidSpLearningGate(
            name="accepted_samples",
            passed=accepted >= MIN_ACCEPTED,
            observed=accepted,
            required=MIN_ACCEPTED,
            unit="samples",
        ),
        PidSpLearningGate(
            name="accepted_duration",
            passed=accepted_seconds >= MIN_ACCEPTED_SECONDS,
            observed=accepted_seconds,
            required=MIN_ACCEPTED_SECONDS,
            unit="seconds",
        ),
        PidSpLearningGate(
            name="duty_standard_deviation",
            passed=duty_std >= MIN_DUTY_STD,
            observed=duty_std,
            required=MIN_DUTY_STD,
            unit="ratio",
        ),
        PidSpLearningGate(
            name="duty_transition",
            passed=transition_seen,
            observed=transition_seen,
            required=True,
            unit=None,
        ),
        PidSpLearningGate(
            name="temperature_span",
            passed=temp_span >= MIN_TEMP_SPAN_F,
            observed=temp_span,
            required=MIN_TEMP_SPAN_F,
            unit="°F",
        ),
    )

    if predictor_disabled:
        status: PidSpLearningStatus = "fallback"
    elif predictor_active and identifier_owned.get("trusted") is not None:
        status = "active"
    elif all(gate.passed for gate in gates):
        status = "evaluating"
    elif gates[0].passed and gates[1].passed:
        status = "insufficient-excitation"
    else:
        status = "collecting"

    return PidSpLiveLearning(
        schema_version=1,
        controller="pid_sp",
        status=status,
        identifier=identifier_owned,
        predictor=predictor_owned,
        confirmation=confirmation,
        gates=gates,
    ).to_dict()
