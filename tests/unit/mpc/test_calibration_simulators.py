"""Deterministic plant exercises for the pure calibration coordinator."""

import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.calibration import (
    CalibrationCommand,
    CalibrationCoordinator,
    CalibrationRuntimeContext,
)


CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))
_FRAME_S = 10


def prediction(baseline_q, probe_q, runtime):
    """A bounded active grey-box stand-in; it never applies an output."""
    return runtime.temp_c + 2.0 + abs(probe_q) * 5.0


def runtime(now_s, temp_c, realized_q, rank_progress, coverage_progress):
    return CalibrationRuntimeContext(
        now_s=float(now_s),
        temp_c=temp_c,
        target_c=CENTERS[0],
        baseline_q=0.50,
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
    """Deliver a deterministic framed duty and return what the plant received."""
    on_seconds = round(requested_q * _FRAME_S)
    for second in range(_FRAME_S):
        plant.step(second < on_seconds, 0.5)
    return on_seconds / _FRAME_S, plant.measured()


@pytest.mark.parametrize("plant_type, seed", [(GrillSim, 111), (MAKGrillSim, 222)])
def test_calibration_drives_both_plants_with_real_framed_evidence_without_readiness(plant_type, seed):
    plant = plant_type(seed=seed)
    coordinator = CalibrationCoordinator(predict_max_c=prediction)
    measured = plant.measured()
    decision = coordinator.start(
        CalibrationCommand(command_revision=3, maximum_temperature_c=240.0, seed=seed),
        runtime(0.0, measured, 0.50, 0.0, 0.0),
    )
    delivered = []
    thermal_changes = []
    maximum_temperature = plant.true_Tc
    for frame in range(1, 45):  # 440 s: far beyond both plants' deadtime.
        requested_q = 0.50 + decision.probe_q
        realized_q, next_measured = run_frame(plant, requested_q)
        delivered.append(realized_q)
        thermal_changes.append(abs(next_measured - measured))
        measured = next_measured
        maximum_temperature = max(maximum_temperature, plant.true_Tc)
        rank_progress = min(1.0, len({round(value, 6) for value in delivered}) / 3.0)
        coverage_progress = min(1.0, sum(thermal_changes) / 1.0)
        decision = coordinator.advance(runtime(frame * _FRAME_S, measured, realized_q, rank_progress, coverage_progress))
        assert abs(decision.probe_q) <= 0.05
        assert maximum_temperature < 240.0

    assert decision.active and decision.stage == "coast"
    completed = coordinator.snapshot()["completed_stages"][-1]
    assert decision.events[-1].kind == "stage_completed"
    assert abs(decision.events[-1].realized_probe_sum) <= 0.05
    assert completed.rank_progress >= 1.0
    assert completed.coverage_progress >= 1.0
    assert decision.activation_ready is False

    aborted = coordinator.cancel_probe("operator_cancel", runtime(450.0, measured, 0.5, 1.0, 1.0))
    assert aborted.probe_q == 0.0
    assert not aborted.active
