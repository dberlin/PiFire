from controller.runtime.modes.startup import StartupMode


class ReigniteMode(StartupMode):
	"""Reignite mode: identical to Startup (fan/power/igniter/auger setup,
	smoke-cycle init, safety baseline, smart-start select+apply,
	on_settings_reload, auger on_tick, check_safety afterstarttemp,
	should_exit timer/exit-temp, teardown afterstarttemp -- all shared inline
	blocks gated `mode in ('Startup', 'Reignite')`), EXCEPT for two things
	that are Startup-only in control.py:
	  1. It does NOT write control['startup_timestamp'] (inline gate is
	     `if mode == 'Startup':`, not Reignite).
	  2. It is excluded from the cycle-ratio MQTT publish (inline gate is
	     `if mode in ('Startup', 'Smoke'):`, not Reignite).
	"""

	name = 'Reignite'

	def _write_startup_timestamp(self):
		pass  # Reignite does not write startup_timestamp (Startup-only inline)

	def on_publish(self, now):
		pass  # Reignite is excluded from the cycle-ratio MQTT publish (Startup/Smoke only)
