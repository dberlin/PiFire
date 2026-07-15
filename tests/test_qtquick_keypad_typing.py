"""The on-screen Keypad (display/qml/components/Keypad.qml) opens showing the
current/default setpoint (e.g. HoldInput.qml seeds it with
Math.round(backend.primarySetpoint) || 200). The first digit the user
presses used to append onto that starting value instead of replacing it --
typing "4" over a displayed "200" produced 200*10+4, clamped to 999, instead
of just "4". Keypad.qml now tracks whether the user has typed a digit yet
(`typing`) so the first press overwrites the starting value.
"""

import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

REPO = Path(__file__).resolve().parents[1]
QML_DIR = REPO / 'display' / 'qml'


def _keypad(initial_value):
	QGuiApplication.instance() or QGuiApplication([])
	engine = QQmlApplicationEngine()
	engine.addImportPath(str(QML_DIR))
	qml = 'import QtQuick\nimport "components"\nKeypad { width: 1280; height: 720; value: %d }' % initial_value
	comp = QQmlComponent(engine)
	comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / '_probe.qml')))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	obj._engine = engine
	return obj


def test_first_digit_overwrites_starting_value():
	kp = _keypad(200)
	kp.pressDigit(4)
	assert kp.property('value') == 4


def test_second_digit_appends_onto_the_first():
	kp = _keypad(200)
	kp.pressDigit(4)
	kp.pressDigit(2)
	assert kp.property('value') == 42


def test_clear_then_digit_starts_fresh():
	kp = _keypad(200)
	kp.pressClear()
	kp.pressDigit(7)
	assert kp.property('value') == 7


def test_value_clamps_at_999_once_typing():
	kp = _keypad(0)
	for d in (9, 9, 9, 9):
		kp.pressDigit(d)
	assert kp.property('value') == 999
