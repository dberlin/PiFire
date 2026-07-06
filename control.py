#!/usr/bin/env python3

"""
==============================================================================
 PiFire Main Control Process
==============================================================================

Description: This script will start at boot, initialize the relays and
  wait for further commands from the web user interface.

 This script runs as a separate process from the Flask / Gunicorn
 implementation which handles the web interface.

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
import logging
import atexit
from common import *  # Common Module for WebUI and Control Program
from notify.notifications import *
from file_mgmt.recipes import convert_recipe_units
from file_mgmt.cookfile import create_cookfile
from file_mgmt.common import read_json_file_data
from controller.runtime.context import ControllerContext
from controller.runtime.devices import build_devices
from controller.runtime.store import ValkeyStore
from controller.runtime.clock import RealClock
from controller.runtime.notifier import ValkeyNotifier
from controller.runtime.state import WorkCycleState
from controller.runtime.modes.monitor import MonitorMode
from controller.runtime.modes.manual import ManualMode
from controller.runtime.modes.shutdown import ShutdownMode
from controller.runtime.modes.prime import PrimeMode
from controller.runtime.modes.startup import StartupMode
from controller.runtime.modes.reignite import ReigniteMode
from controller.runtime.modes.smoke import SmokeMode
from controller.runtime.modes.hold import HoldMode
from os.path import exists

"""
==============================================================================
 Read and initialize Settings, Control, History, Metrics, and Error Data
==============================================================================
"""
# Read Settings to get Modules Configuration

"""
*****************************************
 	Function Definitions
