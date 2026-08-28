"""Isolated grey-box fitting and passive candidate preparation.

This module deliberately imports only the standard library at module load time.
The spawned worker fixes native math-library thread limits before it imports the
existing NumPy/SciPy fitting kernel.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import multiprocessing
import os
import queue
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from numbers import Integral, Real
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from controller.model_learning.contracts import FitRequest


FITTED_PARAMETERS = ("C_c", "K_Q", "theta")
FIT_VALUE_BOUNDS = {
    "C_c": (1.0, 1e6),
    "K_Q": (1e-3, 1e4),
    "theta": (25.0, 1200.0),
}
FIT_LOG_BOUNDS = {key: (math.log(lower), math.log(upper)) for key, (lower, upper) in FIT_VALUE_BOUNDS.items()}
FIT_CADENCE_S = 20.0
MAX_FIT_OBSERVATIONS = 8640
MAX_FIT_SEGMENTS = 256
MAX_PRE_ROLL_PER_SEGMENT = 180
_MAX_FIT_NFEV = 2000
_DIVERGED_RESIDUAL_C = 1e4
_IDENTIFIABILITY_STEP = 1e-3
_TIMESTAMP_TOLERANCE_S = 1e-9
_THREAD_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
_SPAWN_ENVIRONMENT_LOCK = threading.Lock()
_UNSET = object()


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


def _positive_int(value: object, name: str) -> int:
    normalized = _nonnegative_int(value, name)
    if normalized == 0:
        raise ValueError(f"{name} must be positive")
    return normalized


def _digest(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _owned_float_array(values: object, name: str) -> Any:
    import numpy as np

    try:
        array = np.array(values, dtype=np.float64, copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a numeric sequence") from error
    if array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


def _owned_int_array(values: object, name: str) -> Any:
    import numpy as np

    source = np.asarray(values)
    if source.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if source.dtype.kind not in "iu" or source.dtype.kind == "b":
        raise ValueError(f"{name} must contain integers")
    array = np.array(source, dtype=np.int64, copy=True)
    if np.any(array < 0):
        raise ValueError(f"{name} must contain non-negative integers")
    array.setflags(write=False)
    return array


def _owned_bool_array(values: object, name: str) -> Any:
    import numpy as np

    source = np.asarray(values)
    if source.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if source.dtype.kind != "b":
        raise ValueError(f"{name} must contain bool values")
    array = np.array(source, dtype=np.bool_, copy=True)
    array.setflags(write=False)
    return array


class FitSubmission(StrEnum):
    ACCEPTED = "accepted"
    BUSY = "busy"


class FitErrorCode(StrEnum):
    FIT_EXCEPTION = "fit-exception"
    INVALID_RESULT = "invalid-result"
    PROCESS_EXIT = "process-exit"


@dataclass(frozen=True, slots=True)
class GreyFitSegmentArrays:
    """Owned compact arrays for one independently initialized segment prefix."""

    segment_id: str
    cook_id: str
    through_ordinal: int
    prefix_digest: str
    fit_partition_digest: str
    observation_sequences: Any
    initial_load: float
    pre_roll_duration_s: Any
    pre_roll_load: Any
    pre_roll_temperature_c: Any
    hold_anchor_c: float
    scored_duration_s: Any
    scored_load: Any
    scored_ambient_c: Any
    scored_temperature_c: Any
    calibration_origin: Any

    def __post_init__(self) -> None:
        import numpy as np

        for name in ("segment_id", "cook_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")
        ordinal = _nonnegative_int(self.through_ordinal, "through_ordinal")
        object.__setattr__(self, "through_ordinal", ordinal)
        _digest(self.prefix_digest, "prefix_digest")
        _digest(self.fit_partition_digest, "fit_partition_digest")
        initial_load = _finite(self.initial_load, "initial_load")
        if not 0.0 <= initial_load <= 1.0:
            raise ValueError("initial_load must be normalized to [0, 1]")
        object.__setattr__(self, "initial_load", initial_load)
        object.__setattr__(self, "hold_anchor_c", _finite(self.hold_anchor_c, "hold_anchor_c"))

        float_names = (
            "pre_roll_duration_s",
            "pre_roll_load",
            "pre_roll_temperature_c",
            "scored_duration_s",
            "scored_load",
            "scored_ambient_c",
            "scored_temperature_c",
        )
        for name in float_names:
            object.__setattr__(self, name, _owned_float_array(getattr(self, name), name))
        object.__setattr__(
            self,
            "observation_sequences",
            _owned_int_array(self.observation_sequences, "observation_sequences"),
        )
        object.__setattr__(
            self,
            "calibration_origin",
            _owned_bool_array(self.calibration_origin, "calibration_origin"),
        )

        pre_roll_count = len(self.pre_roll_load)
        if not (len(self.pre_roll_duration_s) == len(self.pre_roll_temperature_c) == pre_roll_count):
            raise ValueError("pre-roll arrays must have the same length")
        scored_count = len(self.scored_load)
        if scored_count == 0:
            raise ValueError("scored arrays must not be empty")
        if not (
            len(self.scored_duration_s)
            == len(self.scored_ambient_c)
            == len(self.scored_temperature_c)
            == len(self.observation_sequences)
            == len(self.calibration_origin)
            == scored_count
        ):
            raise ValueError("scored arrays must have the same length")
        if pre_roll_count > MAX_PRE_ROLL_PER_SEGMENT:
            raise ValueError(f"pre-roll arrays must be bounded to {MAX_PRE_ROLL_PER_SEGMENT}")
        if np.any(self.pre_roll_duration_s <= 0.0) or np.any(self.scored_duration_s <= 0.0):
            raise ValueError("segment durations must be positive")
        if np.any(self.pre_roll_duration_s > FIT_CADENCE_S + _TIMESTAMP_TOLERANCE_S):
            raise ValueError(f"pre-roll intervals must not exceed the {FIT_CADENCE_S:g}-second cadence")
        if not all(
            math.isclose(
                float(duration),
                FIT_CADENCE_S,
                rel_tol=0.0,
                abs_tol=_TIMESTAMP_TOLERANCE_S,
            )
            for duration in self.scored_duration_s
        ):
            raise ValueError(f"scored intervals must match the nominal {FIT_CADENCE_S:g}-second cadence")
        if np.any((self.pre_roll_load < 0.0) | (self.pre_roll_load > 1.0)) or np.any(
            (self.scored_load < 0.0) | (self.scored_load > 1.0)
        ):
            raise ValueError("segment loads must be normalized to [0, 1]")
        if scored_count > 1 and np.any(np.diff(self.observation_sequences) != 1):
            raise ValueError("observation sequences must be contiguous within a segment")
        if ordinal != pre_roll_count + scored_count - 1:
            raise ValueError("through ordinal must equal retained segment prefix count minus one")


@dataclass(frozen=True, slots=True)
class GreyFitJob:
    """The sole owned worker fitting contract: one corpus, many independent segments."""

    request: Any
    corpus: Any
    segments: tuple[GreyFitSegmentArrays, ...]
    config: Any

    def __post_init__(self) -> None:
        from common.learning_trajectory import FitCorpusIdentity
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.contracts import FitRequest

        if not isinstance(self.request, FitRequest):
            raise TypeError("request must be a FitRequest")
        if not isinstance(self.corpus, FitCorpusIdentity):
            raise TypeError("corpus must be a FitCorpusIdentity")
        segments = tuple(self.segments)
        if not segments:
            raise ValueError("segments must not be empty")
        if len(segments) > MAX_FIT_SEGMENTS:
            raise ValueError(f"segments must be bounded to {MAX_FIT_SEGMENTS}")
        if not all(isinstance(segment, GreyFitSegmentArrays) for segment in segments):
            raise ValueError("segments must contain GreyFitSegmentArrays values")
        if not isinstance(self.config, GreyBoxMPCConfig):
            raise TypeError("config must be a GreyBoxMPCConfig")
        if len(self.corpus.slices) != len(segments):
            raise ValueError("fit corpus slices must match segments")

        segment_ids: set[str] = set()
        total_pre_roll = 0
        total_scored = 0
        for segment, corpus_slice in zip(segments, self.corpus.slices, strict=True):
            if segment.segment_id in segment_ids:
                raise ValueError("segments must have unique identities")
            segment_ids.add(segment.segment_id)
            if segment.fit_partition_digest != self.corpus.fit_partition_digest:
                raise ValueError("every segment must match the corpus fit partition")
            expected_slice = (
                segment.segment_id,
                segment.through_ordinal,
                segment.prefix_digest,
                len(segment.pre_roll_load),
                len(segment.scored_load),
            )
            actual_slice = (
                corpus_slice.segment_id,
                corpus_slice.through_ordinal,
                corpus_slice.prefix_digest,
                corpus_slice.pre_roll_count,
                corpus_slice.scored_count,
            )
            if actual_slice != expected_slice:
                raise ValueError("fit corpus slices must exactly match ordered segment prefixes")
            total_pre_roll += len(segment.pre_roll_load)
            total_scored += len(segment.scored_load)
        if total_pre_roll > MAX_FIT_OBSERVATIONS:
            raise ValueError(f"pre-roll rows must be bounded to {MAX_FIT_OBSERVATIONS}")
        if total_scored > MAX_FIT_OBSERVATIONS:
            raise ValueError(f"scored rows must be bounded to {MAX_FIT_OBSERVATIONS}")
        first_sequence = min(int(segment.observation_sequences[0]) for segment in segments)
        last_sequence = max(int(segment.observation_sequences[-1]) for segment in segments)
        if self.request.window.first_observation_sequence > first_sequence:
            raise ValueError("request window must include the first scored sequence")
        if self.request.window.last_observation_sequence != last_sequence:
            raise ValueError("request window must end at the last scored sequence")
        object.__setattr__(self, "segments", segments)


@dataclass(frozen=True, slots=True)
class GreyFitMetric:
    sample_count: int
    rmse_c: float
    bias_c: float
    error_band_c: tuple[float, float]
    max_error_c: float
    input_excitation: float
    input_levels: int
    identifiability_row_count: int
    temperature_span_c: float
    identifiability: float = 0.0
    segment_id: str | None = None
    cook_id: str | None = None
    supports_regression_gate: bool = False

    def __post_init__(self) -> None:
        count = _positive_int(self.sample_count, "sample_count")
        object.__setattr__(self, "sample_count", count)
        for name in (
            "rmse_c",
            "max_error_c",
            "input_excitation",
            "temperature_span_c",
            "identifiability",
        ):
            value = _finite(getattr(self, name), name)
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "bias_c", _finite(self.bias_c, "bias_c"))
        band = tuple(self.error_band_c)
        if len(band) != 2:
            raise ValueError("error_band_c must contain two values")
        low, high = (_finite(value, "error_band_c") for value in band)
        if high < low:
            raise ValueError("error_band_c must be increasing")
        object.__setattr__(self, "error_band_c", (low, high))
        object.__setattr__(self, "input_levels", _positive_int(self.input_levels, "input_levels"))
        rows = _positive_int(self.identifiability_row_count, "identifiability_row_count")
        if rows != count:
            raise ValueError("identifiability row count must equal effective sample count")
        object.__setattr__(self, "identifiability_row_count", rows)
        for name in ("segment_id", "cook_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be non-blank when present")
        if not isinstance(self.supports_regression_gate, bool):
            raise TypeError("supports_regression_gate must be a bool")


@dataclass(frozen=True, slots=True)
class GreyFitMetrics:
    pooled: GreyFitMetric
    by_segment: tuple[GreyFitMetric, ...]
    by_cook: tuple[GreyFitMetric, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.pooled, GreyFitMetric):
            raise TypeError("pooled must be a GreyFitMetric")
        by_segment = tuple(self.by_segment)
        by_cook = tuple(self.by_cook)
        if not by_segment or not all(
            isinstance(metric, GreyFitMetric) and metric.segment_id is not None for metric in by_segment
        ):
            raise ValueError("by_segment must contain identified GreyFitMetric values")
        if not by_cook or not all(
            isinstance(metric, GreyFitMetric) and metric.cook_id is not None for metric in by_cook
        ):
            raise ValueError("by_cook must contain identified GreyFitMetric values")
        object.__setattr__(self, "by_segment", by_segment)
        object.__setattr__(self, "by_cook", by_cook)


@dataclass(frozen=True, slots=True)
class GreyFitComparison:
    """One candidate/incumbent score over identical independently reset rows."""

    metrics: GreyFitMetrics
    incumbent_metrics: GreyFitMetrics
    effective_masks: tuple[Any, ...]
    identifiability: float
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, GreyFitMetrics) or not isinstance(self.incumbent_metrics, GreyFitMetrics):
            raise TypeError("comparison metrics must be GreyFitMetrics")
        masks = tuple(_owned_bool_array(mask, "effective_masks") for mask in self.effective_masks)
        object.__setattr__(self, "effective_masks", masks)
        score = _finite(self.identifiability, "identifiability")
        if score < 0.0:
            raise ValueError("identifiability must be non-negative")
        object.__setattr__(self, "identifiability", score)
        reasons = tuple(self.rejection_reasons)
        if not all(isinstance(reason, str) and reason.strip() for reason in reasons):
            raise ValueError("rejection_reasons must contain non-blank strings")
        object.__setattr__(self, "rejection_reasons", reasons)


@dataclass(frozen=True, slots=True)
class GreyFitSuccess:
    request: Any
    config: Any
    rmse_c: float
    max_error_c: float
    identifiability: float
    sample_count: int
    temperature_band_c: tuple[float, float]
    nfev: int
    metrics: GreyFitMetrics | None = None
    incumbent_metrics: GreyFitMetrics | None = None
    effective_masks: tuple[Any, ...] = ()
    optimizer_residual_count: int = 0
    rejection_reasons: tuple[str, ...] = ()
    result_digest: str = ""

    def __post_init__(self) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.contracts import FitRequest

        if not isinstance(self.request, FitRequest):
            raise TypeError("request must be a FitRequest")
        if not isinstance(self.config, GreyBoxMPCConfig):
            raise TypeError("config must be a GreyBoxMPCConfig")
        for name in ("rmse_c", "max_error_c", "identifiability"):
            normalized = _finite(getattr(self, name), name)
            if normalized < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, normalized)
        object.__setattr__(self, "sample_count", _positive_int(self.sample_count, "sample_count"))
        band = tuple(self.temperature_band_c)
        if len(band) != 2:
            raise ValueError("temperature_band_c must contain two values")
        low, high = (_finite(value, "temperature_band_c") for value in band)
        if high < low:
            raise ValueError("temperature_band_c must be increasing")
        object.__setattr__(self, "temperature_band_c", (low, high))
        object.__setattr__(self, "nfev", _nonnegative_int(self.nfev, "nfev"))
        if self.metrics is not None and not isinstance(self.metrics, GreyFitMetrics):
            raise TypeError("metrics must be GreyFitMetrics when present")
        if self.incumbent_metrics is not None and not isinstance(self.incumbent_metrics, GreyFitMetrics):
            raise TypeError("incumbent_metrics must be GreyFitMetrics when present")
        masks = tuple(_owned_bool_array(mask, "effective_masks") for mask in self.effective_masks)
        object.__setattr__(self, "effective_masks", masks)
        object.__setattr__(
            self,
            "optimizer_residual_count",
            _nonnegative_int(self.optimizer_residual_count, "optimizer_residual_count"),
        )
        reasons = tuple(self.rejection_reasons)
        if not all(isinstance(reason, str) and reason.strip() for reason in reasons):
            raise ValueError("rejection_reasons must contain non-blank strings")
        if len(set(reasons)) != len(reasons):
            raise ValueError("rejection_reasons must not contain duplicates")
        object.__setattr__(self, "rejection_reasons", reasons)
        if self.result_digest:
            _digest(self.result_digest, "result_digest")


@dataclass(frozen=True, slots=True)
class GreyFitError:
    request: Any
    code: FitErrorCode
    error_type: str
    detail: str

    def __post_init__(self) -> None:
        from controller.model_learning.contracts import FitRequest

        if not isinstance(self.request, FitRequest):
            raise TypeError("request must be a FitRequest")
        if not isinstance(self.code, FitErrorCode):
            raise TypeError("code must be a FitErrorCode")
        for name in ("error_type", "detail"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")


@dataclass(frozen=True, slots=True)
class GreyFitMessage:
    request: Any
    outcome: GreyFitSuccess | GreyFitError
    worker_start_method: str
    worker_thread_environment: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.request != self.outcome.request:
            raise ValueError("message and outcome requests must match")
        if self.worker_start_method != "spawn":
            raise ValueError("grey fitting must use spawn")
        environment = tuple(self.worker_thread_environment)
        if dict(environment) != {name: "1" for name in _THREAD_VARIABLES}:
            raise ValueError("worker thread environment is incomplete")
        object.__setattr__(self, "worker_thread_environment", environment)


def _fit_failure(
    job: GreyFitJob,
    detail: str,
    *,
    error_type: str = "GreyFitFailure",
    code: FitErrorCode = FitErrorCode.FIT_EXCEPTION,
) -> GreyFitError:
    return GreyFitError(
        request=job.request,
        code=code,
        error_type=error_type,
        detail=detail,
    )


def _parameter_values(log_parameters: Any) -> tuple[float, float, float] | None:
    try:
        logs = tuple(float(value) for value in log_parameters)
    except TypeError, ValueError:
        return None
    if len(logs) != len(FITTED_PARAMETERS) or not all(math.isfinite(value) for value in logs):
        return None
    values: list[float] = []
    for key, log_value in zip(FITTED_PARAMETERS, logs, strict=True):
        lower_log, upper_log = FIT_LOG_BOUNDS[key]
        lower_value, upper_value = FIT_VALUE_BOUNDS[key]
        try:
            value = (
                lower_value
                if log_value <= lower_log
                else upper_value
                if log_value >= upper_log
                else math.exp(log_value)
            )
        except OverflowError:
            return None
        values.append(value)
    return tuple(values)


def _warmup_masks(
    segments: tuple[GreyFitSegmentArrays, ...],
    theta: float,
) -> tuple[Any, ...]:
    import numpy as np

    masks: list[Any] = []
    required_history_s = 3.0 * theta
    for segment in segments:
        pre_roll_s = float(np.sum(segment.pre_roll_duration_s))
        available_before = np.empty(len(segment.scored_duration_s), dtype=float)
        available_before[0] = pre_roll_s
        if len(available_before) > 1:
            available_before[1:] = pre_roll_s + np.cumsum(segment.scored_duration_s[:-1])
        mask = available_before >= required_history_s
        mask.setflags(write=False)
        masks.append(mask)
    return tuple(masks)


def _simulate_segments(
    job: GreyFitJob,
    parameters: tuple[float, float, float],
) -> tuple[Any, ...] | None:
    import numpy as np

    from controller.mpc_model import replay_delay_chain_arrays, simulate_grey_box_intervals

    C_c, K_Q, theta = parameters
    predicted: list[Any] = []
    try:
        for segment in job.segments:
            delay_states = replay_delay_chain_arrays(
                segment.pre_roll_duration_s,
                segment.pre_roll_load,
                theta=theta,
                n_delay=job.config.delay_states,
                initial_load=segment.initial_load,
            )
            trajectory = simulate_grey_box_intervals(
                segment.scored_duration_s,
                segment.scored_load,
                segment.scored_ambient_c,
                C_c=C_c,
                h_amb=job.config.h_amb,
                T0=segment.hold_anchor_c,
                K_Q=K_Q,
                sigma=job.config.sigma,
                theta=theta,
                n_delay=job.config.delay_states,
                initial_delay_states=delay_states,
            )
            if not np.all(np.isfinite(trajectory)):
                return None
            predicted.append(trajectory)
    except FloatingPointError, OverflowError, ValueError:
        return None
    return tuple(predicted)


def _metric(
    errors: Any,
    temperatures: Any,
    loads: Any,
    *,
    segment_id: str | None = None,
    cook_id: str | None = None,
    supports_regression_gate: bool = False,
) -> GreyFitMetric:
    import numpy as np

    return GreyFitMetric(
        sample_count=len(errors),
        rmse_c=float(np.sqrt(np.mean(errors**2))),
        bias_c=float(np.mean(errors)),
        error_band_c=(float(np.min(errors)), float(np.max(errors))),
        max_error_c=float(np.max(np.abs(errors))),
        input_excitation=float(np.var(loads)),
        input_levels=len({float(value) for value in loads}),
        identifiability_row_count=len(errors),
        temperature_span_c=float(np.max(temperatures) - np.min(temperatures)),
        segment_id=segment_id,
        cook_id=cook_id,
        supports_regression_gate=supports_regression_gate,
    )


def _cook_evidence_supported(metric: GreyFitMetric) -> bool:
    thresholds = TriggerConfig()
    return (
        metric.sample_count >= thresholds.min_samples
        and metric.input_excitation >= thresholds.min_input_variance
        and metric.input_levels >= thresholds.min_input_levels
        and metric.temperature_span_c >= thresholds.min_temperature_span_c
    )


def _cook_supported(metric: GreyFitMetric) -> bool:
    return _cook_evidence_supported(metric) and metric.identifiability >= TriggerConfig().min_identifiability


def supported_segmented_cooks(
    job: GreyFitJob,
    *,
    theta: float,
) -> tuple[str, ...]:
    """Return cooks that can satisfy the typed evidence gates at this warm-up."""

    import numpy as np

    if not isinstance(job, GreyFitJob):
        raise TypeError("job must be a GreyFitJob")
    masks = _warmup_masks(job.segments, _finite(theta, "theta"))
    grouped: dict[str, tuple[list[Any], list[Any]]] = {}
    for segment, mask in zip(job.segments, masks, strict=True):
        loads, temperatures = grouped.setdefault(segment.cook_id, ([], []))
        loads.append(segment.scored_load[mask])
        temperatures.append(segment.scored_temperature_c[mask])

    supported: list[str] = []
    for cook_id, (load_parts, temperature_parts) in grouped.items():
        loads = np.concatenate(load_parts)
        temperatures = np.concatenate(temperature_parts)
        if not len(loads):
            continue
        evidence = _metric(
            np.zeros(len(loads), dtype=float),
            temperatures,
            loads,
            cook_id=cook_id,
        )
        if _cook_evidence_supported(evidence):
            supported.append(cook_id)
    return tuple(supported)


def _grouped_metrics(
    job: GreyFitJob,
    predicted: tuple[Any, ...],
    masks: tuple[Any, ...],
) -> GreyFitMetrics:
    import numpy as np

    errors_by_segment: list[Any] = []
    temperatures_by_segment: list[Any] = []
    loads_by_segment: list[Any] = []
    by_segment: list[GreyFitMetric] = []
    cook_indices: dict[str, list[int]] = {}
    for index, (segment, trajectory, mask) in enumerate(zip(job.segments, predicted, masks, strict=True)):
        errors = (trajectory - segment.scored_temperature_c)[mask]
        temperatures = segment.scored_temperature_c[mask]
        loads = segment.scored_load[mask]
        errors_by_segment.append(errors)
        temperatures_by_segment.append(temperatures)
        loads_by_segment.append(loads)
        by_segment.append(
            _metric(
                errors,
                temperatures,
                loads,
                segment_id=segment.segment_id,
            )
        )
        cook_indices.setdefault(segment.cook_id, []).append(index)

    pooled_errors = np.concatenate(errors_by_segment)
    pooled_temperatures = np.concatenate(temperatures_by_segment)
    pooled_loads = np.concatenate(loads_by_segment)
    pooled = _metric(pooled_errors, pooled_temperatures, pooled_loads)
    by_cook: list[GreyFitMetric] = []
    for cook_id, indices in cook_indices.items():
        errors = np.concatenate([errors_by_segment[index] for index in indices])
        temperatures = np.concatenate([temperatures_by_segment[index] for index in indices])
        loads = np.concatenate([loads_by_segment[index] for index in indices])
        by_cook.append(_metric(errors, temperatures, loads, cook_id=cook_id))
    return GreyFitMetrics(pooled=pooled, by_segment=tuple(by_segment), by_cook=tuple(by_cook))


def _identifiability_columns(
    job: GreyFitJob,
    parameters: tuple[float, float, float],
) -> tuple[tuple[Any, ...], ...] | None:
    columns: list[tuple[Any, ...]] = []
    for parameter_index, key in enumerate(FITTED_PARAMETERS):
        lower_bound, upper_bound = FIT_VALUE_BOUNDS[key]
        lower_log, upper_log = FIT_LOG_BOUNDS[key]
        base_log = math.log(parameters[parameter_index])
        distance_to_lower = max(0.0, base_log - lower_log)
        distance_to_upper = max(0.0, upper_log - base_log)

        if distance_to_lower < _IDENTIFIABILITY_STEP:
            step = min(_IDENTIFIABILITY_STEP, distance_to_upper)
            upper = list(parameters)
            upper[parameter_index] = min(
                upper_bound,
                max(lower_bound, math.exp(base_log + step)),
            )
            actual_step = math.log(upper[parameter_index]) - base_log
            upper_prediction = _simulate_segments(job, tuple(upper))
            base_prediction = _simulate_segments(job, parameters)
            if upper_prediction is None or base_prediction is None or actual_step <= 0.0:
                return None
            derivative = tuple(
                (high - base) / actual_step for high, base in zip(upper_prediction, base_prediction, strict=True)
            )
        elif distance_to_upper < _IDENTIFIABILITY_STEP:
            step = min(_IDENTIFIABILITY_STEP, distance_to_lower)
            lower = list(parameters)
            lower[parameter_index] = min(
                upper_bound,
                max(lower_bound, math.exp(base_log - step)),
            )
            actual_step = base_log - math.log(lower[parameter_index])
            lower_prediction = _simulate_segments(job, tuple(lower))
            base_prediction = _simulate_segments(job, parameters)
            if lower_prediction is None or base_prediction is None or actual_step <= 0.0:
                return None
            derivative = tuple(
                (base - low) / actual_step for base, low in zip(base_prediction, lower_prediction, strict=True)
            )
        else:
            upper = list(parameters)
            lower = list(parameters)
            upper[parameter_index] = math.exp(base_log + _IDENTIFIABILITY_STEP)
            lower[parameter_index] = math.exp(base_log - _IDENTIFIABILITY_STEP)
            upper_prediction = _simulate_segments(job, tuple(upper))
            lower_prediction = _simulate_segments(job, tuple(lower))
            if upper_prediction is None or lower_prediction is None:
                return None
            derivative = tuple(
                (high - low) / (2.0 * _IDENTIFIABILITY_STEP)
                for high, low in zip(upper_prediction, lower_prediction, strict=True)
            )
        columns.append(derivative)
    return tuple(columns)


def _score_identifiability(
    columns: tuple[tuple[Any, ...], ...],
    masks: tuple[Any, ...],
) -> float:
    import numpy as np

    row_count = sum(int(np.count_nonzero(mask)) for mask in masks)
    if row_count < len(FITTED_PARAMETERS):
        return 0.0
    jacobian = np.column_stack(
        [np.concatenate([values[mask] for values, mask in zip(column, masks, strict=True)]) for column in columns]
    ) / math.sqrt(row_count)
    singular_values = np.linalg.svd(jacobian, compute_uv=False)
    return float(singular_values[-1])


def _stacked_identifiability(
    job: GreyFitJob,
    parameters: tuple[float, float, float],
    masks: tuple[Any, ...],
) -> float | None:
    columns = _identifiability_columns(job, parameters)
    return None if columns is None else _score_identifiability(columns, masks)


def _metrics_with_identifiability(
    job: GreyFitJob,
    metrics: GreyFitMetrics,
    parameters: tuple[float, float, float],
    masks: tuple[Any, ...],
) -> tuple[GreyFitMetrics, float]:
    import numpy as np

    columns = _identifiability_columns(job, parameters)
    if columns is None:
        raise FloatingPointError("bounded grey fit is not identifiable")
    pooled_identifiability = _score_identifiability(columns, masks)
    if not math.isfinite(pooled_identifiability):
        raise FloatingPointError("bounded grey fit is not identifiable")

    by_cook: list[GreyFitMetric] = []
    for metric in metrics.by_cook:
        cook_masks = tuple(
            mask if segment.cook_id == metric.cook_id else np.zeros_like(mask)
            for segment, mask in zip(job.segments, masks, strict=True)
        )
        identifiability = _score_identifiability(columns, cook_masks)
        if not math.isfinite(identifiability):
            raise FloatingPointError("bounded grey fit is not identifiable")
        identified = replace(metric, identifiability=identifiability)
        by_cook.append(
            replace(
                identified,
                supports_regression_gate=_cook_supported(identified),
            )
        )
    return (
        replace(
            metrics,
            pooled=replace(metrics.pooled, identifiability=pooled_identifiability),
            by_cook=tuple(by_cook),
        ),
        pooled_identifiability,
    )


def compare_segmented_grey(
    job: GreyFitJob,
    *,
    candidate: Any,
    incumbent: Any,
) -> GreyFitComparison:
    """Score two configs on one conservative mask without running an optimizer."""

    import numpy as np

    from controller.acados.contracts import GreyBoxMPCConfig

    if not isinstance(job, GreyFitJob):
        raise TypeError("job must be a GreyFitJob")
    if not isinstance(candidate, GreyBoxMPCConfig) or not isinstance(incumbent, GreyBoxMPCConfig):
        raise TypeError("candidate and incumbent must be GreyBoxMPCConfig values")
    for config in (candidate, incumbent):
        if (
            config.delay_states != job.config.delay_states
            or config.h_amb != job.config.h_amb
            or config.sigma != job.config.sigma
        ):
            raise ValueError("comparison configs must match the fit partition's held physics")

    candidate_parameters = tuple(float(getattr(candidate, key)) for key in FITTED_PARAMETERS)
    incumbent_parameters = tuple(float(getattr(incumbent, key)) for key in FITTED_PARAMETERS)
    candidate_prediction = _simulate_segments(job, candidate_parameters)
    incumbent_prediction = _simulate_segments(job, incumbent_parameters)
    if candidate_prediction is None or incumbent_prediction is None:
        raise FloatingPointError("final grey model simulation was non-finite")
    candidate_masks = _warmup_masks(
        job.segments,
        candidate_parameters[FITTED_PARAMETERS.index("theta")],
    )
    incumbent_masks = _warmup_masks(
        job.segments,
        incumbent_parameters[FITTED_PARAMETERS.index("theta")],
    )
    common_masks = tuple(
        candidate_mask & incumbent_mask
        for candidate_mask, incumbent_mask in zip(
            candidate_masks,
            incumbent_masks,
            strict=True,
        )
    )
    incomplete = [
        segment.segment_id for segment, mask in zip(job.segments, common_masks, strict=True) if not np.any(mask)
    ]
    if incomplete:
        raise ValueError(f"segment-warmup-incomplete:{incomplete[0]}")
    candidate_metrics, identifiability = _metrics_with_identifiability(
        job,
        _grouped_metrics(job, candidate_prediction, common_masks),
        candidate_parameters,
        common_masks,
    )
    incumbent_metrics, _ = _metrics_with_identifiability(
        job,
        _grouped_metrics(job, incumbent_prediction, common_masks),
        incumbent_parameters,
        common_masks,
    )

    supported = tuple(metric for metric in candidate_metrics.by_cook if metric.supports_regression_gate)
    if not supported:
        rejection_reasons = ("insufficient-supported-cooks",)
    else:
        incumbent_by_cook = {metric.cook_id: metric for metric in incumbent_metrics.by_cook}
        rejection_reasons = tuple(
            f"per-cook-regression:{metric.cook_id}"
            for metric in supported
            if metric.rmse_c > incumbent_by_cook[metric.cook_id].rmse_c
        )
    return GreyFitComparison(
        metrics=candidate_metrics,
        incumbent_metrics=incumbent_metrics,
        effective_masks=common_masks,
        identifiability=identifiability,
        rejection_reasons=rejection_reasons,
    )


def _result_digest(
    job: GreyFitJob,
    config: Any,
    metrics: GreyFitMetrics,
    masks: tuple[Any, ...],
    identifiability: float,
) -> str:
    def metric_payload(metric: GreyFitMetric) -> dict[str, Any]:
        return {
            "sample_count": metric.sample_count,
            "rmse_c": metric.rmse_c,
            "bias_c": metric.bias_c,
            "error_band_c": list(metric.error_band_c),
            "max_error_c": metric.max_error_c,
            "input_excitation": metric.input_excitation,
            "input_levels": metric.input_levels,
            "identifiability_row_count": metric.identifiability_row_count,
            "temperature_span_c": metric.temperature_span_c,
            "identifiability": metric.identifiability,
        }

    payload = {
        "request_id": job.request.request_id,
        "corpus_digest": job.corpus.corpus_digest,
        "candidate": {key: getattr(config, key) for key in FITTED_PARAMETERS},
        "effective_masks": [[bool(value) for value in mask] for mask in masks],
        "pooled": metric_payload(metrics.pooled),
        "segments": [{"segment_id": metric.segment_id, **metric_payload(metric)} for metric in metrics.by_segment],
        "cooks": [{"cook_id": metric.cook_id, **metric_payload(metric)} for metric in metrics.by_cook],
        "identifiability": identifiability,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fit_segmented_grey(job: GreyFitJob) -> GreyFitSuccess | GreyFitError:
    """Fit one shared grey parameter vector over independently reset segments."""

    import numpy as np
    from scipy import optimize

    if not isinstance(job, GreyFitJob):
        raise TypeError("job must be a GreyFitJob")
    lower = np.asarray([FIT_LOG_BOUNDS[key][0] for key in FITTED_PARAMETERS], dtype=float)
    upper = np.asarray([FIT_LOG_BOUNDS[key][1] for key in FITTED_PARAMETERS], dtype=float)
    x0 = np.clip(
        np.log(np.asarray([getattr(job.config, key) for key in FITTED_PARAMETERS], dtype=float)),
        lower,
        upper,
    )
    residual_count = sum(len(segment.scored_load) for segment in job.segments)

    def dynamic_residual(log_parameters: Any) -> Any:
        parameters = _parameter_values(log_parameters)
        if parameters is None:
            return np.full(residual_count, _DIVERGED_RESIDUAL_C, dtype=float)
        prediction = _simulate_segments(job, parameters)
        if prediction is None:
            return np.full(residual_count, _DIVERGED_RESIDUAL_C, dtype=float)
        masks = _warmup_masks(job.segments, parameters[FITTED_PARAMETERS.index("theta")])
        return np.concatenate(
            [
                np.where(mask, trajectory - segment.scored_temperature_c, 0.0)
                for segment, trajectory, mask in zip(
                    job.segments,
                    prediction,
                    masks,
                    strict=True,
                )
            ]
        )

    try:
        first = optimize.least_squares(
            dynamic_residual,
            x0,
            method="trf",
            bounds=(lower, upper),
            max_nfev=_MAX_FIT_NFEV,
        )
    except Exception as error:
        return _fit_failure(
            job,
            str(error) or repr(error),
            error_type=type(error).__name__,
        )
    if (
        not bool(getattr(first, "success", int(getattr(first, "status", 0)) > 0))
        or int(getattr(first, "status", 0)) <= 0
    ):
        return _fit_failure(job, "bounded grey fit did not converge", error_type="FitConvergenceError")
    first_parameters = _parameter_values(first.x)
    if first_parameters is None:
        return _fit_failure(job, "bounded grey fit produced non-finite parameters")
    frozen_masks = _warmup_masks(
        job.segments,
        first_parameters[FITTED_PARAMETERS.index("theta")],
    )

    def frozen_residual(log_parameters: Any) -> Any:
        parameters = _parameter_values(log_parameters)
        if parameters is None:
            return np.full(residual_count, _DIVERGED_RESIDUAL_C, dtype=float)
        prediction = _simulate_segments(job, parameters)
        if prediction is None:
            return np.full(residual_count, _DIVERGED_RESIDUAL_C, dtype=float)
        return np.concatenate(
            [
                np.where(mask, trajectory - segment.scored_temperature_c, 0.0)
                for segment, trajectory, mask in zip(
                    job.segments,
                    prediction,
                    frozen_masks,
                    strict=True,
                )
            ]
        )

    try:
        polished = optimize.least_squares(
            frozen_residual,
            first.x,
            method="trf",
            bounds=(lower, upper),
            max_nfev=_MAX_FIT_NFEV,
        )
    except Exception as error:
        return _fit_failure(
            job,
            str(error) or repr(error),
            error_type=type(error).__name__,
        )
    if (
        not bool(getattr(polished, "success", int(getattr(polished, "status", 0)) > 0))
        or int(getattr(polished, "status", 0)) <= 0
    ):
        return _fit_failure(job, "bounded grey polish did not converge", error_type="FitConvergenceError")
    parameters = _parameter_values(polished.x)
    if parameters is None:
        return _fit_failure(job, "bounded grey polish produced non-finite parameters")
    polished_log = tuple(float(value) for value in polished.x)
    parameters = tuple(
        float(getattr(job.config, key)) if log_value == math.log(float(getattr(job.config, key))) else parameter
        for key, log_value, parameter in zip(
            FITTED_PARAMETERS,
            polished_log,
            parameters,
            strict=True,
        )
    )
    polished_masks = _warmup_masks(job.segments, parameters[FITTED_PARAMETERS.index("theta")])
    if any(
        not np.array_equal(frozen, recomputed) for frozen, recomputed in zip(frozen_masks, polished_masks, strict=True)
    ):
        return _fit_failure(
            job,
            "warmup-mask-unstable",
            error_type="WarmupMaskUnstable",
            code=FitErrorCode.INVALID_RESULT,
        )

    candidate = replace(
        job.config,
        **dict(zip(FITTED_PARAMETERS, parameters, strict=True)),
    )
    try:
        comparison = compare_segmented_grey(
            job,
            candidate=candidate,
            incumbent=job.config,
        )
    except (FloatingPointError, ValueError) as error:
        detail = str(error) or repr(error)
        return _fit_failure(
            job,
            detail,
            error_type=(
                "InsufficientWarmup" if detail.startswith("segment-warmup-incomplete:") else type(error).__name__
            ),
        )
    candidate_metrics = comparison.metrics
    incumbent_metrics = comparison.incumbent_metrics
    common_masks = comparison.effective_masks
    identifiability = comparison.identifiability
    rejection_reasons = comparison.rejection_reasons
    pooled_temperatures = np.concatenate(
        [segment.scored_temperature_c[mask] for segment, mask in zip(job.segments, common_masks, strict=True)]
    )
    digest = _result_digest(
        job,
        candidate,
        candidate_metrics,
        common_masks,
        identifiability,
    )
    return GreyFitSuccess(
        request=job.request,
        config=candidate,
        rmse_c=candidate_metrics.pooled.rmse_c,
        max_error_c=candidate_metrics.pooled.max_error_c,
        identifiability=identifiability,
        sample_count=candidate_metrics.pooled.sample_count,
        temperature_band_c=(
            float(np.min(pooled_temperatures)),
            float(np.max(pooled_temperatures)),
        ),
        nfev=int(getattr(first, "nfev", 0)) + int(getattr(polished, "nfev", 0)),
        metrics=candidate_metrics,
        incumbent_metrics=incumbent_metrics,
        effective_masks=common_masks,
        optimizer_residual_count=residual_count,
        rejection_reasons=rejection_reasons,
        result_digest=digest,
    )


def _worker_main(
    requests: Any,
    results: Any,
    kernel: Callable[[GreyFitJob], GreyFitSuccess | GreyFitError],
) -> None:
    for name in _THREAD_VARIABLES:
        os.environ[name] = "1"
    environment = tuple((name, os.environ[name]) for name in _THREAD_VARIABLES)
    while True:
        job = requests.get()
        if job is None:
            return
        try:
            outcome = kernel(job)
            if not isinstance(outcome, (GreyFitSuccess, GreyFitError)) or outcome.request != job.request:
                raise TypeError("fit kernel must return a grey fit outcome for the exact request")
        except Exception as error:
            code = FitErrorCode.INVALID_RESULT if isinstance(error, TypeError) else FitErrorCode.FIT_EXCEPTION
            outcome = GreyFitError(
                request=job.request,
                code=code,
                error_type=type(error).__name__,
                detail=str(error) or repr(error),
            )
        results.put(
            GreyFitMessage(
                request=job.request,
                outcome=outcome,
                worker_start_method="spawn",
                worker_thread_environment=environment,
            )
        )


class GreyFitWorker:
    """One persistent spawned process with one drain-before-reuse request slot."""

    def __init__(
        self,
        kernel: Callable[[GreyFitJob], GreyFitSuccess | GreyFitError] | None = None,
    ) -> None:
        self._kernel = fit_segmented_grey if kernel is None else kernel
        if not callable(self._kernel):
            raise TypeError("kernel must be callable")
        self._context = multiprocessing.get_context("spawn")
        self._requests: Any = None
        self._results: Any = None
        self._process: Any = None
        self._pending: GreyFitJob | None = None
        self._closed = False

    @property
    def start_method(self) -> str:
        return self._context.get_start_method()

    @property
    def process_count(self) -> int:
        return 1 if self._process is not None else 0

    @property
    def alive(self) -> bool:
        return bool(self._process is not None and self._process.is_alive())

    @property
    def busy(self) -> bool:
        return self._pending is not None

    def start(self) -> GreyFitWorker:
        if self._closed:
            raise RuntimeError("GreyFitWorker is closed")
        if self._process is not None:
            return self
        self._requests = self._context.Queue(maxsize=1)
        self._results = self._context.Queue(maxsize=1)
        self._process = self._context.Process(
            target=_worker_main,
            args=(self._requests, self._results, self._kernel),
            name="pifire-grey-fit",
        )
        # A spawned interpreter can import NumPy while reconstructing a custom
        # top-level kernel.  Set limits for the inherited child environment
        # across start(), then restore the parent exactly.
        with _SPAWN_ENVIRONMENT_LOCK:
            previous = {name: os.environ.get(name) for name in _THREAD_VARIABLES}
            try:
                for name in _THREAD_VARIABLES:
                    os.environ[name] = "1"
                self._process.start()
            finally:
                for name, value in previous.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        return self

    def submit(self, job: GreyFitJob) -> FitSubmission:
        if not isinstance(job, GreyFitJob):
            raise TypeError("job must be a GreyFitJob")
        self.start()
        if self._pending is not None:
            return FitSubmission.BUSY
        if not self.alive:
            raise RuntimeError("grey fitting process is not alive")
        self._requests.put_nowait(job)
        self._pending = job
        return FitSubmission.ACCEPTED

    def receive(self, timeout_s: float | None = None) -> GreyFitMessage:
        if self._pending is None:
            raise RuntimeError("no grey fit result is outstanding")
        if timeout_s is not None:
            timeout_s = _finite(timeout_s, "timeout_s")
            if timeout_s < 0.0:
                raise ValueError("timeout_s must be non-negative")
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while True:
            wait_s = 0.1 if deadline is None else max(0.0, min(0.1, deadline - time.monotonic()))
            try:
                message = self._results.get(timeout=wait_s)
                break
            except queue.Empty as error:
                if not self.alive:
                    job = self._pending
                    self._pending = None
                    return GreyFitMessage(
                        request=job.request,
                        outcome=GreyFitError(
                            request=job.request,
                            code=FitErrorCode.PROCESS_EXIT,
                            error_type="WorkerProcessExit",
                            detail=f"grey fitting process exited with code {self._process.exitcode}",
                        ),
                        worker_start_method="spawn",
                        worker_thread_environment=tuple((name, "1") for name in _THREAD_VARIABLES),
                    )
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("grey fit result was not ready before timeout") from error
        if not isinstance(message, GreyFitMessage) or message.request != self._pending.request:
            raise RuntimeError("grey fitting process returned an invalid message")
        self._pending = None
        return message

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            if process.is_alive():
                self._requests.put(None)
                process.join(timeout=5.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5.0)
            process.close()
        for channel in (self._requests, self._results):
            if channel is not None:
                channel.close()
                channel.cancel_join_thread()
        self._process = None
        self._requests = None
        self._results = None
        self._pending = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class HistoryDecision:
    accepted: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TriggerConfig:
    min_samples: int = 120
    min_input_variance: float = 0.02
    min_input_levels: int = 3
    min_temperature_span_c: float = 8.0
    min_identifiability: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(self, "min_samples", _positive_int(self.min_samples, "min_samples"))
        object.__setattr__(self, "min_input_levels", _positive_int(self.min_input_levels, "min_input_levels"))
        for name in ("min_input_variance", "min_temperature_span_c", "min_identifiability"):
            value = _finite(getattr(self, name), name)
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class TriggerDecision:
    ready: bool
    blockers: tuple[str, ...]
    input_variance: float
    input_levels: int


def fit_trigger(
    observations: Sequence[Any], *, identifiability: float, config: TriggerConfig | None = None
) -> TriggerDecision:
    from controller.model_learning.contracts import FrameObservation

    resolved = TriggerConfig() if config is None else config
    if not isinstance(resolved, TriggerConfig):
        raise TypeError("config must be a TriggerConfig")
    frames = tuple(observations)
    if not all(isinstance(frame, FrameObservation) for frame in frames):
        raise ValueError("observations must contain FrameObservation values")
    loads = tuple(frame.realized_q for frame in frames)
    if loads:
        mean = sum(loads) / len(loads)
        input_variance = sum((value - mean) ** 2 for value in loads) / len(loads)
        input_levels = len({round(value, 9) for value in loads})
    else:
        input_variance = 0.0
        input_levels = 0
    if len(frames) < resolved.min_samples:
        return TriggerDecision(False, ("minimum-samples",), input_variance, input_levels)
    blockers: list[str] = []
    if input_variance < resolved.min_input_variance or input_levels < resolved.min_input_levels:
        blockers.append("insufficient-excitation")
    temperatures = tuple(frame.temp_c for frame in frames)
    if max(temperatures) - min(temperatures) < resolved.min_temperature_span_c:
        blockers.append("insufficient-coverage")
    if any(not frame.continuous for frame in frames) or any(
        later.observation_sequence != earlier.observation_sequence + 1
        or not math.isclose(later.frame_start_s, earlier.frame_end_s, rel_tol=0.0, abs_tol=1e-9)
        for earlier, later in itertools.pairwise(frames)
    ):
        blockers.append("discontinuity")
    score = _finite(identifiability, "identifiability")
    if score < resolved.min_identifiability:
        blockers.append("identifiability")
    return TriggerDecision(not blockers, tuple(blockers), input_variance, input_levels)


def persistent_corpus_trigger(
    snapshot: Any,
    *,
    config: TriggerConfig | None = None,
) -> TriggerDecision:
    """Evaluate passive-fit readiness over durable scored rows by segment."""
    from common.persistence.learning_trajectory import FitCorpusSnapshot

    resolved = TriggerConfig() if config is None else config
    if not isinstance(snapshot, FitCorpusSnapshot):
        raise TypeError("snapshot must be a FitCorpusSnapshot")
    if not isinstance(resolved, TriggerConfig):
        raise TypeError("config must be a TriggerConfig")
    frames = tuple(frame for segment in snapshot.segments for frame in segment.scored_hold_frames)
    loads = tuple(float(frame.normalized_combustion_load) for frame in frames)
    if loads:
        mean = sum(loads) / len(loads)
        input_variance = sum((value - mean) ** 2 for value in loads) / len(loads)
        input_levels = len({round(value, 9) for value in loads})
    else:
        input_variance = 0.0
        input_levels = 0
    if len(frames) < resolved.min_samples:
        return TriggerDecision(
            False,
            ("minimum-samples",),
            input_variance,
            input_levels,
        )
    blockers: list[str] = []
    if input_variance < resolved.min_input_variance or input_levels < resolved.min_input_levels:
        blockers.append("insufficient-excitation")
    temperatures = tuple(float(frame.chamber_temperature_c) for frame in frames)
    if not temperatures or max(temperatures) - min(temperatures) < resolved.min_temperature_span_c:
        blockers.append("insufficient-coverage")
    if any(not frame.complete or not frame.continuous or frame.partial for frame in frames):
        blockers.append("discontinuity")
    return TriggerDecision(
        not blockers,
        tuple(blockers),
        input_variance,
        input_levels,
    )


def stale_result_reasons(
    result: Any,
    *,
    request: Any,
    current_window: Any,
    current_candidate_generation: int,
    current_origin: Any,
) -> tuple[str, ...]:
    from controller.model_learning.contracts import FitRequest, FitResult, FitWindowIdentity

    if not isinstance(result, FitResult):
        raise TypeError("result must be a FitResult")
    if not isinstance(request, FitRequest):
        raise TypeError("request must be a FitRequest")
    if not isinstance(current_window, FitWindowIdentity):
        raise TypeError("current_window must be a FitWindowIdentity")
    current_generation = _nonnegative_int(current_candidate_generation, "current_candidate_generation")
    submitted = request.window
    returned = result.window
    reasons: list[str] = []
    if result.origin != request.origin or current_origin != request.origin:
        reasons.append("origin-changed")
    if result.request_id != request.request_id:
        reasons.append("request-changed")
    if returned.session_id != submitted.session_id or current_window.session_id != submitted.session_id:
        reasons.append("session-changed")
    if returned.cook_id != submitted.cook_id or current_window.cook_id != submitted.cook_id:
        reasons.append("cook-changed")
    if (
        returned.first_observation_sequence != submitted.first_observation_sequence
        or returned.last_observation_sequence != submitted.last_observation_sequence
        or current_window.first_observation_sequence != submitted.first_observation_sequence
        or current_window.last_observation_sequence != submitted.last_observation_sequence
    ):
        reasons.append("window-changed")
    if (
        returned.configuration_digest != submitted.configuration_digest
        or current_window.configuration_digest != submitted.configuration_digest
    ):
        reasons.append("configuration-changed")
    if (
        returned.incumbent_digest != submitted.incumbent_digest
        or current_window.incumbent_digest != submitted.incumbent_digest
    ):
        reasons.append("incumbent-changed")
    if (
        returned.role_generation != submitted.role_generation
        or current_window.role_generation != submitted.role_generation
    ):
        reasons.append("role-generation-changed")
    if (
        result.candidate_generation != request.candidate_generation
        or current_generation != request.candidate_generation
    ):
        reasons.append("candidate-generation-changed")
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class TargetTimingEvidence:
    target: str
    samples: int
    p99_ms: float
    limit_ms: float

    def __post_init__(self) -> None:
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError("target must be non-blank")
        object.__setattr__(self, "samples", _positive_int(self.samples, "samples"))
        for name in ("p99_ms", "limit_ms"):
            value = _finite(getattr(self, name), name)
            if value < 0.0 or (name == "limit_ms" and value == 0.0):
                raise ValueError(f"{name} must be {'positive' if name == 'limit_ms' else 'non-negative'}")
            object.__setattr__(self, name, value)

    @property
    def accepted(self) -> bool:
        return self.p99_ms <= self.limit_ms


@dataclass(frozen=True, slots=True)
class CandidatePair:
    estimator: Any
    controller: Any

    def __post_init__(self) -> None:
        if self.estimator is None or self.controller is None:
            raise ValueError("candidate pair requires estimator and controller")


def grey_config_digest(config: Any) -> str:
    from controller.acados.contracts import GreyBoxMPCConfig

    if not isinstance(config, GreyBoxMPCConfig):
        raise TypeError("config must be a GreyBoxMPCConfig")
    document = {name: getattr(config, name) for name in config.__dataclass_fields__}
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidatePreparation:
    candidate: GreyFitSuccess
    incumbent_pair: Any
    accepted: bool
    blockers: tuple[str, ...]
    candidate_pair: CandidatePair | Any | None = None
    dry_solve_finite: bool = False
    timing: TargetTimingEvidence | None = None
    detail: str | None = None
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, GreyFitSuccess):
            raise TypeError("candidate must be a GreyFitSuccess")
        blockers = tuple(self.blockers)
        if not all(isinstance(value, str) and value for value in blockers):
            raise ValueError("blockers must be non-blank strings")
        object.__setattr__(self, "blockers", blockers)
        object.__setattr__(self, "candidate_digest", grey_config_digest(self.candidate.config))
        if self.accepted:
            if blockers or self.candidate_pair is None or not self.dry_solve_finite:
                raise ValueError("accepted preparation must own a finite candidate pair without blockers")
            if not isinstance(self.timing, TargetTimingEvidence) or not self.timing.accepted:
                raise ValueError("accepted preparation requires passing target timing")
        elif self.candidate_pair is not None:
            raise ValueError("rejected preparation cannot retain a candidate pair")

    @classmethod
    def accepted_for_test(
        cls, *, candidate: GreyFitSuccess, candidate_pair: Any, incumbent_pair: Any, timing: TargetTimingEvidence
    ) -> CandidatePreparation:
        return cls(
            candidate=candidate,
            incumbent_pair=incumbent_pair,
            accepted=True,
            blockers=(),
            candidate_pair=candidate_pair,
            dry_solve_finite=True,
            timing=timing,
        )


def _close_if_owned(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _rejected_candidate(
    candidate: GreyFitSuccess,
    incumbent_pair: Any,
    blocker: str,
    error: BaseException | str,
    *,
    timing: TargetTimingEvidence | None = None,
) -> CandidatePreparation:
    return CandidatePreparation(
        candidate=candidate,
        incumbent_pair=incumbent_pair,
        accepted=False,
        blockers=(blocker,),
        timing=timing,
        detail=str(error),
    )


def _finite_dry_solve(solve: Any, expected_horizon: int) -> bool:
    try:
        sequence = tuple(solve.sequence_q)
        objective = float(solve.objective)
    except AttributeError, TypeError, ValueError:
        return False
    return (
        len(sequence) == expected_horizon
        and math.isfinite(objective)
        and all(math.isfinite(float(value)) for value in sequence)
    )


def prepare_candidate_off_path(
    candidate: GreyFitSuccess,
    *,
    incumbent_pair: Any,
    estimator_factory: Callable[[Any], Any],
    controller_factory: Callable[[Any], Any],
    timing_probe: Callable[[Any], TargetTimingEvidence],
) -> CandidatePreparation:
    """Build and exercise a complete challenger without touching the incumbent."""
    if not isinstance(candidate, GreyFitSuccess):
        raise TypeError("candidate must be a GreyFitSuccess")
    try:
        estimator = estimator_factory(candidate.config)
    except Exception as error:
        return _rejected_candidate(candidate, incumbent_pair, "estimator-build", error)
    try:
        controller = controller_factory(candidate.config)
    except Exception as error:
        _close_if_owned(estimator)
        return _rejected_candidate(candidate, incumbent_pair, "native-build", error)
    pair = CandidatePair(estimator=estimator, controller=controller)
    try:
        state = getattr(estimator, "state", getattr(estimator, "x", (0.0,) * candidate.config.state_size))
        state_values = tuple(float(value) for value in state)
        if len(state_values) != candidate.config.state_size or not all(math.isfinite(value) for value in state_values):
            raise ValueError("candidate estimator state must be a finite ten-state vector")
        solve = controller.solve(
            state,
            setpoint_c=candidate.config.T_amb + 50.0,
            q_previous=0.0,
            equilibrium_q=0.4,
        )
        if not _finite_dry_solve(solve, candidate.config.horizon_steps):
            raise ValueError("candidate native dry solve was non-finite")
    except Exception as error:
        _close_if_owned(controller)
        _close_if_owned(estimator)
        return _rejected_candidate(candidate, incumbent_pair, "native-dry-solve", error)
    timing: TargetTimingEvidence | None = None
    try:
        timing_result = timing_probe(controller)
        if not isinstance(timing_result, TargetTimingEvidence):
            raise TypeError("timing probe must return TargetTimingEvidence")
        timing = timing_result
        if not timing.accepted:
            raise RuntimeError(f"target p99 {timing.p99_ms} ms exceeds {timing.limit_ms} ms")
    except Exception as error:
        _close_if_owned(controller)
        _close_if_owned(estimator)
        evidence = timing if isinstance(timing, TargetTimingEvidence) else None
        return _rejected_candidate(candidate, incumbent_pair, "target-timing", error, timing=evidence)
    return CandidatePreparation(
        candidate=candidate,
        incumbent_pair=incumbent_pair,
        accepted=True,
        blockers=(),
        candidate_pair=pair,
        dry_solve_finite=True,
        timing=timing,
    )


@dataclass(frozen=True, slots=True)
class CausalForecastInput:
    frame: Any
    horizon_steps: int
    candidate_generation: int
    incumbent_digest: str
    challenger_digest: str


def paired_forecast_origin(
    frame: Any,
    *,
    horizon_steps: int,
    candidate_generation: int,
    incumbent_digest: str,
    challenger_digest: str,
    incumbent_predict: Callable[[CausalForecastInput], float],
    challenger_predict: Callable[[CausalForecastInput], float],
) -> Any | None:
    """Call both predictors with one shared immutable, pre-observation origin."""
    from controller.model_learning.contracts import FrameObservation
    from controller.model_learning.evaluation import ForecastOrigin

    if not isinstance(frame, FrameObservation):
        raise TypeError("frame must be a FrameObservation")
    if frame.calibration_fit or frame.calibration_stage is not None or frame.probe_q != 0.0:
        return None
    shared = CausalForecastInput(
        frame=frame,
        horizon_steps=horizon_steps,
        candidate_generation=candidate_generation,
        incumbent_digest=incumbent_digest,
        challenger_digest=challenger_digest,
    )
    incumbent_prediction = incumbent_predict(shared)
    challenger_prediction = challenger_predict(shared)
    return ForecastOrigin(
        origin_sequence=frame.observation_sequence,
        origin_time_s=frame.frame_end_s,
        horizon_steps=horizon_steps,
        role_generation=frame.role_generation,
        candidate_generation=candidate_generation,
        incumbent_digest=incumbent_digest,
        challenger_digest=challenger_digest,
        incumbent_prediction_c=incumbent_prediction,
        challenger_prediction_c=challenger_prediction,
        temperature_band=frame.temperature_band or "unknown",
        phase="hold",
        ambient_source=frame.ambient_source,
        calibration_fit=False,
    )


@dataclass(frozen=True, slots=True)
class CandidateHandoff:
    status: Any
    policy: Any | None
    prepared_id: Any | None
    active_pair: Any
    blockers: tuple[str, ...] = ()


class CandidateOwnershipTransferredError(RuntimeError):
    """Preparation failed after the activation runtime accepted pair ownership."""


def handoff_candidate(
    prepared: CandidatePreparation,
    *,
    evaluation: Any,
    confidence_accepted: bool,
    online_enabled: bool,
    prepare: Callable[[CandidatePreparation, Any], Any],
    install: Callable[[Any], Any],
) -> CandidateHandoff:
    """Prepare persistence handoff only; this pipeline never installs or swaps a pair."""
    from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin, LearningStatus

    if not isinstance(prepared, CandidatePreparation):
        raise TypeError("prepared must be a CandidatePreparation")
    if not isinstance(confidence_accepted, bool) or not isinstance(online_enabled, bool):
        raise TypeError("confidence_accepted and online_enabled must be bools")
    if not callable(prepare) or not callable(install):
        raise TypeError("prepare and install must be callable")
    del install  # Ownership transfer is intentionally outside this pipeline.
    blockers: list[str] = []
    if not prepared.accepted:
        blockers.extend(prepared.blockers or ("candidate-rejected",))
    if not bool(getattr(evaluation, "accepted", False)):
        blockers.append("evaluation")
    if int(getattr(evaluation, "consecutive_wins", 0)) < 2:
        blockers.append("consecutive-confidence")
    request = prepared.candidate.request
    if (
        getattr(evaluation, "role_generation", None) != request.window.role_generation
        or getattr(evaluation, "candidate_generation", None) != request.candidate_generation
    ):
        blockers.append("stale-generation")
    if getattr(evaluation, "incumbent_digest", None) != request.window.incumbent_digest:
        blockers.append("incumbent-changed")
    if getattr(evaluation, "challenger_digest", None) != prepared.candidate_digest:
        blockers.append("challenger-changed")
    if not confidence_accepted:
        blockers.append("confidence")
    origin = request.origin
    if origin is CandidateOrigin.COOK_REFIT:
        raise ValueError("cook-refit handoff is not owned by this pipeline")
    policy = (
        ActivationPolicy.OPERATOR_REVIEWED
        if origin is CandidateOrigin.OPERATOR_CALIBRATION
        else ActivationPolicy.PASSIVE_AUTO
    )
    if origin is CandidateOrigin.PASSIVE_ONLINE and not online_enabled:
        blockers.append("online-disabled")
    if blockers:
        return CandidateHandoff(
            status=LearningStatus.EVALUATING,
            policy=None,
            prepared_id=None,
            active_pair=prepared.incumbent_pair,
            blockers=tuple(blockers),
        )
    prepared_id = prepare(prepared, policy)
    status = (
        LearningStatus.READY_FOR_REVIEW if policy is ActivationPolicy.OPERATOR_REVIEWED else LearningStatus.ACTIVATING
    )
    return CandidateHandoff(
        status=status,
        policy=policy,
        prepared_id=prepared_id,
        active_pair=prepared.incumbent_pair,
    )


@dataclass(frozen=True, slots=True)
class LiveLearningIdentity:
    """The scheduler's live identity input to the otherwise off-path fit pipeline."""

    session_id: str
    cook_id: str | None
    configuration_digest: str
    incumbent_digest: str
    role_generation: int
    candidate_generation: int

    def __post_init__(self) -> None:
        from controller.model_learning.contracts import FitWindowIdentity

        # Reuse the neutral validator with an empty-but-valid sequence window.
        FitWindowIdentity(
            session_id=self.session_id,
            cook_id=self.cook_id,
            first_observation_sequence=0,
            last_observation_sequence=0,
            configuration_digest=self.configuration_digest,
            incumbent_digest=self.incumbent_digest,
            role_generation=self.role_generation,
        )
        object.__setattr__(
            self,
            "role_generation",
            _nonnegative_int(self.role_generation, "role_generation"),
        )
        object.__setattr__(
            self,
            "candidate_generation",
            _nonnegative_int(self.candidate_generation, "candidate_generation"),
        )

    def window(self, first_sequence: int, last_sequence: int) -> Any:
        from controller.model_learning.contracts import FitWindowIdentity

        return FitWindowIdentity(
            session_id=self.session_id,
            cook_id=self.cook_id,
            first_observation_sequence=first_sequence,
            last_observation_sequence=last_sequence,
            configuration_digest=self.configuration_digest,
            incumbent_digest=self.incumbent_digest,
            role_generation=self.role_generation,
        )


