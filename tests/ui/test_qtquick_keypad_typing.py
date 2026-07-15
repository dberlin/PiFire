"""The on-screen Keypad (display/qml/components/Keypad.qml) opens showing the
current/default setpoint (e.g. HoldInput.qml seeds it with
Math.round(backend.primarySetpoint) || 200). The first digit the user
presses used to append onto that starting value instead of replacing it --
typing "4" over a displayed "200" produced 200*10+4, clamped to 999, instead
of just "4". Keypad.qml now tracks whether the user has typed a digit yet
(`typing`) so the first press overwrites the starting value.
"""

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent

from tests.conftest import QML_DIR


def _keypad(engine, initial_value):
    qml = 'import QtQuick\nimport "components"\nKeypad { width: 1280; height: 720; value: %d }' % initial_value
    comp = QQmlComponent(engine)
    comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / "_probe.qml")))
    obj = comp.create()
    assert obj is not None, comp.errorString()
    obj.setParent(engine)
    obj._engine = engine
    return obj


def test_first_digit_overwrites_starting_value(qml_engine):
    kp = _keypad(qml_engine, 200)
    kp.pressDigit(4)
    assert kp.property("value") == 4


def test_second_digit_appends_onto_the_first(qml_engine):
    kp = _keypad(qml_engine, 200)
    kp.pressDigit(4)
    kp.pressDigit(2)
    assert kp.property("value") == 42


def test_clear_then_digit_starts_fresh(qml_engine):
    kp = _keypad(qml_engine, 200)
    kp.pressClear()
    kp.pressDigit(7)
    assert kp.property("value") == 7


def test_value_clamps_at_999_once_typing(qml_engine):
    kp = _keypad(qml_engine, 0)
    for d in (9, 9, 9, 9):
        kp.pressDigit(d)
    assert kp.property("value") == 999
