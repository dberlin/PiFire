from common.common import read_settings, read_wizard, load_wizard_install_info

def parse_bt_device_info(bt_devices):
	settings = read_settings()
	# Check if this hardware id is already in use
	for index, peripheral in enumerate(bt_devices):
		for device in settings['probe_settings']['probe_map']['probe_devices']:
			#print(f'[DEBUG] Comparing {device["name"]} ({device["config"].get('hardware_id', None)}) to {name} ({hw_id})')
			if device['config'].get('hardware_id', None) == peripheral['hw_id']:
				bt_devices[index]['info'] += f'This hardware ID is already in use by {device["device"]}'
				return bt_devices
	return bt_devices

def get_settings_dependencies_values(settings, moduleData):
	moduleSettings = {}
	for setting, data in moduleData['settings_dependencies'].items():
		setting_location = data['settings']
		setting_value = settings
		for setting_name in setting_location:
			setting_value = setting_value[setting_name]
		moduleSettings[setting] = setting_value 
	return moduleSettings 

def wizardInstallInfoDefaults(wizardData, settings):
	
	wizardInstallInfo = {
		'modules' : {
			'grillplatform' : {
				'profile_selected' : [],  # Reference the profile in wizardData > wizard_manifest.json
				'settings' : {},
				'config' : {}
			}, 
			'display' : {
				'profile_selected' : [],
				'settings' : {},
				'config' : {}
			}, 
			'distance' : {
				'profile_selected' : [],
				'settings' : {},
				'config' : {}
			}, 
			'probes' : {
				'profile_selected' : [],
				'settings' : {
					'units' : 'F'
				},
				'config' : {}
			}
		},
		'probe_map' : {}
	}
	''' Populate Modules Info with Defaults from Wizard Data including Settings '''
	for component in ['grillplatform', 'display', 'distance']:
		for module in wizardData['modules'][component]:
			if wizardData['modules'][component][module]['default']:
				''' Populate Module Filename'''
				wizardInstallInfo['modules'][component]['profile_selected'].append(module) #TODO: Change wizard.py to reference the module filename instead, or in grill_platform use platform>system_type
				for setting in wizardData['modules'][component][module]['settings_dependencies']: 
					''' Populate all settings with default value '''
					wizardInstallInfo['modules'][component]['settings'][setting] = list(wizardData['modules'][component][module]['settings_dependencies'][setting]['options'].keys())[0]
				if module == 'display':
					wizardInstallInfo['modules'][component]['config'] = settings['display']['config'][module]

	''' Populate the default probe device / probe map from the default PCB Board '''
	wizardInstallInfo['probe_map'] = wizardData['boards'][wizardInstallInfo['modules']['grillplatform']['profile_selected'][0]]['probe_map']

	''' Populate Probes Module List with all configured probe devices '''
	for device in wizardInstallInfo['probe_map']['probe_devices']:
		wizardInstallInfo['modules']['probes']['profile_selected'].append(device['module'])

	return wizardInstallInfo

def wizardInstallInfoExisting(wizardData, settings):
	wizardInstallInfo = {
		'modules' : {
			'grillplatform' : {
				'profile_selected' : [settings['platform']['current']],
				'settings' : {},
				'config' : {}
			}, 
			'display' : {
				'profile_selected' : [settings['modules']['display']],
				'settings' : {},
				'config' : {}
			}, 
			'distance' : {
				'profile_selected' : [settings['modules']['dist']],
				'settings' : {},
				'config' : {}
			}, 
			'probes' : {
				'profile_selected' : [],
				'settings' : {
					'units' : settings['globals']['units']
				},
				'config' : {}
			}
		}, 
		'probe_map' : settings['probe_settings']['probe_map']
	} 
	''' Populate Probes Module List with all configured probe devices '''
	for device in wizardInstallInfo['probe_map']['probe_devices']:
		wizardInstallInfo['modules']['probes']['profile_selected'].append(device['module'])
	
	''' Populate Modules Info with current Settings '''
	for module in ['grillplatform', 'display', 'distance']:
		selected = wizardInstallInfo['modules'][module]['profile_selected'][0]
		''' Error condition if the item in settings doesn't match the wizard manifest '''
		if selected not in wizardData['modules'][module].keys():
			if module == 'grillplatform':
				selected = 'custom'
				settings['platform']['current'] = selected
			else:
				selected = 'none'
			wizardInstallInfo['modules'][module]['profile_selected'] = selected

		for setting in wizardData['modules'][module][selected]['settings_dependencies']:
			settingsLocation = wizardData['modules'][module][selected]['settings_dependencies'][setting]['settings']
			settingsValue = settings.copy() 
			for index in range(0, len(settingsLocation)):
				settingsValue = settingsValue[settingsLocation[index]]
			wizardInstallInfo['modules'][module]['settings'][setting] = str(settingsValue)
		if module == 'display':
			wizardInstallInfo['modules'][module]['config'] = settings['display']['config'][settings['modules']['display']]
	return wizardInstallInfo

