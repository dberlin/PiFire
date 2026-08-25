"""Immutable, model-neutral production boundaries for grey-box learning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from numbers import Integral, Real

import numpy as np
import numpy.typing as npt

from common.control_trace import AllocationClampReason, AmbientSource, AmbientUncertainty
from controller.mpc_allocator import AllocationResult

FloatArray = npt.NDArray[np.float64]


def _finite_float(value: object, name: str) -> float:
    """Normalize one finite real scalar without admitting booleans."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a finite real number")
    normalized = float(value)
    if not np.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _bounded_duty(value: object, name: str) -> float:
    normalized = _finite_float(value, name)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
    return normalized


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def _owned_array(values: npt.ArrayLike, name: str) -> FloatArray:
    array = np.array(values, dtype=np.float64, copy=True)
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


class CandidateOrigin(StrEnum):
    PASSIVE_ONLINE = "passive-online"
    OPERATOR_CALIBRATION = "operator-calibration"
    COOK_REFIT = "cook-refit"


class ActivationPolicy(StrEnum):
    PASSIVE_AUTO = "passive-auto"
    OPERATOR_REVIEWED = "operator-reviewed"
    COOK_REFIT = "cook-refit"


class LearningStatus(StrEnum):
    COLLECTING = "collecting"
    INSUFFICIENT_EXCITATION = "insufficient-excitation"
    FITTING = "fitting"
    EVALUATING = "evaluating"
    READY_FOR_REVIEW = "ready-for-review"
    ACTIVATING = "activating"
    ACTIVE = "active"
    FALLBACK = "fallback"
    ERROR = "error"
    SCHEMA_INVALIDATED = "schema-invalidated"


