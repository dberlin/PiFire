from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


@dataclass(frozen=True, slots=True)
class PidSpDutySegment:
    start_s: float
    end_s: float
    realized_duty: float

    def __post_init__(self) -> None:
        start = _finite_float(self.start_s, "start_s")
        end = _finite_float(self.end_s, "end_s")
        if end <= start:
            raise ValueError("end_s must be greater than start_s")
        duty = _finite_float(self.realized_duty, "realized_duty")
        if not 0.0 <= duty <= 1.0:
            raise ValueError("realized_duty must be in [0, 1]")
        object.__setattr__(self, "start_s", start)
        object.__setattr__(self, "end_s", end)
        object.__setattr__(self, "realized_duty", duty)


@dataclass(frozen=True, slots=True)
class PidSpInterval:
    start_s: float
    end_s: float
    temperature_f: float
    realized_duty: float
    continuous: bool
    observation_sequence: int
    role_generation: int
    duty_segments: tuple[PidSpDutySegment, ...] | None = None

    def __post_init__(self) -> None:
        start = _finite_float(self.start_s, "start_s")
        end = _finite_float(self.end_s, "end_s")
        if end <= start:
            raise ValueError("end_s must be greater than start_s")
        temperature = _finite_float(self.temperature_f, "temperature_f")
        duty = _finite_float(self.realized_duty, "realized_duty")
        if not 0.0 <= duty <= 1.0:
            raise ValueError("realized_duty must be in [0, 1]")
        if not isinstance(self.continuous, bool):
            raise TypeError("continuous must be a bool")
        segments = self.duty_segments
        if segments is None:
            segments = (PidSpDutySegment(start, end, duty),)
        elif not isinstance(segments, tuple) or not segments:
            raise ValueError("duty_segments must be a nonempty tuple")
        if not all(isinstance(segment, PidSpDutySegment) for segment in segments):
            raise TypeError("duty_segments must contain PidSpDutySegment values")
        if segments[0].start_s != start or segments[-1].end_s != end:
            raise ValueError("duty_segments must exactly cover the interval bounds")
        if any(left.end_s != right.start_s for left, right in pairwise(segments)):
            raise ValueError("duty_segments must tile the interval without overlaps or gaps")
        weighted_duty = sum((segment.end_s - segment.start_s) * segment.realized_duty for segment in segments) / (
            end - start
        )
        if not math.isclose(weighted_duty, duty, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("duty_segments weighted mean must equal realized_duty")
        object.__setattr__(self, "start_s", start)
        object.__setattr__(self, "end_s", end)
        object.__setattr__(self, "temperature_f", temperature)
        object.__setattr__(self, "realized_duty", duty)
        object.__setattr__(self, "duty_segments", segments)
        object.__setattr__(
            self,
            "observation_sequence",
            _nonnegative_int(self.observation_sequence, "observation_sequence"),
        )
        object.__setattr__(
            self,
            "role_generation",
            _nonnegative_int(self.role_generation, "role_generation"),
        )


class PidSpObservationDecision(StrEnum):
    ACCEPTED = "accepted"
    INVALID_PROBE = "invalid-probe"
    NON_CONTROLLER_OUTPUT = "non-controller-output"
    DISCONTINUOUS = "discontinuous"
    INHIBITED = "inhibited"


PID_SP_OBSERVATION_MODEL_SCHEMA = "pid-sp-observation-model/v1"


def canonical_pid_sp_observation_model_snapshot(
    model: Mapping[str, object] | None,
) -> dict[str, object]:
    """The typed model authority that actually governed one PID-SP frame."""
    if model is None:
        return {
            "controller": "pid_sp",
            "model_kind": "measured-temperature-fallback",
            "model_schema": PID_SP_OBSERVATION_MODEL_SCHEMA,
            "parameters": {},
        }
    form = model.get("form", "fopdt")
    if form == "ipdt":
        parameters = {
            "K_i": _finite_float(model["K_i"], "K_i"),
            "c0": _finite_float(model["c0"], "c0"),
            "theta": _finite_float(model["theta"], "theta"),
        }
    elif form == "fopdt":
        parameters = {
            "K": _finite_float(model["K"], "K"),
            "tau": _finite_float(model["tau"], "tau"),
            "theta": _finite_float(model["theta"], "theta"),
        }
    else:
        raise ValueError(f"unsupported PID-SP model form: {form!r}")
    return {
        "controller": "pid_sp",
        "model_kind": "smith-predictor",
        "model_schema": PID_SP_OBSERVATION_MODEL_SCHEMA,
        "form": form,
        "parameters": parameters,
    }


def canonical_pid_sp_observation_model_digest(
    model: Mapping[str, object] | None,
) -> str:
    snapshot = canonical_pid_sp_observation_model_snapshot(model)
    encoded = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class PidSpObservationOutcome:
    decision: PidSpObservationDecision
    effective_updates: int
    duty_variance: float
    duty_levels: int
    role_generation: int
    model_digest: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, PidSpObservationDecision):
            raise TypeError("decision must be a PidSpObservationDecision")
        object.__setattr__(
            self,
            "effective_updates",
            _nonnegative_int(self.effective_updates, "effective_updates"),
        )
        variance = _finite_float(self.duty_variance, "duty_variance")
        if variance < 0.0:
            raise ValueError("duty_variance must be nonnegative")
        object.__setattr__(self, "duty_variance", variance)
        object.__setattr__(self, "duty_levels", _nonnegative_int(self.duty_levels, "duty_levels"))
        object.__setattr__(
            self,
            "role_generation",
            _nonnegative_int(self.role_generation, "role_generation"),
        )
        digest: object = self.model_digest
        if digest is not None and (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("model_digest must be a lowercase SHA-256 digest or None")

    def as_runner_outcome(self) -> dict[str, object]:
        accepted = self.decision is PidSpObservationDecision.ACCEPTED
        return {
            "controller": "pid_sp",
            "eligible": accepted,
            "rejection_reasons": () if accepted else (self.decision.value,),
            "input_variance": self.duty_variance,
            "input_levels": self.duty_levels,
            "effective_updates": self.effective_updates,
            "role_generation": self.role_generation,
            "model_digest": self.model_digest,
        }
