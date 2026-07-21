"""Coverage for grillplat/raspberry_pi_all.py: the Raspberry Pi GrillPlatform
that drives relay outputs via gpiozero OutputDevice/Button and (for DC fans)
a 3.3V PWM signal via the RPi Hardware PWM module.

Companion to tests/unit/platform/test_raspberry_pi_system.py, which covers
the SystemCommandsMixin surface (supported_commands, check_throttled, etc.)
via `_bare()` (object.__new__, bypassing __init__ entirely -- no GPIO
objects are ever constructed there). This file instead drives real __init__
and exercises the platform-specific surface: relay outputs, the DC-fan PWM
duty-cycle math, get_output_status, and cleanup. `HardwarePWM`, `OutputDevice`,
and `Button` are mocked out so no real Pi hardware is touched.
"""

import logging
import sys
import types
from unittest import mock

import pytest

# raspberry_pi_all imports `from rpi_hardware_pwm import HardwarePWM` at module
# load; that package is Pi-only and absent in the test venv. Stub it so the
# module imports on a generic host (mirrors test_raspberry_pi_system.py).
if "rpi_hardware_pwm" not in sys.modules:
    _stub = types.ModuleType("rpi_hardware_pwm")
    _stub.HardwarePWM = type("HardwarePWM", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["rpi_hardware_pwm"] = _stub

import grillplat.raspberry_pi_all as rpi  # noqa: E402


def _config(dc_fan=False, frequency=100, standalone=True, triggerlevel="HIGH", pwm_pin=13):
    outputs = {"auger": 14, "igniter": 18, "power": 4}
    if dc_fan:
        outputs["dc_fan"] = 12
        outputs["pwm"] = pwm_pin
    else:
        outputs["fan"] = 15
    return {
        "outputs": outputs,
        "inputs": {"selector": 17},
        "dc_fan": dc_fan,
        "frequency": frequency,
        "standalone": standalone,
        "triggerlevel": triggerlevel,
    }


@pytest.fixture
def hw():
    """Mock out every gpiozero/rpi_hardware_pwm handle the module touches.

    Each OutputDevice(...) call returns a fresh Mock (so fan/auger/igniter/
    power are independently assertable) with is_active seeded False, matching
    the real device's initial_value=False.
    """
    with (
        mock.patch.object(rpi, "OutputDevice") as output_device,
        mock.patch.object(rpi, "Button") as button,
        mock.patch.object(rpi, "HardwarePWM") as hardware_pwm,
    ):
        output_device.side_effect = lambda *a, **k: mock.Mock(is_active=False)
        button.side_effect = lambda *a, **k: mock.Mock(is_active=False)
        hardware_pwm.side_effect = lambda *a, **k: mock.Mock()
        yield types.SimpleNamespace(output_device=output_device, button=button, hardware_pwm=hardware_pwm)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_without_dc_fan_creates_plain_fan_output_device(hw):
    p = rpi.GrillPlatform(_config(dc_fan=False))
    assert p.dc_fan is False
    assert p.selector is None
    assert not hasattr(p, "current_fan_speed_percent")
    # fan/auger/igniter/power are all OutputDevice instances (mocked).
    hw.output_device.assert_any_call(15, active_high=True, initial_value=False)
    hw.output_device.assert_any_call(14, active_high=True, initial_value=False)
    hw.output_device.assert_any_call(18, active_high=True, initial_value=False)
    hw.output_device.assert_any_call(4, active_high=True, initial_value=False)
    hw.hardware_pwm.assert_not_called()


def test_init_with_dc_fan_seeds_zero_speed_and_starts_hardware_pwm(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True, frequency=25000))
    # Fan is not commanded on at init -- report 0% (matches fan_off() and the
    # other platforms' initial seed), not a raw/uninitialized value.
    assert p.current_fan_speed_percent == 0
    assert p._ramp_thread is None
    hw.output_device.assert_any_call(12, active_high=True, initial_value=False)
    hw.hardware_pwm.assert_called_once_with(pwm_channel=1, hz=25000)


def test_init_dc_fan_pin_13_maps_to_pwm_channel_1(hw):
    rpi.GrillPlatform(_config(dc_fan=True, pwm_pin=13))
    hw.hardware_pwm.assert_called_once_with(pwm_channel=1, hz=100)


