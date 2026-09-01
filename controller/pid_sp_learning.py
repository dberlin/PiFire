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
    PidSpActiveModelReport,
    PidSpCheckpointModel,
    PidSpConfirmationProgress,
    PidSpDelayEvidence,
    PidSpDelayEvidenceStatus,
    PidSpDelayProfileForm,
    PidSpFormComparisonReport,
    PidSpGateValue,
    PidSpHorizonLossReport,
    PidSpLearningGate,
    PidSpLearningReport,
    PidSpLearningStatus,
    PidSpLiveLearning,
    PidSpLiveLearningStatus,
    PidSpModelComparisonReport,
)
from controller.fopdt_identifier import (
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)
from controller.pid_sp_delay_evidence import (
    INITIAL_DELAY_BOUND_S,
    DelayBlocker,
    DelayProfile,
)
from controller.pid_sp_model_selection import (
    CONFIRMATION_WINDOW,
    ModelComparison,
    SelectedPidSpModel,
    encode_pid_sp_checkpoint,
    project_pid_sp_persisted_checkpoint,
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
        raise TypeError(f"{name} must be a mapping")
    owned = _owned_json_value(value, name)
    return cast(dict[str, object], owned)


def _number(mapping: Mapping[str, object], field: str) -> int | float:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
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
        raise TypeError(f"{field} must be a boolean")
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
            "raw_best_residual",
            "raw_runner_up_residual",
            "raw_candidates_passing",
            "distrust_count",
            "distrust_ratio",
        ),
    )
    _validate_known_numeric_fields(
        predictor,
        ("x0", "xd", "z0", "zd", "residual_streak", "truncated"),
    )
    for model_name, model in (
        ("identifier.trusted", identifier.get("trusted")),
        ("predictor.model", predictor.get("model")),
    ):
        if model is not None and not isinstance(model, Mapping):
            raise ValueError(f"{model_name} must be a mapping or null")
        if isinstance(model, Mapping):
            _validate_known_numeric_fields(
                model,
                (
                    "K",
                    "tau",
                    "tau_1",
                    "tau_2",
                    "theta",
                    "K_i",
                    "c0",
                    "revision",
                    "identified_at_f",
                    "setpoint_f",
                ),
            )


_DELAY_STATUS_PRIORITY = (
    DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE,
    DelayBlocker.DELAY_RANGE_EXHAUSTED,
    DelayBlocker.DELAY_BASIN_EDGE,
    DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE,
    DelayBlocker.DELAY_BASIN_TOO_WIDE,
    DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES,
)


def _delay_evidence(
    completed_episode_count: int,
    profile: DelayProfile | None,
) -> PidSpDelayEvidence:
    if (
        isinstance(completed_episode_count, bool)
        or not isinstance(completed_episode_count, int)
        or completed_episode_count < 0
    ):
        raise ValueError("completed_episode_count must be a non-negative integer")
    if profile is None:
        return PidSpDelayEvidence(
            status=DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES.value,
            completed_episode_count=completed_episode_count,
            evaluated_bound_s=INITIAL_DELAY_BOUND_S,
            profile_form=None,
            raw_basin_lower_s=None,
            raw_basin_upper_s=None,
            raw_basin_representative_s=None,
            confidence_lower_s=None,
            confidence_upper_s=None,
            confidence_method=None,
            confidence_resamples=None,
            blockers=[DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES.value],
            authorized=False,
        )

    blockers = [blocker.value for blocker in profile.blockers]
    primary = next(
        (blocker.value for blocker in _DELAY_STATUS_PRIORITY if blocker in profile.blockers),
        "delay-basin-stable",
    )
    basin = profile.basin
    return PidSpDelayEvidence(
        status=cast(PidSpDelayEvidenceStatus, primary),
        completed_episode_count=completed_episode_count,
        evaluated_bound_s=profile.evaluated_bound_s,
        profile_form=cast(PidSpDelayProfileForm, profile.model_form),
        raw_basin_lower_s=None if basin is None else basin.lower_s,
        raw_basin_upper_s=None if basin is None else basin.upper_s,
        raw_basin_representative_s=None if basin is None else basin.representative_s,
        confidence_lower_s=None if basin is None else basin.confidence_lower_s,
        confidence_upper_s=None if basin is None else basin.confidence_upper_s,
        confidence_method=None if basin is None else basin.confidence_method,
        confidence_resamples=None if basin is None else basin.confidence_resamples,
        blockers=blockers,
        authorized=profile.authorized,
    )


