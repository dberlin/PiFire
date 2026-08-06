from controller.runtime.logic.cycle import CycleTimes, prime_cycle_times, smoke_cycle_times


def test_smoke_cycle_times_ratio_and_pmode_offset():
    cycle_data = {"SmokeOnCycleTime": 15, "SmokeOffCycleTime": 45, "PMode": 2}

    result = smoke_cycle_times(cycle_data)

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


def test_prime_cycle_times_known_values():
    result = prime_cycle_times(100, 5)

    assert result.on_time == 20
    assert result.off_time == 1
    assert result.cycle_time == 21
    assert result.cycle_ratio == 20 / 21


def test_prime_cycle_times_integer_truncation():
    result = prime_cycle_times(101, 5)

    assert result.on_time == 20
    assert result.off_time == 1
    assert result.cycle_time == 21
    assert result.cycle_ratio == 20 / 21
