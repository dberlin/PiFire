from tests.ft232h_helpers import make_ft232h_platform


def _relay_config(**overrides):
    config = {
        "outputs": {"power": "C0", "igniter": "C1", "auger": "C2", "fan": "C3"},
        "fan_controller": {"chip": "none"},
        "triggerlevel": "LOW",
        "frequency": 25000,
    }
    config.update(overrides)
    return config


def test_relay_only_init_opens_shared_bus_but_no_emc():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        assert plat.pwm_fan is False
        assert plat.emc is None
        harness.open_bus.assert_called_once_with("ft232h", "1")
        harness.emc2101_cls.assert_not_called()
        harness.emc2301_cls.assert_not_called()
        assert set(plat.relays) == {"power", "igniter", "auger", "fan"}
        # Active-low, de-asserted at init -> True.
        assert harness.gpio.values["C0"] is True


def test_output_methods_toggle_mapped_active_low_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.auger_on()
        assert harness.gpio.values["C2"] is False  # auger -> C2 asserted (active-low)
        assert plat._output_state["auger"] is True
        plat.auger_off()
        assert harness.gpio.values["C2"] is True
        assert plat._output_state["auger"] is False


def test_power_and_igniter_use_mapped_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.power_on()
        plat.igniter_on()
        assert harness.gpio.values["C0"] is False  # power -> C0
        assert harness.gpio.values["C1"] is False  # igniter -> C1


def test_active_high_trigger_level_not_inverted():
    with make_ft232h_platform(_relay_config(triggerlevel="HIGH")) as (plat, harness):
        assert harness.gpio.values["C0"] is False  # de-asserted at init (active-high)
        plat.power_on()
        assert harness.gpio.values["C0"] is True


def test_missing_triggerlevel_defaults_to_active_high():
    config = _relay_config()
    del config["triggerlevel"]
    with make_ft232h_platform(config) as (plat, harness):
        assert harness.gpio.values["C0"] is False  # de-asserted at init (active-high default)
        plat.power_on()
        assert harness.gpio.values["C0"] is True


def test_custom_pin_mapping_is_honored():
    with make_ft232h_platform(_relay_config(outputs={"power": "D4", "igniter": "D5", "auger": "D6", "fan": "D7"})) as (
        plat,
        harness,
    ):
        plat.auger_on()
        assert harness.gpio.values["D6"] is False


def test_unknown_pin_name_raises_value_error():
    import pytest

    with pytest.raises(ValueError):
        with make_ft232h_platform(_relay_config(outputs={"power": "Z9", "igniter": "C1", "auger": "C2", "fan": "C3"})):
            pass


def test_relay_only_fan_on_off_and_toggle():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.fan_on()
        assert harness.gpio.values["C3"] is False  # fan -> C3 asserted
        assert plat._output_state["fan"] is True
        plat.fan_toggle()
        assert plat._output_state["fan"] is False
        assert harness.gpio.values["C3"] is True


def test_relay_only_set_duty_cycle_and_frequency_are_noops():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.set_duty_cycle(50)
        plat.set_pwm_frequency(20000)
        assert plat.emc is None


def test_get_output_status_relay_mode_has_no_pwm_keys():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.auger_on()
        status = plat.get_output_status()
        assert status == {"auger": True, "igniter": False, "power": False, "fan": False}


def test_get_input_status_is_false():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        assert plat.get_input_status() is False


def test_cleanup_deasserts_pins():
    with make_ft232h_platform(_relay_config()) as (plat, harness):
        plat.power_on()
        plat.cleanup()
        for pin in ("C0", "C1", "C2", "C3"):
            assert harness.gpio.values[pin] is True  # all de-asserted


def test_import_does_not_enable_ft232h_backend():
    import subprocess
    import sys

    # Intentional real-process integration test: a fresh interpreter is required so the
    # module-level import side effect (or absence of one) can't be masked by an
    # earlier test in this process having already imported/mocked the module.
    code = "import os, grillplat.ft232h_relay; assert 'BLINKA_FT232H' not in os.environ"
    subprocess.run([sys.executable, "-c", code], check=True, cwd=".")