def _finite_loss(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _comparison_report(
    comparison: ModelComparison | None,
) -> PidSpModelComparisonReport | None:
    if comparison is None:
        return None
    forms = tuple(
        PidSpFormComparisonReport(
            form=cast(PidSpDelayProfileForm, fit.form.value),
            eligible=fit.eligible,
            blockers=tuple(
                blocker.value if isinstance(blocker, DelayBlocker) else blocker for blocker in fit.all_blockers
            ),
            one_step_loss=_finite_loss(fit.one_step_loss),
            horizon_losses=tuple(
                PidSpHorizonLossReport(
                    horizon_s=horizon,
                    loss=_finite_loss(loss),
                )
                for horizon, loss in fit.horizon_losses
            ),
            fold_losses=tuple(_finite_loss(loss) for loss in fit.fold_losses),
            standard_error=_finite_loss(fit.standard_error),
            basin_lower_s=(None if fit.delay_profile.basin is None else fit.delay_profile.basin.lower_s),
            basin_upper_s=(None if fit.delay_profile.basin is None else fit.delay_profile.basin.upper_s),
            confidence_lower_s=(
                None if fit.delay_profile.basin is None else fit.delay_profile.basin.confidence_lower_s
            ),
            confidence_upper_s=(
                None if fit.delay_profile.basin is None else fit.delay_profile.basin.confidence_upper_s
            ),
            confidence_method=(None if fit.delay_profile.basin is None else fit.delay_profile.basin.confidence_method),
        )
        for fit in comparison.fits
    )
    selected = comparison.selected
    if selected is not None and not selected.authorized:
        primary_blocker = "confirmation-pending"
    elif selected is not None:
        primary_blocker = None
    else:
        primary_blocker = next(
            (
                blocker.value if isinstance(blocker, DelayBlocker) else blocker
                for fit in comparison.fits
                for blocker in fit.all_blockers
            ),
            "no-eligible-model",
        )
    return PidSpModelComparisonReport(
        forms=forms,
        best_form=(None if comparison.best_form is None else cast(PidSpDelayProfileForm, comparison.best_form.value)),
        comparison_threshold=comparison.comparison_threshold,
        selection_margin=comparison.selection_margin,
        selected_form=(None if selected is None else cast(PidSpDelayProfileForm, selected.form.value)),
        confirmation=PidSpConfirmationProgress(
            observed=None if selected is None else selected.confirmation_observed,
            required=CONFIRMATION_WINDOW,
        ),
        primary_blocker=primary_blocker,
    )


def build_pid_sp_live_learning(
    identifier: Mapping[str, object],
    predictor: Mapping[str, object],
    *,
    completed_episode_count: int,
    delay_profile: DelayProfile | None,
    comparison: ModelComparison | None,
    active_selected: SelectedPidSpModel | None = None,
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
    comparison_report = _comparison_report(comparison)
    if active_selected is not None:
        if not isinstance(active_selected, SelectedPidSpModel):
            raise TypeError("active_selected must be a SelectedPidSpModel or null")
        if not active_selected.authorized:
            raise ValueError("active_selected must be authorized")
        active_model = PidSpActiveModelReport(
            form=cast(PidSpDelayProfileForm, active_selected.form.value),
            model_digest=active_selected.model_digest,
        )
    else:
        active_model = None
    if comparison_report is not None:
        confirmation = comparison_report.confirmation
    elif active_selected is not None:
        confirmation = PidSpConfirmationProgress(
            observed=active_selected.confirmation_observed,
            required=active_selected.confirmation_required,
        )
    else:
        confirmation = PidSpConfirmationProgress(
            observed=None,
            required=CONFIRMATION_WINDOW,
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
    elif active_model is not None and predictor_active:
        status = "active"
    elif comparison is not None and comparison.selected is not None or all(gate.passed for gate in gates):
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
        delay_evidence=_delay_evidence(completed_episode_count, delay_profile),
        confirmation=confirmation,
        gates=gates,
        comparison=comparison_report,
        active_model=active_model,
    ).model_dump(mode="json")


@std_dataclass(frozen=True, slots=True)
class _CanonicalPidSpLearningReport:
    """Immutable canonical report bytes safe to cache or serve."""

    payload_bytes: bytes

    def as_dict(self) -> dict[str, object]:
        """Return a caller-owned decoded report."""

        decoded = json.loads(self.payload_bytes)
        if not isinstance(decoded, dict):
            raise TypeError("PID-SP learning report root is not an object")
        return cast(dict[str, object], decoded)

    def to_dict(self) -> dict[str, object]:
        """Return a caller-owned decoded report."""

        return self.as_dict()

    @property
    def revision(self) -> str:
        """Return the report invalidation token."""

        revision = self.as_dict().get("revision")
        if not isinstance(revision, str):
            raise TypeError("PID-SP learning report revision is missing")
        return revision


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        _owned_json_value(value, "report"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _normalize_checkpoint(checkpoint: object) -> dict[str, object] | None:
    if checkpoint is None:
        return None
    try:
        decoded = project_pid_sp_persisted_checkpoint(checkpoint)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"checkpoint is invalid: {error}") from error
    if decoded is None:
        return None
    normalized = encode_pid_sp_checkpoint(
        decoded.selected,
        revision=decoded.revision,
        provenance=decoded.provenance,
        installation_identity_digest=decoded.installation_identity_digest,
    )
    normalized["schema_version"] = 2
    normalized.pop("installation_identity_digest")
    return PidSpCheckpointModel.model_validate_json(
        _canonical_bytes(normalized),
        strict=True,
    ).model_dump(mode="json")


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
    if isinstance(value, (bool, int)):
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
        raise TypeError("gate name must be a string")
    if not isinstance(passed, bool):
        raise TypeError("gate passed must be a boolean")
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
        "delay_evidence",
        "gates",
        "comparison",
        "active_model",
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
    delay_mapping = _owned_json_mapping(mapping["delay_evidence"], "delay_evidence")
    delay_evidence = PidSpDelayEvidence.model_validate(delay_mapping, strict=True)
    gates_value = mapping["gates"]
    if not isinstance(gates_value, Sequence) or isinstance(gates_value, (str, bytes, bytearray)):
        raise TypeError("gates must be an array")
    gates = [_learning_gate(value) for value in gates_value]
    comparison_value = mapping["comparison"]
    comparison = (
        None
        if comparison_value is None
        else PidSpModelComparisonReport.model_validate(
            _owned_json_mapping(comparison_value, "comparison"),
            strict=True,
        )
    )
    active_model_value = mapping["active_model"]
    active_model = (
        None
        if active_model_value is None
        else PidSpActiveModelReport.model_validate(
            _owned_json_mapping(active_model_value, "active_model"),
            strict=True,
        )
    )
    if (status == "active") != (
        active_model is not None and predictor["active"] is True and predictor["disabled"] is False
    ):
        raise ValueError("active status must match active model authority")
    normalized = PidSpLiveLearning(
        schema_version=1,
        controller="pid_sp",
        status=cast(PidSpLiveLearningStatus, status),
        identifier=identifier,
        predictor=predictor,
        confirmation=confirmation,
        delay_evidence=delay_evidence,
        gates=tuple(gates),
        comparison=comparison,
        active_model=active_model,
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
        "delay_evidence": None,
        "checkpoint": normalized_checkpoint,
        "comparison": None,
        "active_model": None,
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
                    "delay_evidence": normalized_live["delay_evidence"],
                    "comparison": normalized_live["comparison"],
                    "active_model": normalized_live["active_model"],
                }
            )
    payload["revision"] = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    contract = PidSpLearningReport.model_validate_json(_canonical_bytes(payload), strict=True)
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
