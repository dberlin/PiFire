from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

import display.qtapp as qtapp
from display.qtbackend import PiFireBackend

QML_DIR = Path("display/qml").resolve()


def _app():
    return QGuiApplication.instance() or QGuiApplication([])


def _stub_backend():
    in_data = {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}
    status = {"mode": "Stop", "units": "F", "outpins": {}}
    probe_info = {"primary": {"name": "Grill", "max_temp": 600}, "food": [], "aux": []}
    b = PiFireBackend(lambda: (in_data, status), lambda c, d: None, probe_info)
    b.poll()
    return b


def _rotor(rotation):
    """Load Main.qml with the given rotation and return (root, rotor item)."""
    _app()
    config = {
        "display_data_filename": "./display/qtquick_dsi_1280x720t.json",
        "rotation": rotation,
    }
    engine = qtapp.build_engine(config, _stub_backend())
    roots = engine.rootObjects()
    assert roots, "Main.qml failed to load (see QML errors above)"
    root = roots[0]
    rotor = root.findChild(QObject, "rotor")
    assert rotor is not None, "rotor item not found in Main.qml"
    # Keep engine alive for the duration of the caller's assertions.
    rotor._engine = engine
    return root, rotor


def test_rotation_zero_is_unrotated_native_size():
    root, rotor = _rotor(0)
    assert rotor.property("rotation") == 0
    assert rotor.property("width") == root.property("width")
    assert rotor.property("height") == root.property("height")


def test_rotation_180_keeps_native_size():
    root, rotor = _rotor(180)
    assert rotor.property("rotation") == 180
    assert rotor.property("width") == root.property("width")
    assert rotor.property("height") == root.property("height")


def test_rotation_90_swaps_dimensions():
    root, rotor = _rotor(90)
    assert rotor.property("rotation") == 90
    # rotor takes the portrait-logical (swapped) size and rotates to fill the
    # native landscape framebuffer.
    assert rotor.property("width") == root.property("height")
    assert rotor.property("height") == root.property("width")


def test_rotation_270_swaps_dimensions():
    root, rotor = _rotor(270)
    assert rotor.property("rotation") == 270
    assert rotor.property("width") == root.property("height")
    assert rotor.property("height") == root.property("width")


def test_invalid_rotation_normalizes_to_zero():
    root, rotor = _rotor(45)
    assert rotor.property("rotation") == 0
    assert rotor.property("width") == root.property("width")
    assert rotor.property("height") == root.property("height")
