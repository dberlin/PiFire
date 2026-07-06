"""Fixtures for characterization tests: realistic settings/control/pellet dicts,
derived from common.common defaults, with timers shrunk to tiny values so the
work-cycle loop's own timer-based exits fire in a handful of ManualClock ticks.

TERMINATION SAFETY: every duration-like setting that could keep _work_cycle's
`while status == 'Active':` loop spinning forever is deliberately made tiny
here. Individual tests may override further, but should not make things
*larger* without re-checking the scenario still terminates (or bounding it
via the probe-cap technique described in harness.py).
"""

import copy

from common.common import default_settings, default_control, default_pellets


def base_settings():
	settings = default_settings()

	# --- Termination safety: shrink every timer so natural loop exits fire
	# in a handful of ManualClock.sleep(0.05) ticks. ---
	settings['startup']['duration'] = 0.2
	settings['startup']['startup_exit_temp'] = 0  # disabled unless a test opts in
	settings['startup']['smartstart']['enabled'] = False
	settings['shutdown']['shutdown_duration'] = 0.2

	# Prime: prime_duration = int(prime_amount / augerrate). Keep augerrate at
	# the default (0.3) and let individual prime tests set prime_amount small
	# via control['prime_amount'] so prime_duration rounds to a tiny int.
	settings['globals']['augerrate'] = 0.3

	# Smoke/startup auger cycle timing -- default 15s on / 65s off (PMode 2) is
	# fine for scenarios that terminate on a safety/mode transition before a
	# full cycle elapses, but scenarios that must observe an auger on/off
	# *transition* shrink these further inline.

	# Safety defaults: keep maxtemp/minstartuptemp etc. as shipped; tests
	# override safety['maxtemp'] etc. per-scenario.

	return settings


def base_control(mode='Smoke'):
	control = default_control()
	control['mode'] = mode
	control['updated'] = False  # loop's "new mode requested" check reads this
	control['primary_setpoint'] = 225
	return control


def base_pellet_db():
	return default_pellets()
