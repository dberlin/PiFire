"""Deterministic plant exercises for the pure calibration coordinator."""

import numpy as np
import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.calibration import CalibrationCommand, CalibrationCoordinator, CalibrationRuntimeContext

CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))
_FRAME_S = 20
_BASELINE_Q = 0.20


def prediction(baseline_q, probe_q, runtime):
    """Bound prospective temperature from the same ±0.05 framed-load authority."""
    assert abs(probe_q) <= 0.05
    return runtime.temp_c + 2.0 + abs(probe_q) * 5.0


def runtime(now_s, temp_c, realized_q, rank_progress, coverage_progress):
    return CalibrationRuntimeContext(
        now_s=float(now_s),
        temp_c=temp_c,
        target_c=CENTERS[0],
        baseline_q=_BASELINE_Q,
        realized_q=realized_q,
        safety_ceiling_c=260.0,
        allocator_headroom=0.05,
        error_rate_headroom=0.05,
        capability_headroom=0.05,
        saturation_headroom=0.05,
        rank_progress=rank_progress,
        coverage_progress=coverage_progress,
    )


def run_frame(plant, requested_q):
    on_seconds = round(requested_q * _FRAME_S)
    for second in range(_FRAME_S):
        plant.step(second < on_seconds, 0.5)
    return on_seconds / _FRAME_S, plant.measured()


def identification_metrics(rows):
    """Rank and Fisher-like coverage of actual lagged thermal evidence."""
    matrix = np.asarray(rows, dtype=float)
    rank = np.linalg.matrix_rank(matrix)
    rank_progress = min(1.0, rank / 3.0)
    if rank < 3:
        return rank_progress, 0.0
    normalized = matrix / np.linalg.norm(matrix, axis=0)
    smallest = np.linalg.svd(normalized, compute_uv=False)[-1] / np.sqrt(len(matrix))
    return rank_progress, min(1.0, smallest / 0.0001)


def test_unexcited_control_trace_has_no_identification_coverage():
    temperatures = np.linspace(20.0, 80.0, 80)
    rows = [[temperatures[index - 1], temperatures[index], 0.0] for index in range(1, len(temperatures))]
    rank, coverage = identification_metrics(rows)
    assert rank < 1.0
    assert coverage == 0.0


@pytest.mark.parametrize("plant_type, seed", [(GrillSim, 111), (MAKGrillSim, 222)])
def test_calibration_drives_both_plants_with_identifying_framed_evidence_without_readiness(plant_type, seed):
    plant = plant_type(seed=seed)
    coordinator = CalibrationCoordinator(predict_max_c=prediction)
    measured = plant.measured()
    decision = coordinator.start(
        CalibrationCommand(command_revision=3, maximum_temperature_c=240.0, seed=seed),
        runtime(0.0, measured, _BASELINE_Q, 0.0, 0.0),
    )
    rows = []
    maximum_temperature = plant.true_Tc
    for frame in range(1, 45):  # 880 seconds, well beyond MAK's deadtime.
        requested_q = _BASELINE_Q + decision.probe_q
        realized_q, next_measured = run_frame(plant, requested_q)
        assert abs(realized_q - _BASELINE_Q) <= 0.05 + 1e-12
        rows.append((measured, next_measured, realized_q - _BASELINE_Q))
        rank_progress, coverage_progress = identification_metrics(rows)
        measured = next_measured
        maximum_temperature = max(maximum_temperature, plant.true_Tc)
        decision = coordinator.advance(
            runtime(frame * _FRAME_S, measured, realized_q, rank_progress, coverage_progress)
        )
        assert abs(decision.probe_q) <= 0.05
        assert maximum_temperature < 240.0

    completed = coordinator.snapshot()["completed_stages"][-1]
    assert decision.active and decision.stage == "coast"
    assert decision.events[-1].kind == "stage_completed"
    assert abs(decision.events[-1].realized_probe_sum) <= 0.05
    assert completed.rank_progress >= 1.0
    assert completed.coverage_progress >= 1.0
    assert decision.activation_ready is False
    assert coordinator.cancel_probe(
        "operator_cancel", runtime(900.0, measured, _BASELINE_Q, 1.0, 1.0)
    ).probe_q == 0.0
