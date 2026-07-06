"""ControlMode template-method base: reproduces the SHARED skeleton of
control.py's `_work_cycle` (see `.superpowers/sdd/workcycle-map.md`).

Concrete subclasses (Monitor, Manual, ...) override the hooks below to
supply their mode-specific behavior. `run()` reproduces every SHARED
operation from `_work_cycle` in the SAME ORDER: pre-loop setup, the main
loop body, and teardown. Do not reorder anything here without re-checking
the blueprint's "Risk notes" section -- several orderings (e.g.
`current_output_status` captured once before the manual-override block,
the auger-cycle block running BEFORE the probe re-read) are load-bearing.
"""
import logging

from common.common import WriteKind
from common.process_mon import Process_Monitor
from controller.runtime.logic.safety import over_max_temp


class ControlMode:
	"""Template-method base for a single `_work_cycle` invocation.

	Subclasses set `name` (matches the legacy `mode` string) and override
	hooks with safe no-op defaults:
	  - setup(): pre-loop mode-specific configuration (fan/power, cycle
	    params, runner, ...).
	  - setup_safety() -> str: pre-loop safety check. Return 'Active' to
	    allow the loop to run, 'Inactive' to skip it entirely (abort
	    contract -- teardown still runs).
	  - on_tick(now, current_output_status): per-iteration mode-specific
	    control logic. `current_output_status` is captured ONCE per tick
	    by the shared skeleton, BEFORE the manual-override block, and
	    passed in here -- never re-fetch it inside a hook.
	  - check_safety(now, ptemp): per-iteration mode-specific safety check.
	  - should_exit(now, ptemp) -> bool: per-iteration mode-specific exit
	    condition (default False -- rely on the universal breaks).
	  - status_fragment() -> dict: extra fields merged into status_data at
	    publish time (default {}).
	  - teardown(ptemp): mode-specific cleanup after the loop ends.
	"""

	name: str = ''

	def __init__(self, ctx, state):
		self.ctx = ctx
		self.state = state
		self.grill = ctx.devices.grill_platform
		self.probe_complex = ctx.devices.probe_complex
		self.dist_device = ctx.devices.dist_device

	# ---- hooks (safe defaults) ----
	def setup(self):
		pass

	def setup_safety(self) -> str:
		return 'Active'

	def on_tick(self, now, current_output_status):
		pass

	def check_safety(self, now, ptemp):
		pass

	def should_exit(self, now, ptemp) -> bool:
		return False

	def status_fragment(self) -> dict:
		return {}

	def teardown(self, ptemp):
		pass

	# ---- shared skeleton ----
	def run(self):
		import control as _control  # module globals: eventLogger, _process_system_commands

		ctx = self.ctx
		mode = self.name
		grill_platform = self.grill
		probe_complex = self.probe_complex
		dist_device = self.dist_device

		# Setup Process Monitor and Start
		monitor = Process_Monitor('control', ['supervisorctl', 'restart', 'control'], timeout=30)
		monitor.start_monitor()

		# Precondition for entering into main control loop
		status = 'Active'

		# Setup Cycle Parameters
		settings = ctx.store.read_settings()
		control = ctx.store.read_control()
		pelletdb = ctx.store.read_pellet_db()
		control['hopper_check'] = True
		ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')

		_control.eventLogger.info(f'{mode} Mode started.')

		# Pre-Loop Setup Recipe Triggers
		if control['mode'] == 'Recipe':
			if mode in ['Smoke', 'Hold']:
				recipe_trigger_set = False
				if control['recipe']['step_data']['timer'] > 0:
					for index, item in enumerate(control['notify_data']):
						if item['type'] == 'timer':
							control['notify_data'][index]['req'] = True
							timer_start = ctx.clock.now()
							control['timer']['start'] = timer_start
							control['timer']['paused'] = 0
							control['timer']['end'] = timer_start + (control['recipe']['step_data']['timer'] * 60)
							control['timer']['shutdown'] = False
							control['notify_data'][index]['shutdown'] = False
							control['notify_data'][index]['keep_warm'] = False
							recipe_trigger_set = True

				for probe, value in control['recipe']['step_data']['trigger_temps'].items():
					if value > 0:
						for index, item in enumerate(control['notify_data']):
							if item['type'] == 'probe' and item['label'] == probe:
								control['notify_data'][index]['target'] = value
								control['notify_data'][index]['req'] = True
								recipe_trigger_set = True
								break

				if recipe_trigger_set:
					ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
				else:
					_control.eventLogger.warning('No trigger set for Hold/Smoke mode in recipe.')

		# Get ON/OFF Switch state and set as last state
		last = grill_platform.get_input_status()

		# Set DC fan frequency if it has changed since init
		if settings['platform']['dc_fan']:
			pwm_frequency = settings['pwm']['frequency']
			frequency_status = grill_platform.get_output_status()
			if not pwm_frequency == frequency_status['frequency']:
				grill_platform.set_pwm_frequency(pwm_frequency)

		# Set Starting Configuration for Igniter, Fan, Auger
		grill_platform.igniter_off()
		grill_platform.auger_off()

		# ---- mode-specific pre-loop setup ----
		self.setup()

		ctx.store.write_metrics(new_metric=True)
		metrics = ctx.store.read_metrics()
		metrics['mode'] = mode
		metrics['smokeplus'] = control['s_plus']
		metrics['primary_setpoint'] = control['primary_setpoint']
		metrics['pellet_level_start'] = pelletdb['current']['hopper_level']
		current_pellet_id = pelletdb['current']['pelletid']
		pellet_brand = pelletdb['archive'][current_pellet_id]['brand']
		pellet_type = pelletdb['archive'][current_pellet_id]['wood']
		metrics['pellet_brand_type'] = f'{pellet_brand} {pellet_type}'
		ctx.store.write_metrics(metrics)

		# Get initial probe sensor data, temperatures
		sensor_data = probe_complex.read_probes()
		ptemp = list(sensor_data['primary'].values())[0]  # Primary Temperature or the Pit Temperature

		# ---- mode-specific pre-loop safety check (abort contract) ----
		status = self.setup_safety()

		# Apply Smart Start Settings if Enabled (default; Startup/Reignite/Smoke
		# override self.state.startup_timer from their own setup())
		self.state.startup_timer = settings['startup']['duration']

		# Set the start time
		start_time = ctx.clock.now()

		# Set time since toggle for temperature
		temp_toggle_time = start_time
		# Set time since toggle for checking ETA
		eta_toggle_time = start_time
		# Set time since toggle for auger
		auger_toggle_time = start_time
		# Set time since toggle for display
		display_toggle_time = start_time
		# Initializing Start Time for Fan
		fan_cycle_toggle_time = start_time
		# Set time since toggle for hopper check
		hopper_toggle_time = start_time
		# Set time since fan speed update
		fan_update_time = start_time

		# Setup Display Data
		status_data = {}
		in_data = {}

		# Clear Manual Overrides
		manual_override = {'igniter': 0, 'auger': 0, 'fan': 0, 'power': 0, 'pwm': 0}

		# ============ Main Work Cycle ============
		while status == 'Active':
			now = ctx.clock.now()

			ctx.store.execute_control_writes()
			control = ctx.store.read_control()

			_control._process_system_commands(ctx)

			# Check if new mode has been requested
			if control['updated']:
				break

			# Check if user changed settings and reload
			if control['settings_update']:
				control['settings_update'] = False
				ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
				settings = ctx.store.read_settings()
				if settings['globals']['debug_mode']:
					_control.eventLogger.setLevel(logging.DEBUG)
				else:
					_control.eventLogger.setLevel(logging.INFO)

			# Check if user changed hopper levels and update if required
			if control['distance_update']:
				empty = settings['pelletlevel']['empty']
				full = settings['pelletlevel']['full']
				dist_device.update_distances(empty, full)
				control['distance_update'] = False
				ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')

			# Check hopper level when requested or every 300 seconds
			if control['hopper_check'] or (now - hopper_toggle_time) > 60:
				pelletdb = ctx.store.read_pellet_db()
				override = False
				if control['hopper_check']:
					control['hopper_check'] = False
					ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
					override = True
				pelletdb['current']['hopper_level'] = dist_device.get_level(override=override)
				ctx.store.write_pellet_db(pelletdb)
				hopper_toggle_time = now
				_control.eventLogger.info('Hopper Level Checked @ ' + str(pelletdb['current']['hopper_level']) + '%')

			# Check for update in ON/OFF Switch
			if not settings['platform']['standalone'] and last != grill_platform.get_input_status():
				last = grill_platform.get_input_status()
				if not last:
					_control.eventLogger.info('Switch set to off, going to monitor mode.')
					control['updated'] = True  # Change mode
					control['mode'] = 'Stop'
					control['status'] = 'active'
					ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
					break

			current_output_status = grill_platform.get_output_status()

			if mode == 'Manual' or settings['safety']['allow_manual_changes']:
				if control['manual']['change'] in ['power', 'igniter', 'fan', 'auger', 'pwm']:
					if mode != 'Manual':
						override_time = now + settings['safety']['manual_override_time']
					else:
						override_time = 0

					if control['manual']['change'] == 'fan':
						if control['manual']['output'] and not current_output_status['fan']:
							grill_platform.fan_on()
							_control.eventLogger.debug('Fan ON')
						elif not control['manual']['output'] and current_output_status['fan']:
							grill_platform.fan_off()
							_control.eventLogger.debug('Fan OFF')
						manual_override['fan'] = override_time

					if control['manual']['change'] == 'auger':
						if control['manual']['output'] and not current_output_status['auger']:
							grill_platform.auger_on()
							_control.eventLogger.debug('Auger ON')
						elif not control['manual']['output'] and current_output_status['auger']:
							grill_platform.auger_off()
							_control.eventLogger.debug('Auger OFF')
						manual_override['auger'] = override_time

					if control['manual']['change'] == 'igniter':
						if control['manual']['output'] and not current_output_status['igniter']:
							grill_platform.igniter_on()
							_control.eventLogger.debug('Igniter ON')
						elif not control['manual']['output'] and current_output_status['igniter']:
							grill_platform.igniter_off()
							_control.eventLogger.debug('Igniter OFF')
						manual_override['igniter'] = override_time

					if control['manual']['change'] == 'power':
						if control['manual']['output'] and not current_output_status['power']:
							grill_platform.power_on()
							_control.eventLogger.debug('Power ON')
						elif not control['manual']['output'] and current_output_status['power']:
							grill_platform.power_off()
							_control.eventLogger.debug('Power OFF')
						manual_override['power'] = override_time

					if (
						settings['platform']['dc_fan']
						and control['manual']['change'] == 'pwm'
						and current_output_status['fan']
						and not control['manual']['pwm'] == current_output_status['pwm']
					):
						speed = control['manual']['pwm']
						_control.eventLogger.debug('PWM Speed: ' + str(speed) + '%')
						grill_platform.set_duty_cycle(speed)
						manual_override['pwm'] = override_time
						control['manual']['pwm'] = 100  # Reset PWM

					control['manual']['change'] = None
					control['manual']['output'] = None
					ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')

			# ---- mode-specific per-tick control logic ----
			self.on_tick(now, current_output_status)

			# Grab current probe profiles if they have changed since the last loop.
			if control['probe_profile_update']:
				settings = ctx.store.read_settings()
				control['probe_profile_update'] = False
				ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
				probe_complex.update_probe_profiles(settings['probe_settings']['probe_map']['probe_info'])

			# Get probe device info for frontend
			ctx.store.write_generic_key('probe_device_info', probe_complex.get_device_info())

			# Get temperatures from all probes
			sensor_data = probe_complex.read_probes()
			ptemp = list(sensor_data['primary'].values())[0]  # Primary Temperature or the Pit Temperature

			in_data['probe_history'] = sensor_data
			in_data['primary_setpoint'] = control['primary_setpoint'] if mode == 'Hold' else 0
			in_data['notify_targets'] = ctx.notifications.get_targets(control['notify_data'])

			# If Extended Data Mode is Enabled, Populate Extra Data Here
			if settings['globals']['ext_data']:
				in_data['ext_data'] = {}
				in_data['ext_data']['CR'] = 0
				in_data['ext_data']['RCR'] = 0

			# Save current data to the database
			ctx.store.write_current(in_data)

			# Write Tr data to the database if in tuning mode
			if control['tuning_mode']:
				ctx.store.write_tr(in_data['probe_history']['tr'])

			# Every 20 seconds, update ETA for any pending notifications
			if (now - eta_toggle_time) > 20:
				eta_toggle_time = ctx.clock.now()
				update_eta = True
			else:
				update_eta = False
			control = ctx.notifications.check(
				settings, control, in_data=in_data, pelletdb=pelletdb, grill_platform=grill_platform, update_eta=update_eta
			)

			# Send Current Status / Temperature Data to Display Device every 0.5 second
			if (now - display_toggle_time) > 0.5:
				status_data['notify_data'] = control['notify_data']
				status_data['timer'] = control['timer']
				status_data['s_plus'] = control['s_plus']
				status_data['hopper_level_enabled'] = False if settings['modules']['dist'] == 'none' else True
				status_data['hopper_level'] = pelletdb['current']['hopper_level']
				status_data['units'] = settings['globals']['units']
				status_data['mode'] = mode
				status_data['recipe'] = True if control['mode'] == 'Recipe' else False
				status_data['start_time'] = start_time
				status_data['start_duration'] = self.state.startup_timer
				status_data['shutdown_duration'] = settings['shutdown']['shutdown_duration']
				status_data['prime_duration'] = 0
				status_data['prime_amount'] = 0
				status_data['lid_open_detected'] = False
				status_data['lid_open_endtime'] = 0
				status_data['p_mode'] = metrics.get('p_mode', None)
				status_data['startup_timestamp'] = control['startup_timestamp']
				if control['mode'] == 'Recipe':
					status_data['recipe_paused'] = (
						True
						if control['recipe']['step_data']['triggered'] and control['recipe']['step_data']['pause']
						else False
					)
				else:
					status_data['recipe_paused'] = False
				status_data['outpins'] = {}
				current = grill_platform.get_output_status()
				for item in settings['platform']['outputs']:
					try:
						status_data['outpins'][item] = current[item]
					except KeyError:
						continue
				# ---- mode-specific status fields ----
				status_data.update(self.status_fragment())
				ctx.store.write_status(status_data)
				display_toggle_time = ctx.clock.now()

			# ---- mode-specific per-tick safety check ----
			self.check_safety(now, ptemp)

			# Write History & Issue Heartbeat after 3 seconds has passed
			if (now - temp_toggle_time) > 3:
				temp_toggle_time = ctx.clock.now()
				ext_data = True if settings['globals']['ext_data'] else False
				ctx.store.write_history(in_data, ext_data=ext_data)
				monitor.heartbeat()

			# ---- mode-specific per-tick exit condition ----
			if self.should_exit(now, ptemp):
				break

			# Max Temp Safety Control (UNIVERSAL)
			if over_max_temp(ptemp, settings['safety']):
				ctx.store.display_commands().push(('text', 'ERROR'))
				control['mode'] = 'Error'
				control['updated'] = True
				ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
				ctx.notifications.send('Grill_Error_01')
				break

			# End of Loop Recipe Check
			if control['mode'] == 'Recipe':
				if control['recipe']['step_data']['triggered'] and not control['recipe']['step_data']['pause']:
					if control['recipe']['step_data']['notify']:
						ctx.notifications.send('Recipe_Step_Message')
					break
				elif control['recipe']['step_data']['triggered'] and control['recipe']['step_data']['pause']:
					if control['recipe']['step_data']['notify']:
						ctx.notifications.send('Recipe_Step_Message')
						control['recipe']['step_data']['notify'] = False
						ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
					# Continue until 'pause' variable is cleared

			ctx.clock.sleep(0.05)

		# *********
		# END Mode Loop
		# *********

		# Clean-up and Exit
		grill_platform.auger_off()
		grill_platform.igniter_off()

		_control.eventLogger.debug('Auger OFF, Igniter OFF')

		# ---- mode-specific teardown ----
		self.teardown(ptemp)

		_control.eventLogger.info(f'{mode} mode ended.')

		# Save Pellets Used
		pelletdb = ctx.store.read_pellet_db()
		pelletdb['current']['est_usage'] += metrics['augerontime'] * settings['globals']['augerrate']
		ctx.store.write_pellet_db(pelletdb)

		# Log the end time
		metrics['endtime'] = ctx.clock.now() * 1000
		metrics['pellet_level_end'] = pelletdb['current']['hopper_level']
		ctx.store.write_metrics(metrics)

		monitor.stop_monitor()

		if status_data != {}:
			status_data['mode'] = control['mode']

		return ()
