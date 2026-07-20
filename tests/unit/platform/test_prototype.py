"""Coverage for grillplat/prototype.py: the no-hardware "software" GrillPlatform
used for dev/testing.  Companion to test_prototype_system.py, which covers the
SystemCommandsMixin surface (supported_commands, check_throttled, etc.) and
the prototype's overrides of the system commands.  This file covers the
platform-specific surface: __init__, relay outputs, PWM/duty-cycle, the
software fan ramp, input status, get_output_status, and cleanup.
"""

import logging

import pytest
from unittest import mock

import grillplat.prototype as proto
from grillplat.prototype import GrillPlatform


def _config(dc_fan=False, frequency=100, standalone=True):
    # Real (mutable) dicts: __init__ mutates config["outputs"]/config["inputs"]
    # in place to seed relay/selector state. "pwm" is only a meaningful pin
    # config for the dc_fan variant, matching common/defaults.py's shape.
    outputs = {"auger": 14, "fan": 15, "igniter": 18, "power": 4}
    if dc_fan:
        outputs["pwm"] = 13
    return {
        "outputs": outputs,
        "inputs": {"selector": 17},
        "dc_fan": dc_fan,
        "frequency": frequency,
        "standalone": standalone,
    }


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


def test_init_seeds_all_relay_outputs_off_without_dc_fan():
    p = GrillPlatform(_config(dc_fan=False))
    assert p.out_pins["auger"] is False
    assert p.out_pins["fan"] is False
    assert p.out_pins["igniter"] is False
    assert p.out_pins["power"] is False
    assert p.in_pins["selector"] is False
    assert "pwm" not in p.out_pins
    assert not hasattr(p, "_ramp_thread")


def test_init_stores_config_scalars():
    p = GrillPlatform(_config(dc_fan=False, frequency=250, standalone=False))
    assert p.dc_fan is False
    assert p.frequency == 250
    assert p.standalone is False
    assert p.current == {}


def test_init_with_dc_fan_seeds_ramp_thread_and_pwm_at_full_speed():
    p = GrillPlatform(_config(dc_fan=True))
    assert p._ramp_thread is None
    # Seeded through set_duty_cycle's inverted (100-percent)/100.0 convention
    # (percent=100 -> 0.0), so get_output_status() reports a sane 100% initial
    # fan speed instead of the old nonsensical raw-percent seed -- see
    # test_get_output_status_immediately_after_init_is_sane below.
    assert p.out_pins["pwm"] == pytest.approx(0.0)


def test_init_config_parse_failure_is_logged_and_reraised(caplog):
    """Regression test (grillplat/prototype.py:35-44): __init__ wraps
    config.get(...) parsing in a bare `except:` that logs "Error parsing
    platform configuration." and now re-raises the original exception,
    matching raspberry_pi_all.py's identical log-then-raise pattern for the
    same block. This used to swallow the exception and fall through to an
    unconditional `if self.dc_fan:`, which raised a confusing, unrelated
    AttributeError ('GrillPlatform' object has no attribute 'dc_fan') instead
    of the original, more informative error.
    """
    with caplog.at_level(logging.ERROR, logger="control"):
        with pytest.raises(AttributeError, match="NoneType"):
            GrillPlatform(None)
    assert "Error parsing platform configuration" in caplog.text


# ---------------------------------------------------------------------------
# Relay outputs: auger / igniter / power
# ---------------------------------------------------------------------------


def test_auger_on_off():
    p = GrillPlatform(_config())
    p.auger_on()
    assert p.out_pins["auger"] is True
    p.auger_off()
    assert p.out_pins["auger"] is False


def test_igniter_on_off():
    p = GrillPlatform(_config())
    p.igniter_on()
    assert p.out_pins["igniter"] is True
    p.igniter_off()
    assert p.out_pins["igniter"] is False


def test_power_on_off():
    p = GrillPlatform(_config())
    p.power_on()
    assert p.out_pins["power"] is True
    p.power_off()
    assert p.out_pins["power"] is False


