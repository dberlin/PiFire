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


def prime_cycle_times(prime_amount, auger_rate):
    prime_duration = int(prime_amount / auger_rate)
    on_time = prime_duration
    off_time = 1
    cycle_time = on_time + off_time
    cycle_ratio = on_time / cycle_time
    return CycleTimes(on_time, off_time, cycle_time, cycle_ratio)
