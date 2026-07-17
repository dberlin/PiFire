from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

from display.qtbackend import PiFireBackend

QML_DIR = Path("display/qml").resolve()


def _app():
    return QGuiApplication.instance() or QGuiApplication([])


def _engine():
    _app()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        {"primary": {"name": "Grill", "max_temp": 600}, "food": [], "aux": []},
    )
    b.poll()
    engine.rootContext().setContextProperty("backend", b)
    engine._backend = b  # keep alive
    return engine


def _create(engine, qml_file):
    comp = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / qml_file)))
    obj = comp.create()
    assert obj is not None, comp.errorString()
    obj.setParent(engine)
    return obj


def test_press_overlay_defaults():
    engine = _engine()
    fx = _create(engine, "components/PressOverlay.qml")
    assert fx.property("pressed") is False
    # Sits above sibling content so the press reads on filled cards.
    assert fx.property("z") == 100
    # Idle overlay is fully transparent.
    assert fx.property("opacity") == 0


# Every component that had press feedback wired in — loading each guards
# against a QML syntax slip in the edits.
@pytest.mark.parametrize(
    "qml_file",
    [
        "components/ControlPanel.qml",
        "components/MenuButton.qml",
        "components/ProbeCard.qml",
        "components/SystemCard.qml",
        "components/HopperCard.qml",
        "components/HeaderBar.qml",
        "components/Gauge.qml",
        "components/CompactGauge.qml",
        "components/ModeBar.qml",
        "components/SmokePlusControl.qml",
        "components/PModeControl.qml",
    ],
)
def test_component_loads(qml_file):
    engine = _engine()
    obj = _create(engine, qml_file)
    assert obj is not None
