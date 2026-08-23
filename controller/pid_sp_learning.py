"""Normalized live-learning disclosure for the PID-SP controller."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass as std_dataclass
from typing import cast

from common.cook_diagnostics import ControllerLearningReport
from common.persistence.protocols import JsonValue

from common.web_contracts.learning import (
    FopdtPidSpCheckpoint,
    IpdtPidSpCheckpoint,
    PidSpCheckpointModel,
    PidSpConfirmationProgress,
    PidSpGateValue,
    PidSpLearningGate,
    PidSpLearningReport,
    PidSpLearningStatus,
    PidSpLiveLearning,
    PidSpLiveLearningStatus,
)

from controller.fopdt_identifier import (
    AMBIENT_F,
    CONFIRM_WINDOW,
    DELAYS,
    FORM_FOPDT,
    FORM_IPDT,
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_RISE_F,
    MIN_TEMP_SPAN_F,
    RESTORE_BOUNDS,
)


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
        identifier=identifier,
        predictor=predictor,
        confirmation=confirmation,
        gates=gates,
    ).model_dump(mode="json")


@std_dataclass(frozen=True, slots=True)
class _CanonicalPidSpLearningReport:
    """Immutable canonical report bytes safe to cache or serve."""

    payload_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        """Return a caller-owned decoded report."""

        decoded = json.loads(self.payload_bytes)
        if not isinstance(decoded, dict):
            raise ValueError("PID-SP learning report root is not an object")
        return cast(dict[str, object], decoded)

    def to_dict(self) -> dict[str, object]:
        """Return a caller-owned decoded report."""

        return self.as_dict()

    @property
    def revision(self) -> str:
        """Return the report invalidation token."""

        revision = self.as_dict().get("revision")
        if not isinstance(revision, str):
            raise ValueError("PID-SP learning report revision is missing")
        return revision


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _owned_json_value(value, "report"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _checkpoint_error(detail: str, error: Exception | None = None) -> ValueError:
    failure = ValueError(f"checkpoint {detail}")
    if error is not None:
        failure.__cause__ = error
    return failure


def _checkpoint_number(checkpoint: Mapping[str, object], field: str) -> float:
    try:
        raw = checkpoint[field]
    except KeyError as error:
        raise _checkpoint_error(f"{field} must be a number", error)
    if isinstance(raw, bool):
        raise _checkpoint_error(f"{field} must be a number")
    try:
        value = float(cast(str | int | float, raw))
    except (OverflowError, TypeError, ValueError) as error:
        raise _checkpoint_error(f"{field} must be a number", error)
    if not math.isfinite(value):
        raise _checkpoint_error(f"{field} must be finite")
    return value


def _normalize_checkpoint(checkpoint: object) -> dict[str, object] | None:
    if checkpoint is None:
        return None
    if not isinstance(checkpoint, Mapping):
        raise _checkpoint_error("must be an object")
    owned = _owned_json_mapping(checkpoint, "checkpoint")
    form = owned.get("form", FORM_FOPDT)
    if form not in (FORM_FOPDT, FORM_IPDT):
        raise _checkpoint_error("form is invalid")
    bounds = RESTORE_BOUNDS[form]
    values = {bound[0]: _checkpoint_number(owned, bound[0]) for bound in bounds}
    for name, lower, upper in bounds:
        if not lower <= values[name] <= upper:
            raise _checkpoint_error(f"{name} is outside the restore bounds")
    theta = _checkpoint_number(owned, "theta")
    if not float(DELAYS.min()) <= theta <= float(DELAYS.max()):
        raise _checkpoint_error("theta is outside the restore bounds")
    revision_value = owned.get("revision")
    if isinstance(revision_value, bool):
        raise _checkpoint_error("revision must be a non-negative integer")
    try:
        revision = int(cast(str | int | float, revision_value))
    except (TypeError, ValueError) as error:
        raise _checkpoint_error("revision must be a non-negative integer", error)
    if revision < 0:
        raise _checkpoint_error("revision must be a non-negative integer")

    identified_value = owned.get("identified_at_f", owned.get("setpoint_f"))
    identified_at_f = None
    if identified_value is not None:
        if isinstance(identified_value, bool):
            raise _checkpoint_error("identified_at_f must be a number")
        try:
            identified = float(cast(str | int | float, identified_value))
        except (OverflowError, TypeError, ValueError) as error:
            raise _checkpoint_error("identified_at_f must be a number", error)
        if not math.isfinite(identified):
            raise _checkpoint_error("identified_at_f must be finite")
        if identified > AMBIENT_F + MIN_RISE_F:
            identified_at_f = identified

    contract: PidSpCheckpointModel
    if form == FORM_FOPDT:
        contract = FopdtPidSpCheckpoint(
            form="fopdt",
            K=values["K"],
            tau=values["tau"],
            theta=theta,
            revision=revision,
            identified_at_f=identified_at_f,
        )
    else:
        contract = IpdtPidSpCheckpoint(
            form="ipdt",
            K_i=values["K_i"],
            c0=values["c0"],
            theta=theta,
            revision=revision,
            identified_at_f=identified_at_f,
        )
    return contract.model_dump(mode="json", exclude_none=True)


def _marked_pid_sp_live(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema_version = value.get("schema_version")
    return (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version == 1
        and value.get("controller") == "pid_sp"
    )


def _live_from_status(status: object) -> object:
    if _marked_pid_sp_live(status):
        return status
    if not isinstance(status, Mapping):
        return None
    direct = status.get("learning")
    if _marked_pid_sp_live(direct):
        return direct
    controller = status.get("controller")
    nested = controller.get("learning") if isinstance(controller, Mapping) else None
    return nested if _marked_pid_sp_live(nested) else None


def _gate_value(mapping: Mapping[str, object], field: str) -> PidSpGateValue:
    value = mapping.get(field)
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"gate {field} must be a finite number or boolean")


def _learning_gate(value: object) -> PidSpLearningGate:
    mapping = _owned_json_mapping(value, "gate")
    if set(mapping) != {"name", "passed", "observed", "required", "unit"}:
        raise ValueError("gate fields are invalid")
    name = mapping["name"]
    passed = mapping["passed"]
    unit = mapping["unit"]
    if not isinstance(name, str):
        raise ValueError("gate name must be a string")
    if not isinstance(passed, bool):
        raise ValueError("gate passed must be a boolean")
    if unit is not None and not isinstance(unit, str):
        raise ValueError("gate unit must be a string or null")
    return PidSpLearningGate(
        name=name,
        passed=passed,
        observed=_gate_value(mapping, "observed"),
        required=_gate_value(mapping, "required"),
        unit=unit,
    )


def _normalize_live(live: object) -> dict[str, object]:
    mapping = _owned_json_mapping(live, "live status")
    required = {
        "schema_version",
        "controller",
        "status",
        "identifier",
        "predictor",
        "confirmation",
        "gates",
    }
    if set(mapping) != required:
        raise ValueError("live status fields are invalid")
    status = mapping["status"]
    if status not in {
        "collecting",
        "insufficient-excitation",
        "evaluating",
        "active",
        "fallback",
    }:
        raise ValueError("live status value is invalid")
    identifier = _owned_json_mapping(mapping["identifier"], "identifier")
    predictor = _owned_json_mapping(mapping["predictor"], "predictor")
    _validate_status_fields(identifier, predictor)
    for field in ("accepted", "accepted_seconds", "duty_std", "temp_span"):
        _number(identifier, field)
    _boolean(identifier, "transition_seen")
    _boolean(predictor, "active")
    _boolean(predictor, "disabled")

    confirmation_mapping = _owned_json_mapping(mapping["confirmation"], "confirmation")
    if set(confirmation_mapping) != {"observed", "required"}:
        raise ValueError("confirmation fields are invalid")
    required_confirmations = _optional_nonnegative_int(confirmation_mapping, "required")
    if required_confirmations is None:
        raise ValueError("confirmation required must be a non-negative integer")
    confirmation = PidSpConfirmationProgress(
        observed=_optional_nonnegative_int(confirmation_mapping, "observed"),
        required=required_confirmations,
    )
    gates_value = mapping["gates"]
    if not isinstance(gates_value, Sequence) or isinstance(gates_value, (str, bytes, bytearray)):
        raise ValueError("gates must be an array")
    gates = [_learning_gate(value) for value in gates_value]
    normalized = PidSpLiveLearning(
        schema_version=1,
        controller="pid_sp",
        status=cast(PidSpLiveLearningStatus, status),
        identifier=identifier,
        predictor=predictor,
        confirmation=confirmation,
        gates=tuple(gates),
    )
    return normalized.model_dump(mode="json")


def current_pid_sp_learning_report(
    *,
    status: object,
    checkpoint: object,
) -> _CanonicalPidSpLearningReport:
    """Project one live status and one durable checkpoint without side effects."""

    normalized_checkpoint = _normalize_checkpoint(checkpoint)
    live = _live_from_status(status)
    payload: dict[str, object] = {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "idle",
        "live": False,
        "gates": [],
        "identifier": None,
        "predictor": None,
        "confirmation": None,
        "checkpoint": normalized_checkpoint,
        "failure": None,
    }
    if live is not None:
        try:
            normalized_live = _normalize_live(live)
        except (TypeError, ValueError) as error:
            payload["status"] = "error"
            payload["failure"] = {
                "code": "live-status-invalid",
                "detail": str(error),
                "terminal": False,
            }
        else:
            payload.update(
                {
                    "status": normalized_live["status"],
                    "live": True,
                    "gates": normalized_live["gates"],
                    "identifier": normalized_live["identifier"],
                    "predictor": normalized_live["predictor"],
                    "confirmation": normalized_live["confirmation"],
                }
            )
    payload["revision"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    contract = PidSpLearningReport.model_validate(payload, strict=True)
    return _CanonicalPidSpLearningReport(_canonical_bytes(contract.model_dump(mode="json", exclude_unset=True)))


def backend_pid_sp_learning_report() -> _CanonicalPidSpLearningReport:
    """Read each PID-SP report authority once and compose its projection."""

    from common.controller_model_state import ControllerModelStore
    from common.persistence.runtime import read_status

    status = read_status()
    checkpoint = ControllerModelStore().load_strict("pid_sp")
    return current_pid_sp_learning_report(status=status, checkpoint=checkpoint)


def diagnostic_learning_report() -> ControllerLearningReport:
    """Return the generic owned envelope for the final PID-SP report."""

    report = backend_pid_sp_learning_report()
    return ControllerLearningReport(
        controller="pid_sp",
        schema_version=1,
        revision=report.revision,
        report=cast(Mapping[str, JsonValue], report.as_dict()),
    )
