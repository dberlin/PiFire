from common.common import WriteKind
from controller.runtime.logic.cycle import smoke_cycle_times
from controller.runtime.logic.fan import start_fan
from controller.runtime.logic.safety import startup_temp_bounds
from controller.runtime.logic.smartstart import select_profile, profile_cycle
from controller.runtime.modes.base import ControlMode


class StartupMode(ControlMode):
	"""Startup mode: fan+power on at setup (shared branch with Reignite/Smoke/
	Hold/Shutdown, plus the Startup/Reignite dc_fan pwm_duty_cycle special
	case); igniter+auger ON at setup; initializes the smoke-cycle timing
	(shared init path with Reignite/Smoke). Safety baseline
	(`self.state.startup.raw_temp`, startuptemp bounds, afterstarttemp) and
	smart-start profile selection are computed post-probe-read in
	setup_safety(), since they need the initial ptemp. Per-tick, runs the
	shared (non-Hold) auger-cycle toggle and publishes `self.state.cycle.ratio`
	to MQTT. check_safety() just tracks afterstarttemp (no write -- teardown
	does the write). should_exit() recomputes the smart-start-vs-normal
	`self.state.startup.timer`/exit_temp on every tick (not just once at setup)
	and exits when the timer elapses or exit_temp is reached."""

	name = 'Startup'

	def setup(self):
		import control as _control

		settings = self.settings

		if settings['platform']['dc_fan'] and settings['startup'].get('pwm_duty_cycle') is not None:
			start_fan(self.grill, settings, duty_cycle=settings['startup']['pwm_duty_cycle'])
		else:
			start_fan(self.grill, settings)
		self.grill.power_on()
		_control.eventLogger.debug('Power ON, Fan ON, Igniter OFF, Auger OFF')

		self.grill.igniter_on()
		_control.eventLogger.debug('Igniter ON')

		self.grill.auger_on()
		_control.eventLogger.debug('Auger ON')

		self._init_smoke_cycle()

	def _init_smoke_cycle(self):
		_ct = smoke_cycle_times(self.settings['cycle_data'])
		self.state.cycle.on_time = _ct.on_time
		self.state.cycle.off_time = _ct.off_time
		self.state.cycle.cycle_time = _ct.cycle_time
		self.state.cycle.ratio = _ct.cycle_ratio
		self.state.cycle.raw_ratio = _ct.cycle_ratio
		self.state.lid.open_detected = False
		self.state.lid.expires = 0
		# Write Metrics (note these will be overwritten if smart start is enabled)
		self.state.metrics['p_mode'] = self.settings['cycle_data']['PMode']
		self.state.metrics['auger_cycle_time'] = self.settings['cycle_data']['SmokeOnCycleTime']
		self.ctx.store.write_metrics(self.state.metrics)

	def setup_safety(self, ptemp) -> str:
		# This value is needed for the case when the grill starts hot and exit
		# temp has been exceeded
		self.state.startup.raw_temp = ptemp
		self.control['safety']['startuptemp'] = startup_temp_bounds(ptemp, self.settings['safety'])
		self.control['safety']['afterstarttemp'] = ptemp
		self.ctx.store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

		# Apply Smart Start Settings if Enabled
		if self.settings['startup']['smartstart']['enabled']:
			# If Startup, then save initial temperature & select the profile
			self.control['smartstart']['startuptemp'] = int(ptemp)
			# Cycle through profiles, and set profile if startup temperature falls
			# below the minimum temperature
			self.control['smartstart']['profile_selected'] = select_profile(
				self.control['smartstart']['startuptemp'], self.settings['startup']['smartstart']['temp_range_list']
			)
			self.ctx.store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

			# Apply the profile
			profile_selected = self.control['smartstart']['profile_selected']
			profile = self.settings['startup']['smartstart']['profiles'][profile_selected]
			_ct, startup_timer, _mbits = profile_cycle(profile, self.settings['cycle_data'])
			self.state.cycle.on_time = _ct.on_time
			self.state.cycle.off_time = _ct.off_time
			self.state.cycle.cycle_time = _ct.cycle_time
			self.state.cycle.ratio = _ct.cycle_ratio
			self.state.cycle.raw_ratio = _ct.cycle_ratio
			self.state.startup.timer = startup_timer
			# Write Metrics
			self.state.metrics['smart_start_profile'] = profile_selected
			self.state.metrics['startup_temp'] = self.control['smartstart']['startuptemp']
			self.state.metrics.update(_mbits)
			self.ctx.store.write_metrics(self.state.metrics)

		self._write_startup_timestamp()

		return 'Active'

	def _write_startup_timestamp(self):
		"""Startup writes control['startup_timestamp'] at the start of the run.
		Overridden as a no-op by ReigniteMode (which doesn't reset it).

		NOTE: this runs from setup_safety(), which `ControlMode.run()` calls
		BEFORE it sets self.state.timers.start_time (that happens later in the
		shared pre-loop). We therefore take our own ctx.clock.now() reading here
		rather than reading self.state.timers.start_time (which is still its 0.0
		default at this point)."""
		self.control['startup_timestamp'] = self.ctx.clock.now()
		self.ctx.store.write_control(self.control, WriteKind.OVERWRITE, origin='control')

	def on_settings_reload(self):
		_ct = smoke_cycle_times(self.settings['cycle_data'])
		self.state.cycle.on_time = _ct.on_time
		self.state.cycle.off_time = _ct.off_time
		self.state.cycle.cycle_time = _ct.cycle_time
		self.state.cycle.ratio = _ct.cycle_ratio
		self.state.cycle.raw_ratio = _ct.cycle_ratio
		# Write Metrics (note these will overwrite the previous value)
		self.state.metrics['p_mode'] = self.settings['cycle_data']['PMode']
		self.state.metrics['auger_cycle_time'] = self.settings['cycle_data']['SmokeOnCycleTime']
		self.ctx.store.write_metrics(self.state.metrics)

	def on_tick(self, now, ptemp, current_output_status):
		self._auger_cycle_tick(now, current_output_status)

	def on_publish(self, now):
		pid_data = {'cycle_ratio': round(self.state.cycle.ratio, 2)}
		self.ctx.notifications.check(self.settings, self.control, pid_data=pid_data)

	def check_safety(self, now, ptemp):
		self.control['safety']['afterstarttemp'] = ptemp

	def should_exit(self, now, ptemp) -> bool:
		settings = self.settings
		control = self.control
		if settings['startup']['smartstart']['enabled']:
			profile_selected = control['smartstart']['profile_selected']
			startup_timer = settings['startup']['smartstart']['profiles'][profile_selected]['startuptime']
			# Check case where the grill starts hot (perhaps due to previous failure)
			if self.state.startup.raw_temp >= settings['startup']['smartstart']['exit_temp']:
				exit_temp = 0  # Force ignite
			else:
				exit_temp = settings['startup']['smartstart']['exit_temp']
		else:
			startup_timer = settings['startup']['duration']
			exit_temp = settings['startup']['startup_exit_temp']
			# Check case where the grill starts hot (perhaps due to previous failure)
			if self.state.startup.raw_temp >= settings['startup']['startup_exit_temp']:
				exit_temp = 0  # Force ignite
			else:
				exit_temp = settings['startup']['startup_exit_temp']

		self.state.startup.timer = startup_timer

		if (now - self.state.timers.start_time) > startup_timer:
			return True

		if (exit_temp != 0) and (ptemp >= exit_temp):
			return True

		return False

	def teardown(self, ptemp):
		self.control['safety']['afterstarttemp'] = ptemp
		self.ctx.store.write_control(self.control, WriteKind.OVERWRITE, origin='control')
