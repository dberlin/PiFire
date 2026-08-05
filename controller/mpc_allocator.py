#!/usr/bin/env python3

"""
*****************************************
 PiFire MPC Combustion Allocator
*****************************************

 Maps the MPC's scalar firing-rate demand Q to physical actuators (auger duty
 and, on PWM/DC-fan builds, fan duty) along a sensible air-fuel curve. Air
 tracks fuel so the air-fuel ratio stays near its target across the firing
 range, which keeps combustion sensible by construction.

*****************************************
"""

from dataclasses import dataclass

from common.control_trace import AllocationClampReason


ALLOCATOR_REVISION = 1


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """One physical allocation with every pure allocator input retained."""

    normalized_combustion_load: float
    auger_duty: float
    fan_duty: float | None
    q_min: float
    q_max: float
    u_min: float
    u_max: float
    fan_min_pct: float
    fan_max_pct: float
    fan_enabled: bool
    auger_clamp_reason: AllocationClampReason
    fan_clamp_reason: AllocationClampReason
    allocator_revision: int = ALLOCATOR_REVISION


def allocate(
    Q: float,
    *,
    Q_min: float,
    Q_max: float,
    u_min: float,
    u_max: float,
    fan_min_pct: float,
    fan_max_pct: float,
    enable_fan: bool,
) -> AllocationResult:
    """Map a firing-load request to one immutable, traceable actuator allocation."""
    span = (Q_max - Q_min) if Q_max > Q_min else 1.0
    normalized_load = max(float(Q_min), min(float(Q_max), float(Q)))
    fraction = (normalized_load - Q_min) / span
    auger_duty = u_min + fraction * (u_max - u_min)
    fan_duty = fan_min_pct + fraction * (fan_max_pct - fan_min_pct) if enable_fan else None
    return AllocationResult(
        normalized_combustion_load=normalized_load,
        auger_duty=auger_duty,
        fan_duty=fan_duty,
        q_min=Q_min,
        q_max=Q_max,
        u_min=u_min,
        u_max=u_max,
        fan_min_pct=fan_min_pct,
        fan_max_pct=fan_max_pct,
        fan_enabled=enable_fan,
        auger_clamp_reason=AllocationClampReason.AUGER_MAX
        if normalized_load == Q_max and Q > Q_max
        else AllocationClampReason.NONE,
        fan_clamp_reason=(
            AllocationClampReason.FAN_MIN
            if enable_fan and normalized_load == Q_min and Q < Q_min
            else AllocationClampReason.FAN_MAX
            if enable_fan and normalized_load == Q_max and Q > Q_max
            else AllocationClampReason.NONE
        ),
    )