# ---------------------------------------------------------------------------
# Fan: on/off/toggle, non-DC and DC (PWM) variants
# ---------------------------------------------------------------------------


def test_fan_on_off_without_dc_fan_does_not_touch_duty_cycle():
    p = GrillPlatform(_config(dc_fan=False))
    with mock.patch.object(p, "set_duty_cycle") as set_dc:
        p.fan_on()
    assert p.out_pins["fan"] is True
    set_dc.assert_not_called()
    p.fan_off()
    assert p.out_pins["fan"] is False


def test_fan_on_with_dc_fan_applies_inverted_duty_cycle():
    p = GrillPlatform(_config(dc_fan=True))
    p.fan_on(duty_cycle=60)
    assert p.out_pins["fan"] is True
    # (100 - 60) / 100.0 -- see set_duty_cycle's inverted-logic docstring.
    assert p.out_pins["pwm"] == pytest.approx(0.4)


def test_fan_on_default_duty_cycle_is_full_speed_when_dc_fan():
    p = GrillPlatform(_config(dc_fan=True))
    p.fan_on()
    assert p.out_pins["pwm"] == pytest.approx(0.0)


def test_fan_toggle_flips_both_directions():
    p = GrillPlatform(_config())
    assert p.out_pins["fan"] is False
    p.fan_toggle()
    assert p.out_pins["fan"] is True
    p.fan_toggle()
    assert p.out_pins["fan"] is False


# ---------------------------------------------------------------------------
# set_duty_cycle / set_pwm_frequency
# ---------------------------------------------------------------------------


def test_set_duty_cycle_inverts_percent_to_0_1_float():
    p = GrillPlatform(_config(dc_fan=True))
    p.set_duty_cycle(0)  # 0% requested speed -> fully off -> 1.0
    assert p.out_pins["pwm"] == pytest.approx(1.0)
    p.set_duty_cycle(100)  # 100% requested speed -> fully on -> 0.0
    assert p.out_pins["pwm"] == pytest.approx(0.0)
    p.set_duty_cycle(25)
    assert p.out_pins["pwm"] == pytest.approx(0.75)


def test_set_duty_cycle_stops_an_active_ramp():
    p = GrillPlatform(_config(dc_fan=True))
    with mock.patch.object(p, "_stop_ramp") as stop_ramp:
        p.set_duty_cycle(50)
    stop_ramp.assert_called_once()


def test_set_pwm_frequency_updates_attribute():
    p = GrillPlatform(_config(dc_fan=True, frequency=100))
    p.set_pwm_frequency(20000)
    assert p.frequency == 20000


# ---------------------------------------------------------------------------
# Input status
# ---------------------------------------------------------------------------


def test_get_and_set_input_status():
    p = GrillPlatform(_config())
    assert p.get_input_status() is False
    p.set_input_status(True)
    assert p.get_input_status() is True
    assert p.in_pins["selector"] is True


# ---------------------------------------------------------------------------
# get_output_status
# ---------------------------------------------------------------------------


def test_get_output_status_without_dc_fan_has_no_pwm_keys():
    p = GrillPlatform(_config(dc_fan=False))
    p.auger_on()
    p.power_on()
    status = p.get_output_status()
    assert status == {"auger": True, "igniter": False, "power": True, "fan": False}
    assert "pwm" not in status
    assert "frequency" not in status


def test_get_output_status_with_dc_fan_reports_duty_and_frequency():
    p = GrillPlatform(_config(dc_fan=True, frequency=15000))
    p.set_duty_cycle(75)
    status = p.get_output_status()
    assert status["pwm"] == pytest.approx(75.0)
    assert status["frequency"] == 15000