class FitStatus(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


class CheckStatus(StrEnum):
    NOT_RUN = "not-run"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FitWindowIdentity:
    """Exact immutable observation window submitted to the fitting worker."""

    session_id: str
    cook_id: str | None
    first_observation_sequence: int
    last_observation_sequence: int
    configuration_digest: str
    incumbent_digest: str
    role_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be non-blank")
        if self.cook_id is not None and (not isinstance(self.cook_id, str) or not self.cook_id.strip()):
            raise ValueError("cook_id must be non-blank when present")
        first = _nonnegative_int(self.first_observation_sequence, "first_observation_sequence")
        last = _nonnegative_int(self.last_observation_sequence, "last_observation_sequence")
        if last < first:
            raise ValueError("last_observation_sequence must not precede first_observation_sequence")
        object.__setattr__(self, "first_observation_sequence", first)
        object.__setattr__(self, "last_observation_sequence", last)
        _require_digest(self.configuration_digest, "configuration_digest")
        _require_digest(self.incumbent_digest, "incumbent_digest")
        object.__setattr__(self, "role_generation", _nonnegative_int(self.role_generation, "role_generation"))


@dataclass(frozen=True, slots=True)
class FitRequest:
    request_id: str
    origin: CandidateOrigin
    window: FitWindowIdentity
    candidate_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-blank")
        if not isinstance(self.origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if not isinstance(self.window, FitWindowIdentity):
            raise TypeError("window must be a FitWindowIdentity")
        object.__setattr__(
            self,
            "candidate_generation",
            _nonnegative_int(self.candidate_generation, "candidate_generation"),
        )


@dataclass(frozen=True, slots=True)
class FitResult:
    request_id: str
    origin: CandidateOrigin
    window: FitWindowIdentity
    candidate_generation: int
    status: FitStatus
    candidate_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-blank")
        if not isinstance(self.origin, CandidateOrigin):
            raise TypeError("origin must be a CandidateOrigin")
        if not isinstance(self.window, FitWindowIdentity):
            raise TypeError("window must be a FitWindowIdentity")
        object.__setattr__(
            self,
            "candidate_generation",
            _nonnegative_int(self.candidate_generation, "candidate_generation"),
        )
        if not isinstance(self.status, FitStatus):
            raise TypeError("status must be a FitStatus")
        if self.candidate_digest is not None:
            _require_digest(self.candidate_digest, "candidate_digest")


def _require_digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


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
    observation_sequence: int = 0
    probe_valid: bool = True
    probe_source: str | None = None
    ambient_source: AmbientSource = AmbientSource.CONFIGURED
    ambient_uncertainty: AmbientUncertainty = AmbientUncertainty.UNMEASURED
    baseline_q: float | None = None
    probe_q: float = 0.0
    allocated_q: float | None = None
    scheduled_on_s: float | None = None
    realized_auger_duty: float | None = None
    allocator_revision: int | None = None
    allocation_clamp_reasons: tuple[AllocationClampReason, ...] = ()
    calibration_stage: str | None = None
    calibration_fit: bool = False
    allocation_join_reason: str | None = None
    temperature_band: str | None = None
    calibration_command_revision: int = 0
    calibration_command_action: str = "none"
    calibration_cancellation_reason: str | None = None
    baseline_allocation: AllocationResult | None = None
    combined_allocation: AllocationResult | None = None
    calibration_status: str = "inactive"
    cancellation_command_revision: int = 0
    cancellation_command_action: str = "none"
    completed_calibration_stages: tuple[str, ...] = ()

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
        observation_sequence = _nonnegative_int(self.observation_sequence, "observation_sequence")
        object.__setattr__(self, "observation_sequence", observation_sequence)
        if not isinstance(self.probe_valid, bool):
            raise TypeError("probe_valid must be a bool")
        if self.probe_source is not None and (not isinstance(self.probe_source, str) or not self.probe_source.strip()):
            raise ValueError("probe_source must be a non-empty string when present")
        if not isinstance(self.ambient_source, AmbientSource):
            raise TypeError("ambient_source must be an AmbientSource")
        if not isinstance(self.ambient_uncertainty, AmbientUncertainty):
            raise TypeError("ambient_uncertainty must be an AmbientUncertainty")
        baseline_q = self.requested_q if self.baseline_q is None else _bounded_duty(self.baseline_q, "baseline_q")
        probe_q = _finite_float(self.probe_q, "probe_q")
        if not -1.0 <= probe_q <= 1.0:
            raise ValueError("probe_q must be in [-1, 1]")
        requested_q = min(1.0, max(0.0, baseline_q + probe_q))
        if not np.isclose(self.requested_q, requested_q, rtol=0.0, atol=1e-12):
            raise ValueError("requested_q must equal clipped baseline_q plus probe_q")
        object.__setattr__(self, "baseline_q", baseline_q)
        object.__setattr__(self, "probe_q", probe_q)
        allocated_q = self.requested_q if self.allocated_q is None else _bounded_duty(self.allocated_q, "allocated_q")
        object.__setattr__(self, "allocated_q", allocated_q)
        scheduled_on_s = (
            self.delivered_on_s if self.scheduled_on_s is None else _finite_float(self.scheduled_on_s, "scheduled_on_s")
        )
        if scheduled_on_s < 0.0:
            raise ValueError("scheduled_on_s must be non-negative")
        object.__setattr__(self, "scheduled_on_s", scheduled_on_s)
        realized_auger_duty = (
            self.realized_q
            if self.realized_auger_duty is None
            else _bounded_duty(self.realized_auger_duty, "realized_auger_duty")
        )
        object.__setattr__(self, "realized_auger_duty", realized_auger_duty)
        allocator_revision = (
            0 if self.allocator_revision is None else _nonnegative_int(self.allocator_revision, "allocator_revision")
        )
        object.__setattr__(self, "allocator_revision", allocator_revision)
        clamp_reasons = tuple(self.allocation_clamp_reasons)
        if not all(isinstance(reason, AllocationClampReason) for reason in clamp_reasons):
            raise ValueError("allocation_clamp_reasons must contain AllocationClampReason values")
        object.__setattr__(self, "allocation_clamp_reasons", clamp_reasons)
        command_revision = _nonnegative_int(self.calibration_command_revision, "calibration_command_revision")
        if self.calibration_command_action not in {
            "none",
            "start",
            "pause",
            "resume",
            "stop",
            "reset-progress",
            "safety-cancel",
        }:
            raise ValueError("invalid calibration_command_action")
        if self.calibration_cancellation_reason is not None and (
            not isinstance(self.calibration_cancellation_reason, str)
            or not self.calibration_cancellation_reason.strip()
        ):
            raise ValueError("calibration_cancellation_reason must be a non-empty string when present")
        object.__setattr__(self, "calibration_command_revision", command_revision)
        for name, expected in (
            ("baseline_allocation", baseline_q),
            ("combined_allocation", self.requested_q),
        ):
            allocation = getattr(self, name)
            if allocation is not None and (
                not isinstance(allocation, AllocationResult)
                or not np.isclose(allocation.normalized_combustion_load, expected, rtol=0.0, atol=1e-12)
            ):
                raise ValueError(f"{name} must match its attributed combustion load")
        if self.calibration_status not in {"inactive", "accepted", "rejected", "active", "cancelled"}:
            raise ValueError("invalid calibration_status")
        _nonnegative_int(self.cancellation_command_revision, "cancellation_command_revision")
        if self.cancellation_command_action not in {"none", "pause", "stop", "reset-progress", "safety-cancel"}:
            raise ValueError("invalid cancellation_command_action")
        temperature_band = self.temperature_band
        if temperature_band is None:
            temperature_band = (
                "below-target"
                if self.temp_c < self.setpoint_c - 1.0
                else "above-target"
                if self.temp_c > self.setpoint_c + 1.0
                else "near-target"
            )
        if not isinstance(temperature_band, str) or not temperature_band.strip():
            raise ValueError("temperature_band must be a non-empty string")
        object.__setattr__(self, "temperature_band", temperature_band)
        if self.calibration_stage is not None and (
            not isinstance(self.calibration_stage, str) or not self.calibration_stage.strip()
        ):
            raise ValueError("calibration_stage must be a non-empty string when present")
        completed_stages = tuple(self.completed_calibration_stages)
        if len(set(completed_stages)) != len(completed_stages) or any(
            stage not in {"low", "middle", "high"} for stage in completed_stages
        ):
            raise ValueError("completed_calibration_stages must be unique known heating stages")
        object.__setattr__(self, "completed_calibration_stages", completed_stages)
        if not isinstance(self.calibration_fit, bool):
            raise TypeError("calibration_fit must be a bool")
        if self.calibration_fit and self.calibration_stage is None:
            raise ValueError("calibration_fit requires calibration_stage")
        if self.allocation_join_reason is not None and (
            not isinstance(self.allocation_join_reason, str) or not self.allocation_join_reason.strip()
        ):
            raise ValueError("allocation_join_reason must be a non-empty string when present")
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
                raise TypeError(f"{name} must be a bool")

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