def segmented_corpus_fit_job(
    snapshot: Any,
    request: Any,
    config: Any,
) -> GreyFitJob:
    """Materialize one immutable persistent-corpus snapshot for the fit worker."""
    from common.persistence.learning_trajectory import FitCorpusSnapshot
    from controller.model_learning.contracts import FitRequest

    if not isinstance(snapshot, FitCorpusSnapshot):
        raise TypeError("snapshot must be a FitCorpusSnapshot")
    if not isinstance(request, FitRequest):
        raise TypeError("request must be a FitRequest")
    if len(snapshot.identity.slices) != len(snapshot.segments):
        raise ValueError("fit corpus snapshot slices must match segments")

    arrays: list[GreyFitSegmentArrays] = []
    for corpus_slice, segment in zip(
        snapshot.identity.slices,
        snapshot.segments,
        strict=True,
    ):
        pre_roll = tuple(segment.pre_roll_frames)
        scored = tuple(segment.scored_hold_frames)
        if not scored or segment.hold_entry is None:
            raise ValueError(f"fit corpus segment {segment.segment_id} has no scored Hold anchor")
        oldest = pre_roll[0] if pre_roll else scored[0]
        arrays.append(
            GreyFitSegmentArrays(
                segment_id=segment.segment_id,
                cook_id=segment.cook_id,
                through_ordinal=corpus_slice.through_ordinal,
                prefix_digest=corpus_slice.prefix_digest,
                fit_partition_digest=snapshot.identity.fit_partition_digest,
                observation_sequences=tuple(frame.sequence for frame in scored),
                initial_load=oldest.normalized_combustion_load,
                pre_roll_duration_s=tuple(
                    (frame.monotonic_end_ms - frame.monotonic_start_ms) / 1_000.0 for frame in pre_roll
                ),
                pre_roll_load=tuple(frame.normalized_combustion_load for frame in pre_roll),
                pre_roll_temperature_c=tuple(frame.chamber_temperature_c for frame in pre_roll),
                hold_anchor_c=segment.hold_entry.chamber_temperature_c,
                scored_duration_s=tuple(
                    (frame.monotonic_end_ms - frame.monotonic_start_ms) / 1_000.0 for frame in scored
                ),
                scored_load=tuple(frame.normalized_combustion_load for frame in scored),
                scored_ambient_c=tuple(frame.ambient_temperature_c for frame in scored),
                scored_temperature_c=tuple(frame.chamber_temperature_c for frame in scored),
                calibration_origin=tuple(frame.calibration_origin for frame in scored),
            )
        )
    return GreyFitJob(
        request=request,
        corpus=snapshot.identity,
        segments=tuple(arrays),
        config=config,
    )