def test_get_output_status_immediately_after_init_is_sane():
    """Regression test (grillplat/prototype.py:47 vs 116): __init__ now seeds
    out_pins["pwm"] using the same inverted 0.0-1.0 duty-cycle convention that
    get_output_status()/set_duty_cycle() read/write, instead of a raw percent.
    Calling get_output_status() right after construction, before any
    fan_on()/set_duty_cycle() call, reports a sane 100% fan speed (matching
    raspberry_pi_all's initial current_fan_speed_percent = 100) instead of the
    old nonsensical -9900.
    """
    p = GrillPlatform(_config(dc_fan=True))
    status = p.get_output_status()
    assert status["pwm"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Software PWM ramp: _start_ramp / _stop_ramp / _ramp_device / pwm_fan_ramp
# ---------------------------------------------------------------------------


def test_start_ramp_foreground_runs_to_completion_and_clears_thread():
    p = GrillPlatform(_config(dc_fan=True))
    # min == max duty cycle collapses the ramp to a single short step so this
    # runs synchronously (background=False) in well under a second.
    p._start_ramp(on_time=0.02, min_duty_cycle=100, max_duty_cycle=100, background=False)
    assert p._ramp_thread is None
    assert isinstance(p.out_pins["pwm"], float)


def test_stop_ramp_is_a_noop_with_no_active_thread():
    p = GrillPlatform(_config(dc_fan=True))
    assert p._ramp_thread is None
    p._stop_ramp()  # must not raise
    assert p._ramp_thread is None


def test_pwm_fan_ramp_turns_fan_on_and_runs_a_background_thread_to_completion():
    p = GrillPlatform(_config(dc_fan=True))
    p.pwm_fan_ramp(on_time=0.02, min_duty_cycle=100, max_duty_cycle=100)
    assert p.out_pins["fan"] is True
    thread = p._ramp_thread
    assert thread is not None
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_stop_ramp_interrupts_an_active_background_ramp():
    p = GrillPlatform(_config(dc_fan=True))
    p._start_ramp(on_time=5, min_duty_cycle=20, max_duty_cycle=100, background=True)
    thread = p._ramp_thread
    assert thread.is_alive()
    p._stop_ramp()
    assert p._ramp_thread is None
    assert not thread.is_alive()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_turns_off_power_igniter_auger_and_fan():
    p = GrillPlatform(_config())
    p.power_on()
    p.igniter_on()
    p.auger_on()
    p.fan_on()
    p.cleanup()
    assert p.out_pins["power"] is False
    assert p.out_pins["igniter"] is False
    assert p.out_pins["auger"] is False
    assert p.out_pins["fan"] is False


# ---------------------------------------------------------------------------
# System command overrides: branches not exercised via the real host
# (test_prototype_system.py covers the happy-path overrides themselves).
# ---------------------------------------------------------------------------


def test_check_cpu_temp_falls_back_to_zero_when_is_float_false(monkeypatch):
    p = GrillPlatform(_config())
    monkeypatch.setattr(proto, "is_float", lambda _v: False)
    data = p.check_cpu_temp([])
    assert data["data"]["cpu_temp"] == 0.0


def test_hardware_info_populates_hardware_field_from_proc_cpuinfo():
    # Real dev-host /proc/cpuinfo (x86) has no "Hardware" line (that's a
    # Raspberry-Pi-specific field), so the `if "hardware" in line.lower()`
    # branch is unreachable without mocking the file content. psutil's own
    # cpu_count/cpu_freq also read /proc/cpuinfo internally (in binary mode),
    # so those are stubbed too -- otherwise mocking builtins.open with text
    # read_data breaks psutil's separate real read of the same path.
    p = GrillPlatform(_config())
    cpuinfo = "Hardware\t: BCM2711\nmodel\t\t: 3\nmodel name\t: ARMv7 Processor rev 3\n"
    with (
        mock.patch("psutil.cpu_count", return_value=4),
        mock.patch("psutil.cpu_freq", return_value=mock.Mock(current=1500.0)),
        mock.patch("psutil.virtual_memory", return_value=mock.Mock(total=100, available=50)),
        mock.patch("builtins.open", mock.mock_open(read_data=cpuinfo)),
    ):
        info = p.hardware_info([])
    assert info["data"]["cpu_info"]["hardware"] == "BCM2711"
