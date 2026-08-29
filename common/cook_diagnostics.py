"""Shared controller-learning diagnostic report contracts."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, Protocol, TypeGuard, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from common.control_trace import ControlTraceRecord, TraceEventKind
from common.learning_trajectory import (
    Digest,
    LearningTrajectorySegment,
    TrajectoryBreakReason,
)
from common.model_evidence import ModelEvidenceRecord
from common.persistence.control_trace import read_control_trace_cook
from common.persistence.learning_trajectory import (
    LearningTrajectoryRepository,
    TrajectoryCorpusReport,
)
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


_DIAGNOSTIC_SCHEMA_VERSION = 2
_NonBlankString = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
_NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
_PositiveInt = Annotated[int, Field(gt=0, strict=True)]
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
    record_schema_versions: tuple[Literal[2, 3, 4, 5, 6, 7, 8], ...]


class CookModelEvidence(BaseModel):
    """Compatible cook-scoped model evidence and its schema set."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    records: tuple[ModelEvidenceRecord, ...]
    record_schema_versions: tuple[Literal[1, 2, 3, 4], ...]


class CookTrajectorySegmentReference(BaseModel):
    """Lightweight current-cook reference to one validated trajectory segment."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    cook_id: _NonBlankString
    segment_schema_version: _PositiveInt
    observation_schema_version: _PositiveInt
    segment_id: _NonBlankString
    trajectory_session_id: _NonBlankString
    trace_session_ids: tuple[_NonBlankString, ...]
    state: Literal["open", "finalized", "quarantined"]
    source_trace_digest: Digest
    content_digest: Digest
    fit_partition_digest: Digest
    source_row_digest: Digest
    source_schema_version: _PositiveInt
    pre_roll_frame_count: _NonNegativeInt
    scored_hold_frame_count: _NonNegativeInt
    terminal_break_reason: TrajectoryBreakReason | None


class CookTrajectoryBreakReasonCount(BaseModel):
    """One typed terminal trajectory break total."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    reason: TrajectoryBreakReason
    count: _NonNegativeInt


