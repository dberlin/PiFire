from controller.runtime.logic.cycle import (
    CycleTimes,
    smoke_cycle_times,
    hold_initial_cycle,
    hold_update_cycle,
    prime_cycle_times,
)


def test_smoke_cycle_times_ratio_and_pmode_offset():
    cycle_data = {"SmokeOnCycleTime": 15, "SmokeOffCycleTime": 45, "PMode": 2}
    result = smoke_cycle_times(cycle_data)
    # OffTime = 45 + 2*10 = 65
    assert result.on_time == 15
    assert result.off_time == 65
    assert result.cycle_time == 80
    assert result.cycle_ratio == 15 / 80
    assert isinstance(result, CycleTimes)


def test_smoke_cycle_times_zero_pmode():
    cycle_data = {"SmokeOnCycleTime": 20, "SmokeOffCycleTime": 30, "PMode": 0}
    result = smoke_cycle_times(cycle_data)
    assert result.on_time == 20
    assert result.off_time == 30
    assert result.cycle_time == 50
    assert result.cycle_ratio == 20 / 50


def test_hold_initial_cycle_matches_u_min():
    cycle_data = {"HoldCycleTime": 20, "u_min": 0.3}
    result = hold_initial_cycle(cycle_data)
    assert result.on_time == 20 * 0.3
    assert result.off_time == 20 * (1 - 0.3)
    assert result.cycle_time == 20
    assert result.cycle_ratio == 0.3


def test_hold_update_cycle_clamps_below_u_min_up_to_u_min():
    cycle_data = {"HoldCycleTime": 20, "u_min": 0.3, "u_max": 0.8}
    result = hold_update_cycle(0.1, cycle_data, lid_open=False)
    assert result.cycle_ratio == 0.3
    assert result.on_time == 20 * 0.3
    assert result.off_time == 20 * (1 - 0.3)
    assert result.cycle_time == 20 * 0.3 + 20 * (1 - 0.3)


def test_hold_update_cycle_clamps_above_u_max_down_to_u_max():
    cycle_data = {"HoldCycleTime": 20, "u_min": 0.3, "u_max": 0.8}
    result = hold_update_cycle(0.95, cycle_data, lid_open=False)
    assert result.cycle_ratio == 0.8
    assert result.on_time == 20 * 0.8
    assert result.off_time == 20 * (1 - 0.8)
    assert result.cycle_time == 20 * 0.8 + 20 * (1 - 0.8)


def test_hold_update_cycle_lid_open_forces_u_min_regardless_of_output():
    cycle_data = {"HoldCycleTime": 20, "u_min": 0.3, "u_max": 0.8}
    result = hold_update_cycle(0.95, cycle_data, lid_open=True)
    assert result.cycle_ratio == 0.3
    assert result.on_time == 20 * 0.3
    assert result.off_time == 20 * (1 - 0.3)


def test_hold_update_cycle_mid_range_passes_through():
    cycle_data = {"HoldCycleTime": 20, "u_min": 0.3, "u_max": 0.8}
    result = hold_update_cycle(0.5, cycle_data, lid_open=False)
    assert result.cycle_ratio == 0.5
    assert result.on_time == 20 * 0.5
    assert result.off_time == 20 * (1 - 0.5)
    assert result.cycle_time == 20 * 0.5 + 20 * (1 - 0.5)


def test_prime_cycle_times_known_values():
    # prime_amount=100 grams, auger_rate=5 g/s -> prime_duration=20
    result = prime_cycle_times(100, 5)
    assert result.on_time == 20
    assert result.off_time == 1
    assert result.cycle_time == 21
    assert result.cycle_ratio == 20 / 21


def test_prime_cycle_times_integer_truncation():
    # prime_amount / auger_rate truncated via int()
    result = prime_cycle_times(101, 5)  # 20.2 -> 20
    assert result.on_time == 20
    assert result.off_time == 1
    assert result.cycle_time == 21
    assert result.cycle_ratio == 20 / 21
