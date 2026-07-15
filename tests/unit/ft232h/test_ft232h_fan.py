from unittest import mock

from tests.ft232h_helpers import make_ft232h_platform


def _emc_config(chip="emc2101", **overrides):
    config = {
        "outputs": {"power": "C0", "igniter": "C1", "auger": "C2", "fan": "C3"},
        "fan_controller": {"chip": chip},
        "triggerlevel": "LOW",
        "frequency": 25000,
    }
    config.update(overrides)
    return config


def test_emc2101_init_opens_i2c_and_controller():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        assert plat.pwm_fan is True
        harness.open_bus.assert_called_once_with("ft232h", "1")
        harness.emc2101_cls.assert_called_once_with(mock.sentinel.ft232h_bus)
        harness.emc2301_cls.assert_not_called()
        assert plat.emc is harness.emc2101_cls.return_value
        # Fan curve disabled so PiFire drives speed directly, and speed starts 0.
        assert plat.emc.lut_enabled is False
        assert plat.emc.manual_fan_speed == 0


def test_emc2301_init_uses_emc2301_at_default_address():
    with make_ft232h_platform(_emc_config("emc2301")) as (plat, harness):
        harness.emc2301_cls.assert_called_once()
        # Default EMC2301 address is 0x2F.
        assert harness.emc2301_cls.call_args.kwargs.get("address") == 0x2F


def test_fan_on_sets_relay_and_speed():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        plat.fan_on(80)
        assert harness.gpio.values["C3"] is False  # fan relay asserted (active-low)
        assert plat._output_state["fan"] is True
        assert plat.emc.manual_fan_speed == 80
        assert plat._fan_speed_percent == 80


def test_fan_off_zeroes_speed_and_deasserts_relay():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        plat.fan_on(80)
        plat.fan_off()
        assert plat.emc.manual_fan_speed == 0
        assert plat._output_state["fan"] is False
        assert harness.gpio.values["C3"] is True


def test_set_duty_cycle_clamps_to_0_100():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        plat.set_duty_cycle(150)
        assert plat.emc.manual_fan_speed == 100
        plat.set_duty_cycle(-20)
        assert plat.emc.manual_fan_speed == 0


def test_ramp_device_ends_at_max_duty_cycle():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        # Pre-set the stop event so the loop body runs once then exits without sleeping.
        plat._ramp_stop.set()
        plat._ramp_device(on_time=1, min_duty_cycle=20, max_duty_cycle=90, fps=25)
        assert plat.emc.manual_fan_speed == 90


def test_get_output_status_emc_mode_reports_pwm_and_frequency():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        plat.fan_on(60)
        status = plat.get_output_status()
        assert status["fan"] is True
        assert status["pwm"] == 60
        assert status["frequency"] == plat.frequency


def test_cleanup_zeroes_emc_speed():
    with make_ft232h_platform(_emc_config("emc2101")) as (plat, harness):
        plat.fan_on(50)
        plat.cleanup()
        assert plat.emc.manual_fan_speed == 0
