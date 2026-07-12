from tests.ft232h_helpers import make_ft232h_platform


def _relay_config(**overrides):
	config = {
		'outputs': {'power': 'C0', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'},
		'fan_controller': {'chip': 'none'},
		'triggerlevel': 'LOW',
		'frequency': 25000,
	}
	config.update(overrides)
	return config


def test_relay_only_init_opens_no_i2c_or_emc():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		assert plat.pwm_fan is False
		assert plat.emc is None
		harness.busio.I2C.assert_not_called()
		harness.emc2101_cls.assert_not_called()
		harness.emc2301_cls.assert_not_called()
		# Four output pins created and de-asserted (active-low -> value True).
		assert set(plat.relays) == {'power', 'igniter', 'auger', 'fan'}
		assert plat.relays['power']._dio.value is True


def test_output_methods_toggle_mapped_active_low_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.auger_on()
		# 'auger' maps to C2; active-low asserted -> value False.
		assert harness.dio.pins['C2'].value is False
		assert plat._output_state['auger'] is True
		plat.auger_off()
		assert harness.dio.pins['C2'].value is True
		assert plat._output_state['auger'] is False


def test_power_and_igniter_use_mapped_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.power_on()
		plat.igniter_on()
		assert harness.dio.pins['C0'].value is False  # power -> C0
		assert harness.dio.pins['C1'].value is False  # igniter -> C1


def test_active_high_trigger_level_not_inverted():
	with make_ft232h_platform(_relay_config(triggerlevel='HIGH')) as (plat, harness):
		# De-asserted at init -> value False for active-high.
		assert harness.dio.pins['C0'].value is False
		plat.power_on()
		assert harness.dio.pins['C0'].value is True


def test_custom_pin_mapping_is_honored():
	with make_ft232h_platform(_relay_config(outputs={'power': 'D4', 'igniter': 'D5', 'auger': 'D6', 'fan': 'D7'})) as (
		plat,
		harness,
	):
		plat.auger_on()
		assert harness.dio.pins['D6'].value is False


def test_unknown_pin_name_raises_value_error():
	import pytest

	with pytest.raises(ValueError):
		with make_ft232h_platform(_relay_config(outputs={'power': 'Z9', 'igniter': 'C1', 'auger': 'C2', 'fan': 'C3'})):
			pass


def test_relay_only_fan_on_off_and_toggle():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.fan_on()
		assert harness.dio.pins['C3'].value is False  # fan -> C3 asserted
		assert plat._output_state['fan'] is True
		plat.fan_toggle()
		assert plat._output_state['fan'] is False
		assert harness.dio.pins['C3'].value is True


def test_relay_only_set_duty_cycle_and_frequency_are_noops():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		# Must not raise and must not create an EMC.
		plat.set_duty_cycle(50)
		plat.set_pwm_frequency(20000)
		assert plat.emc is None


def test_get_output_status_relay_mode_has_no_pwm_keys():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.auger_on()
		status = plat.get_output_status()
		assert status == {'auger': True, 'igniter': False, 'power': False, 'fan': False}


def test_get_input_status_is_false():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		assert plat.get_input_status() is False


def test_cleanup_deasserts_and_closes_pins():
	with make_ft232h_platform(_relay_config()) as (plat, harness):
		plat.power_on()
		plat.cleanup()
		# All relays de-asserted (active-low -> True) and closed.
		for pin in ('C0', 'C1', 'C2', 'C3'):
			assert harness.dio.pins[pin].value is True
			assert harness.dio.pins[pin].deinit_called is True
