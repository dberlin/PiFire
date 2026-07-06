from common.common import WriteKind
from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.safety import evaluate_flameout, SafetyVerdict
from controller.runtime.logic.smartstart import profile_cycle
from controller.runtime.modes.base import ControlMode


class SmokeMode(ControlMode):
	"""Smoke mode: fan+power on at setup (shared branch with Startup/Reignite/
	Hold/Shutdown -- Smoke always takes the plain `start_fan(grill, settings)`
	path, never the Startup/Reignite dc_fan pwm_duty_cycle special case);
	auger ON at setup (shared with Startup/Reignite/Hold/Prime); initializes
	the smoke-cycle timing (shared init path with Startup/Reignite); sets up
	Recipe-mode triggers (shared with Hold). setup_safety() runs the pre-loop
	flameout check FIRST (evaluate_flameout against the carried-over
	afterstarttemp/startuptemp -- can abort into Error/Reignite before the
	loop even starts), THEN applies smart-start (Smoke skips the
	Startup/Reignite profile-SELECTION sub-branch and just re-applies
	control['smartstart']['profile_selected'] chosen by a prior Startup/
	Reignite run). Per-tick, runs the shared (non-Hold) auger-cycle toggle,
	publishes cycle_ratio to MQTT (shared with Startup), and re-checks
	flameout in-loop via check_safety (stashing ptemp on self.state for
	on_fan_tick). on_fan_tick delegates entirely to the shared
	`_smoke_plus_fan_tick` helper -- Smoke never touches the Hold-only lid-
	open/PWM-duty-from-temp/fan-assist parts (target_temp_achieved stays
	False for Smoke, so that gate structurally excludes it). No mode-specific
	teardown."""

	name = 'Smoke'

	def setup(self):
		# NOTE: the Recipe-mode trigger setup (inline ~136-160, gated
		# `control['mode']=='Recipe' and mode in ('Smoke','Hold')`) is already
		# reproduced UNCONDITIONALLY in base.run()'s shared pre-loop section
		# (base.py ~262-291, gated on `mode in ['Smoke', 'Hold']` using the
		# same `self.name`) -- it runs for every ControlMode subclass, so it
		# is NOT duplicated here.
		import control as _control

		start_fan(self.grill, self.settings)
		self.grill.power_on()
		_control.eventLogger.debug('Power ON, Fan ON, Igniter OFF, Auger OFF')

		self.grill.auger_on()
		_control.eventLogger.debug('Auger ON')

		self._init_smoke_cycle()

	def _init_smoke_cycle(self):
		_ct = smoke_cycle_times(self.settings['cycle_data'])
		self.state.on_time = _ct.on_time
		self.state.off_time = _ct.off_time
		self.state.cycle_time = _ct.cycle_time
		self.state.cycle_ratio = _ct.cycle_ratio
		self.state.raw_cycle_ratio = _ct.cycle_ratio
		self.state.lid_open_detect = False
		self.state.lid_open_expires = 0
		# Write Metrics (note these will be overwritten if smart start is enabled)
		self.state.metrics['p_mode'] = self.settings['cycle_data']['PMode']
		self.state.metrics['auger_cycle_time'] = self.settings['cycle_data']['SmokeOnCycleTime']
		self.ctx.store.write_metrics(self.state.metrics)

	def setup_safety(self, ptemp) -> str:
		ctx = self.ctx
		control = self.control
		settings = self.settings
		status = 'Active'

		# Check if the temperature of the grill dropped below the startuptemp
		verdict = evaluate_flameout(
			control['safety']['afterstarttemp'], control['safety']['startuptemp'], control['safety']['reigniteretries']
		)
		if verdict is SafetyVerdict.ERROR:
			status = 'Inactive'
			ctx.store.display_commands().push(('text', 'ERROR'))
			control['mode'] = 'Error'
			control['updated'] = True
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
			ctx.notifications.send('Grill_Error_02')
		elif verdict is SafetyVerdict.REIGNITE:
			control['safety']['reigniteretries'] -= 1
			control['safety']['reignitelaststate'] = self.name
			status = 'Inactive'
			ctx.store.display_commands().push(('text', 'Re-Ignite'))
			control['mode'] = 'Reignite'
			control['updated'] = True
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
			ctx.notifications.send('Grill_Error_03')

		# Apply Smart Start Settings if Enabled (Smoke re-applies the profile
		# already selected by a prior Startup/Reignite run -- no selection here)
		if settings['startup']['smartstart']['enabled']:
			profile_selected = control['smartstart']['profile_selected']
			profile = settings['startup']['smartstart']['profiles'][profile_selected]
			_ct, startup_timer, _mbits = profile_cycle(profile, settings['cycle_data'])
			self.state.on_time = _ct.on_time
			self.state.off_time = _ct.off_time
			self.state.cycle_time = _ct.cycle_time
			self.state.cycle_ratio = _ct.cycle_ratio
			self.state.raw_cycle_ratio = _ct.cycle_ratio
			self.state.startup_timer = startup_timer
			# Write Metrics
			self.state.metrics['smart_start_profile'] = profile_selected
			self.state.metrics['startup_temp'] = control['smartstart']['startuptemp']
			self.state.metrics.update(_mbits)
			ctx.store.write_metrics(self.state.metrics)

		return status

	def on_settings_reload(self):
		_ct = smoke_cycle_times(self.settings['cycle_data'])
		self.state.on_time = _ct.on_time
		self.state.off_time = _ct.off_time
		self.state.cycle_time = _ct.cycle_time
		self.state.cycle_ratio = _ct.cycle_ratio
		self.state.raw_cycle_ratio = _ct.cycle_ratio
		# Write Metrics (note these will overwrite the previous value)
		self.state.metrics['p_mode'] = self.settings['cycle_data']['PMode']
		self.state.metrics['auger_cycle_time'] = self.settings['cycle_data']['SmokeOnCycleTime']
		self.ctx.store.write_metrics(self.state.metrics)

	def on_tick(self, now, current_output_status):
		self._auger_cycle_tick(now, current_output_status)

	def on_publish(self, now):
		pid_data = {'cycle_ratio': round(self.state.cycle_ratio, 2)}
		self.ctx.notifications.check(self.settings, self.control, pid_data=pid_data)

	def check_safety(self, now, ptemp) -> bool:
		ctx = self.ctx
		control = self.control
		self.state.ptemp = ptemp

		verdict = evaluate_flameout(ptemp, control['safety']['startuptemp'], control['safety']['reigniteretries'])
		if verdict is SafetyVerdict.ERROR:
			ctx.store.display_commands().push(('text', 'ERROR'))
			control['mode'] = 'Error'
			control['updated'] = True
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
			ctx.notifications.send('Grill_Error_02')
			return True
		elif verdict is SafetyVerdict.REIGNITE:
			control['safety']['reigniteretries'] -= 1
			control['safety']['reignitelaststate'] = self.name
			ctx.store.display_commands().push(('text', 'Re-Ignite'))
			control['mode'] = 'Reignite'
			control['updated'] = True
			ctx.store.write_control(control, WriteKind.OVERWRITE, origin='control')
			ctx.notifications.send('Grill_Error_03')
			return True
		return False

	def on_fan_tick(self, now, current_output_status):
		self._smoke_plus_fan_tick(now, current_output_status)
