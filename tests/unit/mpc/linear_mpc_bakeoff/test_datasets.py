"""Contracts for deterministic linear-model calibration data."""

import numpy as np
import pytest

from docs.superpowers.experiments.linear_mpc_bakeoff.datasets import (
    CalibrationProgram,
    ProgramSegment,
    generate_calibration_record,
)


TEST_CONFIG = CalibrationProgram(
    segments=(
        ProgramSegment(
            duration_s=600,
            center_q=0.15,
            perturbation_q=0.08,
            dwell_s=120,
        ),
        ProgramSegment(
            duration_s=600,
            center_q=0.35,
            perturbation_q=0.08,
            dwell_s=120,
        ),
        ProgramSegment(
            duration_s=600,
            center_q=0.65,
            perturbation_q=0.08,
            dwell_s=120,
        ),
    ),
    coast_duration_s=120,
)


def test_calibration_is_repeatable() -> None:
    left = generate_calibration_record("GrillSim", seed=7, config=TEST_CONFIG)
    right = generate_calibration_record("GrillSim", seed=7, config=TEST_CONFIG)

    np.testing.assert_array_equal(left.q, right.q)
    np.testing.assert_array_equal(left.temp_c, right.temp_c)


def test_calibration_contains_plateaus_prbs_and_coast() -> None:
    record = generate_calibration_record("GrillSim", seed=3, config=TEST_CONFIG)

    assert np.count_nonzero(np.diff(record.q)) >= 12
    assert np.any(record.q == 0.0)
    assert {0.15, 0.35, 0.65}.issubset(set(np.round(record.q, 2)))


def test_calibration_outputs_complete_energy_preserving_frames() -> None:
    record = generate_calibration_record("MAKGrillSim", seed=5, config=TEST_CONFIG)

    np.testing.assert_array_equal(np.diff(record.time_s), np.full(record.time_s.size - 1, 20.0))
    assert record.time_s[-1] == 1_920.0
    assert record.provenance == "simulator-calibration"
    assert record.metadata["plant"] == "MAKGrillSim"
    assert record.metadata["fan_frac"] == 1.0


def test_calibration_records_realized_non_integer_pulse_energy() -> None:
    program = CalibrationProgram(
        segments=(ProgramSegment(20, 0.13, 0.0, 20),),
        coast_duration_s=20,
    )

    record = generate_calibration_record("GrillSim", seed=1, config=program)

    np.testing.assert_array_equal(record.q, np.array([0.10, 0.0]))


def test_seeded_prbs_permits_repeated_adjacent_binary_levels() -> None:
    program = CalibrationProgram(
        segments=(ProgramSegment(100, 0.2, 0.1, 20),),
        coast_duration_s=20,
    )

    left = generate_calibration_record("GrillSim", seed=3, config=program)
    right = generate_calibration_record("GrillSim", seed=3, config=program)

    np.testing.assert_array_equal(left.q, right.q)
    assert left.q[2] == left.q[3] == 0.1


def test_calibration_rejects_non_twenty_second_output_frames() -> None:
    with pytest.raises(ValueError, match="20"):
        CalibrationProgram(
            segments=(ProgramSegment(20, 0.2, 0.0, 20),),
            coast_duration_s=20,
            frame_s=10,
        )
