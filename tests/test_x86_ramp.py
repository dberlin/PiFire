from unittest import mock

import pytest


@pytest.fixture
def platform():
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay'), \
         mock.patch.object(mod, 'EMC2101'), \
         mock.patch.object(mod, 'ExtendedI2C'), \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3}, 'frequency': 100}
        yield mod.GrillPlatform(config)


def test_pwm_fan_ramp_runs_to_completion(platform):
    # Use a very short ramp so the test is fast; join the thread before asserting.
    platform.pwm_fan_ramp(on_time=0.1, min_duty_cycle=20, max_duty_cycle=100)
    platform._ramp_thread.join(timeout=5)
    assert platform._ramp_thread.is_alive() is False
    # Fan power relay enabled and final speed is the max duty cycle.
    platform.relay.relay_on.assert_any_call(3)
    assert platform._fan_speed_percent == 100


def test_stop_ramp_halts_thread(platform):
    platform.pwm_fan_ramp(on_time=10, min_duty_cycle=20, max_duty_cycle=100)
    platform._stop_ramp()
    assert platform._ramp_thread is None