@dataclass(frozen=True, slots=True)
class GreyLearningObservation:
    history: HistoryDecision
    trigger: TriggerDecision
    submission: FitSubmission | None
    request: Any | None
    completed_forecasts: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class GreyLearningDelivery:
    message: GreyFitMessage | None
    stale_reasons: tuple[str, ...]
    preparation: CandidatePreparation | None
    blockers: tuple[str, ...] = ()


class GreyLearningOrchestrator:
    """Cohesive grey-learning fit pipeline, scheduled off the control worker.

    This owner deliberately has no Controller/Hold references and never installs
    a pair.  The scheduler supplies live identities, calls ``poll_fit_off_path``
    on its lifecycle worker, and supplies the preparation callback.
    """

    def __init__(
        self,
        *,
        identity: LiveLearningIdentity,
        config: Any,
        incumbent_pair: Any,
        estimator_factory: Callable[[Any], Any],
        controller_factory: Callable[[Any], Any],
        timing_probe: Callable[[Any], TargetTimingEvidence],
        trigger_config: TriggerConfig | None = None,
        evaluation_config: Any | None = None,
        worker: Any | None = None,
    ) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig
        from controller.model_learning.evaluation import EvaluationConfig

        if not isinstance(identity, LiveLearningIdentity):
            raise TypeError("identity must be a LiveLearningIdentity")
        if not isinstance(config, GreyBoxMPCConfig):
            raise TypeError("config must be a GreyBoxMPCConfig")
        self.identity = identity
        self.config = config
        self.incumbent_pair = incumbent_pair
        self.estimator_factory = estimator_factory
        self.controller_factory = controller_factory
        self.timing_probe = timing_probe
        self.trigger_config = TriggerConfig() if trigger_config is None else trigger_config
        self.evaluation_config = EvaluationConfig() if evaluation_config is None else evaluation_config
        if not isinstance(self.trigger_config, TriggerConfig):
            raise TypeError("trigger_config must be a TriggerConfig")
        if not isinstance(self.evaluation_config, EvaluationConfig):
            raise TypeError("evaluation_config must be an EvaluationConfig")
        self.worker = GreyFitWorker() if worker is None else worker
        self._pending_request: FitRequest | None = None
        self._prepared: CandidatePreparation | None = None
        self._evaluator: Any | None = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._evaluation_epoch = 0
        self._last_evaluation: Any | None = None
        self._handoff: CandidateHandoff | None = None
        self._started = False
        self._ownership_transferred = False
        self._forced_stale_request_id: str | None = None

    @property
    def pending_request(self) -> FitRequest | None:
        return self._pending_request

    @property
    def prepared(self) -> CandidatePreparation | None:
        return self._prepared

    @property
    def last_evaluation(self) -> Any | None:
        return self._last_evaluation

    @property
    def evaluation_epoch(self) -> int:
        return self._evaluation_epoch

    @property
    def handoff(self) -> CandidateHandoff | None:
        return self._handoff

    def start(self) -> GreyLearningOrchestrator:
        if not self._started:
            start = getattr(self.worker, "start", None)
            if callable(start):
                start()
            self._started = True
        return self

    def _release_prepared(self) -> None:
        if (
            self._prepared is not None
            and self._prepared.accepted
            and not self._ownership_transferred
            and self._prepared.candidate_pair is not None
        ):
            pair = self._prepared.candidate_pair
            owned = (
                (pair.controller, pair.estimator)
                if hasattr(pair, "controller") and hasattr(pair, "estimator")
                else (pair,)
            )
            closed: set[int] = set()
            for value in owned:
                if id(value) not in closed:
                    _close_if_owned(value)
                    closed.add(id(value))
        self._prepared = None
        self._ownership_transferred = False

    def _can_supersede_prepared_candidate(
        self,
        expected: CandidatePreparation,
    ) -> bool:
        return self._prepared is expected and expected.accepted and not self._ownership_transferred

    def _reset_prepared_evaluation(self) -> None:
        self._evaluator = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._evaluation_epoch = 0
        self._last_evaluation = None
        self._handoff = None

    def submit_superseding_corpus_fit(
        self,
        job: GreyFitJob,
        expected: CandidatePreparation,
        *,
        persist: Callable[[], None],
    ) -> tuple[FitSubmission, bool]:
        """Reserve the worker before releasing an older prepared candidate."""
        if not isinstance(job, GreyFitJob):
            raise TypeError("job must be a GreyFitJob")
        if not callable(persist):
            raise TypeError("persist must be callable")
        if self._pending_request is not None or self._prepared is not expected or not expected.accepted:
            return FitSubmission.BUSY, False
        ownership_transferred = self._ownership_transferred
        submission = self.worker.submit(job)
        if submission is not FitSubmission.ACCEPTED:
            return submission, False
        self._pending_request = job.request
        if ownership_transferred:
            self._release_prepared()
            self._reset_prepared_evaluation()
            return submission, False
        try:
            persist()
        except Exception:
            self._forced_stale_request_id = job.request.request_id
            return submission, False
        self._release_prepared()
        self._reset_prepared_evaluation()
        return submission, True

    def update_identity(
        self,
        identity: LiveLearningIdentity,
        *,
        config: Any | None = None,
        incumbent_pair: Any = _UNSET,
    ) -> None:
        from controller.acados.contracts import GreyBoxMPCConfig

        if not isinstance(identity, LiveLearningIdentity):
            raise TypeError("identity must be a LiveLearningIdentity")
        configuration_changed = identity.configuration_digest != self.identity.configuration_digest
        incumbent_changed = identity.incumbent_digest != self.identity.incumbent_digest
        if configuration_changed and config is None:
            raise ValueError("configuration digest change requires the corresponding config")
        if incumbent_changed and incumbent_pair is _UNSET:
            raise ValueError("incumbent digest change requires the corresponding incumbent pair")
        replacement_config = self.config if config is None else config
        if not isinstance(replacement_config, GreyBoxMPCConfig):
            raise TypeError("config must be a GreyBoxMPCConfig")
        if identity == self.identity and config is None and incumbent_pair is _UNSET:
            return
        replacement_pair = self.incumbent_pair if incumbent_pair is _UNSET else incumbent_pair
        self._release_prepared()
        self.identity = identity
        self.config = replacement_config
        self.incumbent_pair = replacement_pair
        self._evaluator = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._evaluation_epoch = 0
        self._last_evaluation = None
        self._handoff = None

    def rebind_process(
        self,
        identity: LiveLearningIdentity,
        *,
        config: Any,
        incumbent_pair: Any,
        estimator_factory: Callable[..., Any] | None = None,
        controller_factory: Callable[..., Any] | None = None,
        timing_probe: Callable[..., Any] | None = None,
    ) -> None:
        """Rebind a new cook while retaining a compatible prepared candidate."""
        compatible = (
            identity.configuration_digest == self.identity.configuration_digest
            and identity.incumbent_digest == self.identity.incumbent_digest
            and identity.role_generation == self.identity.role_generation
            and identity.candidate_generation == self.identity.candidate_generation
        )
        if not compatible:
            self.update_identity(
                identity,
                config=config,
                incumbent_pair=incumbent_pair,
            )
        self.identity = identity
        self.config = config
        self.incumbent_pair = incumbent_pair
        if estimator_factory is not None:
            self.estimator_factory = estimator_factory
        if controller_factory is not None:
            self.controller_factory = controller_factory
        if timing_probe is not None:
            self.timing_probe = timing_probe

    @staticmethod
    def _frame_rejection(frame: Any, role_generation: int) -> str | None:
        if frame.manual_override or frame.output_source == "manual-override":
            return "manual"
        if frame.lid_open:
            return "lid-open"
        if frame.safety_inhibited:
            return "safety"
        if frame.stale:
            return "stale"
        if frame.skipped or frame.reset:
            return "skipped-or-reset"
        if not frame.continuous:
            return "discontinuity"
        if frame.role_generation != role_generation:
            return "stale-generation"
        if frame.allocation_join_reason is not None:
            return "unknown-actuation"
        if frame.output_source != "controller":
            return "non-controller-output"
        if frame.calibration_fit and not frame.probe_valid:
            return "invalid-probe"
        return None

    def observe_completed_frame(
        self,
        frame: Any,
        *,
        identifiability: float,
    ) -> GreyLearningObservation:
        """Complete causal origins without retaining a volatile fit corpus."""
        from controller.model_learning.contracts import FrameObservation

        if not isinstance(frame, FrameObservation):
            raise TypeError("frame must be a FrameObservation")
        del identifiability
        self.start()
        completed = () if self._evaluator is None or frame.calibration_fit else self._evaluator.observe(frame)
        reason = self._frame_rejection(frame, self.identity.role_generation)
        decision = HistoryDecision(reason is None, () if reason is None else (reason,))
        trigger = TriggerDecision(False, ("persistent-corpus",), 0.0, 0)
        return GreyLearningObservation(
            decision,
            trigger,
            None,
            None,
            tuple(completed),
        )

    def submit_corpus_fit(self, job: GreyFitJob) -> FitSubmission:
        """Submit the one repository-materialized job to this owner's worker."""
        if not isinstance(job, GreyFitJob):
            raise TypeError("job must be a GreyFitJob")
        if self._pending_request is not None or (self._prepared is not None and self._prepared.accepted):
            return FitSubmission.BUSY
        submission = self.worker.submit(job)
        if submission is FitSubmission.ACCEPTED:
            self._pending_request = job.request
        return submission

    def poll_fit_off_path(
        self,
        *,
        live_identity: LiveLearningIdentity,
        live_origin: Any,
    ) -> GreyLearningDelivery | None:
        """Drain, stale-check, and build a candidate; called off the control worker."""
        from controller.model_learning.contracts import FitResult, FitStatus

        if self._pending_request is None:
            return None
        try:
            message = self.worker.receive(timeout_s=0.0)
        except TimeoutError:
            return None
        request = self._pending_request
        self._pending_request = None
        forced_stale = (
            ("candidate-supersession-persistence-failed",)
            if self._forced_stale_request_id == request.request_id
            else ()
        )
        if forced_stale:
            self._forced_stale_request_id = None
            return GreyLearningDelivery(message, forced_stale, None)
        if isinstance(message.outcome, GreyFitError):
            return GreyLearningDelivery(message, (), None, ("fit-error",))
        success = message.outcome
        result = FitResult(
            request_id=request.request_id,
            origin=request.origin,
            window=request.window,
            candidate_generation=request.candidate_generation,
            status=FitStatus.SUCCEEDED,
            candidate_digest=grey_config_digest(success.config),
        )
        current_window = replace(
            request.window,
            configuration_digest=live_identity.configuration_digest,
            incumbent_digest=live_identity.incumbent_digest,
            role_generation=live_identity.role_generation,
        )
        stale = stale_result_reasons(
            result,
            request=request,
            current_window=current_window,
            current_candidate_generation=live_identity.candidate_generation,
            current_origin=live_origin,
        )
        if stale:
            return GreyLearningDelivery(message, stale, None)
        if success.rejection_reasons:
            return GreyLearningDelivery(message, (), None, success.rejection_reasons)
        if success.identifiability < self.trigger_config.min_identifiability:
            return GreyLearningDelivery(message, (), None, ("identifiability",))
        prepared = prepare_candidate_off_path(
            success,
            incumbent_pair=self.incumbent_pair,
            estimator_factory=self.estimator_factory,
            controller_factory=self.controller_factory,
            timing_probe=self.timing_probe,
        )
        self._release_prepared()
        self._prepared = prepared
        if prepared.accepted:
            from controller.model_learning.evaluation import CausalForecastEvaluator

            self._evaluator = CausalForecastEvaluator(
                role_generation=request.window.role_generation,
                candidate_generation=request.candidate_generation,
            )
            self._evaluation_cursor = 0
            self._evaluation_epoch = 0
            self._consecutive_wins = 0
        return GreyLearningDelivery(message, (), prepared)

    def restore_persisted_challenger(
        self,
        preparation: CandidatePreparation,
        *,
        evaluation_epoch: int,
        consecutive_wins: int,
    ) -> None:
        """Restore complete durable progress into a fresh empty evaluator."""

        from controller.model_learning.evaluation import CausalForecastEvaluator

        if not isinstance(preparation, CandidatePreparation) or not preparation.accepted:
            raise ValueError("persisted challenger requires an accepted preparation")
        if isinstance(evaluation_epoch, bool) or not isinstance(evaluation_epoch, int) or evaluation_epoch < 0:
            raise ValueError("evaluation_epoch must be a non-negative integer")
        if (
            isinstance(consecutive_wins, bool)
            or not isinstance(consecutive_wins, int)
            or consecutive_wins < 0
            or consecutive_wins > self.evaluation_config.required_consecutive_wins
        ):
            raise ValueError("consecutive_wins is outside the evaluation requirement")
        if self._prepared is not preparation:
            self._release_prepared()
            self._prepared = preparation
        request = preparation.candidate.request
        self._evaluator = CausalForecastEvaluator(
            role_generation=request.window.role_generation,
            candidate_generation=request.candidate_generation,
        )
        self._evaluation_cursor = 0
        self._evaluation_epoch = evaluation_epoch
        self._consecutive_wins = consecutive_wins
        self._last_evaluation = None
        self._handoff = None
        self._ownership_transferred = False

    def register_causal_forecasts(
        self,
        frame: Any,
        *,
        incumbent_predict: Callable[[CausalForecastInput], float],
        challenger_predict: Callable[[CausalForecastInput], float],
    ) -> tuple[Any, ...]:
        if self._prepared is None or not self._prepared.accepted or self._evaluator is None:
            return ()
        request = self._prepared.candidate.request
        origins = []
        for horizon in self.evaluation_config.required_horizons:
            origin = paired_forecast_origin(
                frame,
                horizon_steps=horizon,
                candidate_generation=request.candidate_generation,
                incumbent_digest=request.window.incumbent_digest,
                challenger_digest=self._prepared.candidate_digest,
                incumbent_predict=incumbent_predict,
                challenger_predict=challenger_predict,
            )
            if origin is not None:
                self._evaluator.register(origin)
                origins.append(origin)
        return tuple(origins)

    def evaluate_ready_off_path(self) -> Any | None:
        from controller.model_learning.evaluation import evaluate_forecasts

        if self._evaluator is None or self._prepared is None:
            return None
        rows = self._evaluator.completed_origins[self._evaluation_cursor :]
        horizons = {row.horizon_steps for row in rows}
        if not set(self.evaluation_config.required_horizons) <= horizons:
            return None
        request = self._prepared.candidate.request
        decision = evaluate_forecasts(
            tuple(rows),
            role_generation=request.window.role_generation,
            candidate_generation=request.candidate_generation,
            prior_consecutive_wins=self._consecutive_wins,
            config=self.evaluation_config,
        )
        self._evaluation_cursor = len(self._evaluator.completed_origins)
        self._consecutive_wins = decision.consecutive_wins
        self._last_evaluation = decision
        return decision

    def handoff_if_ready(
        self,
        *,
        confidence_accepted: bool,
        online_enabled: bool,
        prepare: Callable[[CandidatePreparation, Any], Any],
    ) -> CandidateHandoff | None:
        if self._ownership_transferred:
            return self._handoff
        if self._prepared is None or self._last_evaluation is None:
            return None
        try:
            self._handoff = handoff_candidate(
                self._prepared,
                evaluation=self._last_evaluation,
                confidence_accepted=confidence_accepted,
                online_enabled=online_enabled,
                prepare=prepare,
                install=lambda _pair: (_ for _ in ()).throw(
                    AssertionError("this pipeline cannot install a runtime pair")
                ),
            )
        except CandidateOwnershipTransferredError:
            self._ownership_transferred = True
            self._release_prepared()
            raise
        if not self._handoff.blockers:
            self._ownership_transferred = True
        return self._handoff

    def retire_evaluated_candidate(self, decision: Any) -> bool:
        """Release one terminally rejected candidate so a later fit may proceed."""
        if (
            decision is not self._last_evaluation
            or bool(getattr(decision, "accepted", False))
            or not tuple(getattr(decision, "blockers", ()))
        ):
            return False
        self._release_prepared()
        self._evaluator = None
        self._evaluation_cursor = 0
        self._consecutive_wins = 0
        self._last_evaluation = None
        self._handoff = None
        return True

    def close(self) -> None:
        self._release_prepared()
        close = getattr(self.worker, "close", None)
        if callable(close):
            close()
        self._started = False

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
