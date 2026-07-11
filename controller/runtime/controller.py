"""Controller orchestrator: the outer control-process loop.

`Controller` owns the persistent per-process state (settings/control/status/
pelletdb/last-switch-state) and dispatches to per-mode work cycles.
`Controller.tick()` is one iteration: poll the on/off switch, refresh
status/probe-device info, apply pending control/settings/notification/hopper
changes, then -- if a mode change is pending -- run the requested mode's work
cycle via `work_cycle()`/`run_work_cycle()` and hand off to `next_mode()`.
`Controller.run()` is `setup()` followed by `while True: tick(); sleep(0.1)`
(RealClock in production).

All datastore access goes through `self.ctx.store` (a `Store`; production uses
`SqliteStore`, tests inject `InMemoryStore`), and all timing goes through
`self.ctx.clock` (`RealClock` in production, `ManualClock` in tests), so the
loop is deterministic and testable without a real Valkey server or wall clock.

Notification/cookfile helpers (`check_notify`, `send_notifications`,
`create_cookfile`) and `os.system` remain module-level references so tests can
monkeypatch them.
"""

import os

from common.common import WriteKind, default_control
from notify.notifications import check_notify, send_notifications
from file_mgmt.cookfile import create_cookfile
from file_mgmt.recipes import convert_recipe_units
from file_mgmt.common import read_json_file_data
from os.path import exists

from controller.runtime.state import WorkCycleState
from controller.runtime.system_commands import process_system_commands
from controller.runtime.modes.monitor import MonitorMode
from controller.runtime.modes.manual import ManualMode
from controller.runtime.modes.shutdown import ShutdownMode
from controller.runtime.modes.prime import PrimeMode
from controller.runtime.modes.startup import StartupMode
from controller.runtime.modes.reignite import ReigniteMode
from controller.runtime.modes.smoke import SmokeMode
from controller.runtime.modes.hold import HoldMode


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


def run_work_cycle(mode, ctx):
	"""Run a single per-mode work cycle: look up the `ControlMode` subclass for
	`mode` in `_MODE_HANDLERS`, construct it with a fresh `WorkCycleState`, and
	run it to completion. Module-level so it can be exercised in isolation (the
	characterization/E2E harness runs one cycle at a time) without constructing
	a full Controller."""
	return _MODE_HANDLERS[mode](ctx, WorkCycleState()).run()


