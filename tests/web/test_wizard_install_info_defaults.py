from blueprints.wizard.wizard import wizardInstallInfoDefaults

_WIZARD_DATA = {
	'modules': {
		'grillplatform': {
			'x86': {
				'default': True,
				'settings_dependencies': {
					'i2c_bus_kind': {
						'options': {'basic': 'Basic', 'extended': 'Extended'},
						'settings': ['platform', 'fan_controller', 'i2c_bus_kind'],
					},
					'i2c_bus_num': {
						'type': 'i2c_bus_num',
						'default': 'CP2112',
						'settings': ['platform', 'fan_controller', 'i2c_bus_num'],
					},
				},
			}
		},
		'display': {'none': {'default': True, 'settings_dependencies': {}}},
		'distance': {'none': {'default': True, 'settings_dependencies': {}}},
	},
	'boards': {'x86': {'probe_map': {'probe_devices': []}}},
}


def test_wizard_install_info_defaults_handles_options_free_field():
	settings = {'display': {'config': {'none': {}}}}
	info = wizardInstallInfoDefaults(_WIZARD_DATA, settings)
	# 'options'-based dependency still seeds its first key.
	assert info['modules']['grillplatform']['settings']['i2c_bus_kind'] == 'basic'
	# 'type: i2c_bus_num' dependency (no 'options') seeds its explicit 'default'.
	assert info['modules']['grillplatform']['settings']['i2c_bus_num'] == 'CP2112'