class CookTrajectoryCorpusReport(BaseModel):
    """Normalized global trajectory corpus diagnostics."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    schema_version: Literal[1]
    corpus_revision: _NonNegativeInt
    segment_count: _NonNegativeInt
    pre_roll_count: _NonNegativeInt
    pre_roll_capacity: _NonNegativeInt
    scored_count: _NonNegativeInt
    scored_capacity: _NonNegativeInt
    evicted_segment_count: _NonNegativeInt
    evicted_pre_roll_count: _NonNegativeInt
    evicted_scored_count: _NonNegativeInt
    open_segment_count: _NonNegativeInt
    finalized_segment_count: _NonNegativeInt
    quarantined_segment_count: _NonNegativeInt
    distinct_cook_count: _NonNegativeInt
    distinct_session_count: _NonNegativeInt
    earliest_wall_ms: _NonNegativeInt | None
    latest_wall_ms: _NonNegativeInt | None
    break_reason_counts: tuple[CookTrajectoryBreakReasonCount, ...]
    last_persistence_error: _NonBlankString | None
    last_recovery_error: _NonBlankString | None

    @model_validator(mode="after")
    def normalize_break_reason_counts(self) -> CookTrajectoryCorpusReport:
        reasons = tuple(item.reason for item in self.break_reason_counts)
        if len(reasons) != len(set(reasons)):
            raise ValueError("break reason counts must be unique")
        object.__setattr__(
            self,
            "break_reason_counts",
            tuple(sorted(self.break_reason_counts, key=lambda item: item.reason.value)),
        )
        return self


class CookLearningDiagnostics(BaseModel):
    """Validated schema-two cook learning diagnostics envelope."""

    model_config: ClassVar[ConfigDict] = _MODEL_CONFIG

    schema_version: Literal[2] = _DIAGNOSTIC_SCHEMA_VERSION
    cook_id: _NonBlankString | None
    captured_at_ms: _NonNegativeInt
    controllers: tuple[_NonBlankString, ...]
    reports: tuple[ControllerLearningReport, ...]
    control_trace: CookControlTrace
    model_evidence: CookModelEvidence
    trajectory_segments: tuple[CookTrajectorySegmentReference, ...]
    trajectory_schema_versions: tuple[_PositiveInt, ...]
    corpus: CookTrajectoryCorpusReport | None
    capture_errors: tuple[CookDiagnosticCaptureError, ...]


class LearningReportProvider(Protocol):
    def __call__(self, controller: str, /) -> ControllerLearningReport | None: ...


class ReadControlTrace(Protocol):
    def __call__(self, cook_id: str, /) -> Sequence[ControlTraceRecord]: ...


class ReadModelEvidence(Protocol):
    def __call__(self, *, cook_id: str) -> Sequence[ModelEvidenceRecord]: ...


class ReadTrajectorySegments(Protocol):
    def __call__(self, cook_id: str, /) -> Sequence[LearningTrajectorySegment]: ...


class ReadCorpusReport(Protocol):
    def __call__(
        self,
    ) -> TrajectoryCorpusReport | CookTrajectoryCorpusReport: ...


type ClockMs = Callable[[], int]
type WarningSink = Callable[[str], object]


def wall_clock_ms() -> int:
    """Return wall-clock milliseconds."""
    return time.time_ns() // 1_000_000


def _read_persisted_trajectory_segments(
    cook_id: str,
) -> tuple[LearningTrajectorySegment, ...]:
    return LearningTrajectoryRepository().read_cook_segments(cook_id)


def _read_persisted_corpus_report() -> TrajectoryCorpusReport:
    return LearningTrajectoryRepository().corpus_report()


def _trajectory_reference(
    segment: LearningTrajectorySegment,
) -> CookTrajectorySegmentReference:
    return CookTrajectorySegmentReference(
        cook_id=segment.cook_id,
        segment_schema_version=segment.schema_version,
        observation_schema_version=segment.observation_schema_version,
        segment_id=segment.segment_id,
        trajectory_session_id=segment.trajectory_session_id,
        trace_session_ids=segment.trace_session_ids,
        state=segment.state,
        source_trace_digest=segment.source_trace_digest,
        content_digest=segment.content_digest,
        fit_partition_digest=segment.fit_partition_digest,
        source_row_digest=segment.source_row_digest,
        source_schema_version=segment.source_schema_version,
        pre_roll_frame_count=len(segment.pre_roll_frames),
        scored_hold_frame_count=len(segment.scored_hold_frames),
        terminal_break_reason=segment.terminal_break_reason,
    )


def _corpus_projection(
    report: TrajectoryCorpusReport | CookTrajectoryCorpusReport,
) -> CookTrajectoryCorpusReport:
    if not isinstance(report, (TrajectoryCorpusReport, CookTrajectoryCorpusReport)):
        raise TypeError(f"reader returned unsupported corpus report type: {type(report).__name__}")
    return CookTrajectoryCorpusReport(
        schema_version=report.schema_version,
        corpus_revision=report.corpus_revision,
        segment_count=report.segment_count,
        pre_roll_count=report.pre_roll_count,
        pre_roll_capacity=report.pre_roll_capacity,
        scored_count=report.scored_count,
        scored_capacity=report.scored_capacity,
        evicted_segment_count=report.evicted_segment_count,
        evicted_pre_roll_count=report.evicted_pre_roll_count,
        evicted_scored_count=report.evicted_scored_count,
        open_segment_count=report.open_segment_count,
        finalized_segment_count=report.finalized_segment_count,
        quarantined_segment_count=report.quarantined_segment_count,
        distinct_cook_count=report.distinct_cook_count,
        distinct_session_count=report.distinct_session_count,
        earliest_wall_ms=report.earliest_wall_ms,
        latest_wall_ms=report.latest_wall_ms,
        break_reason_counts=tuple(
            CookTrajectoryBreakReasonCount(
                reason=item.reason,
                count=item.count,
            )
            for item in report.break_reason_counts
        ),
        last_persistence_error=report.last_persistence_error,
        last_recovery_error=report.last_recovery_error,
    )


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
        return


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
        trajectory_segments=(),
        trajectory_schema_versions=(),
        corpus=None,
        capture_errors=capture_errors,
    )


def _collect_cook_learning_diagnostics(
    cook_id: str | None,
    report_provider: LearningReportProvider,
    *,
    read_trace: ReadControlTrace,
    read_evidence: ReadModelEvidence,
    read_trajectory_segments: ReadTrajectorySegments,
    read_corpus_report: ReadCorpusReport,
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
                detail=(f"provider returned unsupported report type: {type(report).__name__}"),
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

    try:
        raw_trajectory_segments = tuple(read_trajectory_segments(cook_id))
        if any(not isinstance(segment, LearningTrajectorySegment) for segment in raw_trajectory_segments):
            invalid = next(
                segment for segment in raw_trajectory_segments if not isinstance(segment, LearningTrajectorySegment)
            )
            invalid_type = type(invalid).__name__
            raise TypeError(f"reader returned unsupported trajectory segment type: {invalid_type}")
        matching_trajectory_segments = tuple(
            segment for segment in raw_trajectory_segments if segment.cook_id == cook_id
        )
        trajectory_segments = tuple(_trajectory_reference(segment) for segment in matching_trajectory_segments)
    except Exception as exc:
        raw_trajectory_segments = ()
        trajectory_segments = ()
        _capture_error(
            errors,
            source="learning_trajectory",
            code="learning-trajectory-read-failed",
            detail=_exception_detail(exc),
            warn=warn,
        )
    else:
        if len(matching_trajectory_segments) != len(raw_trajectory_segments):
            _capture_error(
                errors,
                source="learning_trajectory",
                code="learning-trajectory-cook-mismatch",
                detail="segment cook_id does not match requested cook_id",
                warn=warn,
            )

    try:
        corpus = _corpus_projection(read_corpus_report())
    except Exception as exc:
        corpus = None
        _capture_error(
            errors,
            source="trajectory_corpus",
            code="trajectory-corpus-read-failed",
            detail=_exception_detail(exc),
            warn=warn,
        )

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
        trajectory_segments=trajectory_segments,
        trajectory_schema_versions=tuple(sorted({segment.segment_schema_version for segment in trajectory_segments})),
        corpus=corpus,
        capture_errors=tuple(errors),
    )


def collect_cook_learning_diagnostics(
    cook_id: str | None,
    report_provider: LearningReportProvider,
    *,
    read_trace: ReadControlTrace = read_control_trace_cook,
    read_evidence: ReadModelEvidence = read_model_evidence,
    read_trajectory_segments: ReadTrajectorySegments = (_read_persisted_trajectory_segments),
    read_corpus_report: ReadCorpusReport = _read_persisted_corpus_report,
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
            read_trajectory_segments=read_trajectory_segments,
            read_corpus_report=read_corpus_report,
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
