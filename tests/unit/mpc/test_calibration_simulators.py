"""Deterministic causal plant exercises for the pure calibration coordinator."""

import numpy as np
import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.calibration import CalibrationCommand, CalibrationCoordinator, CalibrationRuntimeContext

CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))
_FRAME_S = 20
_BASELINE_Q = 0.20
# A five-percent later-block MSE reduction is the shared minimum meaningful
# causal gain: smaller improvements are indistinguishable from short-trace noise.
_MIN_RELATIVE_HELD_OUT_ERROR_IMPROVEMENT = 0.05


def prediction(baseline_q, probe_q, runtime):
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


def causal_metrics(temperatures, realized_q, delay_frames):
    """Score delayed actuation on unseen future ARX response blocks only."""
    temperature = np.asarray(temperatures, dtype=float)
    input_q = np.asarray(realized_q, dtype=float)
    if temperature.size != input_q.size + 1:
        raise ValueError("each realized frame needs its ending temperature")

    delta_t = np.diff(temperature)
    frame = np.arange(max(delay_frames, 2), input_q.size)
    if frame.size < 18:
        return 0.0, 0.0

    features = np.column_stack(
        (
            temperature[frame],
            delta_t[frame - 1],
            delta_t[frame - 2],
            input_q[frame - delay_frames],
        )
    )
    response = delta_t[frame]
    rank_progress = 0.0
    held_out_improvements = []
    splits = ((frame.size // 2, 3 * frame.size // 4), (3 * frame.size // 4, frame.size))
    for train_end, test_end in splits:
        if train_end < 12 or test_end - train_end < 6:
            continue

        training_features = features[:train_end]
        center = np.mean(training_features, axis=0)
        scale = np.maximum(np.std(training_features, axis=0), 1e-12)
        training_design = np.column_stack((np.ones(train_end), (training_features - center) / scale))
        design_rank = np.linalg.matrix_rank(training_design)
        rank_progress = max(rank_progress, design_rank / training_design.shape[1])
        if design_rank != training_design.shape[1]:
            continue

        baseline_training = training_design[:, :-1]
        if np.linalg.matrix_rank(baseline_training) != baseline_training.shape[1]:
            continue
        full_coefficients = np.linalg.lstsq(training_design, response[:train_end], rcond=None)[0]
        baseline_coefficients = np.linalg.lstsq(baseline_training, response[:train_end], rcond=None)[0]

        future_design = np.column_stack(
            (
                np.ones(test_end - train_end),
                (features[train_end:test_end] - center) / scale,
            )
        )
        future_response = response[train_end:test_end]
        full_mse = np.mean((future_response - future_design @ full_coefficients) ** 2)
        baseline_mse = np.mean((future_response - future_design[:, :-1] @ baseline_coefficients) ** 2)
        if baseline_mse > 1e-12:
            held_out_improvements.append(1.0 - full_mse / baseline_mse)

    if len(held_out_improvements) != len(splits):
        return rank_progress, 0.0
    coverage = min(held_out_improvements) / _MIN_RELATIVE_HELD_OUT_ERROR_IMPROVEMENT
    return rank_progress, float(np.clip(coverage, 0.0, 1.0))


def test_constant_trace_cannot_establish_causal_coverage():
    temperatures = np.linspace(20.0, 80.0, 100)
    constant_q = np.full(99, _BASELINE_Q)

    rank, coverage = causal_metrics(temperatures, constant_q, 1)

    assert rank < 1.0
    assert coverage == 0.0


@pytest.mark.parametrize("plant_type, seed, delay_frames", [(GrillSim, 111, 1), (MAKGrillSim, 222, 5)])
def test_calibration_drives_both_plants_with_causal_identifying_evidence_without_readiness(
    plant_type, seed, delay_frames
):
    plant = plant_type(seed=seed)
    coordinator = CalibrationCoordinator(predict_max_c=prediction)
    measured = plant.measured()
    decision = coordinator.start(
        CalibrationCommand(command_revision=3, seed=seed),
        runtime(0.0, measured, _BASELINE_Q, 0.0, 0.0),
    )
    temperatures = [measured]
    realized_q_trace = []
    maximum_temperature = plant.true_Tc
    for frame in range(1, 45):
        requested_q = _BASELINE_Q + decision.probe_q
        realized_q, next_measured = run_frame(plant, requested_q)
        assert requested_q == pytest.approx(realized_q, abs=1e-12)
        assert abs(realized_q - _BASELINE_Q) <= 0.05 + 1e-12
        realized_q_trace.append(realized_q)
        temperatures.append(next_measured)
        rank_progress, coverage_progress = causal_metrics(temperatures, realized_q_trace, delay_frames)
        measured = next_measured
        maximum_temperature = max(maximum_temperature, plant.true_Tc)
        decision = coordinator.advance(
            runtime(frame * _FRAME_S, measured, realized_q, rank_progress, coverage_progress)
        )
        assert abs(decision.probe_q) <= 0.05
        assert maximum_temperature < 240.0

    aligned_rank, aligned_coverage = causal_metrics(temperatures, realized_q_trace, delay_frames)
    if plant_type is MAKGrillSim:
        assert causal_metrics(temperatures[:27], realized_q_trace[:26], delay_frames)[1] == 0.0
    shuffled_q = np.roll(np.asarray(realized_q_trace), 7)
    shuffled_rank, shuffled_coverage = causal_metrics(temperatures, shuffled_q, delay_frames)

    assert aligned_rank == 1.0
    assert aligned_coverage == 1.0
    assert shuffled_rank == 1.0
    assert shuffled_coverage < 1.0
    if plant_type is MAKGrillSim:
        assert len(realized_q_trace) * _FRAME_S > 100

    completed = coordinator.snapshot()["completed_stages"][-1]
    assert decision.active and decision.stage == "coast"
    assert decision.events[-1].kind == "stage_completed"
    assert abs(decision.events[-1].realized_probe_sum) <= 0.05
    assert completed.rank_progress >= 1.0
    assert completed.coverage_progress >= 0.01
    assert decision.activation_ready is False
    assert coordinator.cancel_probe("operator_cancel", runtime(900.0, measured, _BASELINE_Q, 1.0, 1.0)).probe_q == 0.0