class Controller:
	"""Owns the outer control loop that dispatches to per-mode work cycles."""

	def __init__(self, ctx):
		self.ctx = ctx
		self.grill_platform = ctx.devices.grill_platform
		self.probe_complex = ctx.devices.probe_complex
		self.dist_device = ctx.devices.dist_device
		self.eventLogger = ctx.event_log
		self.controlLogger = ctx.control_log
		# Persistent loop state, held across tick() calls for the process lifetime.
		self.settings = ctx.store.read_settings()
		self.control = None
		self.status = None
		self.pelletdb = None
		self.last = None

	# --- work-cycle dispatch helpers ---

	def work_cycle(self, mode):
		"""Run one per-mode work cycle."""
		return run_work_cycle(mode, self.ctx)

	def next_mode(self, next_mode, setpoint=0):
		ctx = self.ctx
		ctx.store.execute_control_writes()
		control = ctx.store.read_control()
		# If no other request, then transition to next mode, otherwise exit
		if not control['updated']:
			control['mode'] = next_mode
			control['primary_setpoint'] = setpoint if next_mode == 'Hold' else 0  # If next mode is 'Hold'
			control['updated'] = True
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
		return control

	def process_system_commands(self):
		process_system_commands(self.ctx)

	def recipe_mode(self, start_step=0):
		"""Recipe Mode Control -- walks recipe steps, running a work cycle each."""
		ctx = self.ctx
		settings = ctx.store.read_settings()
		self.eventLogger.info('Recipe Mode started.')

		# Find Recipe File
		control = ctx.store.read_control()
		recipe_file = control['recipe']['filename']

		if not exists(recipe_file):
			# File not found, exit
			self.eventLogger.warning(f'Recipe file {recipe_file} not found!')
			return ()

		# 1. Read metadata from the recipe file
		metadata, status = read_json_file_data(recipe_file, 'metadata')
		if status != 'OK':
			self.eventLogger.warning(f'Failed to load metadata for {recipe_file}.')
			return ()

		# 2. Read recipe steps (& other data) from the recipe file
		recipe, status = read_json_file_data(recipe_file, 'recipe')
		if status != 'OK':
			self.eventLogger.warning(f'Failed to load recipe data for {recipe_file}.')
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
			self.work_cycle(recipe['steps'][step_num]['mode'])

			# 4c. If reignite is required, run a reignite cycle and retry current step
			ctx.store.execute_control_writes()
			control = ctx.store.read_control()
			if control['mode'] == 'Reignite' and control['updated']:
				control['updated'] = False
				control['mode'] = 'Recipe'
				ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
				self.work_cycle('Reignite')
				control = ctx.store.read_control()
				if control['updated'] and control['mode'] != 'Recipe':
					# If another mode was requested (or an error occurred) then exit recipe mode
					self.eventLogger.info(f'Recipe mode cancelled due to mode change: {control["mode"]}')
					break
				# 4c-2. Rerun current step
			# 4d. If another mode was requested (or an error occurred) then exit recipe mode
			elif control['mode'] != 'Recipe' and control['updated']:
				self.eventLogger.info(f'Recipe mode cancelled due to mode change: {control["mode"]}')
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
			self.eventLogger.info('Recipe mode ended.')
		ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')

		return ()

	# --- lifecycle ---

	def cleanup(self):
		"""atexit handler: log and clean up the grill platform on process exit."""
		self.eventLogger.info('Control Script Exiting.')
		self.controlLogger.info('Control Script Exiting.')
		self.grill_platform.cleanup()

	def setup(self):
		"""One-time initialization run before the main loop starts."""
		store = self.ctx.store

		# Initial hopper-level check on boot. Without this, `pelletdb` is unbound
		# the first time the loop calls check_notify, and the boot-time hopper
		# read never happens.
		self.pelletdb = store.read_pellet_db()
		self.pelletdb['current']['hopper_level'] = self.dist_device.get_level(override=True)
		store.write_pellet_db(self.pelletdb)
		self.eventLogger.info(f'Hopper Level Checked @ {self.pelletdb["current"]["hopper_level"]}%')

		self.last = self.grill_platform.get_input_status()

		""" If the user has selected boot-to-monitor mode, then issue the command prior to the main loop """
		if self.settings['globals']['boot_to_monitor']:
			control = store.read_control()
			control['mode'] = 'Monitor'
			control['updated'] = True
			store.write_control(control, WriteKind.OVERWRITE, origin='control')

		""" Initialize the status data on first run. """
		self.status = store.read_status(init=True)

		# Bind `control` before the loop so the iteration-1 switch check has it
		# (the entry point already flushed control; boot_to_monitor may have just
		# updated it).
		self.control = store.read_control()

	def run(self):
		"""setup() then loop forever, one tick() per 0.1s (RealClock)."""
		self.setup()
		while True:
			self.tick()
			self.ctx.clock.sleep(0.1)

	def tick(self):
		"""One iteration of the control loop. Persistent state lives on self."""
		ctx = self.ctx
		store = ctx.store
		grill_platform = self.grill_platform
		settings = self.settings

		# Check the On/Off switch for changes
		if not settings['platform']['standalone'] and self.last != grill_platform.get_input_status():
			self.last = grill_platform.get_input_status()
			if not self.last:
				self.eventLogger.info('Switch set to off, going to stop mode.')
				self.controlLogger.info(f'Switch set to off, going to stop mode.')
				self.control['updated'] = True  # Change mode
				self.control['mode'] = 'Stop'
				store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

		self.status = store.read_status()

		# Get probe device info for frontend
		store.write_generic_key('probe_device_info', self.probe_complex.get_device_info())

		current = grill_platform.get_output_status()  # Get current pin settings
		for item in settings['platform']['outputs']:
			try:
				self.status['outpins'][item] = current[item]
			except KeyError:
				continue
		store.write_status(self.status)

		# Check control for changes
		store.execute_control_writes()
		self.control = store.read_control()

		# Check for system commands
		self.process_system_commands()

		# Check if there were updates to any of the settings that were flagged
		if self.control['settings_update']:
			self.control['settings_update'] = False
			store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
			self.settings = settings = store.read_settings()

		# Check if there are any notifications pending
		check_notify(settings, self.control, pelletdb=self.pelletdb, grill_platform=grill_platform)

		# Check if there is a timer running, see if it has expired, send notification and reset
		for index, item in enumerate(self.control['notify_data']):
			if item['type'] == 'timer' and item['req']:
				if ctx.clock.now() >= self.control['timer']['end']:
					send_notifications('Timer_Expired')
					self.control['notify_data'][index]['req'] = False
					self.control['timer']['start'] = 0
					self.control['timer']['paused'] = 0
					self.control['timer']['end'] = 0
					self.control['notify_data'][index]['shutdown'] = False
					self.control['notify_data'][index]['keep_warm'] = False
					store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

		# Check if user changed hopper levels and update if required
		if self.control['distance_update']:
			empty = settings['pelletlevel']['empty']
			full = settings['pelletlevel']['full']
			self.dist_device.update_distances(empty, full)
			self.control['distance_update'] = False
			store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

		if self.control['hopper_check']:
			self.pelletdb = store.read_pellet_db()
			# Get current hopper level and save it to the current pellet information
			self.pelletdb['current']['hopper_level'] = self.dist_device.get_level(override=True)
			store.write_pellet_db(self.pelletdb)
			self.eventLogger.info('Hopper Level Checked @ ' + str(self.pelletdb['current']['hopper_level']) + '%')
			self.control['hopper_check'] = False
			store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

		# Grab current probe profiles if they have changed since the last loop.
		if self.control['probe_profile_update']:
			self.settings = settings = store.read_settings()
			self.control['probe_profile_update'] = False
			store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
			# Add new probe profiles to probe complex object
			self.probe_complex.update_probe_profiles(settings['probe_settings']['probe_map']['probe_info'])
			self.eventLogger.info('Active probe profiles updated in control script.')

		if self.control['updated'] and not self.control['critical_error']:
			self.eventLogger.debug(
				f'Control Settings Updated.  Mode: {self.control["mode"]}, Units Change: {self.control["units_change"]} '
			)
			# Clear control flag
			self.control['updated'] = False  # Reset Control Updated to False
			store.write_control(
				self.control, WriteKind.OVERWRITE, origin='control'
			)  # Commit change in 'updated' status to the file

			if self.control['units_change']:
				self.eventLogger.debug('Changing Base Units.')
				self.settings = settings = store.read_settings()
				# Update ADC objects and set profiles
				self.probe_complex.update_units(settings['globals']['units'])
				self.control['mode'] = 'Stop'  # Stop any activity
				self.control['units_change'] = False
				store.read_history(0, flushhistory=True)  # Clear history data
				# No need to write control, as it should be written by the 'Stop' mode change

			# Check if there was an Error flagged in Monitor Mode - If no, then change status to active
			if self.control['status'] != 'monitor' and self.control['mode'] != 'Error':
				self.control['status'] = 'active'  # Set status to active
				store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

			if self.control['mode'] in ('Stop', 'Error'):
				grill_platform.auger_off()
				grill_platform.igniter_off()
				grill_platform.fan_off()
				# Register Stop Mode in Metrics DB if this is not initial stop-mode on startup (i.e. DB is empty)
				metrics_list = store.read_metrics(all=True)
				if len(metrics_list) != 0:
					store.write_metrics(new_metric=True)
					metrics = store.read_metrics()
					metrics['mode'] = 'Stop'
					store.write_metrics(metrics)
					if metrics_list[-1]['mode'] != 'Prime':
						create_cookfile()

				self.status['p_mode'] = 0
				self.status['mode'] = 'Stop'
				self.status['recipe'] = False
				self.status['recipe_paused'] = False
				self.status['start_time'] = 0
				self.status['lid_open_detected'] = False
				self.status['lid_open_endtime'] = 0
				self.status['startup_timestamp'] = 0
				store.write_status(self.status)

				if self.control['status'] == 'monitor' and self.control['mode'] == 'Error':
					grill_platform.power_on()
				else:
					grill_platform.power_off()

				if self.control['mode'] == 'Stop':
					self.eventLogger.info('Stop Mode Started.')
					store.display_commands().push(('clear', None))
					self.control['status'] = 'inactive'
					# Reset Control to Defaults
					self.control = store.read_control(flush=True)
					self.control['updated'] = False
					self.control['tuning_mode'] = False  # Turn off Tuning Mode on Stop just in case it is on
					self.control['next_mode'] = 'Stop'
					self.control['safety']['reigniteretries'] = settings['safety'][
						'reigniteretries'
					]  # Reset retry counter to default
					self.control['startup_timestamp'] = 0  # Reset the startup timestamp to 0
					store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
				else:
					self.eventLogger.error('An error has occurred, Stop Mode enabled.')
					self.controlLogger.error('An error has occurred, Stop Mode enabled.')
					# Reset Control to Defaults but preserve 'Error' mode condition
					self.control = default_control()
					self.control['mode'] = 'Error'
					self.control['status'] = 'inactive'
					self.control['tuning_mode'] = False  # Turn off Tuning Mode on Stop just in case it is on
					self.control['updated'] = False
					self.control['next_mode'] = 'Stop'
					self.control['safety']['reigniteretries'] = settings['safety'][
						'reigniteretries'
					]  # Reset retry counter to default
					store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
					ctx.clock.sleep(3)
					store.display_commands().push(('clear', None))

				store.read_current(zero_out=True)  # Zero out the current values

			# Prime (dump preset amount of pellets into the firepot)
			elif self.control['mode'] == 'Prime':
				if not settings['platform']['standalone'] and not grill_platform.get_input_status():
					self.eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
					)
				# Call Work Cycle for Startup Mode
				self.work_cycle('Prime')
				# Select Next Mode
				self.settings = settings = store.read_settings()
				self.next_mode(
					self.control['next_mode'], setpoint=settings['startup']['start_to_mode']['primary_setpoint']
				)

			# Startup (startup sequence)
			elif self.control['mode'] == 'Startup':
				if not settings['platform']['standalone'] and not grill_platform.get_input_status():
					self.eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
					)
				self.settings = settings = store.read_settings()
				# Clear History (in the case it wasn't already cleared fromt he last run)
				self.eventLogger.debug('Clearing History and Current Log on Startup Mode.')
				store.read_history(0, flushhistory=True)  # Clear all history
				# Check if Prime on Startup is selected
				if settings['startup']['prime_on_startup'] > 0:
					self.control['prime_amount'] = settings['startup']['prime_on_startup']
					self.control['mode'] = 'Prime'
					store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
					# Call Work Cycle for Prime Mode
					self.work_cycle('Prime')
					self.control = (
						store.read_control()
					)  # Refresh control in case any changes were made during the cycle
					if self.control['mode'] in ['Prime', 'Startup']:
						self.control['updated'] = False
						self.control['mode'] = 'Startup'
				# Check if there was a mode change during Priming
				if self.control['mode'] == 'Startup':
					# Setup Next Mode (after startup mode)
					self.control['next_mode'] = settings['startup']['start_to_mode']['after_startup_mode']
					store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
					# Call Work Cycle for Startup Mode
					self.work_cycle('Startup')
					# Select Next Mode
					self.settings = settings = store.read_settings()
					self.next_mode(
						self.control['next_mode'], setpoint=settings['startup']['start_to_mode']['primary_setpoint']
					)

			# Smoke (smoke cycle)
			elif self.control['mode'] == 'Smoke':
				self.work_cycle('Smoke')
				self.next_mode(self.control['next_mode'])

			# Hold (hold at setpoint)
			elif self.control['mode'] == 'Hold':
				self.work_cycle('Hold')
				self.next_mode(self.control['next_mode'])

			# Shutdown (shutdown sequence)
			elif self.control['mode'] == 'Shutdown':
				self.control['next_mode'] = 'Stop'
				store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
				self.work_cycle('Shutdown')
				self.next_mode(self.control['next_mode'])
				if settings['shutdown']['auto_power_off']:
					self.eventLogger.info('Shutdown mode ended powering off grill')
					os.system('sleep 3 && sudo shutdown -h now &')

			# Monitor (monitor the OEM controller)
			elif self.control['mode'] == 'Monitor':
				self.control['status'] = 'monitor'  # Set status to monitor
				store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
				self.work_cycle('Monitor')

			# Manual Mode
			elif self.control['mode'] == 'Manual':
				self.work_cycle('Manual')

			# Recipe Mode
			elif self.control['mode'] == 'Recipe':
				self.recipe_mode(start_step=self.control['recipe']['start_step'])

			# Reignite (reignite sequence)
			elif self.control['mode'] == 'Reignite':
				if (not settings['platform']['standalone']) and (not grill_platform.get_input_status()):
					self.eventLogger.warning(
						"PiFire is set to OFF. This doesn't prevent reignite, "
						"but this means the switch won't behave as normal."
					)
				self.control['next_mode'] = self.control['safety']['reignitelaststate']
				setpoint = self.control['primary_setpoint']
				store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
				self.work_cycle('Reignite')
				self.next_mode(self.control['next_mode'], setpoint=setpoint)

		if settings['notify_services'].get('mqtt') != None and settings['notify_services']['mqtt']['enabled']:
			check_notify(settings, self.control, pelletdb=self.pelletdb)
