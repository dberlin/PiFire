"""
*****************************************
 PiFire MPC Combustion Allocator
*****************************************

 Maps one normalized combustion-load command q in [0, 1] to physical actuators:
 auger duty and, on PWM/DC-fan builds, fan duty. Air tracks fuel along the
 same scalar axis, so no independent fan decision exists.

*****************************************
"""

import math
from dataclasses import dataclass

from common.control_trace import AllocationClampReason

ALLOCATOR_REVISION = 2


def _normalized_combustion_load(value: float) -> float:
    load = float(value)
    if not math.isfinite(load) or not 0.0 <= load <= 1.0:
        raise ValueError("normalized combustion load must be finite and within [0, 1]")
    return load


def normalized_load_from_auger_duty(auger_duty: float, *, u_max: float) -> float:
    """Recover the bounded normalized load from measured mean auger duty."""
    duty = float(auger_duty)
    maximum = float(u_max)
    if not math.isfinite(duty):
        raise ValueError("measured auger duty must be finite")
    if not math.isfinite(maximum) or maximum <= 0.0:
        raise ValueError("u_max must be finite and greater than zero")
    return min(1.0, max(0.0, duty / maximum))


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """One physical allocation with every pure allocator input retained."""

    normalized_combustion_load: float
    auger_duty: float
    fan_duty: float | None
    u_max: float
    fan_min_pct: float
    fan_max_pct: float
    fan_enabled: bool
    auger_clamp_reason: AllocationClampReason
    fan_clamp_reason: AllocationClampReason
    allocator_revision: int = ALLOCATOR_REVISION


def allocate(
    normalized_combustion_load: float,
    *,
    u_max: float,
    fan_min_pct: float,
    fan_max_pct: float,
    enable_fan: bool,
) -> AllocationResult:
    """Allocate the only MPC decision variable to coupled fuel and air commands."""
    load = _normalized_combustion_load(normalized_combustion_load)
    maximum = float(u_max)
    fan_min = float(fan_min_pct)
    fan_max = float(fan_max_pct)
    if not math.isfinite(maximum) or maximum <= 0.0:
        raise ValueError("u_max must be finite and greater than zero")
    if not math.isfinite(fan_min) or not math.isfinite(fan_max) or fan_min > fan_max:
        raise ValueError("fan bounds must be finite and ordered")
    fan_duty = fan_min + load * (fan_max - fan_min) if enable_fan else None
    return AllocationResult(
        normalized_combustion_load=load,
        auger_duty=load * maximum,
        fan_duty=fan_duty,
        u_max=maximum,
        fan_min_pct=fan_min,
        fan_max_pct=fan_max,
        fan_enabled=enable_fan,
        auger_clamp_reason=AllocationClampReason.NONE,
        fan_clamp_reason=AllocationClampReason.NONE,
    )
