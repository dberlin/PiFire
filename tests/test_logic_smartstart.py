from controller.runtime.logic.cycle import CycleTimes
from controller.runtime.logic.smartstart import select_profile, profile_cycle


def test_select_profile_below_first_range():
    temp_range_list = [50, 70, 90]
    assert select_profile(40, temp_range_list) == 0


def test_select_profile_between_ranges():
    temp_range_list = [50, 70, 90]
    assert select_profile(60, temp_range_list) == 1


def test_select_profile_above_all_ranges():
    temp_range_list = [50, 70, 90]
    assert select_profile(100, temp_range_list) == len(temp_range_list)


def test_select_profile_equal_to_boundary_does_not_select_that_index():
    # startup_temp == temp_range_list[i] must NOT match (strict <), so it
    # falls through to the next index (or len() if it's the last one).
    temp_range_list = [50, 70, 90]
    assert select_profile(70, temp_range_list) == 2


def test_select_profile_equal_to_first_boundary():
    temp_range_list = [50, 70, 90]
    assert select_profile(50, temp_range_list) == 1


def test_select_profile_equal_to_last_boundary():
    temp_range_list = [50, 70, 90]
    assert select_profile(90, temp_range_list) == 3


def test_select_profile_empty_list_returns_zero():
    # Documented behavior: range(len([])) is empty, loop body never runs,
    # so no index is ever selected. len([]) == 0.
    assert select_profile(999, []) == 0


def test_profile_cycle_known_values():
    profile = {'augerontime': 15, 'p_mode': 2, 'startuptime': 240}
    cycle_data = {'SmokeOffCycleTime': 45}
    cycle_times, startup_timer, metrics_bits = profile_cycle(profile, cycle_data)

    # OnTime = 15
    # OffTime = 45 + 2*10 = 65
    # CycleTime = 15 + 65 = 80
    # CycleRatio = 15 / 80
    assert isinstance(cycle_times, CycleTimes)
    assert cycle_times.on_time == 15
    assert cycle_times.off_time == 65
    assert cycle_times.cycle_time == 80
    assert cycle_times.cycle_ratio == 15 / 80

    assert startup_timer == 240

    assert metrics_bits == {'p_mode': 2, 'auger_cycle_time': 15}


def test_profile_cycle_zero_pmode():
    profile = {'augerontime': 20, 'p_mode': 0, 'startuptime': 300}
    cycle_data = {'SmokeOffCycleTime': 30}
    cycle_times, startup_timer, metrics_bits = profile_cycle(profile, cycle_data)

    assert cycle_times.on_time == 20
    assert cycle_times.off_time == 30
    assert cycle_times.cycle_time == 50
    assert cycle_times.cycle_ratio == 20 / 50
    assert startup_timer == 300
    assert metrics_bits == {'p_mode': 0, 'auger_cycle_time': 20}


def test_profile_cycle_does_not_include_index_or_startup_temp():
    # profile_cycle is purely a function of profile+cycle_data; the caller
    # is responsible for metrics['smart_start_profile'] and
    # metrics['startup_temp'], which depend on state outside the profile dict.
    profile = {'augerontime': 15, 'p_mode': 2, 'startuptime': 240}
    cycle_data = {'SmokeOffCycleTime': 45}
    _, _, metrics_bits = profile_cycle(profile, cycle_data)

    assert 'smart_start_profile' not in metrics_bits
    assert 'startup_temp' not in metrics_bits
