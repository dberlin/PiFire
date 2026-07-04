import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from display.qtbackend import PiFireBackend

PROBE_INFO = {
	'primary': {'name': 'Grill', 'max_temp': 600},
	'food': [{'name': 'Probe 1', 'max_temp': 300}],
	'aux': [],
}


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
	in_data = {
		'P': {'Grill': 225},
		'F': {'Probe 1': 145},
		'AUX': {},
		'PSP': 250,
		'NT': {'Grill': 0, 'Probe 1': 0},
	}
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
	b = make_backend(
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		{'mode': 'Stop', 'units': 'F', 'outpins': {}},
	)
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
	status = {
		'mode': 'Startup',
		'units': 'F',
		'outpins': {},
		'start_time': 1000.0,
		'start_duration': 240,
	}
	in_data = {'P': {'Grill': 100}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}
	b = make_backend(in_data, status)
	b._now = lambda: 1000.0 + 45  # 45s elapsed -> 195s -> 03:15
	b.poll()
	assert b.timerText == '03:15'


def test_food_probe_model_reflects_current_data():
	info = {
		'primary': {'name': 'Grill', 'max_temp': 600},
		'food': [
			{'name': 'Probe 1', 'max_temp': 300},
			{'name': 'Probe 2', 'max_temp': 300},
		],
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


def test_nav_slots_emit_navevent():
	b = make_backend(
		{'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		{'mode': 'Stop', 'units': 'F', 'outpins': {}},
	)
	events = []
	b.navEvent.connect(lambda e: events.append(e))
	b.navUp()
	b.navDown()
	b.navEnter()
	assert events == ['UP', 'DOWN', 'ENTER']
