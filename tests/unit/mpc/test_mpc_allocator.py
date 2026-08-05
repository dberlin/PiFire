import dataclasses
import math

import pytest

from common.control_trace import AllocationClampReason
from controller.mpc_allocator import AllocationResult, allocate, normalized_load_from_auger_duty

CFG = dict(u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)


def test_zero_load_maps_to_zero_auger_and_minimum_fan_with_frozen_diagnostics():
    allocation = allocate(0.0, **CFG)

    assert isinstance(allocation, AllocationResult)
    assert allocation.normalized_combustion_load == 0.0
    assert allocation.auger_duty == 0.0
    assert allocation.fan_duty == pytest.approx(40.0)
    assert allocation.u_max == pytest.approx(0.9)
    assert (allocation.fan_min_pct, allocation.fan_max_pct, allocation.fan_enabled) == (40.0, 100.0, True)
    assert allocation.auger_clamp_reason is AllocationClampReason.NONE
    assert allocation.fan_clamp_reason is AllocationClampReason.NONE
    assert dataclasses.is_dataclass(allocation)
    with pytest.raises(dataclasses.FrozenInstanceError):
        allocation.auger_duty = 0.2


def test_full_load_maps_to_maximum_auger_and_fan():
    allocation = allocate(1.0, **CFG)

    assert allocation.auger_duty == pytest.approx(0.9)
    assert allocation.fan_duty == pytest.approx(100.0)


def test_midpoint_couples_auger_and_fan_on_the_same_scalar_axis():
    allocation = allocate(0.5, **CFG)

    assert allocation.auger_duty == pytest.approx(0.45)
    assert allocation.fan_duty == pytest.approx(70.0)
    assert allocation.auger_duty / allocation.u_max == pytest.approx(
        (allocation.fan_duty - allocation.fan_min_pct) / (allocation.fan_max_pct - allocation.fan_min_pct)
    )


def test_disabled_fan_authority_leaves_auger_allocation_unchanged():
    fanless = allocate(0.5, **{**CFG, "enable_fan": False})
    with_fan = allocate(0.5, **CFG)

    assert fanless.fan_duty is None
    assert fanless.fan_enabled is False
    assert fanless.auger_duty == pytest.approx(with_fan.auger_duty)


@pytest.mark.parametrize("load", (-0.01, 1.01, math.nan, math.inf, -math.inf))
def test_allocator_rejects_loads_outside_the_normalized_finite_domain(load):
    with pytest.raises(ValueError, match="normalized combustion load"):
        allocate(load, **CFG)


@pytest.mark.parametrize("load", (0.0, 0.125, 0.5, 1.0))
def test_auger_inverse_round_trips_the_normalized_applied_load_including_zero(load):
    allocation = allocate(load, **CFG)

    assert normalized_load_from_auger_duty(allocation.auger_duty, u_max=CFG["u_max"]) == pytest.approx(load)


def test_inverse_bounds_measured_duty_to_the_normalized_domain():
    assert normalized_load_from_auger_duty(-0.2, u_max=CFG["u_max"]) == 0.0
    assert normalized_load_from_auger_duty(2.0, u_max=CFG["u_max"]) == 1.0