*****************************************
"""


def _process_system_commands(ctx):
	grill_platform = ctx.devices.grill_platform
	# Setup access to the system command queue
	system_commands = ctx.store.system_commands()
	# Setup access to the system output queue
	system_output = ctx.store.system_output()
	# Initialize variable for supported commands (only look for supported commands if we have something to process)
	supported_cmds = []

	while system_commands.length() > 0:
		if supported_cmds == []:
			# Get list of supported system commands
			supported_cmds = grill_platform.supported_commands(None)['data']['supported_cmds']
		command = system_commands.pop()
		if command[0] in supported_cmds:
			command_method = getattr(grill_platform, command[0])
			result = command_method(command)
			result['command'] = command
		else:
			result = {
				'command': command,
				'result': 'ERROR',
				'message': f'ERROR: Command [{command[0]}] is not supported with the current platform.',
				'data': {},
			}
		system_output.push(result)


_MODE_HANDLERS = {
	'Monitor': MonitorMode,
	'Manual': ManualMode,
	'Shutdown': ShutdownMode,
	'Prime': PrimeMode,
	'Startup': StartupMode,
	'Reignite': ReigniteMode,
	'Smoke': SmokeMode,
	'Hold': HoldMode,
}


def _work_cycle(mode, ctx):
	"""
	Work Cycle Function

	:param mode: Requested Mode
	:param ctx: ControllerContext
	"""
	return _MODE_HANDLERS[mode](ctx, WorkCycleState()).run()


def _next_mode(ctx, next_mode, setpoint=0):
	ctx.store.execute_control_writes()
	control = ctx.store.read_control()
	# If no other request, then transition to next mode, otherwise exit
	if not control['updated']:
		control['mode'] = next_mode
		control['primary_setpoint'] = setpoint if next_mode == 'Hold' else 0  # If next mode is 'Hold'
		control['updated'] = True
		ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
	return control


def _recipe_mode(ctx, start_step=0):
	"""
	Recipe Mode Control

	:param ctx: ControllerContext
	"""
	settings = ctx.store.read_settings()
	eventLogger.info('Recipe Mode started.')

	# Find Recipe File
	control = ctx.store.read_control()
	recipe_file = control['recipe']['filename']

	if not exists(recipe_file):
		# File not found, exit
		eventLogger.warning(f'Recipe file {recipe_file} not found!')
		return ()

	# 1. Read metadata from the recipe file
	metadata, status = read_json_file_data(recipe_file, 'metadata')
	if status != 'OK':
		eventLogger.warning(f'Failed to load metadata for {recipe_file}.')
		return ()

	# 2. Read recipe steps (& other data) from the recipe file
	recipe, status = read_json_file_data(recipe_file, 'recipe')
	if status != 'OK':
		eventLogger.warning(f'Failed to load recipe data for {recipe_file}.')
		return ()

	# 3. Check and convert temperature units, if there is a mismatch
	if settings['globals']['units'] != metadata['units']:
		recipe = convert_recipe_units(recipe, settings['globals']['units'])

	num_steps = len(recipe['steps'])
	step_num = start_step  # Start at step 0 by default unless requested to start at a later step

	# 4. Walk through steps, and execute work cycle
	while step_num < num_steps:
		# 4a. Setup all step data and write to control
		control['recipe']['step'] = step_num
		control['recipe']['step_data'] = recipe['steps'][step_num]
		""" Setup trigger_temps structure that the work_cycle expects, mapping to real probes """
		trigger_temps = {}
		trigger_temps[settings['recipe']['probe_map']['primary']] = recipe['steps'][step_num]['trigger_temps'][
			'primary'
		]
		for index, value in enumerate(recipe['steps'][step_num]['trigger_temps']['food']):
			trigger_temps[settings['recipe']['probe_map']['food'][index]] = value
		control['recipe']['step_data']['trigger_temps'] = trigger_temps
		control['recipe']['step_data']['triggered'] = False
		control['primary_setpoint'] = recipe['steps'][step_num]['hold_temp']  # Set Hold Temp if applicable.
		control['updated'] = False  # Clear Updated Flag if Set
		ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
		# 4b. Start the recipe step work cycle
		_work_cycle(recipe['steps'][step_num]['mode'], ctx)

		# 4c. If reignite is required, run a reignite cycle and retry current step
		ctx.store.execute_control_writes()
		control = ctx.store.read_control()
		if control['mode'] == 'Reignite' and control['updated']:
			control['updated'] = False
			control['mode'] = 'Recipe'
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
			_work_cycle('Reignite', ctx)
			control = ctx.store.read_control()
			if control['updated'] and control['mode'] != 'Recipe':
				# If another mode was requested (or an error occurred) then exit recipe mode
				eventLogger.info(f'Recipe mode cancelled due to mode change: {control["mode"]}')
				break
			# 4c-2. Rerun current step
		# 4d. If another mode was requested (or an error occurred) then exit recipe mode
		elif control['mode'] != 'Recipe' and control['updated']:
			eventLogger.info(f'Recipe mode cancelled due to mode change: {control["mode"]}')
			break
		else:
			# 4e. Continue to next step number
			step_num += 1

	# 5. Clean up control data and exit
	control['recipe']['step'] = 0
	control['recipe']['step_data'] = {}
	control['recipe']['filename'] = ''

	# If recipe is exiting normally (i.e. no other mode requested, then initiate stop mode)
	if not control['updated'] or (step_num == num_steps):
		control['updated'] = True
		control['mode'] = 'Stop'
		eventLogger.info('Recipe mode ended.')
	ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')

	return ()


def exit_handler():
	"""
	Exit handler function that logs a message and performs cleanup operations before exiting the control script.

	This function is called when the control script is about to exit. It logs a message indicating that the script is exiting using the `eventLogger.info()` function. It also logs a formatted message using the `controlLogger.info()` function to provide additional information about the exit.

	After logging the messages, the function calls the `grill_platform.cleanup()` function to perform any necessary cleanup operations related to the grill platform.

	This function does not take any parameters and does not return any values.

	Example usage:
		python
		exit_handler()
	"""
	eventLogger.info('Control Script Exiting.')
	controlLogger.info('Control Script Exiting.')
	grill_platform.cleanup()
	return


# Only run hardware init and the control loop when executed as the main
# program. Guarding this lets the module be imported (e.g. by tests) without
# initializing hardware, flushing the datastore, or entering the control loop.
if __name__ == '__main__':
	settings = read_settings(init=True)

	# Setup logging
	log_level = logging.DEBUG if settings['globals']['debug_mode'] else logging.ERROR
	controlLogger = create_logger(
		'control',
		filename='./logs/control.log',
		messageformat='%(asctime)s [%(levelname)s] %(message)s',
		level=log_level,
	)

	log_level = logging.DEBUG if settings['globals']['debug_mode'] else logging.INFO
	eventLogger = create_logger(
		'events', filename='./logs/events.log', messageformat='%(asctime)s [%(levelname)s] %(message)s', level=log_level
	)

	event_message = f'PiFire Control Process started. PiFire Version: {settings["versions"]["server"]} Build: {settings["versions"]["build"]}, Debug Mode: {settings["globals"]["debug_mode"]}'

	eventLogger.info(event_message)
	controlLogger.info(event_message)

	# Flush Valkey DB and create JSON structure
	control = read_control(flush=True)
	# Delete Valkey DB for history / current
	read_history(0, flushhistory=True)
	# Flush metrics DB for tracking certain metrics
	write_metrics(flush=True)
	# Create/Flush errors list
	errors = read_errors(flush=True)

	eventLogger.info('Flushing Valkey DB and creating new control structure')

	devices, errors = build_devices(
		settings, errors=errors, event_log=eventLogger, control_log=controlLogger
	)
	grill_platform = devices.grill_platform
	probe_complex = devices.probe_complex
	dist_device = devices.dist_device

	# Register the exit handler
	atexit.register(exit_handler)

	# Build the injected context used by the work cycle / mode functions instead of bare globals
	ctx = ControllerContext(
		devices=devices,
		store=ValkeyStore(),
		notifications=ValkeyNotifier(),
		clock=RealClock(),
		event_log=eventLogger,
		control_log=controlLogger,
	)

	# *****************************************
	# Main Program Start / Init and Loop
	# *****************************************

	last = grill_platform.get_input_status()

	""" If the user has selected boot-to-monitor mode, then issue the command prior to the main loop """
	if settings['globals']['boot_to_monitor']:
		control = read_control()
		control['mode'] = 'Monitor'
		control['updated'] = True
		write_control(control, WriteKind.OVERWRITE, origin='control')

	""" Initialize the status data on first run. """
	status = read_status(init=True)

	while True:
		# Check the On/Off switch for changes
		if not settings['platform']['standalone'] and last != grill_platform.get_input_status():
			last = grill_platform.get_input_status()
			if not last:
				eventLogger.info('Switch set to off, going to stop mode.')
				controlLogger.info(f'Switch set to off, going to stop mode.')
				control['updated'] = True  # Change mode
				control['mode'] = 'Stop'
				write_control(control, WriteKind.OVERWRITE, origin='control')

		status = read_status()

		# Get probe device info for frontend
		write_generic_key('probe_device_info', probe_complex.get_device_info())

		current = grill_platform.get_output_status()  # Get current pin settings
		for item in settings['platform']['outputs']:
			try:
				status['outpins'][item] = current[item]
			except KeyError:
				continue
		write_status(status)

		# Check control for changes
		execute_control_writes()
		control = read_control()

		# Check for system commands
		_process_system_commands(ctx)

		# Check if there were updates to any of the settings that were flagged
		if control['settings_update']:
			control['settings_update'] = False
			write_control(control, WriteKind.OVERWRITE, origin='control')
			settings = read_settings()

		# Check if there are any notifications pending
		check_notify(settings, control, pelletdb=pelletdb, grill_platform=grill_platform)

		# Check if there is a timer running, see if it has expired, send notification and reset
		for index, item in enumerate(control['notify_data']):
			if item['type'] == 'timer' and item['req']:
				if time.time() >= control['timer']['end']:
					send_notifications('Timer_Expired')
					control['notify_data'][index]['req'] = False
					control['timer']['start'] = 0
					control['timer']['paused'] = 0
					control['timer']['end'] = 0
					control['notify_data'][index]['shutdown'] = False
					control['notify_data'][index]['keep_warm'] = False
					write_control(control, WriteKind.OVERWRITE, origin='control')

		# Check if user changed hopper levels and update if required
		if control['distance_update']:
			empty = settings['pelletlevel']['empty']
			full = settings['pelletlevel']['full']
			dist_device.update_distances(empty, full)
			control['distance_update'] = False
			write_control(control, WriteKind.OVERWRITE, origin='control')

		if control['hopper_check']:
			pelletdb = read_pellet_db()
			# Get current hopper level and save it to the current pellet information
			pelletdb['current']['hopper_level'] = dist_device.get_level(override=True)
			write_pellet_db(pelletdb)
			eventLogger.info('Hopper Level Checked @ ' + str(pelletdb['current']['hopper_level']) + '%')
			control['hopper_check'] = False
			write_control(control, WriteKind.OVERWRITE, origin='control')

		# Grab current probe profiles if they have changed since the last loop.
		if control['probe_profile_update']:
			settings = read_settings()
			control['probe_profile_update'] = False
			write_control(control, WriteKind.OVERWRITE, origin='control')
			# Add new probe profiles to probe complex object
			probe_complex.update_probe_profiles(settings['probe_settings']['probe_map']['probe_info'])
			eventLogger.info('Active probe profiles updated in control script.')

		if control['updated'] and not control['critical_error']:
			eventLogger.debug(
				f'Control Settings Updated.  Mode: {control["mode"]}, Units Change: {control["units_change"]} '
			)
			# Clear control flag
			control['updated'] = False  # Reset Control Updated to False
			write_control(control, WriteKind.OVERWRITE, origin='control')  # Commit change in 'updated' status to the file

			if control['units_change']:
				eventLogger.debug('Changing Base Units.')
				settings = read_settings()
				# Update ADC objects and set profiles
				probe_complex.update_units(settings['globals']['units'])
				control['mode'] = 'Stop'  # Stop any activity
				control['units_change'] = False
				read_history(0, flushhistory=True)  # Clear history data
				# No need to write control, as it should be written by the 'Stop' mode change

			# Check if there was an Error flagged in Monitor Mode - If no, then change status to active
			if control['status'] != 'monitor' and control['mode'] != 'Error':
				control['status'] = 'active'  # Set status to active
				write_control(control, WriteKind.OVERWRITE, origin='control')

			if control['mode'] in ('Stop', 'Error'):
				grill_platform.auger_off()
				grill_platform.igniter_off()
				grill_platform.fan_off()
				# Register Stop Mode in Metrics DB if this is not initial stop-mode on startup (i.e. DB is empty)
				metrics_list = read_metrics(all=True)
				if len(metrics_list) != 0:
					write_metrics(new_metric=True)
					metrics = read_metrics()
					metrics['mode'] = 'Stop'
					write_metrics(metrics)
					if metrics_list[-1]['mode'] != 'Prime':
						create_cookfile()

				status['p_mode'] = 0
				status['mode'] = 'Stop'
				status['recipe'] = False
				status['recipe_paused'] = False
				status['start_time'] = 0
				status['lid_open_detected'] = False
				status['lid_open_endtime'] = 0
				status['startup_timestamp'] = 0
				write_status(status)

				if control['status'] == 'monitor' and control['mode'] == 'Error':
					grill_platform.power_on()
				else:
					grill_platform.power_off()

				if control['mode'] == 'Stop':
					eventLogger.info('Stop Mode Started.')
					ctx.store.display_commands().push(('clear', None))
					control['status'] = 'inactive'
					# Reset Control to Defaults
					control = read_control(flush=True)
					control['updated'] = False
					control['tuning_mode'] = False  # Turn off Tuning Mode on Stop just in case it is on
					control['next_mode'] = 'Stop'
					control['safety']['reigniteretries'] = settings['safety'][
						'reigniteretries'
					]  # Reset retry counter to default
					control['startup_timestamp'] = 0  # Reset the startup timestamp to 0
					write_control(control, WriteKind.OVERWRITE, origin='control')
				else:
					eventLogger.error('An error has occurred, Stop Mode enabled.')
					controlLogger.error('An error has occurred, Stop Mode enabled.')
					# Reset Control to Defaults but preserve 'Error' mode condition
					control = default_control()
					control['mode'] = 'Error'
					control['status'] = 'inactive'
					control['tuning_mode'] = False  # Turn off Tuning Mode on Stop just in case it is on
					control['updated'] = False
					control['next_mode'] = 'Stop'
					control['safety']['reigniteretries'] = settings['safety'][
						'reigniteretries'
					]  # Reset retry counter to default
					write_control(control, WriteKind.OVERWRITE, origin='control')
					time.sleep(3)
					ctx.store.display_commands().push(('clear', None))

				read_current(zero_out=True)  # Zero out the current values

			# Prime (dump preset amount of pellets into the firepot)
			elif control['mode'] == 'Prime':
				if not settings['platform']['standalone'] and not grill_platform.get_input_status():
					eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
					)
				# Call Work Cycle for Startup Mode
				_work_cycle('Prime', ctx)
				# Select Next Mode
				settings = read_settings()
				_next_mode(ctx, control['next_mode'], setpoint=settings['startup']['start_to_mode']['primary_setpoint'])

			# Startup (startup sequence)
			elif control['mode'] == 'Startup':
				if not settings['platform']['standalone'] and not grill_platform.get_input_status():
					eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
					)
				settings = read_settings()
				# Clear History (in the case it wasn't already cleared fromt he last run)
				eventLogger.debug('Clearing History and Current Log on Startup Mode.')
				read_history(0, flushhistory=True)  # Clear all history
				# Check if Prime on Startup is selected
				if settings['startup']['prime_on_startup'] > 0:
					control['prime_amount'] = settings['startup']['prime_on_startup']
					control['mode'] = 'Prime'
					write_control(control, WriteKind.OVERWRITE, origin='control')
					# Call Work Cycle for Prime Mode
					_work_cycle('Prime', ctx)
					control = read_control()  # Refresh control in case any changes were made during the cycle
					if control['mode'] in ['Prime', 'Startup']:
						control['updated'] = False
						control['mode'] = 'Startup'
				# Check if there was a mode change during Priming
				if control['mode'] == 'Startup':
					# Setup Next Mode (after startup mode)
					control['next_mode'] = settings['startup']['start_to_mode']['after_startup_mode']
					write_control(control, WriteKind.OVERWRITE, origin='control')
					# Call Work Cycle for Startup Mode
					_work_cycle('Startup', ctx)
					# Select Next Mode
					settings = read_settings()
					_next_mode(ctx, control['next_mode'], setpoint=settings['startup']['start_to_mode']['primary_setpoint'])

			# Smoke (smoke cycle)
			elif control['mode'] == 'Smoke':
				_work_cycle('Smoke', ctx)
				_next_mode(ctx, control['next_mode'])

			# Hold (hold at setpoint)
			elif control['mode'] == 'Hold':
				_work_cycle('Hold', ctx)
				_next_mode(ctx, control['next_mode'])

			# Shutdown (shutdown sequence)
			elif control['mode'] == 'Shutdown':
				control['next_mode'] = 'Stop'
				write_control(control, WriteKind.OVERWRITE, origin='control')
				_work_cycle('Shutdown', ctx)
				_next_mode(ctx, control['next_mode'])
				if settings['shutdown']['auto_power_off']:
					eventLogger.info('Shutdown mode ended powering off grill')
					os.system('sleep 3 && sudo shutdown -h now &')

			# Monitor (monitor the OEM controller)
			elif control['mode'] == 'Monitor':
				control['status'] = 'monitor'  # Set status to monitor
				write_control(control, WriteKind.OVERWRITE, origin='control')
				_work_cycle('Monitor', ctx)

			# Manual Mode
			elif control['mode'] == 'Manual':
				_work_cycle('Manual', ctx)

			# Recipe Mode
			elif control['mode'] == 'Recipe':
				_recipe_mode(
					ctx,
					start_step=control['recipe']['start_step'],
				)

			# Reignite (reignite sequence)
			elif control['mode'] == 'Reignite':
				if (not settings['platform']['standalone']) and (not grill_platform.get_input_status()):
					eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent reignite, "
						"but this means the switch won't behave as normal."
					)
				control['next_mode'] = control['safety']['reignitelaststate']
				setpoint = control['primary_setpoint']
				write_control(control, WriteKind.OVERWRITE, origin='control')
				_work_cycle('Reignite', ctx)
				_next_mode(ctx, control['next_mode'], setpoint=setpoint)

		if settings['notify_services'].get('mqtt') != None and settings['notify_services']['mqtt']['enabled']:
			check_notify(settings, control, pelletdb=pelletdb)

		time.sleep(0.1)
	# ===================
	# End of Main Loop
	# ===================
