"""Contracts for the standalone linear-MPC model bake-off data path."""

from pathlib import Path

import numpy as np
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord
from docs.superpowers.experiments.linear_mpc_bakeoff.data import (
    ValidationError,
    chronological_split,
    reconstruct_mak_fixture,
    resample_record,
    validate_record,
)

FIXTURE = Path("tests/unit/mpc/fixtures/mak_cook_2026-08-02.csv")


def make_record(
    count: int | None = None,
    *,
    time_s: list[float] | None = None,
    q: list[float] | None = None,
) -> SignalRecord:
    """Build a finite, monotonic record without depending on later modules."""
    if time_s is None:
        assert count is not None
        time_s = np.arange(count, dtype=np.float64).tolist()
    if q is None:
        q = np.full(len(time_s), 0.5, dtype=np.float64).tolist()
    return SignalRecord(
        time_s=np.asarray(time_s),
        temp_c=np.asarray(time_s),
        q=np.asarray(q),
        ambient_c=np.full(len(time_s), 20.0),
        provenance="test-fixture",
        metadata={},
    )


def test_mak_q_is_reconstructed_mean_auger_duty() -> None:
    record = reconstruct_mak_fixture(FIXTURE)

    assert record.q[0] == pytest.approx(0.9)
    assert record.q[168] == pytest.approx(0.8735385263157894)
    assert record.q[-1] == pytest.approx(0.1)
    assert record.provenance == "requested-input-reconstruction"


def test_resampling_preserves_auger_energy() -> None:
    record = make_record(time_s=[0, 5, 10, 15, 20], q=[0, 1, 0, 1, 0])

    framed = resample_record(record, frame_s=20.0)

    assert framed.time_s.tolist() == pytest.approx([20.0])
    assert framed.q.tolist() == pytest.approx([0.5])
    assert framed.temp_c.tolist() == pytest.approx([20.0])


def test_resampling_rejects_frame_with_unknown_actuation_interval() -> None:
    record = make_record(time_s=[0, 5, 30], q=[0.2, 0.3, 0.4])

    with pytest.raises(ValidationError, match="unknown actuation interval"):
        resample_record(record, frame_s=20.0)


def test_validation_rejects_unknown_input_gap() -> None:
    record = make_record(time_s=[0, 20, 65], q=[0.2, 0.3, 0.4])

    with pytest.raises(ValidationError, match="unknown actuation interval"):
        validate_record(record, expected_frame_s=20.0)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            SignalRecord(
                time_s=np.array([[0.0, 1.0]]),
                temp_c=np.array([20.0, 21.0]),
                q=np.array([0.2, 0.3]),
                ambient_c=np.array([20.0, 20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "time_s must be one-dimensional",
        ),
        (
            SignalRecord(
                time_s=np.array([0.0, 1.0]),
                temp_c=np.array([20.0, np.nan]),
                q=np.array([0.2, 0.3]),
                ambient_c=np.array([20.0, 20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "temp_c must contain only finite values",
        ),
        (
            SignalRecord(
                time_s=np.array([0.0, 1.0]),
                temp_c=np.array([20.0, 21.0]),
                q=np.array([0.2, 1.1]),
                ambient_c=np.array([20.0, 20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "q must be within \\[0, 1\\]",
        ),
        (
            SignalRecord(
                time_s=np.array([0.0, 1.0]),
                temp_c=np.array([20.0, 21.0]),
                q=np.array([0.2, 0.3]),
                ambient_c=np.array([20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "numeric arrays must have equal lengths",
        ),
        (
            SignalRecord(
                time_s=np.array([0.0]),
                temp_c=np.array([20.0]),
                q=np.array([0.2]),
                ambient_c=np.array([20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "record must contain at least two samples",
        ),
        (
            SignalRecord(
                time_s=np.array([0.0, 0.0]),
                temp_c=np.array([20.0, 21.0]),
                q=np.array([0.2, 0.3]),
                ambient_c=np.array([20.0, 20.0]),
                provenance="test-fixture",
                metadata={},
            ),
            "timestamps must be strictly increasing",
        ),
    ],
)
def test_validation_rejects_each_invalid_property(
    record: SignalRecord, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        validate_record(record)


def test_chronological_split_never_overlaps() -> None:
    split = chronological_split(make_record(100), 0.5, 0.25)

    assert split.fit.time_s[-1] < split.validation.time_s[0]
    assert split.validation.time_s[-1] < split.test.time_s[0]


def test_signal_record_copies_and_freezes_every_numeric_array() -> None:
    source_arrays = {
        "time_s": np.array([0, 1, 2], dtype=np.int64),
        "temp_c": np.array([20, 21, 22], dtype=np.int64),
        "q": np.array([0.2, 0.3, 0.4]),
        "ambient_c": np.array([19, 19, 19], dtype=np.int64),
    }
    record = SignalRecord(
        **source_arrays,
        provenance="test-fixture",
        metadata={},
    )
    for values in source_arrays.values():
        values[0] = -1

    for name, values in source_arrays.items():
        record_values = getattr(record, name)
        assert record_values.dtype == np.float64
        assert record_values[0] != values[0]
        with pytest.raises(ValueError):
            record_values[0] = 0.0


def test_signal_record_defensively_freezes_nested_metadata() -> None:
    source_metadata = {"items": [{"name": "original"}], "settings": {"ids": [1, 2]}}
    record = SignalRecord(
        time_s=np.array([0.0, 1.0]),
        temp_c=np.array([20.0, 21.0]),
        q=np.array([0.2, 0.3]),
        ambient_c=np.array([20.0, 20.0]),
        provenance="test-fixture",
        metadata=source_metadata,
    )
    source_metadata["items"][0]["name"] = "mutated"
    source_metadata["settings"]["ids"].append(3)

    assert record.metadata["items"][0]["name"] == "original"
    assert record.metadata["settings"]["ids"] == (1, 2)
    with pytest.raises(TypeError):
        record.metadata["items"][0]["name"] = "mutated"
    with pytest.raises(AttributeError):
        record.metadata["settings"]["ids"].append(3)
