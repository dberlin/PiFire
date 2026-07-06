"""Hardware device construction. Moved verbatim (prototype-fallback try/except
logic and all) from control.py's __main__ block so that both the controller
process and the future display.py process (Phase 8) can share it."""
import importlib

from common import (
	read_control,
	read_pellet_db,
	write_pellet_db,
	write_control,
	write_errors,
	write_generic_key,
	get_probe_info,
	WriteKind,
)

from controller.runtime.context import Devices


def build_devices(settings, *, include_display, errors, event_log, control_log):
	"""
	Construct the grill platform, probe complex, (optionally) display, and
	distance/hopper-level devices, using the same prototype-fallback logic
	that used to live in control.py's __main__ block.

	:param settings: Settings dictionary
	:param include_display: If True, also construct the display device (used
		by the future display.py process). If False (controller), skip
		display construction entirely and return None for it.
	:param errors: Errors list to append to (and persist via write_errors)
	:param event_log: Event logger (was module-global eventLogger)
	:param control_log: Control logger (was module-global controlLogger)
	:return: (Devices, display_or_None, errors)
	"""
	platform_config = settings['platform']
	platform_config['frequency'] = settings['pwm']['frequency']
	units = settings['globals']['units']

	"""
	Set up GrillPlatform Module
	"""
	try:
		grill_platform = settings['modules']['grillplat']
		GrillPlatModule = importlib.import_module(f'grillplat.{grill_platform}')

	except:
		control = read_control()
		control['critical_error'] = True
		write_control(control, WriteKind.OVERWRITE, origin='control')
		control_log.exception(
			f'Error occurred importing grillplatform module ({settings["modules"]["grillplat"]}). Trace dump: '
		)
		GrillPlatModule = importlib.import_module('grillplat.prototype')
		error_event = (
			f'An error occurred importing the [{settings["modules"]["grillplat"]}] platform module.  The '
			f'prototype module has been imported instead.  This sometimes means that the module does not exist or is not '
			f'properly named.  Please run the configuration wizard again from the admin '
			f'panel to fix this issue.'
		)
		errors.append(error_event)
		write_errors(errors)
		event_log.error(error_event)
		control_log.error(error_event)
		if settings['globals']['debug_mode']:
			raise

	try:
		grill_platform = GrillPlatModule.GrillPlatform(platform_config)
	except:
		control = read_control()
		control['critical_error'] = True
		write_control(control, WriteKind.OVERWRITE, origin='control')
		control_log.exception(
			f'Error occurred configuring grillplatform module ({settings["modules"]["grillplat"]}). Trace dump: '
		)
		from grillplat.prototype import GrillPlatform  # Simulated Library for controlling the grill platform

		grill_platform = GrillPlatform(platform_config)
		error_event = (
			f'An error occurred configuring the [{settings["modules"]["grillplat"]}] platform object.  The '
			f'prototype module has been loaded instead.  This sometimes means that the hardware is not '
			f'connected properly, or the module is not configured.  Please run the configuration wizard '
			f'again from the admin panel to fix this issue.'
		)
		errors.append(error_event)
		write_errors(errors)
		event_log.error(error_event)
		control_log.error(error_event)
		if settings['globals']['debug_mode']:
			raise

	"""
	Set up Probes Input Module
	"""
	try:
		from probes.main import ProbesMain  # Probe device library: loads probe devices and maps them to ports

		probe_complex = ProbesMain(settings['probe_settings']['probe_map'], settings['globals']['units'])

	except:
		control_log.exception(f'Error occurred loading probes modules. Trace dump: ')
		# settings['probe_settings']['probe_map'] = default_probe_map(settings["probe_settings"]['probe_profiles'])
		probe_complex = ProbesMain(settings['probe_settings']['probe_map'], settings['globals']['units'], disable=True)
		error_event = (
			f'An error occurred loading the probes module(s).  All probes & probe devices have been disabled. '
			f'This sometimes means that the hardware is not connected properly, or the module is not configured correctly. '
			f'Please run the configuration wizard again from the admin panel to fix this issue. '
		)
		errors.append(error_event)
		write_errors(errors)
		event_log.error(error_event)
		control_log.error(error_event)
		if settings['globals']['debug_mode']:
			raise

	# Get probe initialization errors and pass along to the frontend
	probe_errors = probe_complex.get_errors()
	if len(probe_errors) > 0:
		for error in probe_errors:
			event_log.error(error)
			errors.append(error)
			write_errors(errors)

	# Get probe device info for frontend
	write_generic_key('probe_device_info', probe_complex.get_device_info())

	display_device = None
	if include_display:
		"""
		Set up Display Module
		"""
		try:
			display_name = settings['modules']['display']
			DisplayModule = importlib.import_module(f'display.{display_name}')
			display_config = settings['display']['config'][display_name]
			display_config['probe_info'] = get_probe_info(settings['probe_settings']['probe_map']['probe_info'])
			disp_rotation = display_config.get('rotation', 0)

		except:
			control_log.exception(f'Error occurred loading the display module ({display_name}). Trace dump: ')
			DisplayModule = importlib.import_module('display_none')
			error_event = (
				f'An error occurred loading the [{settings["modules"]["display"]}] display module.  The '
				f'"display_none" module has been loaded instead.  This sometimes means that the hardware is '
				f'not connected properly, or the module is not configured.  Please run the configuration wizard '
				f'again from the admin panel to fix this issue.'
			)
			errors.append(error_event)
			write_errors(errors)
			event_log.error(error_event)
			control_log.error(error_event)
			if settings['globals']['debug_mode']:
				raise

		try:
			display_device = DisplayModule.Display(
				dev_pins=settings['platform']['devices'],
				buttonslevel=settings['platform']['buttonslevel'],
				rotation=disp_rotation,
				units=units,
				config=display_config,
			)
		except:
			control_log.exception(
				f'Error occurred configuring the display module ({settings["modules"]["display"]}). Trace dump: '
			)
			from display.none import Display  # Simulated Library for controlling the grill platform

			display_device = Display(
				dev_pins=settings['platform']['devices'],
				buttonslevel=settings['platform']['buttonslevel'],
				rotation=disp_rotation,
				units=units,
				config={},
			)
			error_event = (
				f'An error occurred configuring the [{settings["modules"]["display"]}] display object.  The '
				f'"display_none" module has been loaded instead.  This sometimes means that the hardware is '
				f'not connected properly, or the module is not configured.  Please run the configuration wizard '
				f'again from the admin panel to fix this issue.'
			)
			errors.append(error_event)
			write_errors(errors)
			event_log.error(error_event)
			control_log.error(error_event)
			if settings['globals']['debug_mode']:
				raise

	"""
	Set up Distance (Hopper Level) Module
	"""
	try:
		dist_name = settings['modules']['dist']
		DistanceModule = importlib.import_module(f'distance.{dist_name}')

	except:
		control_log.exception(f'Error occurred loading the distance module ({dist_name}). Trace dump: ')
		DistanceModule = importlib.import_module('distance.none')
		error_event = (
			f'An error occurred loading the [{settings["modules"]["dist"]}] distance module.  The none '
			f'module has been loaded instead.  This sometimes means that the hardware is not connected '
			f'properly, or the module is not configured.  Please run the configuration wizard again from the '
			f'admin panel to fix this issue.'
		)
		errors.append(error_event)
		write_errors(errors)
		event_log.error(error_event)
		control_log.error(error_event)

	try:
		if settings['modules']['grillplat'] == 'prototype' and settings['modules']['dist'] == 'prototype':
			# If in prototype mode, enable test reading (i.e. random values from proto distance sensor)
			dist_device = DistanceModule.HopperLevel(
				dev_pins=settings['platform']['devices'],
				empty=settings['pelletlevel']['empty'],
				full=settings['pelletlevel']['full'],
				debug=settings['globals']['debug_mode'],
				random=True,
			)
		else:
			dist_device = DistanceModule.HopperLevel(
				dev_pins=settings['platform']['devices'],
				empty=settings['pelletlevel']['empty'],
				full=settings['pelletlevel']['full'],
				debug=settings['globals']['debug_mode'],
			)
	except:
		control_log.exception(f'Error occurred configuring the distance module ({dist_name}). Trace dump: ')
		from distance.none import HopperLevel  # Simulated Library for controlling the grill platform

		dist_device = HopperLevel(
			dev_pins=settings['platform']['devices'],
			empty=settings['pelletlevel']['empty'],
			full=settings['pelletlevel']['full'],
			debug=settings['globals']['debug_mode'],
		)
		error_event = (
			f'An error occurred configuring the [{settings["modules"]["dist"]}] distance object.  The '
			f'none module has been loaded instead.  This sometimes means that the hardware is not '
			f'connected properly, or the module is not configured.  Please run the configuration wizard again '
			f'from the admin panel to fix this issue.'
		)
		errors.append(error_event)
		write_errors(errors)
		event_log.error(error_event)
		control_log.error(error_event)

	# Get current hopper level and save it to the current pellet information
	pelletdb = read_pellet_db()
	pelletdb['current']['hopper_level'] = dist_device.get_level(override=True)
	write_pellet_db(pelletdb)
	event_log.info(f'Hopper Level Checked @ {pelletdb["current"]["hopper_level"]}%')

	devices = Devices(grill_platform=grill_platform, probe_complex=probe_complex, dist_device=dist_device)
	return devices, display_device, errors