def prepare_wizard_data(form_data):
	wizardData = read_wizard()
	
	wizardInstallInfo = load_wizard_install_info()

	wizardInstallInfo['modules'] = {
		'grillplatform' : {
			'profile_selected' : [form_data['grillplatformSelect']],
			'settings' : {},
			'config' : {}
		}, 
		'display' : {
			'profile_selected' : [form_data['displaySelect']],
			'settings' : {},
			'config' : {}
		}, 
		'distance' : {
			'profile_selected' : [form_data['distanceSelect']],
			'settings' : {},
			'config' : {}
		}, 
		'probes' : {
			'profile_selected' : [],
			'settings' : {
				'units' : form_data['probes_units']
			},
			'config' : {}
		}
	}

	for device in wizardInstallInfo['probe_map']['probe_devices']:
		wizardInstallInfo['modules']['probes']['profile_selected'].append(device['module'])

	for module in ['grillplatform', 'display', 'distance']:
		module_ = module + '_'
		moduleSelect = module + 'Select'
		selected = form_data[moduleSelect]
		for setting in wizardData['modules'][module][selected]['settings_dependencies']:
			settingName = module_ + setting
			if(settingName in form_data):
				wizardInstallInfo['modules'][module]['settings'][setting] = form_data[settingName]
		for config, value in form_data.items():
			if config.startswith(module_ + 'config_'):
				wizardInstallInfo['modules'][module]['config'][config.replace(module_ + 'config_', '')] = value

	return(wizardInstallInfo)

def find_platform_pin_collisions(settings_dependencies, submitted_settings):
	"""
	Detects GPIO pins assigned to more than one grill platform function in a submitted
	wizard configuration.

	Only fields that grillplat/raspberry_pi_all.py's GrillPlatform.__init__ actually turns
	into a pin object are checked - mirroring that constructor exactly - so that fields left
	inert by other settings (e.g. the AC fan pin on a DC-fan build, the selector pin on a
	standalone build) are never flagged as claimed:
	  - outputs.auger, outputs.igniter, outputs.power are always claimed
	  - outputs.fan is claimed only when dc_fan is False
	  - outputs.dc_fan and outputs.pwm are claimed only when dc_fan is True
	  - inputs.selector is claimed only when standalone is False
	  - outputs.aux1..aux4 are claimed only when not 'None'

	Parameters:
	- settings_dependencies (dict): wizardData['modules']['grillplatform'][<profile>]['settings_dependencies'].
	  Used only to look up each field's friendly_name for the error message.
	- submitted_settings (dict): wizardInstallInfo['modules']['grillplatform']['settings'], the raw
	  setting-name -> submitted-string-value mapping produced by prepare_wizard_data().

	Returns:
	- list of str: one human-readable error message per colliding pair of fields, naming both
	  fields and the shared pin. Empty if there are no collisions.
	"""
	dc_fan = submitted_settings.get('dc_fan') == 'True'
	standalone = submitted_settings.get('standalone') == 'True'

	pin_fields = ['output_auger', 'output_igniter', 'output_power', 'output_aux1', 'output_aux2', 'output_aux3', 'output_aux4']
	if dc_fan:
		pin_fields += ['output_dc_fan', 'output_pwm']
	else:
		pin_fields.append('output_fan')
	if not standalone:
		pin_fields.append('input_selector')

	claimed_by = {}  # pin value (str) -> (field name, friendly name)
	errors = []
	for field in pin_fields:
		value = submitted_settings.get(field)
		if value is None or value == 'None':
			continue  # unset/'Not Installed' pins are never claimed

		friendly_name = settings_dependencies.get(field, {}).get('friendly_name', field)
		if value in claimed_by:
			_, other_friendly_name = claimed_by[value]
			errors.append(
				f'{other_friendly_name} and {friendly_name} are both set to GPIO{value}. '
				f'Each pin may only be assigned to one function.'
			)
		else:
			claimed_by[value] = (field, friendly_name)

	return errors
