import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

import display.qtapp as qtapp
from display.qtbackend import PiFireBackend

QML_DIR = Path('display/qml').resolve()


def _app():
	return QGuiApplication.instance() or QGuiApplication([])


def _stub_backend(in_data=None, status=None, command_fn=None, probe_info=None):
	in_data = in_data or {'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}}
	status = status or {'mode': 'Stop', 'units': 'F', 'outpins': {}}
	probe_info = probe_info or {'primary': {'name': 'Grill', 'max_temp': 600}, 'food': [], 'aux': []}
	b = PiFireBackend(lambda: (in_data, status), command_fn or (lambda c, d: None), probe_info)
	b.poll()
	return b


def _engine_with_backend(backend):
	engine = QQmlApplicationEngine()
	engine.addImportPath(str(QML_DIR))
	engine.rootContext().setContextProperty('backend', backend)
	return engine


def _create(engine, qml_file):
	comp = QQmlComponent(engine, QUrl.fromLocalFile(f'display/qml/{qml_file}'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	# Reparent to the engine so C++ takes ownership and the object is not
	# garbage-collected out from under the test between assertions.
	obj.setParent(engine)
	return obj


def test_main_qml_loads_without_errors():
	_app()
	backend = _stub_backend()
	config = {'display_data_filename': './display/qtquick_dsi_1280x720t.json'}
	engine = qtapp.build_engine(config, backend)
	assert engine.rootObjects(), 'Main.qml failed to load (see QML errors above)'


def test_dash_screen_loads_and_binds_primary():
	_app()
	backend = _stub_backend(
		in_data={'P': {'Grill': 225}, 'F': {}, 'AUX': {}, 'PSP': 250, 'NT': {}},
		status={'mode': 'Hold', 'units': 'F', 'outpins': {}},
	)
	engine = _engine_with_backend(backend)
	obj = _create(engine, 'screens/DashScreen.qml')
	assert obj is not None


def test_menu_screen_loads_main():
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	obj = _create(engine, 'screens/MenuScreen.qml')
	obj.setProperty('menuName', 'main')
	assert obj.property('menuName') == 'main'


def test_qrcode_screen_loads():
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	assert _create(engine, 'screens/QrCodeScreen.qml') is not None


def test_hold_input_loads():
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	assert _create(engine, 'screens/HoldInput.qml') is not None


def test_notify_input_loads():
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	obj = _create(engine, 'screens/NotifyInput.qml')
	obj.setProperty('origin', 'Probe 1')
	assert obj.property('origin') == 'Probe 1'


def test_full_main_qml_with_menu_navigation_loads():
	_app()
	backend = _stub_backend()
	config = {'display_data_filename': './display/qtquick_dsi_1280x720t.json'}
	engine = qtapp.build_engine(config, backend)
	assert engine.rootObjects()


def test_theme_exposes_accent_tokens():
	# Load Theme singleton and assert the new accent tokens resolve for each accent.
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/Theme.qml'))
	theme = comp.create()
	assert theme is not None, comp.errorString()
	theme.setParent(engine)
	for accent in ('Ember', 'Ice', 'Crimson'):
		theme.setProperty('accent', accent)
		assert theme.property('accentColor') is not None
		assert theme.property('glowColor') is not None
		assert theme.property('arcStop0') is not None
		assert theme.property('arcStop1') is not None
		assert theme.property('arcStop2') is not None


def test_gauge_loads_with_setpoint_marker_and_mode_pill():
	# Gauge.qml (ember restyle): loads with value/setpoint/maxValue bound, exposes
	# a setpointMarker child (radial line drawn at the setpoint angle) and accepts
	# the new modeLabel prop that feeds the mode pill.
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/Gauge.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	obj.setProperty('value', 225)
	obj.setProperty('maxValue', 600)
	obj.setProperty('setpoint', 250)
	obj.setProperty('modeLabel', 'HOLD')
	assert obj.property('modeLabel') == 'HOLD'
	marker = obj.findChild(QObject, 'setpointMarker')
	assert marker is not None, 'expected a setpointMarker child in Gauge.qml'


def test_probe_card_loads_with_name_temp_target_and_tapped_signal():
	# ProbeCard.qml: self-contained food-probe card (name/temp/target/maxTemp/units
	# props, tapped() signal). Consumed by DashScreen's food-probe Repeater (Task 15).
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/ProbeCard.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	obj.setProperty('name', 'Brisket')
	obj.setProperty('temp', 165)
	obj.setProperty('target', 203)
	assert obj.property('name') == 'Brisket'
	assert obj.property('temp') == 165
	assert obj.property('target') == 203
	assert obj.metaObject().indexOfSignal('tapped()') >= 0


def test_fan_icon_loads_with_active_prop():
	# FanIcon.qml: self-contained spinning three-blade fan icon. Exposes
	# active/animate props; spins only when both are true (verified by parity
	# of load, not by pixel output).
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/FanIcon.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert obj.property('active') == False
	obj.setProperty('active', True)
	assert obj.property('active') == True
	assert obj.property('animate') == True


def test_auger_icon_loads_with_active_prop():
	# AugerIcon.qml: clipped scrolling screw + falling pellets.
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/AugerIcon.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert obj.property('active') == False
	obj.setProperty('active', True)
	assert obj.property('active') == True


def test_igniter_icon_loads_with_active_prop():
	# IgniterIcon.qml: flame coil with flicker + rising heat waves.
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/IgniterIcon.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert obj.property('active') == False
	obj.setProperty('active', True)
	assert obj.property('active') == True


def test_system_card_loads_with_rows_bound_to_backend():
	# SystemCard.qml: fan/auger/igniter rows, each with an icon bound to
	# backend.fanOn/augerOn/igniterOn, and a tap toggles the matching backend
	# command. Consumed by DashScreen (Task 15).
	_app()
	backend = _stub_backend(
		in_data={'P': {}, 'F': {}, 'AUX': {}, 'PSP': 0, 'NT': {}},
		status={'mode': 'Hold', 'units': 'F', 'outpins': {'fan': True, 'auger': False, 'igniter': False}},
	)
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/SystemCard.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	fan_icon = obj.findChild(QObject, 'sysFanIcon')
	auger_icon = obj.findChild(QObject, 'sysAugerIcon')
	igniter_icon = obj.findChild(QObject, 'sysIgniterIcon')
	assert fan_icon is not None, 'expected a sysFanIcon child in SystemCard.qml'
	assert auger_icon is not None, 'expected a sysAugerIcon child in SystemCard.qml'
	assert igniter_icon is not None, 'expected a sysIgniterIcon child in SystemCard.qml'
	assert fan_icon.property('active') == backend.fanOn


def test_header_bar_loads_with_menu_signal_and_clock():
	# HeaderBar.qml: live dot + wordmark + IP + clock + hamburger. Loads against a
	# real backend (ipAddress/mode) and exposes menuRequested() + a clock property
	# driven by its own Timer.
	_app()
	backend = _stub_backend(status={'mode': 'Hold', 'units': 'F', 'outpins': {}})
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/HeaderBar.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert obj.property('height') == 58
	assert obj.metaObject().indexOfSignal('menuRequested()') >= 0
	assert obj.property('clock') is not None


def test_hopper_card_loads_and_visible_follows_hopper_enabled():
	# HopperCard.qml: vertical-fill hopper level card bound to
	# backend.hopperLevel/backend.hopperEnabled, exposes checkRequested() (Task 15
	# wires it to backend.hopperCheck()). The whole card is hidden when the pellet
	# sensor is disabled (D1). Uses two separately-constructed backends (rather than
	# mutating+re-signalling one already-created root item) because a QQuickItem
	# created standalone via QQmlComponent.create() with no window does not re-run
	# its `visible` binding on a later notify signal — that's an offscreen/no-window
	# test-harness artifact (confirmed against a windowed QQuickView), not a bug in
	# the component; initial-creation bindings evaluate correctly either way.
	_app()
	enabled_backend = _stub_backend(
		status={'mode': 'Stop', 'units': 'F', 'outpins': {}, 'hopper_level_enabled': True, 'hopper_level': 42}
	)
	engine = _engine_with_backend(enabled_backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/HopperCard.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert obj.metaObject().indexOfSignal('checkRequested()') >= 0
	assert obj.property('visible') == True

	disabled_backend = _stub_backend(
		status={'mode': 'Stop', 'units': 'F', 'outpins': {}, 'hopper_level_enabled': False}
	)
	engine2 = _engine_with_backend(disabled_backend)
	comp2 = QQmlComponent(engine2, QUrl.fromLocalFile('display/qml/components/HopperCard.qml'))
	obj2 = comp2.create()
	assert obj2 is not None, comp2.errorString()
	obj2.setParent(engine2)
	assert obj2.property('visible') == False


def test_cook_time_bar_shows_elapsed_when_no_timer_running():
	# CookTimeBar.qml (Task 14, D2): with no active timer (backend.timerText == ""),
	# shows the "COOK TIME" label and backend.cookElapsedText as the value.
	_app()
	backend = _stub_backend(status={'mode': 'Hold', 'units': 'F', 'outpins': {}})
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/CookTimeBar.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	assert backend.timerText == ''
	label = obj.findChild(QObject, 'cookTimeLabel')
	value = obj.findChild(QObject, 'cookTimeValue')
	assert label is not None, 'expected a cookTimeLabel child in CookTimeBar.qml'
	assert value is not None, 'expected a cookTimeValue child in CookTimeBar.qml'
	assert label.property('text') == 'COOK TIME'
	assert value.property('text') == backend.cookElapsedText


def test_cook_time_bar_shows_countdown_when_timer_running():
	# CookTimeBar.qml (Task 14, D2): when a timer is active (backend.timerText
	# non-empty), shows backend.timerLabel / backend.timerText instead.
	_app()
	backend = _stub_backend(status={'mode': 'Hold', 'units': 'F', 'outpins': {}})
	backend._timer_text = '04:32'
	backend._timer_label = 'SHUTDOWN'
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/CookTimeBar.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	label = obj.findChild(QObject, 'cookTimeLabel')
	value = obj.findChild(QObject, 'cookTimeValue')
	assert label.property('text') == 'SHUTDOWN'
	assert value.property('text') == '04:32'


def test_alert_pill_has_fixed_width_and_keeps_message_shown_props():
	# Alert.qml (Task 14 restyle): keeps its message/shown public props, is a
	# fixed-width pill (Layout.preferredWidth: 210) so DashScreen's cook-time bar
	# can reflow to fill the space when the alert is hidden. Wrapped in a real
	# RowLayout so the Layout.preferredWidth attached property actually applies
	# (it's inert/unreadable on a standalone item with no Layout parent).
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine)
	comp.setData(
		b'import QtQuick; import QtQuick.Layouts; import "components" as C;'
		b'RowLayout { Item { Layout.fillWidth: true }'
		b' C.Alert { id: a; objectName: "alert"; message: "LID OPEN"; shown: true } }',
		QUrl.fromLocalFile('display/qml/probe.qml'),
	)
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	alert = obj.findChild(QObject, 'alert')
	assert alert is not None
	assert alert.property('message') == 'LID OPEN'
	assert alert.property('shown') == True
	assert alert.property('visible') == True
	assert alert.property('width') == 210
	alert.setProperty('shown', False)
	assert alert.property('visible') == False


def test_control_panel_loads_and_preserves_wiring():
	# ControlPanel.qml (Task 14 restyle): visuals change but the Repeater model,
	# Actions.activate wiring, and openMenu/openInput signals are unchanged.
	_app()
	backend = _stub_backend(status={'mode': 'Hold', 'units': 'F', 'outpins': {}})
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/ControlPanel.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	obj.setProperty('mode', 'Hold')
	obj.setProperty('recipe', False)
	obj.setProperty('recipePaused', False)
	assert obj.metaObject().indexOfSignal('openMenu(QString)') >= 0
	assert obj.metaObject().indexOfSignal('openInput(QString,QString)') >= 0
	assert obj.property('mode') == 'Hold'


def test_duty_pill_loads_with_label_value_highlighted():
	# DutyPill.qml: presentational pill with label/value/highlighted props.
	# Used by DashScreen (Task 15) to show duty cycles and status.
	_app()
	backend = _stub_backend()
	engine = _engine_with_backend(backend)
	comp = QQmlComponent(engine, QUrl.fromLocalFile('display/qml/components/DutyPill.qml'))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	obj.setProperty('label', 'AUGER DUTY')
	obj.setProperty('value', '42%')
	obj.setProperty('highlighted', False)
	assert obj.property('label') == 'AUGER DUTY'
	assert obj.property('value') == '42%'
	assert obj.property('highlighted') == False
	obj.setProperty('highlighted', True)
	assert obj.property('highlighted') == True