def test_init_dc_fan_pin_19_maps_to_pwm_channel_1(hw):
    rpi.GrillPlatform(_config(dc_fan=True, pwm_pin=19))
    hw.hardware_pwm.assert_called_once_with(pwm_channel=1, hz=100)


def test_init_dc_fan_other_pin_maps_to_pwm_channel_0(hw):
    rpi.GrillPlatform(_config(dc_fan=True, pwm_pin=12))
    hw.hardware_pwm.assert_called_once_with(pwm_channel=0, hz=100)


def test_init_active_high_false_when_triggerlevel_low(hw):
    rpi.GrillPlatform(_config(dc_fan=False, triggerlevel="LOW"))
    hw.output_device.assert_any_call(15, active_high=False, initial_value=False)


def test_init_standalone_false_creates_selector_button(hw):
    p = rpi.GrillPlatform(_config(dc_fan=False, standalone=False))
    hw.button.assert_called_once_with(17)
    assert p.selector is not None


def test_init_config_parse_failure_is_logged_and_reraised(caplog):
    with caplog.at_level(logging.ERROR, logger="control"):
        with pytest.raises(AttributeError):
            rpi.GrillPlatform(None)
    assert "Error parsing platform configuration" in caplog.text


# ---------------------------------------------------------------------------
# Relay outputs: auger / igniter / power
# ---------------------------------------------------------------------------


def test_auger_on_off(hw):
    p = rpi.GrillPlatform(_config())
    p.auger_on()
    p.auger.on.assert_called_once()
    p.auger_off()
    p.auger.off.assert_called_once()


def test_igniter_on_off(hw):
    p = rpi.GrillPlatform(_config())
    p.igniter_on()
    p.igniter.on.assert_called_once()
    p.igniter_off()
    p.igniter.off.assert_called_once()


def test_power_on_off(hw):
    p = rpi.GrillPlatform(_config())
    p.power_on()
    p.power.on.assert_called_once()
    p.power_off()
    p.power.off.assert_called_once()


# ---------------------------------------------------------------------------
# Fan: fan_on / fan_off / fan_toggle / set_duty_cycle
# (the inverted `100 - percent` PWM duty-cycle math)
# ---------------------------------------------------------------------------


def test_fan_on_without_dc_fan_only_toggles_relay_and_skips_pwm(hw):
    p = rpi.GrillPlatform(_config(dc_fan=False))
    p.fan_on()
    p.fan.on.assert_called_once()
    hw.hardware_pwm.assert_not_called()
    assert not hasattr(p, "current_fan_speed_percent")


