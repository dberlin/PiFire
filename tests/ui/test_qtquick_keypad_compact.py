"""On the 1024x600 QtQuick display profile (display/qtquick_dsi_1024x600t.json),
the on-screen numeric Keypad (display/qml/components/Keypad.qml, used by the
Hold Temperature and Notify Target input screens) used fixed pixel sizes that
totalled more than 600px tall, clipping the Cancel button off the bottom of
the screen. Keypad.qml now follows the same width-based `compact` convention
DashScreen.qml already uses (see test_qtquick_dashscreen_compact.py) to
shrink fonts/spacing/buttons so the whole keypad fits at 1024x600, while
leaving the larger 1280x720 profile's sizing unchanged.
"""

from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlComponent

from tests.conftest import QML_DIR


def _keypad(engine, width, height):
    qml = 'import QtQuick\nimport "components"\nKeypad { width: %d; height: %d }' % (width, height)
    comp = QQmlComponent(engine)
    comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / "_probe.qml")))
    obj = comp.create()
    assert obj is not None, comp.errorString()
    obj.setParent(engine)
    # Keep the engine alive as long as the caller holds `obj` -- otherwise
    # Python GC drops the last reference to `engine` when this function
    # returns, deleting the underlying C++ engine (and the Keypad it owns)
    # out from under the caller's assertions.
    obj._engine = engine
    return obj


def _column_height(keypad_obj):
    column = keypad_obj.findChild(QObject, "keypadColumn")
    assert column is not None, 'Keypad.qml ColumnLayout must keep objectName "keypadColumn"'
    return column.property("height")


def test_compact_true_at_1024x600(qml_engine):
    assert _keypad(qml_engine, 1024, 600).property("compact") is True


def test_compact_false_at_1280x720(qml_engine):
    assert _keypad(qml_engine, 1280, 720).property("compact") is False


def test_compact_keypad_fits_1024x600_screen(qml_engine):
    # The real bug: at full size, the keypad's content is taller than the
    # 600px-tall 1024x600 screen, clipping the Cancel button.
    assert _column_height(_keypad(qml_engine, 1024, 600)) <= 600


def test_noncompact_keypad_fits_1280x720_screen(qml_engine):
    assert _column_height(_keypad(qml_engine, 1280, 720)) <= 720


def test_compact_keypad_is_shorter_than_full_size(qml_engine):
    compact_height = _column_height(_keypad(qml_engine, 1024, 600))
    full_height = _column_height(_keypad(qml_engine, 1280, 720))
    assert compact_height < full_height
