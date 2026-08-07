"""Deterministic causal plant exercises for the pure calibration coordinator."""

import numpy as np
import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.calibration import CalibrationCommand, CalibrationCoordinator, CalibrationRuntimeContext

CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))
_FRAME_S = 20
_BASELINE_Q = 0.20


def prediction(baseline_q, probe_q, runtime):
    assert abs(probe_q) <= 0.05
    return runtime.temp_c + 2.0 + abs(probe_q) * 5.0


def runtime(now_s, temp_c, realized_q, rank_progress, coverage_progress):
    return CalibrationRuntimeContext(
        now_s=float(now_s), temp_c=temp_c, target_c=CENTERS[0], baseline_q=_BASELINE_Q,
        realized_q=realized_q, safety_ceiling_c=260.0, allocator_headroom=0.05,
        error_rate_headroom=0.05, capability_headroom=0.05, saturation_headroom=0.05,
        rank_progress=rank_progress, coverage_progress=coverage_progress,
    )


def run_frame(plant, requested_q):
    on_seconds = round(requested_q * _FRAME_S)
    for second in range(_FRAME_S):
        plant.step(second < on_seconds, 0.5)
    return on_seconds / _FRAME_S, plant.measured()


def causal_metrics(temperatures, probe_q, delay_frames):
    """ARX rank and incremental delayed-Q prediction over autoregression."""
    rows = []
    targets = []
    for index in range(delay_frames + 1, len(temperatures)):
        rows.append((temperatures[index - 1], temperatures[index - 2], probe_q[index - delay_frames - 1]))
        targets.append(temperatures[index])
    if len(rows) < 4:
        return 0.0, 0.0
    matrix = np.asarray(rows, dtype=float)
    scale = np.maximum(np.std(matrix, axis=0), 1e-12)
    scaled = (matrix - np.mean(matrix, axis=0)) / scale
    rank = np.linalg.matrix_rank(scaled)
    rank_progress = min(1.0, rank / 3.0)
    if rank < 3:
        return rank_progress, 0.0
    target = np.asarray(targets)
    baseline = np.column_stack((np.ones(len(matrix)), scaled[:, :2]))
    full = np.column_stack((baseline, scaled[:, 2]))
    baseline_residual = target - baseline @ np.linalg.lstsq(baseline, target, rcond=None)[0]
    full_residual = target - full @ np.linalg.lstsq(full, target, rcond=None)[0]
    information = 1.0 - np.mean(full_residual ** 2) / max(np.mean(baseline_residual ** 2), 1e-12)
    return rank_progress, 1.0 if information > 0.0 else 0.0


def test_constant_and_noncausal_excited_traces_fail_causal_coverage():
    temperatures = np.linspace(20.0, 80.0, 100)
    constant = [0.0] * 99
    assert causal_metrics(temperatures, constant, 1)[1] == 0.0

    input_q = [0.05 if index % 2 else -0.05 for index in range(99)]
    shuffled = input_q[1:] + input_q[:1]
    assert causal_metrics(temperatures, shuffled, 1)[1] < 0.01


@pytest.mark.parametrize("plant_type, seed, delay_frames", [(GrillSim, 111, 1), (MAKGrillSim, 222, 5)])
def test_calibration_drives_both_plants_with_causal_identifying_evidence_without_readiness(plant_type, seed, delay_frames):
    plant = plant_type(seed=seed)
    coordinator = CalibrationCoordinator(predict_max_c=prediction)
    measured = plant.measured()
    decision = coordinator.start(
        CalibrationCommand(command_revision=3, maximum_temperature_c=240.0, seed=seed),
        runtime(0.0, measured, _BASELINE_Q, 0.0, 0.0),
    )
    temperatures = [measured]
    realized_probe_q = []
    maximum_temperature = plant.true_Tc
    for frame in range(1, 45):
        requested_q = _BASELINE_Q + decision.probe_q
        realized_q, next_measured = run_frame(plant, requested_q)
        assert abs(realized_q - _BASELINE_Q) <= 0.05 + 1e-12
        realized_probe_q.append(realized_q - _BASELINE_Q)
        temperatures.append(next_measured)
        rank_progress, coverage_progress = causal_metrics(temperatures, realized_probe_q, delay_frames)
        measured = next_measured
        maximum_temperature = max(maximum_temperature, plant.true_Tc)
        decision = coordinator.advance(runtime(frame * _FRAME_S, measured, realized_q, rank_progress, coverage_progress))
        assert abs(decision.probe_q) <= 0.05
        assert maximum_temperature < 240.0

    completed = coordinator.snapshot()["completed_stages"][-1]
    assert decision.active and decision.stage == "coast"
    assert decision.events[-1].kind == "stage_completed"
    assert abs(decision.events[-1].realized_probe_sum) <= 0.05
    assert completed.rank_progress >= 1.0
    assert completed.coverage_progress >= 0.01
    assert decision.activation_ready is False
    assert coordinator.cancel_probe("operator_cancel", runtime(900.0, measured, _BASELINE_Q, 1.0, 1.0)).probe_q == 0.0
