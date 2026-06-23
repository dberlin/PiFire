from unittest import mock

import pytest


@pytest.fixture
def platform():
    """A GrillPlatform with all hardware mocked out."""
    import grillplat.x86_numato_emc2101 as mod
    with mock.patch.object(mod, 'NumatoUSBRelay') as relay_cls, \
         mock.patch.object(mod, 'EMC2101') as emc_cls, \
         mock.patch.object(mod, 'ExtendedI2C') as i2c_cls, \
         mock.patch.object(mod, 'find_i2c_bus', return_value=7):
        config = {
            'outputs': {'power': 0, 'igniter': 1, 'auger': 2, 'fan': 3},
            'frequency': 100,
        }
        plat = mod.GrillPlatform(config)
        plat._relay_cls = relay_cls
        plat._emc_cls = emc_cls
        plat._i2c_cls = i2c_cls
        yield plat


def test_init_opens_relay_and_emc(platform):
    # Relay opened on the default device; EMC2101 constructed on discovered bus.
    platform._relay_cls.assert_called_once()
    assert platform._relay_cls.call_args.args[0] == '/dev/ttyACM0'
    platform._i2c_cls.assert_called_once_with(7)
    platform._emc_cls.assert_called_once()


def test_auger_on_off_uses_mapped_relay(platform):
    platform.auger_on()
    platform.relay.relay_on.assert_called_with(2)
    platform.auger_off()
    platform.relay.relay_off.assert_called_with(2)


def test_power_and_igniter_use_mapped_relays(platform):
    platform.power_on()
    platform.relay.relay_on.assert_called_with(0)
    platform.igniter_on()
    platform.relay.relay_on.assert_called_with(1)


def test_get_output_status_reflects_cached_state(platform):
    platform.auger_on()
    platform.igniter_on()
    status = platform.get_output_status()
    assert status['auger'] is True
    assert status['igniter'] is True
    assert status['power'] is False
    assert status['fan'] is False


def test_get_input_status_is_false_when_standalone(platform):
    assert platform.get_input_status() is False
