import dataclasses

import pytest

from common.control_trace import AllocationClampReason
from controller.mpc_allocator import AllocationResult, allocate

CFG = dict(Q_min=5.0, Q_max=100.0, u_min=0.1, u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=True)


def test_min_fire_maps_to_lower_bounds_with_a_frozen_traceable_result():
    allocation = allocate(5.0, **CFG)

    assert isinstance(allocation, AllocationResult)
    assert allocation.auger_duty == pytest.approx(0.1)
    assert allocation.fan_duty == pytest.approx(40.0)
    assert allocation.normalized_combustion_load == pytest.approx(5.0)
    assert allocation.auger_clamp_reason is AllocationClampReason.NONE
    assert (allocation.q_min, allocation.q_max, allocation.u_min, allocation.u_max) == (5.0, 100.0, 0.1, 0.9)
    assert (allocation.fan_min_pct, allocation.fan_max_pct, allocation.fan_enabled) == (40.0, 100.0, True)
    assert dataclasses.is_dataclass(allocation)
    with pytest.raises(dataclasses.FrozenInstanceError):
        allocation.auger_duty = 0.2


def test_max_fire_maps_to_upper_bounds():
    allocation = allocate(100.0, **CFG)

    assert allocation.auger_duty == pytest.approx(0.9)
    assert allocation.fan_duty == pytest.approx(100.0)


def test_monotonic_and_clamped():
    low = allocate(-50, **CFG)
    high = allocate(999, **CFG)
    middle = allocate(52.5, **CFG)

    assert low.auger_duty == pytest.approx(0.1)
    assert low.auger_clamp_reason is AllocationClampReason.NONE
    assert high.auger_duty == pytest.approx(0.9)
    assert high.auger_clamp_reason is AllocationClampReason.AUGER_MAX
    assert 0.1 < middle.auger_duty < 0.9
    assert allocate(40, **CFG).auger_duty < allocate(60, **CFG).auger_duty


def test_air_tracks_fuel_constant_afr():
    allocation = allocate(52.5, **CFG)
    auger_fraction = (allocation.auger_duty - 0.1) / (0.9 - 0.1)
    fan_fraction = (allocation.fan_duty - 40.0) / (100.0 - 40.0)

    assert auger_fraction == pytest.approx(fan_fraction)


def test_fan_disabled_returns_no_fan_command():
    cfg = dict(CFG)
    cfg["enable_fan"] = False

    allocation = allocate(60, **cfg)

    assert allocation.fan_duty is None
    assert allocation.fan_enabled is False
    assert 0.1 < allocation.auger_duty < 0.9
