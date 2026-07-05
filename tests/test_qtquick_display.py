import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

import display.qtquick_flex as mod


@pytest.fixture
def no_spawn(monkeypatch):
	# Prevent launching a real Qt child process during unit tests.
	monkeypatch.setattr(mod.Display, '_start_qt_process', lambda self: None)


def make_display(monkeypatch):
	sent = []
	monkeypatch.setattr(mod, 'write_control', lambda data, kind=None, origin=None: sent.append(data))
	cfg = {
		'display_data_filename': './display/qtquick_dsi_1280x720t.json',
		'probe_info': {'primary': {'name': 'Grill', 'max_temp': 600}, 'food': [], 'aux': []},
	}
	d = mod.Display(dev_pins={}, config=cfg)
	d._sent = sent
	return d


def test_module_reexports_display():
	import display.qtquick_dsi_1280x720t as wrapper

	assert wrapper.Display is mod.Display


def test_dispatch_stop_writes_stop_without_init_framework(monkeypatch, no_spawn):
	d = make_display(monkeypatch)
	called = {'init_framework': False}
	monkeypatch.setattr(d, '_init_framework', lambda: called.__setitem__('init_framework', True))
	d._dispatch_command('cmd_stop', 0)
	assert {'updated': True, 'mode': 'Stop'} in d._sent
	assert called['init_framework'] is False


def test_dispatch_hold_uses_command_data(monkeypatch, no_spawn):
	d = make_display(monkeypatch)
	d._dispatch_command('cmd_hold', 275)
	assert any(x.get('primary_setpoint') == 275 and x.get('mode') == 'Hold' for x in d._sent)


def test_dispatch_primestartup_does_not_also_send_startup(monkeypatch, no_spawn):
	d = make_display(monkeypatch)
	d._dispatch_command('cmd_primestartup', 25)
	assert {'updated': True, 'mode': 'Prime', 'prime_amount': 25, 'next_mode': 'Startup'} in d._sent
	# The 'startup' substring branch must NOT have fired a bare Startup command.
	assert {'updated': True, 'mode': 'Startup'} not in d._sent


def test_dispatch_delegates_pmode_to_command_handler(monkeypatch, no_spawn):
	d = make_display(monkeypatch)
	handled = {}
	monkeypatch.setattr(d, '_command_handler', lambda: handled.update(cmd=d.command, data=d.command_data))
	d._dispatch_command('cmd_pmode', 4)
	assert handled == {'cmd': 'cmd_pmode', 'data': 4}


def test_public_stubs_are_noops(monkeypatch, no_spawn):
	d = make_display(monkeypatch)
	assert d.display_status({}, {}) is None
	assert d.display_text('X') is None
	assert d.clear_display() is None