def test_fan_on_with_dc_fan_starts_pwm_with_inverted_duty_cycle(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.fan_on(fan_speed_percent=60)
    p.fan.on.assert_called_once()
    # PWM duty cycle = (100 - fan percent speed) -- the amplifier inverts.
    p.pwm.start.assert_called_once_with(40.0)
    assert p.current_fan_speed_percent == 60


def test_fan_on_default_is_full_speed_zero_pwm_duty(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.fan_on()
    p.pwm.start.assert_called_once_with(0.0)
    assert p.current_fan_speed_percent == 100


def test_fan_off_without_dc_fan_only_toggles_relay(hw):
    p = rpi.GrillPlatform(_config(dc_fan=False))
    p.fan_off()
    p.fan.off.assert_called_once()


def test_fan_off_with_dc_fan_stops_pwm_and_resets_speed_to_zero(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.fan_on(fan_speed_percent=75)
    p.fan_off()
    p.fan.off.assert_called_once()
    p.pwm.stop.assert_called_once()
    assert p.current_fan_speed_percent == 0


def test_fan_toggle_delegates_to_device_toggle(hw):
    p = rpi.GrillPlatform(_config())
    p.fan_toggle()
    p.fan.toggle.assert_called_once()


def test_set_duty_cycle_inverts_percent_and_stops_ramp_by_default(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    with mock.patch.object(p, "_stop_ramp") as stop_ramp:
        p.set_duty_cycle(75)
    stop_ramp.assert_called_once()
    p.pwm.change_duty_cycle.assert_called_once_with(25.0)
    assert p.current_fan_speed_percent == 75


def test_set_duty_cycle_does_not_stop_ramp_when_overridden_off(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    with mock.patch.object(p, "_stop_ramp") as stop_ramp:
        p.set_duty_cycle(30, override_ramping=False)
    stop_ramp.assert_not_called()
    p.pwm.change_duty_cycle.assert_called_once_with(70.0)


def test_set_duty_cycle_boundaries(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.set_duty_cycle(0)
    p.pwm.change_duty_cycle.assert_called_with(100.0)
    p.set_duty_cycle(100)
    p.pwm.change_duty_cycle.assert_called_with(0.0)


def test_set_pwm_frequency_calls_hardware_pwm_change_frequency(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.set_pwm_frequency(20000)
    p.pwm.change_frequency.assert_called_once_with(20000)


# ---------------------------------------------------------------------------
# get_input_status
# ---------------------------------------------------------------------------


def test_get_input_status_always_false_when_standalone(hw):
    p = rpi.GrillPlatform(_config(standalone=True))
    assert p.get_input_status() is False


def test_get_input_status_reads_selector_when_not_standalone(hw):
    p = rpi.GrillPlatform(_config(standalone=False))
    p.selector.is_active = True
    assert p.get_input_status() is True


# ---------------------------------------------------------------------------
# get_output_status
# ---------------------------------------------------------------------------


def test_get_output_status_without_dc_fan_has_no_pwm_keys(hw):
    p = rpi.GrillPlatform(_config(dc_fan=False))
    p.auger.is_active = True
    p.power.is_active = True
    status = p.get_output_status()
    assert status == {"auger": True, "igniter": False, "power": True, "fan": False}
    assert "pwm" not in status
    assert "frequency" not in status


def test_get_output_status_immediately_after_init_reports_zero_percent(hw):
    """Regression/pinning test: current_fan_speed_percent is unconditionally
    seeded to 0 in __init__ (raspberry_pi_all.py:73), matching the "unified"
    initial-seed convention used by fan_off() and the other platform drivers
    (e.g. grillplat/prototype.py). get_output_status() called right after
    construction -- before any fan_on()/set_duty_cycle() call -- must report
    a sane 0% fan speed, not a stale/uninitialized value.
    """
    p = rpi.GrillPlatform(_config(dc_fan=True, frequency=15000))
    status = p.get_output_status()
    assert status["pwm"] == 0
    assert status["frequency"] == 15000


def test_get_output_status_with_dc_fan_reports_current_speed_after_fan_on(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True, frequency=15000))
    p.fan.is_active = True
    p.fan_on(fan_speed_percent=60)
    status = p.get_output_status()
    assert status["pwm"] == 60
    assert status["frequency"] == 15000
    assert status["fan"] is True


# ---------------------------------------------------------------------------
# pwm_fan_ramp / _start_ramp / _stop_ramp
# ---------------------------------------------------------------------------


def test_pwm_fan_ramp_turns_fan_on_and_runs_a_background_thread_to_completion(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p.pwm_fan_ramp(on_time=0.05, min_duty_cycle=100, max_duty_cycle=100)
    p.fan.on.assert_called()
    thread = p._ramp_thread
    assert thread is not None
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_stop_ramp_is_a_noop_with_no_active_thread(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    assert p._ramp_thread is None
    p._stop_ramp()  # must not raise
    assert p._ramp_thread is None


def test_stop_ramp_interrupts_an_active_background_ramp(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True))
    p._start_ramp(on_time=5, min_duty_cycle=20, max_duty_cycle=100, background=True)
    thread = p._ramp_thread
    assert thread.is_alive()
    p._stop_ramp()
    assert p._ramp_thread is None
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_closes_all_devices_and_stops_pwm(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True, standalone=True))
    p.cleanup()
    p.power.close.assert_called_once()
    p.igniter.close.assert_called_once()
    p.auger.close.assert_called_once()
    p.fan.close.assert_called_once()
    p.pwm.stop.assert_called_once()


def test_cleanup_closes_selector_when_not_standalone(hw):
    p = rpi.GrillPlatform(_config(dc_fan=True, standalone=False))
    p.cleanup()
    p.selector.close.assert_called_once()
