from unittest import mock


def test_fan_on_closes_relay_and_sets_speed(x86_platform):
    x86_platform.fan_on(60)
    x86_platform.relay.relay_on.assert_called_with(3)
    assert x86_platform.emc.manual_fan_speed == 60
    assert x86_platform.get_output_status()["fan"] is True


def test_fan_off_zeroes_speed_and_opens_relay(x86_platform):
    x86_platform.fan_on(60)
    x86_platform.fan_off()
    x86_platform.relay.relay_off.assert_called_with(3)
    assert x86_platform.emc.manual_fan_speed == 0
    assert x86_platform.get_output_status()["fan"] is False


def test_set_duty_cycle_sets_manual_fan_speed_directly(x86_platform):
    x86_platform.set_duty_cycle(42)
    # No inversion: requested percent maps directly to EMC2101 duty.
    assert x86_platform.emc.manual_fan_speed == 42
    assert x86_platform.get_output_status()["pwm"] == 42


def test_fan_toggle_flips_state(x86_platform):
    assert x86_platform.get_output_status()["fan"] is False
    x86_platform.fan_toggle()
    assert x86_platform.get_output_status()["fan"] is True
    x86_platform.fan_toggle()
    assert x86_platform.get_output_status()["fan"] is False


def test_frequency_defaults_to_25000(x86_platform):
    assert x86_platform.frequency == 25000
    assert x86_platform.get_output_status()["frequency"] == 25000


def test_init_configures_emc2101_for_25khz(x86_platform):
    # EMC2101_LUT is configured for ~25 kHz at init: 360 kHz preset clock,
    # PWM_F = 7, divisor 1.
    x86_platform.emc.set_pwm_clock.assert_called_with(use_preset=False, use_slow=False)
    assert x86_platform.emc.pwm_frequency == 7
    assert x86_platform.emc.pwm_frequency_divisor == 1


def test_set_pwm_frequency_reports_requested_value(x86_platform):
    x86_platform.set_pwm_frequency(26000)
    assert x86_platform.frequency == 26000
    assert x86_platform.get_output_status()["frequency"] == 26000
    # 26 kHz still maps to PWM_F = 7 on the EMC2101.
    assert x86_platform.emc.pwm_frequency == 7


def test_set_pwm_frequency_on_emc2301_passes_hz():
    import grillplat.x86_numato as mod

    with (
        mock.patch.object(mod, "NumatoUSBRelay"),
        mock.patch.object(mod, "EMC2101_LUT"),
        mock.patch.object(mod, "EMC2301"),
        mock.patch.object(mod, "open_i2c_bus"),
    ):
        platform = mod.GrillPlatform({"fan_controller": {"chip": "emc2301"}})
    # EMC2301 takes a frequency in Hz directly.
    assert platform.emc.pwm_frequency == 25000


def test_get_output_status_includes_pwm_and_frequency(x86_platform):
    x86_platform.fan_on(75)
    status = x86_platform.get_output_status()
    assert status["pwm"] == 75
    assert status["frequency"] == 25000


def test_set_duty_cycle_clamps_out_of_range(x86_platform):
    # The EMC2101 raises ValueError outside 0-100; a bad settings value must
    # not propagate (it would kill the ramp thread). It is clamped instead.
    x86_platform.set_duty_cycle(150)
    assert x86_platform.emc.manual_fan_speed == 100
    assert x86_platform.get_output_status()["pwm"] == 100
    x86_platform.set_duty_cycle(-20)
    assert x86_platform.emc.manual_fan_speed == 0
    assert x86_platform.get_output_status()["pwm"] == 0
