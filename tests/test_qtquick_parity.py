"""
Parity guard: the pygame dsi_1280x720t layout is the source of truth. This test
parses every action out of that JSON and asserts the Qt Quick display has a
matching capability — command dispatch, menu, input screen, or control-panel
entry. If pygame later gains an action the QT side lacks, this fails.

Also asserts the backend exposes the status-driven parity surface, and that the
dynamic control panel matches pygame's per-mode button sets (evaluated against
the real Menus.js via a small QML harness so there is no Python copy to drift).
"""

import json
import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

import display.qtquick_flex as qmod
from display.qtbackend import PiFireBackend

REPO = Path(__file__).resolve().parents[1]
QML_DIR = REPO / 'display' / 'qml'
PYGAME_JSON = REPO / 'display' / 'dsi_1280x720t.json'


# --------------------------------------------------------------------------
# Parse the pygame layout for every action string.
# --------------------------------------------------------------------------
def _pygame_actions():
	data = json.loads(PYGAME_JSON.read_text())['profile_1']
	cmds, menus, inputs = set(), set(), set()

	def collect(button_list):
		for a in button_list or []:
			if a.startswith('cmd_'):
				cmds.add(a)
			elif a.startswith('menu_'):
				menus.add(a[len('menu_') :])
			elif a.startswith('input_'):
				inputs.add(a[len('input_') :])

	for obj in data['dash']:
		collect(obj.get('button_list'))
	for menu in data['menus'].values():
		collect(menu.get('button_list'))
	for name, inp in data['input'].items():
		inputs.add(name)
		cmd = inp.get('command', '')
		if cmd.startswith('cmd_'):
			cmds.add(cmd)
	return cmds, menus, inputs


PYGAME_CMDS, PYGAME_MENUS, PYGAME_INPUTS = _pygame_actions()


# --------------------------------------------------------------------------
# Command coverage: every cmd_* is handled by Display._dispatch_command.
# --------------------------------------------------------------------------
def _dispatch_probe(monkeypatch):
	effects = []
	monkeypatch.setattr(
		qmod, 'write_control', lambda data, kind=None, origin=None: effects.append(('write_control', data))
	)
	monkeypatch.setattr(qmod, 'read_status', lambda: {'s_plus': False})
	monkeypatch.setattr(qmod, 'read_control', lambda: {'notify_data': [], 'recipe': {'step_data': {}}})
	monkeypatch.setattr(qmod, 'is_real_hardware', lambda: False)
	# _command_handler (inherited) uses names in base_flex's namespace.
	import display.base_flex as bf

	monkeypatch.setattr(
		bf, 'write_control', lambda data, kind=None, origin=None: effects.append(('write_control', data))
	)
	monkeypatch.setattr(bf, 'read_control', lambda: {'notify_data': [], 'recipe': {'step_data': {}}, 'updated': False})
	monkeypatch.setattr(bf, 'read_settings', lambda: {'cycle_data': {}})
	monkeypatch.setattr(bf, 'write_settings', lambda s: effects.append(('write_settings', s)))
	monkeypatch.setattr(bf, 'read_status', lambda: {'s_plus': False})

	class _Resp:
		pass

	monkeypatch.setattr(bf.requests, 'get', lambda url: effects.append(('requests.get', url)))
	disp = qmod.Display.for_dispatch({'display_data_filename': './display/qtquick_dsi_1280x720t.json'}, 'F')
	return disp, effects


@pytest.mark.parametrize('cmd', sorted(PYGAME_CMDS))
def test_every_pygame_command_is_handled(monkeypatch, cmd):
	disp, effects = _dispatch_probe(monkeypatch)
	# A representative value; hold/pmode/prime use it, others ignore it.
	disp._dispatch_command(cmd, 100)
	assert effects, f'{cmd} produced no control write / API call — unhandled in QT'


