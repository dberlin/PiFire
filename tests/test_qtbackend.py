import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from display.qtbackend import PiFireBackend

PROBE_INFO = {'primary': {'name': 'Grill', 'max_temp': 600}, 'food': [{'name': 'Probe 1', 'max_temp': 300}], 'aux': []}


def make_backend(in_data, status_data):
	fetched = {'in': in_data, 'st': status_data}

	def fetch_fn():
		return fetched['in'], fetched['st']

	calls = []

	def command_fn(cmd, data):
		calls.append((cmd, data))

	b = PiFireBackend(fetch_fn, command_fn, PROBE_INFO)
	b._calls = calls
	return b


def test_poll_updates_primary_and_mode():
	in_data = {'P': {'Grill': 225}, 'F': {'Probe 1': 145}, 'AUX': {}, 'PSP': 250, 'NT': {'Grill': 0, 'Probe 1': 0}}
	status = {
		'mode': 'Hold',
		'units': 'F',
		'outpins': {'fan': True, 'auger': False, 'igniter': False, 'pwm': 0},
		'p_mode': 2,
		's_plus': True,
		'hopper_level': 80,
		'hopper_level_enabled': True,
		'recipe': False,
		'recipe_paused': False,
		'lid_open_detected': False,
	}
	b = make_backend(in_data, status)
	b.poll()
	assert b.mode == 'Hold'
	assert b.primaryTemp == 225
	assert b.primarySetpoint == 250
	assert b.primaryName == 'Grill'
	assert b.units == 'F'
	assert b.hopperLevel == 80
	assert b.smokePlus is True
	assert b.fanOn is True
	assert b.pMode == 2


def test_action_slots_dispatch_expected_commands():
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}})
	b.startup()
	b.stop()
	b.setHold(275)
	b.setPMode(4)
	b.primeStartup(25)
	b.toggleSmokePlus()
	assert ('cmd_startup', 0) in b._calls
	assert ('cmd_stop', 0) in b._calls
	assert ('cmd_hold', 275) in b._calls
	assert ('cmd_pmode', 4) in b._calls
	assert ('cmd_primestartup', 25) in b._calls
	assert ('cmd_splus', 0) in b._calls


def test_timer_text_counts_down_in_startup():
	status = {'mode': 'Startup', 'units': 'F', 'outpins': {}, 'start_time': 1000.0, 'start_duration': 240}
	in_data = {'P': {'Grill': 100}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}
	b = make_backend(in_data, status)
	b._now = lambda: 1000.0 + 45  # 45s elapsed -> 195s -> 03:15
	b.poll()
	assert b.timerText == '03:15'


def test_food_probe_model_reflects_current_data():
	info = {
		'primary': {'name': 'Grill', 'max_temp': 600},
		'food': [{'name': 'Probe 1', 'max_temp': 300}, {'name': 'Probe 2', 'max_temp': 300}],
		'aux': [],
	}

	def fetch_fn():
		return (
			{
				'P': {'Grill': 200},
				'F': {'Probe 1': 140, 'Probe 2': 0},
				'AUX': {},
				'PSP': 225,
				'NT': {'Probe 1': 165, 'Probe 2': 0},
			},
			{'mode': 'Hold', 'units': 'F', 'outpins': {}},
		)

	b = PiFireBackend(fetch_fn, lambda c, d: None, info)
	b.poll()
	model = b.foodProbes
	assert model.rowCount() == 2
	idx = model.index(0, 0)
	role = {v: k for k, v in model.roleNames().items()}
	assert model.data(idx, role[b'name']) == 'Probe 1'
	assert model.data(idx, role[b'temp']) == 140
	assert model.data(idx, role[b'target']) == 165


def test_data_keyed_by_label_display_by_name():
	# Live data keyed by probe label; display/notify use probe name.
	info = {'primary': {'name': 'Grill', 'label': 'P0'}, 'food': [{'name': 'Brisket', 'label': 'F0'}], 'aux': []}

	def fetch_fn():
		return (
			{'P': {'P0': 210}, 'F': {'F0': 155}, 'AUX': {}, 'PSP': 225, 'NT': {'P0': 235, 'F0': 190}},
			{'mode': 'Hold', 'units': 'F', 'outpins': {}},
		)

	b = PiFireBackend(fetch_fn, lambda c, d: None, info)
	b.poll()
	assert b.primaryTemp == 210
	assert b.primaryNotifyTarget == 235
	assert b.primaryName == 'Grill'
	model = b.foodProbes
	role = {v: k for k, v in model.roleNames().items()}
	idx = model.index(0, 0)
	assert model.data(idx, role[b'name']) == 'Brisket'  # display name
	assert model.data(idx, role[b'temp']) == 155  # looked up by label F0
	assert model.data(idx, role[b'target']) == 190


