"""Shared controller-learning diagnostic report contracts."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, Protocol, TypeGuard, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

from common.control_trace import ControlTraceRecord, TraceEventKind
from common.model_evidence import ModelEvidenceRecord
from common.persistence.control_trace import read_control_trace_cook
from common.persistence.model_evidence import read_model_evidence


def _owned_json(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if isinstance(value, Mapping):
        owned: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            owned[key] = _owned_json(item, f"{path}.{key}")
        return owned
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_owned_json(item, f"{path}[]") for item in value]
    raise TypeError(f"{path} contains unsupported {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ControllerLearningReport:
    """Deeply owned final learning report from one controller provider."""

    controller: str
    schema_version: int
    revision: str
    report: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.controller, str) or not self.controller.strip():
            raise ValueError("controller must be a non-blank string")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("revision must be a non-blank string")
        if not isinstance(self.report, Mapping):
            raise TypeError("report must be a mapping")
        object.__setattr__(
            self,
            "report",
            cast(Mapping[str, JsonValue], _owned_json(self.report, "report")),
        )


_DIAGNOSTIC_SCHEMA_VERSION = 1
_NonBlankString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
_NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
_MODEL_CONFIG: ConfigDict = ConfigDict(extra="forbid", frozen=True, strict=True)
logger = logging.getLogger(__name__)


class CookDiagnosticCaptureError(BaseModel):
    """One contained diagnostics source failure."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    source: _NonBlankString
    code: _NonBlankString
    detail: _NonBlankString


class CookControlTrace(BaseModel):
    """Compatible cook-scoped trace records and their schema set."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    records: tuple[ControlTraceRecord, ...]
    record_schema_versions: tuple[Literal[2, 3, 4, 5, 6, 7], ...]


class CookModelEvidence(BaseModel):
    """Compatible cook-scoped model evidence and its schema set."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    records: tuple[ModelEvidenceRecord, ...]
    record_schema_versions: tuple[Literal[1, 2, 3], ...]


