import pytest

from controller.runtime.logic.pwm import hold_duty_cycle, ramp_params

PWM_SETTINGS = {
    "min_duty_cycle": 20,
    "max_duty_cycle": 100,
    "temp_range_list": [5, 15, 30],
    "profiles": [{"duty_cycle": 50}, {"duty_cycle": 70}, {"duty_cycle": 90}],
}


@pytest.mark.parametrize(
    ("ptemp", "profiles", "expected"),
    [
        # ptemp > setpoint (strict >) short-circuits to min_duty_cycle
        # regardless of temp_range_list/profiles.
        pytest.param(230, None, 20, id="over-setpoint-returns-min"),
        # ptemp == setpoint means (setpoint - ptemp) == 0, which is <= the
        # first range entry, so profile 0 is used (not the over-setpoint
        # branch, since that requires strict >).
        pytest.param(225, None, 50, id="at-setpoint-uses-profile-zero"),
        # setpoint - ptemp = 10, which is > temp_range_list[0]=5 but <= [1]=15,
        # so profile index 1 (duty_cycle=70) is used.
        pytest.param(215, None, 70, id="matches-early-profile"),
        # setpoint - ptemp == temp_range_list[i] exactly must match index i
        # (uses <=, not <).
        pytest.param(210, None, 70, id="boundary-uses-le-match"),
        # Matched profile's duty_cycle (10) is below min_duty_cycle (20), so
        # the clamp raises it to min_duty_cycle. Clamp order is max-then-min,
        # so the min clamp must win here.
        pytest.param(
            225,
            [{"duty_cycle": 10}, {"duty_cycle": 70}, {"duty_cycle": 90}],
            20,
            id="clamps-below-min",
        ),
        # Matched profile's duty_cycle (150) is above max_duty_cycle (100), so
        # the clamp lowers it to max_duty_cycle.
        pytest.param(
            225,
            [{"duty_cycle": 150}, {"duty_cycle": 70}, {"duty_cycle": 90}],
            100,
            id="clamps-above-max",
        ),
        # setpoint - ptemp = 50, larger than every entry in temp_range_list, so
        # the loop falls through all comparisons and the last-index fallthrough
        # branch returns max_duty_cycle directly (bypassing profiles/clamps).
        pytest.param(175, None, 100, id="fallthrough-beyond-all-ranges-returns-max"),
        # setpoint - ptemp == temp_range_list[-1] exactly still matches via <=
        # on the last iteration, using that profile's clamped duty_cycle rather
        # than the fallthrough max_duty_cycle.
        pytest.param(195, None, 90, id="last-boundary-uses-profile-not-fallthrough"),
    ],
)
def test_hold_duty_cycle(ptemp, profiles, expected):
    pwm_settings = dict(PWM_SETTINGS)
    if profiles is not None:
        pwm_settings["profiles"] = profiles

    assert hold_duty_cycle(setpoint=225, ptemp=ptemp, pwm_settings=pwm_settings) == expected


def test_hold_duty_cycle_empty_temp_range_list_returns_none():
    # Documented behavior: range(len([])) is empty, so the for-loop body
    # never executes and there is no explicit return in the else branch.
    # The function falls off the end and implicitly returns None. This
    # mirrors control.py, which would leave control['duty_cycle'] untouched
    # in this case (no assignment happens in the loop).
    pwm_settings = {"min_duty_cycle": 20, "max_duty_cycle": 100, "temp_range_list": [], "profiles": []}
    assert hold_duty_cycle(setpoint=225, ptemp=225, pwm_settings=pwm_settings) is None


def test_ramp_params_known_values():
    smoke_plus = {"on_time": 30, "off_time": 60, "duty_cycle": 50}
    pwm_settings = {"min_duty_cycle": 20, "max_duty_cycle": 100}

    result = ramp_params(smoke_plus, pwm_settings)

    # on_time = 30
    # min_duty_cycle = 20
    # max_ramp = 100 * (50 / 100) = 50.0
    assert result == (30, 20, 50.0)


def test_ramp_params_returns_tuple_of_three():
    smoke_plus = {"on_time": 45, "off_time": 90, "duty_cycle": 80}
    pwm_settings = {"min_duty_cycle": 10, "max_duty_cycle": 90}

    result = ramp_params(smoke_plus, pwm_settings)

    assert isinstance(result, tuple)
    assert len(result) == 3
    assert result == (45, 10, 72.0)
