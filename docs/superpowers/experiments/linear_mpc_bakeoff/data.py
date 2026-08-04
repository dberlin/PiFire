"""Input reconstruction, validation, framing, and dataset partitioning."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import numpy as np

from .contracts import DatasetSplit, SignalRecord


class ValidationError(ValueError):
    """A signal record cannot safely support model fitting."""


_MAK_AMBIENT_C: Final = 20.0


def validate_record(record: SignalRecord, expected_frame_s: float | None = None) -> None:
    """Reject records whose samples cannot be interpreted as one safe signal."""
    arrays = {
        "time_s": record.time_s,
        "temp_c": record.temp_c,
        "q": record.q,
        "ambient_c": record.ambient_c,
    }
    for name, values in arrays.items():
        if values.ndim != 1:
            raise ValidationError(f"{name} must be one-dimensional")
        if not np.isfinite(values).all():
            raise ValidationError(f"{name} must contain only finite values")

    lengths = {values.size for values in arrays.values()}
    if len(lengths) != 1:
        raise ValidationError("numeric arrays must have equal lengths")
    if record.time_s.size < 2:
        raise ValidationError("record must contain at least two samples")
    if not np.all(np.diff(record.time_s) > 0.0):
        raise ValidationError("timestamps must be strictly increasing")
    if not np.all((0.0 <= record.q) & (record.q <= 1.0)):
        raise ValidationError("q must be within [0, 1]")

    if expected_frame_s is not None:
        if not np.isfinite(expected_frame_s) or expected_frame_s <= 0.0:
            raise ValidationError("expected_frame_s must be finite and positive")
        if np.any(np.diff(record.time_s) > expected_frame_s):
            raise ValidationError("unknown actuation interval exceeds the expected frame")


def reconstruct_mak_fixture(path: Path) -> SignalRecord:
    """Reconstruct MAK's normalized requested auger duty from its raw ``Q``."""
    rows = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.float64, ndmin=2)
    if rows.shape[1] != 3:
        raise ValidationError("MAK fixture must have time_s, temp_c, and Q columns")

    time_s, temp_c, requested_q = rows.T
    frac = np.clip((requested_q - 5.0) / 95.0, 0.0, 1.0)
    q = 0.1 + frac * 0.8
    return SignalRecord(
        time_s=time_s,
        temp_c=temp_c,
        q=q,
        ambient_c=np.full_like(temp_c, _MAK_AMBIENT_C),
        provenance="requested-input-reconstruction",
        metadata={
            "source": str(path),
            "input_column": "Q",
            "ambient_c": _MAK_AMBIENT_C,
        },
    )


def resample_record(record: SignalRecord, frame_s: float) -> SignalRecord:
    """Frame a record without changing zero-order-held requested-input energy."""
    validate_record(record, expected_frame_s=frame_s)
    if not np.isfinite(frame_s) or frame_s <= 0.0:
        raise ValidationError("frame_s must be finite and positive")

    frame_count = int(np.floor((record.time_s[-1] - record.time_s[0]) / frame_s))
    boundaries = record.time_s[0] + frame_s * np.arange(1, frame_count + 1)
    elapsed = np.diff(record.time_s)
    cumulative_q = np.concatenate(
        (np.array([0.0]), np.cumsum(record.q[:-1] * elapsed))
    )

    def integral_at(boundary_s: np.ndarray) -> np.ndarray:
        indexes = np.searchsorted(record.time_s, boundary_s, side="right") - 1
        return cumulative_q[indexes] + record.q[indexes] * (
            boundary_s - record.time_s[indexes]
        )

    starts = boundaries - frame_s
    q = (integral_at(boundaries) - integral_at(starts)) / frame_s
    return SignalRecord(
        time_s=boundaries,
        temp_c=np.interp(boundaries, record.time_s, record.temp_c),
        q=q,
        ambient_c=np.interp(boundaries, record.time_s, record.ambient_c),
        provenance=record.provenance,
        metadata=dict(record.metadata),
    )


def chronological_split(
    record: SignalRecord,
    fit_fraction: float,
    validation_fraction: float,
) -> DatasetSplit:
    """Split one validated record into contiguous fit, validation, and test data."""
    validate_record(record)
    if not (0.0 < fit_fraction < 1.0):
        raise ValidationError("fit_fraction must be between 0 and 1")
    if not (0.0 < validation_fraction < 1.0):
        raise ValidationError("validation_fraction must be between 0 and 1")
    if fit_fraction + validation_fraction >= 1.0:
        raise ValidationError("fit and validation fractions must leave a test partition")

    fit_end = int(record.time_s.size * fit_fraction)
    validation_end = fit_end + int(record.time_s.size * validation_fraction)
    if min(fit_end, validation_end - fit_end, record.time_s.size - validation_end) < 1:
        raise ValidationError("each chronological split must contain at least one sample")

    def partition(start: int, end: int) -> SignalRecord:
        return SignalRecord(
            time_s=record.time_s[start:end],
            temp_c=record.temp_c[start:end],
            q=record.q[start:end],
            ambient_c=record.ambient_c[start:end],
            provenance=record.provenance,
            metadata=dict(record.metadata),
        )

    return DatasetSplit(
        fit=partition(0, fit_end),
        validation=partition(fit_end, validation_end),
        test=partition(validation_end, record.time_s.size),
    )