def test_notify_origin_dispatch_uses_name():
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Hold', 'units': 'F', 'outpins': {}})
	b.setNotify('Brisket', 203)
	assert ('cmd_notify', {'origin': 'Brisket', 'target': 203}) in b._calls


def test_mode_text_shows_recipe_label():
	b = make_backend(
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Hold', 'units': 'F', 'outpins': {}, 'recipe': True}
	)
	b.poll()
	assert b.modeText == 'Recipe: Hold'
	# Recipe label suppressed in Shutdown.
	b._fetch_fn = lambda: (
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		{'mode': 'Shutdown', 'units': 'F', 'outpins': {}, 'recipe': True},
	)
	b.poll()
	assert b.modeText == 'Shutdown'


def test_pmode_active_only_in_startup_smoke():
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Smoke', 'units': 'F', 'outpins': {}})
	b.poll()
	assert b.pModeActive is True
	b._fetch_fn = lambda: (
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		{'mode': 'Hold', 'units': 'F', 'outpins': {}},
	)
	b.poll()
	assert b.pModeActive is False


def test_hold_lid_open_countdown_timer():
	status = {'mode': 'Hold', 'units': 'F', 'outpins': {}, 'lid_open_detected': True, 'lid_open_endtime': 2000.0}
	b = make_backend({'P': {'Grill': 225}, 'F': {}, 'AUX': {}, 'PSP': 250, 'NT': {}}, status)
	b._now = lambda: 2000.0 - 65  # 65s remaining -> 01:05
	b.poll()
	assert b.timerText == '01:05'
	assert b.timerLabel == 'Lid Pause'


def test_sleep_wake_state_machine():
	clock = {'t': 1000.0}
	status = {'st': {'mode': 'Stop', 'units': 'F', 'outpins': {}}}
	b = PiFireBackend(
		lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, status['st']),
		lambda c, d: None,
		{'primary': {'name': 'Grill'}, 'food': [], 'aux': []},
	)
	b._now = lambda: clock['t']
	b._last_interaction = clock['t']
	# In Stop, before timeout: awake.
	b.poll()
	assert b.asleep is False
	# After 11s idle in Stop: asleep.
	clock['t'] = 1011.0
	b.poll()
	assert b.asleep is True
	# Interaction wakes it.
	b.registerInteraction()
	assert b.asleep is False
	# Leaving Stop (cook starts) keeps it awake even past the timeout.
	clock['t'] = 1100.0
	status['st'] = {'mode': 'Hold', 'units': 'F', 'outpins': {}}
	b.poll()
	assert b.asleep is False


def test_nav_slots_emit_navevent():
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}})
	events = []
	b.navEvent.connect(lambda e: events.append(e))
	b.navUp()
	b.navDown()
	b.navEnter()
	assert events == ['UP', 'DOWN', 'ENTER']


def test_poll_exposes_duty_cycles():
	in_data = {'P': {'Grill': 225}, 'F': {}, 'AUX': {}, 'PSP': 250, 'NT': {}}
	status = {'mode': 'Hold', 'units': 'F', 'outpins': {'fan': True}, 'cycle_ratio': 0.35, 'fan_duty': 100}
	b = make_backend(in_data, status)
	b.poll()
	assert b.augerDuty == 35
	assert b.fanDuty == 100


def test_food_probe_count_reflects_config():
	# PROBE_INFO has one food probe.
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}})
	assert b.foodProbeCount == 1
	none = PiFireBackend(lambda: (None, None), lambda c, d: None, {'primary': {'name': 'Grill'}, 'food': [], 'aux': []})
	assert none.foodProbeCount == 0


def test_accent_theme_updates_live_and_throttles():
	state = {'accent': 'Ember'}
	b = PiFireBackend(
		lambda: ({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, {'mode': 'Stop', 'units': 'F', 'outpins': {}}),
		lambda c, d: None,
		PROBE_INFO,
		accent_fn=lambda: state['accent'],
	)
	clock = {'t': 1000.0}
	b._now = lambda: clock['t']
	events = []
	b.accentThemeChanged.connect(lambda: events.append(b.accentTheme))
	b.poll()
	assert b.accentTheme == 'Ember'
	state['accent'] = 'Ice'
	clock['t'] = 1000.5
	b.poll()
	assert b.accentTheme == 'Ember'
	clock['t'] = 1002.0
	b.poll()
	assert b.accentTheme == 'Ice'
	assert 'Ice' in events


def test_cook_elapsed_text_counts_up_else_zero():
	status = {'mode': 'Smoke', 'units': 'F', 'outpins': {}, 'startup_timestamp': 1000.0}
	b = make_backend({'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}, status)
	b._now = lambda: 1000.0 + 125  # 2:05 elapsed
	b.poll()
	assert b.cookElapsedText == '02:05'
	b._fetch_fn = lambda: (
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		{'mode': 'Stop', 'units': 'F', 'outpins': {}, 'startup_timestamp': 0},
	)
	b.poll()
	assert b.cookElapsedText == '00:00'