class CookLearningDiagnostics(BaseModel):
    """Validated schema-one cook learning diagnostics envelope."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    schema_version: Literal[1] = _DIAGNOSTIC_SCHEMA_VERSION
    cook_id: _NonBlankString | None
    captured_at_ms: _NonNegativeInt
    controllers: tuple[_NonBlankString, ...]
    reports: tuple[ControllerLearningReport, ...]
    control_trace: CookControlTrace
    model_evidence: CookModelEvidence
    capture_errors: tuple[CookDiagnosticCaptureError, ...]


class LearningReportProvider(Protocol):
    def __call__(self, controller: str, /) -> ControllerLearningReport | None: ...


class ReadControlTrace(Protocol):
    def __call__(self, cook_id: str, /) -> Sequence[ControlTraceRecord]: ...


class ReadModelEvidence(Protocol):
    def __call__(self, *, cook_id: str) -> Sequence[ModelEvidenceRecord]: ...


type ClockMs = Callable[[], int]
type WarningSink = Callable[[str], object]


def wall_clock_ms() -> int:
    """Return wall-clock milliseconds."""
    return time.time_ns() // 1_000_000


def _exception_detail(exc: Exception) -> str:
    try:
        detail = str(exc).strip()
    except Exception:
        return type(exc).__name__
    return detail or type(exc).__name__


def _capture_error(
    errors: list[CookDiagnosticCaptureError],
    *,
    source: str,
    code: str,
    detail: str,
    warn: WarningSink,
) -> None:
    errors.append(CookDiagnosticCaptureError(source=source, code=code, detail=detail))
    try:
        warn(f"{source}: {detail}")
    except Exception:
        pass


def _valid_cook_id(cook_id: object) -> TypeGuard[str]:
    return isinstance(cook_id, str) and bool(cook_id) and cook_id == cook_id.strip()


def _empty_diagnostics(
    *,
    cook_id: str | None,
    captured_at_ms: int,
    capture_errors: tuple[CookDiagnosticCaptureError, ...],
) -> CookLearningDiagnostics:
    return CookLearningDiagnostics(
        cook_id=cook_id,
        captured_at_ms=captured_at_ms,
        controllers=(),
        reports=(),
        control_trace=CookControlTrace(records=(), record_schema_versions=()),
        model_evidence=CookModelEvidence(records=(), record_schema_versions=()),
        capture_errors=capture_errors,
    )


def _collect_cook_learning_diagnostics(
    cook_id: str | None,
    report_provider: LearningReportProvider,
    *,
    read_trace: ReadControlTrace,
    read_evidence: ReadModelEvidence,
    clock_ms: ClockMs,
    warn: WarningSink,
) -> CookLearningDiagnostics:
    captured_at_ms = clock_ms()
    errors: list[CookDiagnosticCaptureError] = []
    if not _valid_cook_id(cook_id):
        _capture_error(
            errors,
            source="collector",
            code="cook-identity-invalid",
            detail="cook_id must be a non-blank, whitespace-trimmed string",
            warn=warn,
        )
        return _empty_diagnostics(
            cook_id=None,
            captured_at_ms=captured_at_ms,
            capture_errors=tuple(errors),
        )

    try:
        raw_trace_records = tuple(read_trace(cook_id))
    except Exception as exc:
        raw_trace_records = ()
        trace_read_succeeded = False
        _capture_error(
            errors,
            source="control_trace",
            code="control-trace-read-failed",
            detail=_exception_detail(exc),
            warn=warn,
        )
    else:
        trace_read_succeeded = True

    trace_records = tuple(record for record in raw_trace_records if record.cook_id == cook_id)
    if len(trace_records) != len(raw_trace_records):
        _capture_error(
            errors,
            source="control_trace",
            code="control-trace-cook-mismatch",
            detail="record cook_id does not match requested cook_id",
            warn=warn,
        )

    try:
        raw_evidence_records = tuple(read_evidence(cook_id=cook_id))
    except Exception as exc:
        raw_evidence_records = ()
        _capture_error(
            errors,
            source="model_evidence",
            code="model-evidence-read-failed",
            detail=_exception_detail(exc),
            warn=warn,
        )

    evidence_records = tuple(record for record in raw_evidence_records if record.cook_id == cook_id)
    if len(evidence_records) != len(raw_evidence_records):
        _capture_error(
            errors,
            source="model_evidence",
            code="model-evidence-cook-mismatch",
            detail="record cook_id does not match requested cook_id",
            warn=warn,
        )

    controllers: list[str] = []
    seen_controllers: set[str] = set()
    for record in trace_records:
        controller = record.controller.value
        if record.event_kind is TraceEventKind.SESSION and controller not in seen_controllers:
            seen_controllers.add(controller)
            controllers.append(controller)
    if trace_read_succeeded and not controllers:
        _capture_error(
            errors,
            source="control_trace",
            code="trace-session-missing",
            detail="no session records found for requested cook",
            warn=warn,
        )

    reports: list[ControllerLearningReport] = []
    for controller in controllers:
        try:
            report = report_provider(controller)
        except Exception as exc:
            _capture_error(
                errors,
                source=f"report:{controller}",
                code="report-read-failed",
                detail=_exception_detail(exc),
                warn=warn,
            )
            continue
        if report is None:
            continue
        if not isinstance(report, ControllerLearningReport):
            _capture_error(
                errors,
                source=f"report:{controller}",
                code="report-type-invalid",
                detail=f"provider returned unsupported report type: {type(report).__name__}",
                warn=warn,
            )
            continue
        if report.controller != controller:
            _capture_error(
                errors,
                source=f"report:{controller}",
                code="report-controller-mismatch",
                detail=f"provider returned report for {report.controller!r}",
                warn=warn,
            )
            continue
        reports.append(report)

    return CookLearningDiagnostics(
        cook_id=cook_id,
        captured_at_ms=captured_at_ms,
        controllers=tuple(controllers),
        reports=tuple(reports),
        control_trace=CookControlTrace(
            records=trace_records,
            record_schema_versions=tuple(sorted({record.schema_version for record in trace_records})),
        ),
        model_evidence=CookModelEvidence(
            records=evidence_records,
            record_schema_versions=tuple(sorted({record.schema_version for record in evidence_records})),
        ),
        capture_errors=tuple(errors),
    )


def collect_cook_learning_diagnostics(
    cook_id: str | None,
    report_provider: LearningReportProvider,
    *,
    read_trace: ReadControlTrace = read_control_trace_cook,
    read_evidence: ReadModelEvidence = read_model_evidence,
    clock_ms: ClockMs = wall_clock_ms,
    warn: WarningSink = logger.warning,
) -> CookLearningDiagnostics:
    """Collect one cook's compatible learning diagnostics without throwing."""
    try:
        return _collect_cook_learning_diagnostics(
            cook_id,
            report_provider,
            read_trace=read_trace,
            read_evidence=read_evidence,
            clock_ms=clock_ms,
            warn=warn,
        )
    except Exception as exc:
        errors: list[CookDiagnosticCaptureError] = []
        _capture_error(
            errors,
            source="collector",
            code="collector-failed",
            detail=_exception_detail(exc),
            warn=warn,
        )
        fallback_cook_id = cook_id if _valid_cook_id(cook_id) else None
        return _empty_diagnostics(
            cook_id=fallback_cook_id,
            captured_at_ms=wall_clock_ms(),
            capture_errors=tuple(errors),
        )
