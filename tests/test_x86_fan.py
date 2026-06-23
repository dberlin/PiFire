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


def test_fan_on_closes_relay_and_sets_speed(platform):
    platform.fan_on(60)
    platform.relay.relay_on.assert_called_with(3)
    assert platform.emc.manual_fan_speed == 60
    assert platform.get_output_status()['fan'] is True


def test_fan_off_zeroes_speed_and_opens_relay(platform):
    platform.fan_on(60)
    platform.fan_off()
    platform.relay.relay_off.assert_called_with(3)
    assert platform.emc.manual_fan_speed == 0
    assert platform.get_output_status()['fan'] is False


def test_set_duty_cycle_sets_manual_fan_speed_directly(platform):
    platform.set_duty_cycle(42)
    # No inversion: requested percent maps directly to EMC2101 duty.
    assert platform.emc.manual_fan_speed == 42
    assert platform.get_output_status()['pwm'] == 42


def test_fan_toggle_flips_state(platform):
    assert platform.get_output_status()['fan'] is False
    platform.fan_toggle()
    assert platform.get_output_status()['fan'] is True
    platform.fan_toggle()
    assert platform.get_output_status()['fan'] is False


def test_set_pwm_frequency_stored_and_reported(platform):
    platform.set_pwm_frequency(30)
    assert platform.frequency == 30
    assert platform.get_output_status()['frequency'] == 30


def test_get_output_status_includes_pwm_and_frequency(platform):
    platform.fan_on(75)
    status = platform.get_output_status()
    assert status['pwm'] == 75
    assert status['frequency'] == 100


def test_set_duty_cycle_clamps_out_of_range(platform):
    # The EMC2101 raises ValueError outside 0-100; a bad settings value must
    # not propagate (it would kill the ramp thread). It is clamped instead.
    platform.set_duty_cycle(150)
    assert platform.emc.manual_fan_speed == 100
    assert platform.get_output_status()['pwm'] == 100
    platform.set_duty_cycle(-20)
    assert platform.emc.manual_fan_speed == 0
    assert platform.get_output_status()['pwm'] == 0