# --------------------------------------------------------------------------
# QML JS harness — evaluate the real Menus.js in an offscreen engine.
# --------------------------------------------------------------------------
def _engine():
	QGuiApplication.instance() or QGuiApplication([])
	engine = QQmlApplicationEngine()
	engine.addImportPath(str(QML_DIR))
	return engine


def _eval_js(engine, expr):
	qml = 'import QtQuick\nimport "Menus.js" as Menus\nItem { property string result: JSON.stringify(%s) }' % expr
	comp = QQmlComponent(engine)
	comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / '_probe.qml')))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	return json.loads(obj.property('result'))


def test_every_pygame_menu_resolves_in_qt():
	engine = _engine()
	for name in PYGAME_MENUS:
		if name == 'close':
			continue  # handled by Actions.js routing, not a screen
		if name == 'qrcode':
			assert (QML_DIR / 'screens' / 'QrCodeScreen.qml').exists()
			continue
		assert _eval_js(engine, 'Menus.hasMenu("%s")' % name), f'menu {name} missing in Menus.js'


def test_every_pygame_input_has_a_qt_screen():
	for name in PYGAME_INPUTS:
		screen = QML_DIR / 'screens' / f'{name.capitalize()}Input.qml'
		assert screen.exists(), f'input screen for {name} missing ({screen.name})'


# --------------------------------------------------------------------------
# Control-panel parity: per-mode button sets match pygame _update_dash_objects,
# except Smoke/Hold: the 1280x720 redesigned dash uses a Set Temp + mode-switch
# set on both stacks (pygame button_row will match it), so those two entries
# no longer mirror the shared pygame control_panel.
# --------------------------------------------------------------------------
EXPECTED_CONTROL_PANEL = {
	('Stop', False, False): ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop'],
	('Prime', False, False): ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop'],
	('Monitor', False, False): ['menu_prime', 'menu_startup', 'cmd_monitor', 'cmd_stop'],
	('Startup', False, False): ['cmd_startup', 'cmd_smoke', 'input_hold', 'cmd_stop'],
	('Reignite', False, False): ['cmd_startup', 'cmd_smoke', 'input_hold', 'cmd_stop'],
	('Smoke', False, False): ['input_hold', 'input_hold', 'cmd_stop', 'cmd_shutdown'],
	('Hold', False, False): ['input_hold', 'cmd_smoke', 'cmd_stop', 'cmd_shutdown'],
	('Shutdown', False, False): ['cmd_smoke', 'input_hold', 'cmd_stop', 'cmd_shutdown'],
	('Hold', True, False): ['cmd_next_step', 'cmd_none', 'cmd_stop', 'cmd_shutdown'],
}


@pytest.mark.parametrize('key,expected', list(EXPECTED_CONTROL_PANEL.items()))
def test_control_panel_matches_pygame_per_mode(key, expected):
	engine = _engine()
	mode, recipe, paused = key
	items = _eval_js(
		engine, 'Menus.controlPanelForMode("%s", %s, %s)' % (mode, str(recipe).lower(), str(paused).lower())
	)
	assert [i['action'] for i in items] == expected


def test_recipe_paused_marks_next_active():
	engine = _engine()
	items = _eval_js(engine, 'Menus.controlPanelForMode("Hold", true, true)')
	nxt = next(i for i in items if i['action'] == 'cmd_next_step')
	assert nxt.get('active') is True


# --------------------------------------------------------------------------
# Status-behavior surface: backend exposes the parity properties/slots.
# --------------------------------------------------------------------------
def test_backend_exposes_parity_surface():
	b = PiFireBackend(lambda: (None, None), lambda c, d: None, {'primary': {'name': 'G'}, 'food': [], 'aux': []})
	meta = b.metaObject()
	for prop in ['modeText', 'primaryNotifyTarget', 'timerLabel', 'pModeActive', 'asleep']:
		assert meta.indexOfProperty(prop) >= 0, f'backend missing property {prop}'
	assert meta.indexOfMethod('registerInteraction()') >= 0
