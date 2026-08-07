"""Deterministic plant exercises for the pure calibration coordinator."""

import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from controller.linear_mpc.calibration import (
    CalibrationCommand,
    CalibrationCoordinator,
    CalibrationRuntimeContext,
)


CENTERS = tuple((fahrenheit - 32.0) * 5.0 / 9.0 for fahrenheit in (225.0, 325.0, 425.0))


def runtime(now_s, temp_c, realized_q, **changes):
    values = dict(
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
        rank_progress=1.0,
        coverage_progress=1.0,
    )
    values.update(changes)
    return CalibrationRuntimeContext(**values)


@pytest.mark.parametrize("plant_type, seed", [(GrillSim, 111), (MAKGrillSim, 222)])
def test_calibration_is_bounded_cancellable_zero_mean_and_never_grants_readiness(plant_type, seed):
    plant = plant_type(seed=seed)
    coordinator = CalibrationCoordinator()
    decision = coordinator.start(
        CalibrationCommand(command_revision=3, maximum_temperature_c=240.0, seed=seed),
        runtime(0.0, plant.measured(), 0.50),
    )
    observed_probes = []
    maximum_temperature = plant.true_Tc
    for second in range(1, 39):
        observed_probes.append(decision.probe_q)
        requested_q = 0.50 + decision.probe_q
        plant.step(requested_q > 0.0, 0.5)
        maximum_temperature = max(maximum_temperature, plant.true_Tc)
        decision = coordinator.advance(runtime(second, plant.measured(), requested_q))
        assert abs(decision.probe_q) <= 0.05
        assert maximum_temperature < 240.0
    assert decision.progress.rank_progress >= 1.0
    assert decision.progress.coverage_progress >= 1.0
    assert abs(decision.progress.realized_probe_sum) <= 0.05
    assert not hasattr(decision, "activation_ready")

    aborted = coordinator.cancel_probe("operator_cancel", runtime(39.0, plant.measured(), 0.5))
    assert aborted.probe_q == 0.0
    assert not aborted.active
