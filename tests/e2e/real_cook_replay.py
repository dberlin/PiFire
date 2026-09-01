"""Deterministic adapters for sanitized real-cook learning evidence."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from common import datastore
from common.control_trace import AmbientSource, AmbientUncertainty, ControllerType, TraceEventKind
from common.controller_model_state import MODEL_STATE_KEY, ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason, trajectory_json_value
from common.persistence.control_trace import read_control_trace_cook
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from controller.model_learning.contracts import FrameObservation
from controller.mpc import Controller as MpcController
from controller.mpc_config import DEFAULT_MPC_CONFIG
from controller.pid_sp_observation import PidSpDutySegment, PidSpInterval
from controller.runtime.actuation_delivery import ActuationDeliveryJournal
from controller.runtime.control_trace_recorder import ControlTraceRecorder
from controller.runtime.control_trace_session import ControlTraceSession, TraceSessionContext
from controller.runtime.learning_trajectory import LearningTrajectoryRuntime, ModeEntered, ModeExited, ThermalSample
from controller.runtime.model_persistence import ModelPersistenceWorker
from controller.runtime.modes.hold_learning import HoldLearningRuntime
from controller.runtime.runner import SyncControllerRunner

PID_SP_AUGUST_28_FIXTURE = Path(__file__).parents[1] / "fixtures" / "pid_sp" / "2026-08-28-intervals.json"
PID_SP_AUGUST_28_SHA256 = "7ac7a06366b414ae84f338159372cfef5fd9f8aa04961ca4c2a9758d9b53c922"
PID_SP_AUGUST_28_SOURCE_SHA256 = "a982d09c4f26c7505ee4d18d9d20b9bf8bf4db7bf59542d9099e98b59a79e1d5"
_ROOT_FIELDS = {
    "actuation_frames",
    "intervals",
    "schema_version",
    "session",
    "source_archive_sha256",
}
_SESSION_FIELDS = {
    "ambient_temperature_f",
    "control_period_s",
    "controller_config",
    "fan_authority",
    "fan_max_duty",
    "fan_min_duty",
    "fan_pwm_capable",
    "pulse_frame_s",
    "pulse_slot_s",
    "temperature_unit",
}
_INTERVAL_FIELDS = {
    "applied_fan_duty",
    "continuous",
    "duty_segments",
    "end_s",
    "inhibit_reason",
    "observation_sequence",
    "output_source",
    "prior_realized_duty",
    "realized_duty",
    "requested_fan_duty",
    "requested_output",
    "result_revision",
    "role_generation",
    "setpoint_f",
    "start_s",
    "temperature_f",
}
_DUTY_SEGMENT_FIELDS = {
    "actual_fan_duty",
    "end_s",
    "output_source",
    "realized_combustion_load",
    "realized_duty",
    "result_revision",
    "sample_complete",
    "start_s",
}
_ACTUATION_FRAME_FIELDS = {
    "applied_fan_duty",
    "delivered_on_s",
    "end_s",
    "inhibit_reason",
    "requested_auger_duty",
    "requested_combustion_load",
    "requested_fan_duty",
    "reset_reason",
    "result_revision",
    "scheduled_on_s",
    "skipped",
    "stale",
    "start_s",
}


@dataclass(frozen=True, slots=True)
class ReplayActuationFrame:
    start_s: float
    end_s: float
    requested_auger_duty: float
    requested_combustion_load: float
    requested_fan_duty: float | None
    applied_fan_duty: float | None
    delivered_on_s: float
    scheduled_on_s: float
    result_revision: int
    inhibit_reason: str
    reset_reason: str | None
    skipped: bool
    stale: bool


@dataclass(frozen=True, slots=True)
class ReplayGap:
    frame: ReplayActuationFrame
    reason: Literal["missing-synchronized-thermal-update"]


@dataclass(frozen=True, slots=True)
class PidSpAugust28Replay:
    """All exact frames, their synchronized observations, and explicit gaps.

    The first update interval is the real 40 ms applied-output tile ending at
    the first controller update. It supplies the left thermal anchor. Each
    later update contributes its terminal temperature to only the latest exact
    actuation frame completed in that window. A frame without such a thermal
    join is a typed gap; it never inherits a later temperature. Requested load
    defaults required by FrameObservation are exact copies of the archived
    requested auger duty because PID-SP has no separate combustion allocator.
    """

    intervals: tuple[PidSpInterval, ...]
    actuation_frames: tuple[ReplayActuationFrame, ...]
    observations: tuple[FrameObservation, ...]
    gaps: tuple[ReplayGap, ...]
    ambient_temperature_f: float
    controller_config: tuple[tuple[str, float | int | bool], ...]

    @property
    def anchor(self) -> PidSpInterval:
        return self.intervals[0]


def _load_payload() -> dict[str, object]:
    fixture_bytes = PID_SP_AUGUST_28_FIXTURE.read_bytes()
    assert hashlib.sha256(fixture_bytes).hexdigest() == PID_SP_AUGUST_28_SHA256
    payload = json.loads(fixture_bytes)
    assert isinstance(payload, dict)
    assert set(payload) == _ROOT_FIELDS
    assert payload["schema_version"] == 2
    assert payload["source_archive_sha256"] == PID_SP_AUGUST_28_SOURCE_SHA256
    assert fixture_bytes == (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode(
        "utf-8"
    )

    session = payload["session"]
    intervals = payload["intervals"]
    frames = payload["actuation_frames"]
    assert isinstance(session, dict) and set(session) == _SESSION_FIELDS
    assert isinstance(intervals, list) and len(intervals) == 408
    assert isinstance(frames, list) and len(frames) == 409
    assert all(isinstance(row, dict) and set(row) == _INTERVAL_FIELDS for row in intervals)
    assert all(
        isinstance(segment, dict) and set(segment) == _DUTY_SEGMENT_FIELDS
        for row in intervals
        for segment in row["duty_segments"]
    )
    assert all(isinstance(frame, dict) and set(frame) == _ACTUATION_FRAME_FIELDS for frame in frames)
    return payload


def _decode_interval(row: dict[str, object]) -> PidSpInterval:
    raw_segments = row["duty_segments"]
    assert isinstance(raw_segments, list)
    duty_segments = tuple(
        PidSpDutySegment(
            start_s=float(segment["start_s"]),
            end_s=float(segment["end_s"]),
            realized_duty=float(segment["realized_duty"]),
        )
        for segment in raw_segments
        if isinstance(segment, dict)
    )
    assert len(duty_segments) == len(raw_segments)
    return PidSpInterval(
        start_s=float(row["start_s"]),
        end_s=float(row["end_s"]),
        temperature_f=float(row["temperature_f"]),
        realized_duty=float(row["realized_duty"]),
        continuous=row["continuous"] is True,
        observation_sequence=int(row["observation_sequence"]),
        role_generation=int(row["role_generation"]),
        duty_segments=duty_segments,
    )


def load_pid_sp_august_28_intervals() -> tuple[PidSpInterval, ...]:
    """Decode and fully validate the committed sanitized interval fixture."""
    payload = _load_payload()
    raw_intervals = payload["intervals"]
    assert isinstance(raw_intervals, list)
    intervals = tuple(_decode_interval(row) for row in raw_intervals if isinstance(row, dict))
    assert len(intervals) == 408
    assert tuple(interval.observation_sequence for interval in intervals) == tuple(range(1, 409))
    assert intervals[0] == PidSpInterval(
        start_s=-0.04,
        end_s=0.0,
        temperature_f=103.7,
        realized_duty=0.0,
        continuous=True,
        observation_sequence=1,
        role_generation=0,
        duty_segments=(PidSpDutySegment(start_s=-0.04, end_s=0.0, realized_duty=0.0),),
    )
    assert all(left.end_s == right.start_s for left, right in pairwise(intervals))
    assert all(
        interval.duty_segments is not None
        and interval.duty_segments[0].start_s == interval.start_s
        and interval.duty_segments[-1].end_s == interval.end_s
        and all(left.end_s == right.start_s for left, right in pairwise(interval.duty_segments))
        and math.isclose(
            sum((segment.end_s - segment.start_s) * segment.realized_duty for segment in interval.duty_segments)
            / (interval.end_s - interval.start_s),
            interval.realized_duty,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for interval in intervals
    )
    return intervals


def _decode_frame(row: dict[str, object]) -> ReplayActuationFrame:
    return ReplayActuationFrame(
        start_s=float(row["start_s"]),
        end_s=float(row["end_s"]),
        requested_auger_duty=float(row["requested_auger_duty"]),
        requested_combustion_load=float(row["requested_combustion_load"]),
        requested_fan_duty=(None if row["requested_fan_duty"] is None else float(row["requested_fan_duty"])),
        applied_fan_duty=(None if row["applied_fan_duty"] is None else float(row["applied_fan_duty"])),
        delivered_on_s=float(row["delivered_on_s"]),
        scheduled_on_s=float(row["scheduled_on_s"]),
        result_revision=int(row["result_revision"]),
        inhibit_reason=str(row["inhibit_reason"]),
        reset_reason=None if row["reset_reason"] is None else str(row["reset_reason"]),
        skipped=row["skipped"] is True,
        stale=row["stale"] is True,
    )


def _to_celsius(temperature_f: float) -> float:
    return (temperature_f - 32.0) * 5.0 / 9.0


def _observation(
    frame: ReplayActuationFrame,
    interval: PidSpInterval,
    row: dict[str, object],
    ambient_temperature_f: float,
) -> FrameObservation:
    duration_s = frame.end_s - frame.start_s
    # JSON decimal frame bounds can subtract one ULP below their exact
    # millisecond duration. Preserve the archived values in ReplayActuationFrame
    # and normalize only that representation artifact at FrameObservation's
    # strict delivered-on boundary.
    assert frame.delivered_on_s - duration_s <= 1e-12
    delivered_on_s = min(frame.delivered_on_s, duration_s)
    scheduled_on_s = min(frame.scheduled_on_s, duration_s)
    realized_duty = delivered_on_s / duration_s
    requested_duty = frame.requested_auger_duty
    return FrameObservation(
        frame_start_s=frame.start_s,
        frame_end_s=frame.end_s,
        temp_c=_to_celsius(interval.temperature_f),
        setpoint_c=_to_celsius(float(row["setpoint_f"])),
        ambient_c=_to_celsius(ambient_temperature_f),
        requested_q=requested_duty,
        realized_q=realized_duty,
        baseline_q=requested_duty,
        allocated_q=requested_duty,
        requested_auger_duty=requested_duty,
        scheduled_on_s=scheduled_on_s,
        delivered_on_s=delivered_on_s,
        realized_auger_duty=realized_duty,
        requested_fan_duty=frame.requested_fan_duty,
        actual_fan_duty=frame.applied_fan_duty,
        result_revision=frame.result_revision,
        output_source=str(row["output_source"]),
        lid_open=False,
        safety_inhibited=frame.inhibit_reason != "none",
        manual_override=False,
        stale=frame.stale,
        skipped=frame.skipped,
        reset=frame.reset_reason is not None,
        continuous=interval.continuous,
        role_generation=interval.role_generation,
        observation_sequence=interval.observation_sequence,
        probe_source="sanitized-august-28-chamber",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
    )


def load_pid_sp_august_28_replay() -> PidSpAugust28Replay:
    payload = _load_payload()
    raw_intervals = payload["intervals"]
    raw_frames = payload["actuation_frames"]
    session = payload["session"]
    assert isinstance(raw_intervals, list)
    assert isinstance(raw_frames, list)
    assert isinstance(session, dict)

    intervals = tuple(_decode_interval(row) for row in raw_intervals if isinstance(row, dict))
    frames = tuple(_decode_frame(row) for row in raw_frames if isinstance(row, dict))
    ambient_temperature_f = float(session["ambient_temperature_f"])
    observations: list[FrameObservation] = []
    synchronized_frames: set[tuple[float, float]] = set()
    for row, interval in zip(raw_intervals[1:], intervals[1:], strict=True):
        assert isinstance(row, dict)
        candidates = tuple(frame for frame in frames if interval.start_s < frame.end_s <= interval.end_s)
        assert candidates
        frame = candidates[-1]
        synchronized_frames.add((frame.start_s, frame.end_s))
        observations.append(_observation(frame, interval, row, ambient_temperature_f))

    gaps = tuple(
        ReplayGap(frame, "missing-synchronized-thermal-update")
        for frame in frames
        if (frame.start_s, frame.end_s) not in synchronized_frames
    )
    controller_config = session["controller_config"]
    assert isinstance(controller_config, dict)
    assert len(frames) == 409
    assert len(observations) == 407
    assert len(gaps) == 2
    return PidSpAugust28Replay(
        intervals=intervals,
        actuation_frames=frames,
        observations=tuple(observations),
        gaps=gaps,
        ambient_temperature_f=ambient_temperature_f,
        controller_config=tuple(sorted(controller_config.items())),
    )


REAL_COOK_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "real_cook_learning"
_MANIFEST_FIELDS = {"campaigns", "schema_version"}
_CAMPAIGN_FIELDS = {"baseline", "controller", "cooks", "id"}
_BASELINE_FIELDS = {
    "cutoff_wall_ms",
    "path",
    "sanitized_sha256",
    "schema_version",
    "source_archive_sha256",
    "source_database_sha256",
}
_COOK_FIELDS = {
    "blocked_reason",
    "cook_end_ms",
    "cook_start_ms",
    "expected_input_frame_count",
    "path",
    "replay_kind",
    "sanitized_sha256",
    "source_outcome_counts",
    "source_sha256",
    "trace_schema_version",
}
_METADATA_FIELDS = {
    "blocked_reason",
    "chamber_sample_count",
    "controller",
    "cook_end_ms",
    "cook_id",
    "cook_start_ms",
    "replay_kind",
    "schema_version",
    "source_outcome_counts",
    "trace_schema_version",
    "units",
}
_CHAMBER_SAMPLE_FIELDS = {"chamber_temperature_f", "setpoint_f", "timestamp_ms"}
_SESSION_RECORD_FIELDS = {"controller", "cook_id", "payload", "schema_version", "session_id", "timestamp_ms"}
_SESSION_PAYLOAD_FIELDS = {
    "ambient_temperature",
    "control_period_seconds",
    "controller",
    "controller_config",
    "fan_authority",
    "fan_max_duty",
    "fan_min_duty",
    "fan_pwm_capable",
    "model_provenance",
    "model_revision",
    "payload_type",
    "pulse_frame_seconds",
    "pulse_slot_seconds",
    "setpoint",
    "temperature_unit",
}
_TRANSITION_FIELDS = {
    "actuation_mode",
    "branch",
    "controller",
    "cook_id",
    "output_source",
    "policy_kind",
    "result_revision",
    "session_id",
    "setpoint_c",
    "timestamp_ms",
}
_FRAME_FIELDS = {
    "actual_fan_duty",
    "allocated_combustion_load",
    "allocator_revision",
    "ambient_c",
    "boundary_reason",
    "chamber_temperature_c",
    "continuous",
    "controller",
    "cook_id",
    "delivered_on_seconds",
    "frame_end_ms",
    "frame_seconds",
    "frame_start_ms",
    "interval_contiguous",
    "lid_open",
    "manual_override",
    "observation_sequence",
    "output_source",
    "realized_auger_duty",
    "realized_combustion_load",
    "requested_auger_duty",
    "requested_combustion_load",
    "requested_fan_duty",
    "reset",
    "result_revision",
    "safety_inhibited",
    "sample_complete",
    "scheduled_on_seconds",
    "schema_version",
    "session_id",
    "setpoint_c",
    "skipped",
    "source_outcome",
    "stale",
    "trace_sequence",
}
_EXACT_ARCHIVE_MEMBERS = (
    "metadata.json",
    "chamber_samples.json",
    "sessions.json",
    "transitions.json",
    "frames.json",
)
_THERMAL_ARCHIVE_MEMBERS = ("metadata.json", "chamber_samples.json")
_BASELINE_TABLES = ("control_trace", "kv", "model_activation_state", "model_evidence")


class FixtureDigestMismatch(ValueError):
    """A committed fixture no longer matches its manifest digest."""


class ExactReplayBlocked(ValueError):
    """The manifest explicitly proves that a cook has no exact evidence join."""

    def __init__(self, cook: CookFixture) -> None:
        self.cook_path = cook.path
        self.replay_kind = cook.replay_kind
        super().__init__(cook.blocked_reason or f"{cook.path} is not exact replay evidence")


@dataclass(frozen=True, slots=True)
class BaselineFixture:
    path: str
    sanitized_sha256: str
    schema_version: int
    cutoff_wall_ms: int
    source_archive_sha256: str
    source_database_sha256: str


@dataclass(frozen=True, slots=True)
class CookFixture:
    path: str
    sanitized_sha256: str
    source_sha256: str
    replay_kind: Literal["exact-evidence", "thermal-smoke-only"]
    controller: Literal["mpc", "pid_sp"]
    cook_start_ms: int
    cook_end_ms: int
    expected_input_frame_count: int | None
    trace_schema_version: int | None
    source_outcome_counts: dict[str, int] | None
    blocked_reason: str | None
    fixture_cook_id: str


@dataclass(frozen=True, slots=True)
class CampaignFixture:
    campaign_id: str
    controller: Literal["mpc", "pid_sp"]
    baseline: BaselineFixture
    cooks: tuple[CookFixture, ...]


@dataclass(frozen=True, slots=True)
class RealCookManifest:
    schema_version: int
    campaigns: tuple[CampaignFixture, ...]

    def campaign(self, campaign_id: str) -> CampaignFixture:
        matches = tuple(campaign for campaign in self.campaigns if campaign.campaign_id == campaign_id)
        if len(matches) != 1:
            raise KeyError(campaign_id)
        return matches[0]


@dataclass(frozen=True, slots=True)
class BaselineCopy:
    path: Path
    campaign_id: str
    schema_version: int
    retained_state: str
    table_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class CookMetadata:
    cook_id: str
    controller: str
    replay_kind: str
    cook_start_ms: int
    cook_end_ms: int
    chamber_sample_count: int
    trace_schema_version: int
    source_outcome_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ChamberSample:
    timestamp_ms: int
    chamber_temperature_f: float
    setpoint_f: float


@dataclass(frozen=True, slots=True)
class ReplaySession:
    session_id: str
    cook_id: str
    controller: str
    timestamp_ms: int
    schema_version: int
    controller_config: tuple[tuple[str, float | int | bool], ...]
    temperature_unit: str
    control_period_seconds: float
    pulse_slot_seconds: float
    pulse_frame_seconds: float
    fan_authority: bool
    fan_pwm_capable: bool
    fan_min_duty: float
    fan_max_duty: float
    setpoint: float
    ambient_temperature: float
    model_revision: int | None
    model_provenance: str | None


@dataclass(frozen=True, slots=True)
class ReplayTransition:
    timestamp_ms: int
    session_id: str
    cook_id: str
    result_revision: int
    setpoint_c: float
    output_source: str
    actuation_mode: str
    policy_kind: str


@dataclass(frozen=True, slots=True)
class SourceTerminalOutcome:
    kind: Literal["accepted", "gap", "rejected", "unmatched"]
    reasons: tuple[str, ...] = ()
    reason: str | None = None
    lost_record_count: int | None = None


@dataclass(frozen=True, slots=True)
class ExactReplayFrame:
    trace_sequence: int
    session_id: str
    cook_id: str
    controller: str
    frame_start_ms: int
    frame_end_ms: int
    result_revision: int
    observation_sequence: int
    role_generation: int
    source_outcome: SourceTerminalOutcome
    boundary_reason: tuple[str, str] | None
    interval_contiguous: bool
    sample_complete: bool
    observation: FrameObservation

    @property
    def terminal_identity(self) -> tuple[object, ...]:
        return (
            self.session_id,
            self.cook_id,
            self.role_generation,
            self.observation_sequence,
            self.result_revision,
            self.frame_start_ms,
            self.frame_end_ms,
        )


@dataclass(frozen=True, slots=True)
class ExactCookReplay:
    fixture: CookFixture
    metadata: CookMetadata
    samples: tuple[ChamberSample, ...]
    sessions: tuple[ReplaySession, ...]
    transitions: tuple[ReplayTransition, ...]
    frames: tuple[ExactReplayFrame, ...]


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _require_fields(value: object, expected: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(f"{context} fields differ: {actual!r}")
    return value


def _require_digest(path: Path, expected: str) -> bytes:
    value = path.read_bytes()
    actual = hashlib.sha256(value).hexdigest()
    if actual != expected:
        raise FixtureDigestMismatch(f"{path.name}: expected {expected}, got {actual}")
    return value


def _read_manifest_rows() -> tuple[dict[str, Any], bytes]:
    path = REAL_COOK_FIXTURE_ROOT / "manifest.json"
    raw = path.read_bytes()
    decoded = json.loads(raw)
    root = _require_fields(decoded, _MANIFEST_FIELDS, "manifest")
    if raw != _canonical_json(root):
        raise ValueError("manifest is not canonical JSON")
    if root["schema_version"] != 1 or not isinstance(root["campaigns"], list):
        raise ValueError("unsupported real-cook manifest schema")
    return root, raw


def _fixture_cook_id(path: Path, expected_digest: str) -> str:
    _require_digest(path, expected_digest)
    with zipfile.ZipFile(path) as archive:
        metadata = json.loads(archive.read("metadata.json"))
    return str(_require_fields(metadata, _METADATA_FIELDS, f"{path.name}:metadata")["cook_id"])


def load_real_cook_manifest() -> RealCookManifest:
    root, _raw = _read_manifest_rows()
    campaigns: list[CampaignFixture] = []
    seen_campaigns: set[str] = set()
    for campaign_index, raw_campaign_value in enumerate(root["campaigns"]):
        raw_campaign = _require_fields(raw_campaign_value, _CAMPAIGN_FIELDS, f"campaign[{campaign_index}]")
        campaign_id = str(raw_campaign["id"])
        if campaign_id in seen_campaigns:
            raise ValueError(f"duplicate campaign: {campaign_id}")
        seen_campaigns.add(campaign_id)
        controller = raw_campaign["controller"]
        if controller not in {"mpc", "pid_sp"}:
            raise ValueError(f"unsupported controller: {controller!r}")
        raw_baseline = _require_fields(raw_campaign["baseline"], _BASELINE_FIELDS, f"{campaign_id}:baseline")
        baseline = BaselineFixture(
            path=str(raw_baseline["path"]),
            sanitized_sha256=str(raw_baseline["sanitized_sha256"]),
            schema_version=int(raw_baseline["schema_version"]),
            cutoff_wall_ms=int(raw_baseline["cutoff_wall_ms"]),
            source_archive_sha256=str(raw_baseline["source_archive_sha256"]),
            source_database_sha256=str(raw_baseline["source_database_sha256"]),
        )
        _require_digest(REAL_COOK_FIXTURE_ROOT / baseline.path, baseline.sanitized_sha256)
        raw_cooks = raw_campaign["cooks"]
        if not isinstance(raw_cooks, list) or not raw_cooks:
            raise ValueError(f"{campaign_id} has no cooks")
        cooks: list[CookFixture] = []
        for cook_index, raw_cook_value in enumerate(raw_cooks):
            raw_cook = _require_fields(raw_cook_value, _COOK_FIELDS, f"{campaign_id}:cook[{cook_index}]")
            replay_kind = raw_cook["replay_kind"]
            if replay_kind not in {"exact-evidence", "thermal-smoke-only"}:
                raise ValueError(f"unsupported replay kind: {replay_kind!r}")
            path = str(raw_cook["path"])
            digest = str(raw_cook["sanitized_sha256"])
            fixture_path = REAL_COOK_FIXTURE_ROOT / path
            fixture_cook_id = _fixture_cook_id(fixture_path, digest)
            raw_counts = raw_cook["source_outcome_counts"]
            counts = None if raw_counts is None else {str(key): int(value) for key, value in raw_counts.items()}
            cooks.append(
                CookFixture(
                    path=path,
                    sanitized_sha256=digest,
                    source_sha256=str(raw_cook["source_sha256"]),
                    replay_kind=replay_kind,
                    cook_start_ms=int(raw_cook["cook_start_ms"]),
                    controller=controller,
                    cook_end_ms=int(raw_cook["cook_end_ms"]),
                    expected_input_frame_count=(
                        None
                        if raw_cook["expected_input_frame_count"] is None
                        else int(raw_cook["expected_input_frame_count"])
                    ),
                    trace_schema_version=(
                        None if raw_cook["trace_schema_version"] is None else int(raw_cook["trace_schema_version"])
                    ),
                    source_outcome_counts=counts,
                    blocked_reason=None if raw_cook["blocked_reason"] is None else str(raw_cook["blocked_reason"]),
                    fixture_cook_id=fixture_cook_id,
                )
            )
        campaigns.append(CampaignFixture(campaign_id, controller, baseline, tuple(cooks)))
    return RealCookManifest(schema_version=1, campaigns=tuple(campaigns))


def load_campaign_baseline(campaign_id: str, path: Path | None = None) -> BaselineCopy:
    campaign = load_real_cook_manifest().campaign(campaign_id)
    baseline_path = REAL_COOK_FIXTURE_ROOT / campaign.baseline.path if path is None else path
    _require_digest(baseline_path, campaign.baseline.sanitized_sha256)
    connection = sqlite3.connect(f"file:{baseline_path}?mode=ro", uri=True)
    try:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
        expected_tables = tuple(sorted((*_BASELINE_TABLES, "fixture_metadata")))
        if tables != expected_tables:
            raise ValueError(f"{campaign_id} baseline tables differ: {tables!r}")
        metadata = connection.execute(
            "SELECT campaign_id,retained_state FROM fixture_metadata WHERE singleton=1"
        ).fetchone()
        table_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in _BASELINE_TABLES
        }
    finally:
        connection.close()
    if schema_version != campaign.baseline.schema_version:
        raise ValueError(f"{campaign_id} baseline schema mismatch")
    if metadata != (campaign_id, "factory-fallback"):
        raise ValueError(f"{campaign_id} baseline identity mismatch")
    if any(table_counts.values()):
        raise ValueError(f"{campaign_id} baseline retains campaign rows")
    return BaselineCopy(baseline_path, campaign_id, schema_version, "factory-fallback", table_counts)


def copy_campaign_baseline(campaign_id: str, destination: Path) -> BaselineCopy:
    campaign = load_real_cook_manifest().campaign(campaign_id)
    source = REAL_COOK_FIXTURE_ROOT / campaign.baseline.path
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return load_campaign_baseline(campaign_id, destination)


def _read_archive(cook: CookFixture) -> dict[str, Any]:
    path = REAL_COOK_FIXTURE_ROOT / cook.path
    _require_digest(path, cook.sanitized_sha256)
    expected_members = _EXACT_ARCHIVE_MEMBERS if cook.replay_kind == "exact-evidence" else _THERMAL_ARCHIVE_MEMBERS
    with zipfile.ZipFile(path) as archive:
        if tuple(archive.namelist()) != expected_members:
            raise ValueError(f"{cook.path} archive members differ")
        decoded: dict[str, Any] = {}
        for member in expected_members:
            raw = archive.read(member)
            value = json.loads(raw)
            if raw != _canonical_json(value):
                raise ValueError(f"{cook.path}:{member} is not canonical JSON")
            decoded[member] = value
    return decoded


def _decode_source_outcome(value: object, context: str) -> SourceTerminalOutcome:
    if not isinstance(value, dict):
        raise TypeError(f"{context} is not an object")
    kind = value.get("kind")
    if kind in {"accepted", "rejected"}:
        if set(value) != {"kind", "reasons"} or not isinstance(value["reasons"], list):
            raise ValueError(f"{context} accepted/rejected shape differs")
        return SourceTerminalOutcome(kind=kind, reasons=tuple(str(reason) for reason in value["reasons"]))
    if kind == "gap":
        if set(value) != {"kind", "lost_record_count", "reason"}:
            raise ValueError(f"{context} gap shape differs")
        return SourceTerminalOutcome(
            kind="gap",
            reason=str(value["reason"]),
            lost_record_count=int(value["lost_record_count"]),
        )
    if kind == "unmatched":
        if set(value) != {"kind", "reason"}:
            raise ValueError(f"{context} unmatched shape differs")
        return SourceTerminalOutcome(kind="unmatched", reason=str(value["reason"]))
    raise ValueError(f"{context} has unsupported kind {kind!r}")


def _optional_duty(value: object, name: str) -> float | None:
    if value is None:
        return None
    duty = float(value)
    if not 0.0 <= duty <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return duty


def _decode_exact_frame(value: object, *, metadata: CookMetadata, sessions: set[str], index: int) -> ExactReplayFrame:
    row = _require_fields(value, _FRAME_FIELDS, f"{metadata.cook_id}:frame[{index}]")
    if (
        row["controller"] != metadata.controller
        or row["cook_id"] != metadata.cook_id
        or row["session_id"] not in sessions
        or row["schema_version"] != metadata.trace_schema_version
        or row["trace_sequence"] != index + 1
        or row["observation_sequence"] != index + 1
    ):
        raise ValueError(f"{metadata.cook_id}:frame[{index}] identity mismatch")
    start_ms = int(row["frame_start_ms"])
    end_ms = int(row["frame_end_ms"])
    if start_ms >= end_ms:
        raise ValueError(f"{metadata.cook_id}:frame[{index}] boundary invalid")
    duration_s = (end_ms - start_ms) / 1_000
    delivered_on_s = float(row["delivered_on_seconds"])
    scheduled_on_s = float(row["scheduled_on_seconds"])
    if delivered_on_s > duration_s + 1e-9 or scheduled_on_s > float(row["frame_seconds"]) + 1e-9:
        raise ValueError(f"{metadata.cook_id}:frame[{index}] duty interval invalid")
    boundary_value = row["boundary_reason"]
    boundary_reason = None
    if boundary_value is not None:
        if not isinstance(boundary_value, dict) or set(boundary_value) != {"kind", "reason"}:
            raise ValueError(f"{metadata.cook_id}:frame[{index}] boundary reason invalid")
        boundary_reason = (str(boundary_value["kind"]), str(boundary_value["reason"]))
    # Sanitization maps the first source generation to fixture generation zero.
    # The frame schema is generation-local and the exact archives contain no
    # generation rotation, so every joined observation belongs to generation 0.
    role_generation = 0
    observation = FrameObservation(
        frame_start_s=start_ms / 1_000,
        frame_end_s=end_ms / 1_000,
        temp_c=float(row["chamber_temperature_c"]),
        setpoint_c=float(row["setpoint_c"]),
        ambient_c=float(row["ambient_c"]),
        requested_q=float(row["requested_combustion_load"]),
        realized_q=float(row["realized_combustion_load"]),
        baseline_q=float(row["requested_combustion_load"]),
        allocated_q=float(row["allocated_combustion_load"]),
        requested_auger_duty=float(row["requested_auger_duty"]),
        scheduled_on_s=scheduled_on_s,
        delivered_on_s=delivered_on_s,
        realized_auger_duty=float(row["realized_auger_duty"]),
        requested_fan_duty=_optional_duty(row["requested_fan_duty"], "requested_fan_duty"),
        actual_fan_duty=_optional_duty(row["actual_fan_duty"], "actual_fan_duty"),
        result_revision=int(row["result_revision"]),
        output_source=str(row["output_source"]),
        lid_open=row["lid_open"] is True,
        safety_inhibited=row["safety_inhibited"] is True,
        manual_override=row["manual_override"] is True,
        stale=row["stale"] is True,
        skipped=row["skipped"] is True,
        reset=row["reset"] is True,
        continuous=row["continuous"] is True and row["interval_contiguous"] is True,
        role_generation=role_generation,
        observation_sequence=int(row["observation_sequence"]),
        probe_source=f"{metadata.cook_id}:sanitized-chamber",
        ambient_source=AmbientSource.CONFIGURED,
        ambient_uncertainty=AmbientUncertainty.UNMEASURED,
        allocator_revision=None if row["allocator_revision"] is None else int(row["allocator_revision"]),
    )
    return ExactReplayFrame(
        trace_sequence=int(row["trace_sequence"]),
        session_id=str(row["session_id"]),
        cook_id=str(row["cook_id"]),
        controller=str(row["controller"]),
        frame_start_ms=start_ms,
        frame_end_ms=end_ms,
        result_revision=int(row["result_revision"]),
        observation_sequence=int(row["observation_sequence"]),
        role_generation=role_generation,
        source_outcome=_decode_source_outcome(row["source_outcome"], f"{metadata.cook_id}:frame[{index}]:outcome"),
        boundary_reason=boundary_reason,
        interval_contiguous=row["interval_contiguous"] is True,
        sample_complete=row["sample_complete"] is True,
        observation=observation,
    )


def load_replay_cook(cook: CookFixture) -> ExactCookReplay:
    decoded = _read_archive(cook)
    raw_metadata = _require_fields(decoded["metadata.json"], _METADATA_FIELDS, f"{cook.path}:metadata")
    raw_counts = raw_metadata["source_outcome_counts"]
    if raw_counts is None:
        source_counts = None
    elif isinstance(raw_counts, dict):
        source_counts = {str(key): int(value) for key, value in raw_counts.items()}
    else:
        raise ValueError(f"{cook.path} source counts are invalid")
    trace_schema_version = (
        None if raw_metadata["trace_schema_version"] is None else int(raw_metadata["trace_schema_version"])
    )
    if (
        raw_metadata["schema_version"] != 1
        or raw_metadata["units"] != "F"
        or str(raw_metadata["cook_id"]) != cook.fixture_cook_id
        or str(raw_metadata["controller"]) != cook.controller
        or str(raw_metadata["replay_kind"]) != cook.replay_kind
        or int(raw_metadata["cook_start_ms"]) != cook.cook_start_ms
        or int(raw_metadata["cook_end_ms"]) != cook.cook_end_ms
        or trace_schema_version != cook.trace_schema_version
        or source_counts != cook.source_outcome_counts
        or raw_metadata["blocked_reason"] != cook.blocked_reason
    ):
        raise ValueError(f"{cook.path} metadata differs from manifest")
    raw_samples = decoded["chamber_samples.json"]
    if not isinstance(raw_samples, list) or not raw_samples:
        raise ValueError(f"{cook.path} chamber samples are not a non-empty list")
    samples = tuple(
        ChamberSample(
            timestamp_ms=int(row["timestamp_ms"]),
            chamber_temperature_f=float(row["chamber_temperature_f"]),
            setpoint_f=float(row["setpoint_f"]),
        )
        for index, value in enumerate(raw_samples)
        for row in (_require_fields(value, _CHAMBER_SAMPLE_FIELDS, f"{cook.path}:sample[{index}]"),)
    )
    if (
        len(samples) != int(raw_metadata["chamber_sample_count"])
        or samples[0].timestamp_ms != cook.cook_start_ms
        or samples[-1].timestamp_ms != cook.cook_end_ms
        or any(left.timestamp_ms > right.timestamp_ms for left, right in pairwise(samples))
    ):
        raise ValueError(f"{cook.path} chamber sample order differs")
    if cook.replay_kind != "exact-evidence":
        raise ExactReplayBlocked(cook)
    if trace_schema_version is None or source_counts is None:
        raise ValueError(f"{cook.path} has no exact metadata")
    metadata = CookMetadata(
        cook_id=cook.fixture_cook_id,
        controller=cook.controller,
        replay_kind=cook.replay_kind,
        cook_start_ms=cook.cook_start_ms,
        cook_end_ms=cook.cook_end_ms,
        chamber_sample_count=len(samples),
        trace_schema_version=trace_schema_version,
        source_outcome_counts=source_counts,
    )
    raw_sessions = decoded["sessions.json"]
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise ValueError(f"{cook.path} has no trace sessions")
    sessions: list[ReplaySession] = []
    for index, value in enumerate(raw_sessions):
        row = _require_fields(value, _SESSION_RECORD_FIELDS, f"{cook.path}:session[{index}]")
        payload = _require_fields(row["payload"], _SESSION_PAYLOAD_FIELDS, f"{cook.path}:session[{index}]:payload")
        raw_config = payload["controller_config"]
        if not isinstance(raw_config, list) or any(
            not isinstance(item, dict) or set(item) != {"key", "value"} for item in raw_config
        ):
            raise ValueError(f"{cook.path}:session[{index}] controller config invalid")
        sessions.append(
            ReplaySession(
                session_id=str(row["session_id"]),
                cook_id=str(row["cook_id"]),
                controller=str(row["controller"]),
                timestamp_ms=int(row["timestamp_ms"]),
                schema_version=int(row["schema_version"]),
                controller_config=tuple((str(item["key"]), item["value"]) for item in raw_config),
                temperature_unit=str(payload["temperature_unit"]),
                control_period_seconds=float(payload["control_period_seconds"]),
                pulse_slot_seconds=float(payload["pulse_slot_seconds"]),
                pulse_frame_seconds=float(payload["pulse_frame_seconds"]),
                fan_authority=payload["fan_authority"] is True,
                fan_pwm_capable=payload["fan_pwm_capable"] is True,
                fan_min_duty=float(payload["fan_min_duty"]),
                fan_max_duty=float(payload["fan_max_duty"]),
                setpoint=float(payload["setpoint"]),
                ambient_temperature=float(payload["ambient_temperature"]),
                model_revision=None if payload["model_revision"] is None else int(payload["model_revision"]),
                model_provenance=None if payload["model_provenance"] is None else str(payload["model_provenance"]),
            )
        )
    if any(
        session.cook_id != metadata.cook_id
        or session.controller != metadata.controller
        or session.schema_version != metadata.trace_schema_version
        for session in sessions
    ):
        raise ValueError(f"{cook.path} session identity differs")
    raw_transitions = decoded["transitions.json"]
    if not isinstance(raw_transitions, list):
        raise TypeError(f"{cook.path} transitions are not a list")
    session_ids = {session.session_id for session in sessions}
    transitions = tuple(
        ReplayTransition(
            timestamp_ms=int(row["timestamp_ms"]),
            session_id=str(row["session_id"]),
            cook_id=str(row["cook_id"]),
            result_revision=int(row["result_revision"]),
            setpoint_c=float(row["setpoint_c"]),
            output_source=str(row["output_source"]),
            actuation_mode=str(row["actuation_mode"]),
            policy_kind=str(row["policy_kind"]),
        )
        for index, value in enumerate(raw_transitions)
        for row in (_require_fields(value, _TRANSITION_FIELDS, f"{cook.path}:transition[{index}]"),)
    )
    if any(
        transition.session_id not in session_ids or transition.cook_id != metadata.cook_id for transition in transitions
    ):
        raise ValueError(f"{cook.path} transition identity differs")
    raw_frames = decoded["frames.json"]
    if not isinstance(raw_frames, list):
        raise TypeError(f"{cook.path} frames are not a list")
    frames = tuple(
        _decode_exact_frame(value, metadata=metadata, sessions=session_ids, index=index)
        for index, value in enumerate(raw_frames)
    )
    if len(frames) != cook.expected_input_frame_count:
        raise ValueError(f"{cook.path} frame count differs")
    counts = {kind: 0 for kind in ("accepted", "gap", "rejected", "unmatched")}
    for frame in frames:
        counts[frame.source_outcome.kind] += 1
    if counts != metadata.source_outcome_counts:
        raise ValueError(f"{cook.path} source terminal accounting differs")
    return ExactCookReplay(cook, metadata, samples, tuple(sessions), transitions, frames)


class _CampaignLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def debug(self, _message: str) -> None:
        pass

    def info(self, _message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def error(self, message: str) -> None:
        self.errors.append(message)


@dataclass(frozen=True, slots=True)
class ScientificBlocker:
    code: str
    cook_path: str
    detail: str


@dataclass(frozen=True, slots=True)
class MpcCookReplayResult:
    cook_path: str
    replay_kind: str
    typed_outcome: str
    detail: str
    input_frame_count: int
    terminal_count: int
    joined_terminal_count: int
    source_outcome_counts: dict[str, int]
    production_outcome_counts: dict[str, int]
    diagnostic_outcome_counts: dict[str, int]
    terminal_reasons: tuple[str, ...]
    segment_delta: int
    pre_roll_delta: int
    scored_delta: int
    pending_observation_count: int
    open_segment_count: int
    live_fit_worker_count: int
    quarantined_segment_ids: tuple[str, ...]
    corpus_digest: str
    cold_corpus_digest: str
    lifecycle: bytes
    cold_lifecycle: bytes


@dataclass(frozen=True, slots=True)
class MpcCampaignReplayResult:
    campaign_id: str
    cooks: tuple[MpcCookReplayResult, ...]
    terminal_reason: TrajectoryBreakReason
    restart_count: int
    corpus_digest: str
    cold_corpus_digest: str
    lifecycle: bytes
    cold_lifecycle: bytes
    final_open_segment_count: int
    final_live_fit_worker_count: int
    unauthorized_activation_count: int
    scientific_blockers: tuple[ScientificBlocker, ...]
    canonical_corpus: bytes
    canonical_fit_requests: bytes
    canonical_assessments: bytes
    canonical_lifecycle: bytes
    canonical_evidence: bytes
    canonical_state: bytes
    primary_identities: tuple[str, ...]

    @property
    def exact_cooks(self) -> tuple[MpcCookReplayResult, ...]:
        return tuple(cook for cook in self.cooks if cook.replay_kind == "exact-evidence")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest_value(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _session_for_first_frame(replay: ExactCookReplay) -> ReplaySession:
    session_id = replay.frames[0].session_id
    return next(session for session in replay.sessions if session.session_id == session_id)


def _mpc_config(session: ReplaySession) -> dict[str, Any]:
    config: dict[str, Any] = dict(DEFAULT_MPC_CONFIG)
    config.update(dict(session.controller_config))
    config["enable_identification"] = True
    config["enable_online_adaptation"] = True
    return config


def _compatibility_digest(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def replay_compatibility_digests(replay: ExactCookReplay) -> tuple[str, ...]:
    """Return production-shaped compatibility inputs without source identity."""
    session = _session_for_first_frame(replay)
    config = _mpc_config(session)
    return (
        _compatibility_digest(
            {
                "schema": "sanitized-replay-cadence-v1",
                "control_period_seconds": session.control_period_seconds,
                "pulse_slot_seconds": session.pulse_slot_seconds,
                "pulse_frame_seconds": session.pulse_frame_seconds,
            }
        ),
        _compatibility_digest(
            {
                "schema": "sanitized-replay-model-structure-v1",
                "controller": "mpc",
                "structure": config,
            }
        ),
        _compatibility_digest(
            {
                "schema": "sanitized-replay-held-physics-v1",
                "configuration": config,
            }
        ),
        _compatibility_digest(
            {
                "schema": "normalized-combustion-load-v1",
                "delay_seconds": config.get("theta"),
            }
        ),
        _compatibility_digest(
            {
                "schema": "sanitized-replay-framed-pulse-v1",
                "pulse_slot_seconds": session.pulse_slot_seconds,
                "pulse_frame_seconds": session.pulse_frame_seconds,
                "u_min": 0.1,
                "u_max": 0.9,
            }
        ),
        _compatibility_digest(
            {
                "schema": "sanitized-replay-fan-regime-v1",
                "fan_authority": session.fan_authority,
                "fan_pwm_capable": session.fan_pwm_capable,
                "fan_min_duty": session.fan_min_duty,
                "fan_max_duty": session.fan_max_duty,
            }
        ),
        _compatibility_digest(
            {
                "schema": "configured-ambient-celsius-v1",
                "source": "configured",
                "T_amb": config.get("T_amb", 20.0),
            }
        ),
    )


def _trace_context(replay: ExactCookReplay, session: ReplaySession, config: dict[str, Any]) -> TraceSessionContext:
    return TraceSessionContext(
        controller=ControllerType.MPC,
        controller_config=config,
        temperature_unit=session.temperature_unit,
        control_period_seconds=session.control_period_seconds,
        fallback_model=None,
        runner_snapshot_fallback_safe=True,
        pulse_slot_seconds=session.pulse_slot_seconds,
        pulse_frame_seconds=session.pulse_frame_seconds,
        fan_authority=session.fan_authority,
        fan_pwm_capable=session.fan_pwm_capable,
        fan_min_duty=session.fan_min_duty,
        fan_max_duty=session.fan_max_duty,
        setpoint=session.setpoint,
        ambient_temperature=session.ambient_temperature,
        software_version="sanitized-fixture",
        build_version="sanitized-fixture",
        cook_id=replay.metadata.cook_id,
        runner_generation=0,
    )


def _mode_entered(replay: ExactCookReplay, session: ReplaySession, config: dict[str, Any]) -> ModeEntered:
    start_ms = replay.frames[0].frame_start_ms
    (
        cadence_digest,
        model_structure_digest,
        held_physics_digest,
        delay_input_mapping_digest,
        actuation_mapping_digest,
        scored_fan_regime_digest,
        ambient_semantics_digest,
    ) = replay_compatibility_digests(replay)
    return ModeEntered(
        effective_mode="Hold",
        persisted_mode="Hold",
        monotonic_ms=start_ms,
        wall_ms=start_ms,
        cook_id=replay.metadata.cook_id,
        trajectory_session_id=f"{replay.metadata.cook_id}-trajectory",
        trace_session_id="",
        recipe_step_id=None,
        units="C",
        settings_revision=1,
        collection_provenance={"source_kind": "sanitized-real-cook-exact-evidence"},
        configuration_provenance=config,
        cadence_digest=cadence_digest,
        model_structure_digest=model_structure_digest,
        held_physics_digest=held_physics_digest,
        delay_input_mapping_digest=delay_input_mapping_digest,
        actuation_mapping_digest=actuation_mapping_digest,
        scored_fan_regime_digest=scored_fan_regime_digest,
        ambient_semantics_digest=ambient_semantics_digest,
        source_trace_digest=replay.fixture.sanitized_sha256,
        source_schema_version=replay.metadata.trace_schema_version,
        source_row_digest=_digest_value(f"{replay.fixture.sanitized_sha256}:frames"),
        build_provenance={"kind": "sanitized-fixture", "campaign_controller": session.controller},
        auger_duty_ceiling=0.9,
    )


def _initial_thermal_sample(replay: ExactCookReplay) -> ThermalSample:
    frame = replay.frames[0]
    return ThermalSample(
        monotonic_ms=frame.frame_start_ms,
        wall_ms=frame.frame_start_ms,
        chamber_temperature=frame.observation.temp_c,
        units="C",
        probe_valid=True,
        probe_source=frame.observation.probe_source,
        ambient_temperature=frame.observation.ambient_c,
        ambient_source=frame.observation.ambient_source.value,
        ambient_uncertainty=0.0,
        settings_revision=1,
    )


def _canonical_lifecycle(connection: sqlite3.Connection) -> bytes:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_activation_state'"
    ).fetchone()
    if exists is None:
        return b"[]"
    columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(model_activation_state)"))
    rows = tuple(
        dict(zip(columns, row, strict=True))
        for row in connection.execute("SELECT * FROM model_activation_state ORDER BY singleton")
    )
    return _json_bytes(rows)


def _canonical_segments(repository: LearningTrajectoryRepository, cook_ids: tuple[str, ...]) -> bytes:
    segments = tuple(segment for cook_id in cook_ids for segment in repository.read_cook_segments(cook_id))
    return _json_bytes(tuple(trajectory_json_value(segment) for segment in segments))


def _corpus_digest(canonical_corpus: bytes) -> str:
    return hashlib.sha256(canonical_corpus).hexdigest()


def _normalized_table_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    columns = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
    retained_columns = tuple(column for column in columns if column != "id")
    if not retained_columns:
        return []
    projection = ",".join(f'"{column}"' for column in retained_columns)
    rows = [
        dict(zip(retained_columns, row, strict=True))
        for row in connection.execute(f'SELECT {projection} FROM "{table}"')
    ]
    rows.sort(key=lambda row: _json_bytes(row))
    return rows


def _canonical_database_state(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND (name='control_trace' OR name LIKE 'learning_%' "
                "OR name LIKE 'model_%') ORDER BY name"
            )
        )
        state: dict[str, object] = {table: _normalized_table_rows(connection, table) for table in tables}
        model_state = connection.execute(
            "SELECT value FROM kv WHERE key=?",
            (MODEL_STATE_KEY,),
        ).fetchone()
        state[MODEL_STATE_KEY] = None if model_state is None else json.loads(model_state[0])
        return _json_bytes(state)
    finally:
        connection.close()


def _canonical_selected_table(path: Path, table: str) -> bytes:
    connection = sqlite3.connect(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return b"[]" if exists is None else _json_bytes(_normalized_table_rows(connection, table))
    finally:
        connection.close()


def _canonical_fit_requests(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_fit_run'"
        ).fetchone()
        if exists is None:
            return b"[]"
        columns = (
            "request_id",
            "status",
            "fit_partition_digest",
            "corpus_revision",
            "corpus_digest",
            "parent_incumbent_digest",
            "parent_incumbent_generation",
            "candidate_generation",
            "trigger_origin",
            "candidate_digest",
            "result_error",
        )
        projection = ",".join(columns)
        rows = [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(f"SELECT {projection} FROM learning_fit_run ORDER BY request_id")
        ]
        return _json_bytes(rows)
    finally:
        connection.close()


def _canonical_assessments(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        rows = [
            {"evidence_id": row[0], "payload": json.loads(row[1])}
            for row in connection.execute(
                "SELECT evidence_id,payload FROM model_evidence WHERE kind='candidate_assessment' ORDER BY evidence_id"
            )
        ]
        return _json_bytes(rows)
    finally:
        connection.close()


def _primary_identities(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(path)
    try:
        identities: list[str] = []
        specs = (
            ("control_trace", "session_id", "trace-session"),
            ("learning_trajectory_segment", "segment_id", "trajectory-segment"),
            ("model_evidence", "evidence_id", "model-evidence"),
            ("learning_fit_run", "request_id", "fit-request"),
        )
        for table, column, label in specs:
            if (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone()
                is None
            ):
                continue
            identities.extend(
                f"{label}:{row[0]}"
                for row in connection.execute(f'SELECT DISTINCT "{column}" FROM "{table}" ORDER BY "{column}"')
            )
        return tuple(identities)
    finally:
        connection.close()


def _lifecycle_bytes(path: Path) -> bytes:
    connection = sqlite3.connect(path)
    try:
        return _canonical_lifecycle(connection)
    finally:
        connection.close()


def _quarantined_segment_ids(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(path)
    try:
        if (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_trajectory_segment'"
            ).fetchone()
            is None
        ):
            return ()
        return tuple(
            row[0]
            for row in connection.execute(
                "SELECT segment_id FROM learning_trajectory_segment WHERE state='quarantined' ORDER BY segment_id"
            )
        )
    finally:
        connection.close()


def _production_terminal_counts(
    cook_id: str,
) -> tuple[dict[str, int], set[tuple[int, int, int, int, int]], tuple[str, ...]]:
    counts = {"accepted": 0, "gap": 0, "rejected": 0}
    identities: set[tuple[int, int, int, int, int]] = set()
    reasons: list[str] = []
    for record in read_control_trace_cook(cook_id):
        payload = record.payload
        if record.event_kind is TraceEventKind.MODEL_OBSERVATION:
            eligible = getattr(payload, "eligible", False)
            counts["accepted" if eligible else "rejected"] += 1
            rejection_reasons = tuple(getattr(payload, "rejection_reasons", ()))
            reasons.append("accepted" if eligible else ",".join(str(reason) for reason in rejection_reasons))
            identities.add(
                (
                    int(payload.frame_start_ms),
                    int(payload.frame_end_ms),
                    int(payload.result_revision),
                    int(payload.observation_sequence),
                    int(payload.role_generation),
                )
            )
        elif record.event_kind is TraceEventKind.RECORDER_GAP:
            counts["gap"] += 1
            reasons.append(str(payload.reason))
            if (
                payload.frame_start_ms is not None
                and payload.frame_end_ms is not None
                and payload.result_revision is not None
                and payload.observation_sequence is not None
            ):
                identities.add(
                    (
                        int(payload.frame_start_ms),
                        int(payload.frame_end_ms),
                        int(payload.result_revision),
                        int(payload.observation_sequence),
                        0,
                    )
                )
    return counts, identities, tuple(reasons)


def _cold_restart_mpc(
    database_path: Path,
    config: dict[str, Any],
    partition_digest: str | None,
    timestamp_ms: int,
    logger: _CampaignLogger,
) -> int:
    datastore._reset_for_tests(str(database_path))
    datastore.init()
    repository = LearningTrajectoryRepository(str(database_path))
    store = ControllerModelStore()
    persistence = ModelPersistenceWorker(store, logger, trajectory_repository=repository)
    core = MpcController(
        config,
        "C",
        {"u_min": 0.1, "u_max": 0.9},
        activation_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition_digest,
    )
    runner = SyncControllerRunner(core, controller_type=ControllerType.MPC)
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=store,
        persistence=persistence,
        trajectory_repository=repository,
        trace=None,
        controller_name="mpc",
        logger=logger,
        initial_generation=0,
    )
    try:
        learning.restore_model(timestamp_ms=timestamp_ms)
        learning.reconcile_activation()
    finally:
        if not learning.barrier_for_teardown(generation=0):
            raise RuntimeError("cold-restart learning teardown barrier failed")
        learning.finish_teardown(generation=0)
        runner.stop()
        if not persistence.close(timeout=30.0):
            raise RuntimeError("cold-restart model persistence close failed")
    worker = getattr(runner, "_corpus_fit_thread", None)
    return int(worker is not None and worker.is_alive())


def _run_exact_mpc_cook(
    replay: ExactCookReplay,
    *,
    database_path: Path,
    prior_report: object,
    all_cook_ids: tuple[str, ...],
    reconcile_each_frame: bool,
    evidence_available_before_submission: bool,
    terminal_reason: TrajectoryBreakReason,
) -> MpcCookReplayResult:
    datastore._reset_for_tests(str(database_path))
    datastore.init()
    logger = _CampaignLogger()
    session = _session_for_first_frame(replay)
    config = _mpc_config(session)
    clock_ms = [replay.frames[0].frame_start_ms]
    repository = LearningTrajectoryRepository(str(database_path))
    store = ControllerModelStore()
    persistence = ModelPersistenceWorker(store, logger, trajectory_repository=repository)
    partition_digest: list[str | None] = [None]
    core = MpcController(
        config,
        "C",
        {"u_min": 0.1, "u_max": 0.9},
        activation_persistence=persistence,
        trajectory_repository=repository,
        fit_partition_digest=lambda: partition_digest[0],
    )
    runner = SyncControllerRunner(core, controller_type=ControllerType.MPC)
    recorder = ControlTraceRecorder(monotonic_clock=lambda: clock_ms[0], wall_clock=lambda: clock_ms[0])
    trace = ControlTraceSession(
        recorder,
        warning=logger.warning,
        session_id_factory=lambda: uuid5(NAMESPACE_URL, f"pifire:{replay.frames[0].session_id}"),
    )
    identity = trace.ensure_open(
        _trace_context(replay, session, config),
        timestamp_ms=replay.frames[0].frame_start_ms,
    )
    if identity is None:
        raise RuntimeError("deterministic fixture trace identity was rejected")
    segment_number = [0]

    def next_segment_id() -> str:
        segment_number[0] += 1
        return f"{replay.metadata.cook_id}-segment-{segment_number[0]:03d}"

    journal = ActuationDeliveryJournal(
        monotonic_clock=lambda: clock_ms[0],
        wall_clock=lambda: clock_ms[0],
    )
    trajectory = LearningTrajectoryRuntime(
        journal=journal,
        persistence=persistence,
        segment_id_factory=next_segment_id,
        trajectory_session_id_factory=lambda: f"{replay.metadata.cook_id}-trajectory",
    )
    if not trajectory.bind_trace_session(
        identity.session_id,
        identity.cook_id,
        trace.trajectory_segment_publisher(identity),
    ):
        raise RuntimeError("fixture trajectory identity binding failed")
    trajectory.mode_entered(_mode_entered(replay, session, config))
    trajectory.observe_temperature(_initial_thermal_sample(replay))
    learning = HoldLearningRuntime(
        runner=runner,
        model_store=store,
        persistence=persistence,
        trajectory_repository=repository,
        trace=trace,
        controller_name="mpc",
        logger=logger,
        initial_generation=0,
        learning_trajectory=trajectory,
    )
    learning.bind_generation(0)
    learning.restore_model(timestamp_ms=replay.frames[0].frame_start_ms)
    learning.reconcile_activation()
    if not evidence_available_before_submission:
        learning.mark_evidence_unavailable()
    for frame in replay.frames:
        clock_ms[0] = frame.frame_end_ms
        observation = frame.observation
        learning.submit_completed_observation(
            (frame.frame_start_ms, frame.frame_end_ms),
            observation,
        )
        if reconcile_each_frame:
            learning.reconcile_outcomes(frame.frame_end_ms / 1_000)
            if not trajectory.barrier(timeout=10.0):
                raise RuntimeError(f"trajectory persistence failed: {trajectory.status()!r}")
            if not persistence.barrier(timeout=10.0):
                raise RuntimeError("model persistence barrier failed")
            segments = repository.read_cook_segments(replay.metadata.cook_id)
            if segments:
                partition_digest[0] = segments[-1].fit_partition_digest
    if not reconcile_each_frame:
        learning.reconcile_outcomes(replay.frames[-1].frame_end_ms / 1_000)
        if not trajectory.barrier(timeout=30.0):
            raise RuntimeError(f"trajectory delayed reconciliation failed: {trajectory.status()!r}")
        if not persistence.barrier(timeout=30.0):
            raise RuntimeError("model persistence delayed reconciliation barrier failed")
        segments = repository.read_cook_segments(replay.metadata.cook_id)
        if segments:
            partition_digest[0] = segments[-1].fit_partition_digest

    terminal_ms = replay.frames[-1].frame_end_ms
    if not runner.stop_and_retain_for_teardown():
        raise RuntimeError("runner teardown retention failed")
    trajectory.mode_exited(
        ModeExited(
            effective_mode="Hold",
            next_effective_mode="Error" if terminal_reason is TrajectoryBreakReason.ERROR else "Stop",
            monotonic_ms=terminal_ms,
            wall_ms=terminal_ms,
            reason=terminal_reason,
        )
    )
    if not trajectory.barrier(timeout=30.0):
        raise RuntimeError(f"trajectory stop barrier failed: {trajectory.status()!r}")
    if not learning.barrier_for_teardown(generation=0):
        raise RuntimeError("learning teardown barrier failed")
    if terminal_reason is TrajectoryBreakReason.STOP:
        stop_fit_scheduled = learning.schedule_stop_fit(
            {"controller": {"config": {"mpc": {"enable_identification": True, "enable_online_adaptation": True}}}}
        )
        expected_stop_fit_schedule = reconcile_each_frame and evidence_available_before_submission
        if stop_fit_scheduled is not expected_stop_fit_schedule:
            raise RuntimeError("production stop-fit scheduling did not match retained evidence")
    learning.finish_teardown(generation=0)
    if not persistence.barrier(timeout=30.0):
        raise RuntimeError("final model persistence barrier failed")
    runner.stop()
    if not persistence.close(timeout=30.0):
        raise RuntimeError("model persistence close failed")
    worker = getattr(runner, "_corpus_fit_thread", None)
    live_workers = int(worker is not None and worker.is_alive())

    report = repository.corpus_report()
    canonical_corpus = _canonical_segments(repository, all_cook_ids)
    corpus_digest = _corpus_digest(canonical_corpus)
    lifecycle = _lifecycle_bytes(database_path)
    production_counts, produced_identities, terminal_reasons = _production_terminal_counts(replay.metadata.cook_id)
    expected_identities = {
        (
            frame.frame_start_ms,
            frame.frame_end_ms,
            frame.result_revision,
            frame.observation_sequence,
            frame.role_generation,
        )
        for frame in replay.frames
    }
    terminal_count = sum(production_counts.values())
    cold_live_workers = _cold_restart_mpc(
        database_path,
        config,
        partition_digest[0],
        terminal_ms + 1,
        logger,
    )
    cold_repository = LearningTrajectoryRepository(str(database_path))
    cold_corpus_digest = _corpus_digest(_canonical_segments(cold_repository, all_cook_ids))
    cold_lifecycle = _lifecycle_bytes(database_path)
    cold_report = cold_repository.corpus_report()
    if (
        cold_report.segment_count != report.segment_count
        or cold_report.pre_roll_count != report.pre_roll_count
        or cold_report.scored_count != report.scored_count
    ):
        raise RuntimeError("cold restart changed the finalized corpus")
    source_counts = dict(replay.metadata.source_outcome_counts)
    return MpcCookReplayResult(
        cook_path=replay.fixture.path,
        replay_kind=replay.fixture.replay_kind,
        typed_outcome="replayed",
        detail="",
        input_frame_count=len(replay.frames),
        terminal_count=terminal_count,
        joined_terminal_count=len(expected_identities & produced_identities),
        source_outcome_counts=source_counts,
        production_outcome_counts=production_counts,
        diagnostic_outcome_counts=source_counts,
        terminal_reasons=terminal_reasons,
        segment_delta=report.segment_count - prior_report.segment_count,
        pre_roll_delta=report.pre_roll_count - prior_report.pre_roll_count,
        scored_delta=report.scored_count - prior_report.scored_count,
        pending_observation_count=max(0, len(replay.frames) - terminal_count),
        open_segment_count=report.open_segment_count,
        live_fit_worker_count=live_workers + cold_live_workers,
        quarantined_segment_ids=_quarantined_segment_ids(database_path),
        corpus_digest=corpus_digest,
        cold_corpus_digest=cold_corpus_digest,
        lifecycle=lifecycle,
        cold_lifecycle=cold_lifecycle,
    )


def run_mpc_campaign(
    campaign_id: str,
    database_path: Path,
    *,
    reconcile_each_frame: bool = True,
    evidence_available_before_submission: bool = True,
    terminal_reason: TrajectoryBreakReason = TrajectoryBreakReason.STOP,
) -> MpcCampaignReplayResult:
    campaign = load_real_cook_manifest().campaign(campaign_id)
    if campaign.controller != "mpc":
        raise ValueError(f"{campaign_id} is not an MPC campaign")
    copy_campaign_baseline(campaign_id, database_path)
    datastore._reset_for_tests(str(database_path))
    datastore.init()
    initial_repository = LearningTrajectoryRepository(str(database_path))
    prior_report = initial_repository.corpus_report()
    results: list[MpcCookReplayResult] = []
    blockers: list[ScientificBlocker] = []
    restart_count = 0
    exact_cook_ids = tuple(cook.fixture_cook_id for cook in campaign.cooks if cook.replay_kind == "exact-evidence")
    latest_config: dict[str, Any] | None = None
    latest_timestamp = campaign.baseline.cutoff_wall_ms
    for cook in campaign.cooks:
        if cook.replay_kind != "exact-evidence":
            detail = cook.blocked_reason or "manifest marks this cook thermal-smoke-only"
            blockers.append(ScientificBlocker("exact-actuation-join-unavailable", cook.path, detail))
            lifecycle = _lifecycle_bytes(database_path)
            canonical_corpus = _canonical_segments(LearningTrajectoryRepository(str(database_path)), exact_cook_ids)
            digest = _corpus_digest(canonical_corpus)
            results.append(
                MpcCookReplayResult(
                    cook_path=cook.path,
                    replay_kind=cook.replay_kind,
                    typed_outcome="exact-actuation-join-unavailable",
                    detail=detail,
                    input_frame_count=0,
                    terminal_count=0,
                    joined_terminal_count=0,
                    source_outcome_counts={},
                    production_outcome_counts={},
                    diagnostic_outcome_counts={},
                    terminal_reasons=(),
                    segment_delta=0,
                    pre_roll_delta=0,
                    scored_delta=0,
                    pending_observation_count=0,
                    open_segment_count=prior_report.open_segment_count,
                    live_fit_worker_count=0,
                    quarantined_segment_ids=(),
                    corpus_digest=digest,
                    cold_corpus_digest=digest,
                    lifecycle=lifecycle,
                    cold_lifecycle=lifecycle,
                )
            )
            continue
        replay = load_replay_cook(cook)
        session = _session_for_first_frame(replay)
        latest_config = _mpc_config(session)
        latest_timestamp = replay.frames[-1].frame_end_ms
        blockers.append(
            ScientificBlocker(
                "exact-pre-roll-actuation-unavailable",
                cook.path,
                "The committed archive contains exact terminal Hold frames but no exact pre-Hold actuation frames; "
                "the approved pre-roll deltas cannot be reconstructed from chamber temperatures alone.",
            )
        )
        if all(frame.observation.actual_fan_duty is None for frame in replay.frames):
            blockers.append(
                ScientificBlocker(
                    "exact-fan-delivery-unavailable",
                    cook.path,
                    "Every committed exact frame has actual_fan_duty=null. "
                    "LearningTrajectoryRuntime correctly fails closed with actuation-unknown because "
                    "the immutable trajectory contract requires exact fan delivery.",
                )
            )
        result = _run_exact_mpc_cook(
            replay,
            database_path=database_path,
            prior_report=prior_report,
            all_cook_ids=exact_cook_ids,
            reconcile_each_frame=reconcile_each_frame,
            evidence_available_before_submission=evidence_available_before_submission,
            terminal_reason=terminal_reason,
        )
        restart_count += 1
        if result.source_outcome_counts != result.production_outcome_counts:
            blockers.append(
                ScientificBlocker(
                    "source-diagnostic-terminal-kind-mismatch",
                    cook.path,
                    f"Cookfile diagnostics report {result.source_outcome_counts!r}, while current production "
                    f"terminal semantics report {result.production_outcome_counts!r}; the immutable final "
                    "discontinuous/safety frame cannot be reclassified without weakening a gate.",
                )
            )
        results.append(result)
        prior_report = LearningTrajectoryRepository(str(database_path)).corpus_report()

    final_repository = LearningTrajectoryRepository(str(database_path))
    canonical_corpus = _canonical_segments(final_repository, exact_cook_ids)
    corpus_digest = _corpus_digest(canonical_corpus)
    if exact_cook_ids and canonical_corpus == b"[]":
        blockers.append(
            ScientificBlocker(
                "cumulative-fit-unreachable",
                campaign.cooks[-1].path,
                "No compatible trajectory segment can be persisted without exact fan delivery, "
                "so production cannot form a FitRequest or candidate without fabricating actuator evidence.",
            )
        )
        if campaign_id == "mpc-aug29":
            blockers.extend(
                (
                    ScientificBlocker(
                        "assessment-digest-mismatch-unreachable",
                        campaign.cooks[-1].path,
                        "The assessment-digest mismatch requires a durable candidate assessment, "
                        "which is unreachable because the fixture's null fan delivery prevents corpus formation.",
                    ),
                    ScientificBlocker(
                        "per-cook-regression-unreachable",
                        campaign.cooks[-1].path,
                        "The per-cook regression gate requires a fitted candidate and retained per-cook corpus; "
                        "neither can be produced from the committed null fan-delivery frames.",
                    ),
                )
            )
        blockers.extend(
            (
                ScientificBlocker(
                    "fit-worker-failure-unreachable",
                    campaign.cooks[-1].path,
                    "A fit worker cannot start without a durable fit corpus.",
                ),
                ScientificBlocker(
                    "stale-fit-result-unreachable",
                    campaign.cooks[-1].path,
                    "No FitRequest exists to become stale after reconfiguration.",
                ),
                ScientificBlocker(
                    "process-restart-open-segment-unreachable",
                    campaign.cooks[-1].path,
                    "Exact fan delivery is rejected before an open trajectory segment can be persisted.",
                ),
                ScientificBlocker(
                    "model-persistence-after-qualification-unreachable",
                    campaign.cooks[-1].path,
                    "Qualification is unreachable without a fitted candidate.",
                ),
                ScientificBlocker(
                    "incompatibility-quarantine-unreachable",
                    campaign.cooks[-1].path,
                    "The manifest contains no explicit incompatible frame and no segment "
                    "survives exact-delivery validation.",
                ),
                ScientificBlocker(
                    "evidence-after-learner-completion-unreachable",
                    campaign.cooks[-1].path,
                    "The learner cannot complete a fit without a durable corpus, so there "
                    "is no post-completion evidence write.",
                ),
                ScientificBlocker(
                    "later-coherent-fit-unreachable",
                    campaign.cooks[-1].path,
                    "A later coherent fit cannot retain history because no source-supported segment can enter history.",
                ),
                ScientificBlocker(
                    "missing-probe-case-unavailable",
                    campaign.cooks[-1].path,
                    "Every committed exact frame has a synchronized chamber temperature; "
                    "changing one would alter immutable evidence.",
                ),
            )
        )
    lifecycle = _lifecycle_bytes(database_path)
    cold_live = 0
    if latest_config is not None:
        segments = tuple(
            segment for cook_id in exact_cook_ids for segment in final_repository.read_cook_segments(cook_id)
        )
        partition = segments[-1].fit_partition_digest if segments else None
        cold_live = _cold_restart_mpc(database_path, latest_config, partition, latest_timestamp + 2, _CampaignLogger())
        restart_count += 1
    cold_repository = LearningTrajectoryRepository(str(database_path))
    cold_corpus = _canonical_segments(cold_repository, exact_cook_ids)
    cold_lifecycle = _lifecycle_bytes(database_path)
    final_report = cold_repository.corpus_report()
    connection = sqlite3.connect(database_path)
    try:
        unauthorized_activation_count = int(
            connection.execute("SELECT COUNT(*) FROM model_activation_state WHERE phase='active'").fetchone()[0]
        )
    finally:
        connection.close()
    canonical_evidence = _canonical_selected_table(database_path, "model_evidence")
    canonical_lifecycle = _canonical_selected_table(database_path, "model_activation_state")
    canonical_state = _canonical_database_state(database_path)
    result = MpcCampaignReplayResult(
        campaign_id=campaign_id,
        cooks=tuple(results),
        terminal_reason=terminal_reason,
        restart_count=restart_count,
        corpus_digest=corpus_digest,
        cold_corpus_digest=_corpus_digest(cold_corpus),
        lifecycle=lifecycle,
        cold_lifecycle=cold_lifecycle,
        final_open_segment_count=final_report.open_segment_count,
        final_live_fit_worker_count=cold_live,
        unauthorized_activation_count=unauthorized_activation_count,
        scientific_blockers=tuple(blockers),
        canonical_corpus=canonical_corpus,
        canonical_fit_requests=_canonical_fit_requests(database_path),
        canonical_assessments=_canonical_assessments(database_path),
        canonical_lifecycle=canonical_lifecycle,
        canonical_evidence=canonical_evidence,
        canonical_state=canonical_state,
        primary_identities=_primary_identities(database_path),
    )
    datastore._reset_for_tests(None)
    return result
