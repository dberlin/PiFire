"""Pure auger cycle-time calculations used by the per-mode setup/on_settings_reload
hooks in controller/runtime/modes/ to derive on_time/off_time/cycle_time/ratio
from settings. No I/O."""

from dataclasses import dataclass


@dataclass
class CycleTimes:
    on_time: float
    off_time: float
    cycle_time: float
    cycle_ratio: float


def smoke_cycle_times(cycle_data):
    on_time = cycle_data["SmokeOnCycleTime"]
    off_time = cycle_data["SmokeOffCycleTime"] + (cycle_data["PMode"] * 10)
    cycle_time = on_time + off_time
    cycle_ratio = on_time / cycle_time
    return CycleTimes(on_time, off_time, cycle_time, cycle_ratio)


def hold_initial_cycle(cycle_data):
    u_min = cycle_data["u_min"]
    on_time = cycle_data["HoldCycleTime"] * u_min
    off_time = cycle_data["HoldCycleTime"] * (1 - u_min)
    cycle_time = cycle_data["HoldCycleTime"]
    cycle_ratio = u_min
    return CycleTimes(on_time, off_time, cycle_time, cycle_ratio)


def hold_update_cycle(controller_output, cycle_data, *, lid_open):
    u_min = cycle_data["u_min"]
    u_max = cycle_data["u_max"]
    ratio = u_min if lid_open else controller_output
    ratio = max(ratio, u_min)
    ratio = min(ratio, u_max)
    on_time = cycle_data["HoldCycleTime"] * ratio
    off_time = cycle_data["HoldCycleTime"] * (1 - ratio)
    cycle_time = on_time + off_time
    return CycleTimes(on_time, off_time, cycle_time, ratio)


def prime_cycle_times(prime_amount, auger_rate):
    prime_duration = int(prime_amount / auger_rate)
    on_time = prime_duration
    off_time = 1
    cycle_time = on_time + off_time
    cycle_ratio = on_time / cycle_time
    return CycleTimes(on_time, off_time, cycle_time, cycle_ratio)
