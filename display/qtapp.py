"""
*****************************************
PiFire Qt Quick Display — Application Host
*****************************************

 Description: Builds the QGuiApplication and QQmlApplicationEngine inside the
 spawned display child process, wires the PiFireBackend to PiFire's Redis data
 and command layer, and drives a poll timer. Never import or instantiate this
 from the control.py parent process.

*****************************************
"""

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from common.common import read_generic_json
from common.datastore_accessors import read_current, read_status
from display.qtbackend import PiFireBackend
from display.screen_power import ScreenPowerController

QML_DIR = Path(__file__).parent / "qml"


def _fetch():
    return read_current(), read_status()


def build_engine(config, backend):
    """Create and load the QML engine. Headless-testable (offscreen)."""
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    ctx = engine.rootContext()
    ctx.setContextProperty("backend", backend)
    meta = read_generic_json(config["display_data_filename"]).get("metadata", {})
    ctx.setContextProperty("screenWidth", meta.get("screen_width", 1280))
    ctx.setContextProperty("screenHeight", meta.get("screen_height", 720))
    rotation = int(config.get("rotation", 0) or 0)
    if rotation not in (90, 180, 270):
        rotation = 0
    ctx.setContextProperty("screenRotation", rotation)
    ctx.setContextProperty("splashImage", meta.get("splash_image", ""))
    ctx.setContextProperty("splashDelay", meta.get("splash_delay", 500))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    return engine


def build_backend(config):
    """Construct the backend wired to the framework's data + command layer."""
    from display.qtquick_flex import Display
    from common.datastore_accessors import read_settings_store
    from common.common import display_sleep_timeout

    def _accent_fn():
        try:
            s = read_settings_store()
            module = s["modules"]["display"]
            return s["display"]["config"][module].get("accent_theme", "Ember")
        except Exception:
            return "Ember"

    def _timeout_fn():
        try:
            return display_sleep_timeout(read_settings_store())
        except Exception:
            return 300

    dispatcher = Display.for_dispatch(config, config.get("units", "F"))
    backend = PiFireBackend(
        _fetch, dispatcher._dispatch_command, config.get("probe_info", {}), accent_fn=_accent_fn, timeout_fn=_timeout_fn
    )
    backend._accent_theme = config.get("accent_theme", "Ember")
    backend._ip_address = config.get("ip_address", "") or backend.ipAddress
    return backend


class DummyBacklight:
    """No-op backlight used when no hardware backlight is present."""

    def __init__(self):
        self.brightness = 100
        self.power = True
        self.fade_duration = 1


def _make_backlight():
    """Return a backlight controller: real on hardware, dummy otherwise."""
    from pathlib import Path

    from common.system import is_real_hardware

    if is_real_hardware() and Path("/sys/class/backlight/").exists():
        try:
            from rpi_backlight import Backlight

            return Backlight()
        except Exception:
            return DummyBacklight()
    return DummyBacklight()


def bind_backend_power(backend, controller):
    """Drive the screen-power controller from the backend's asleep signal.
    Applies once immediately and returns the apply callable."""

    def _apply():
        controller.set_output_power(not backend.asleep)

    backend.asleepChanged.connect(_apply)
    _apply()
    return _apply


def run_app(config, units):
    config = dict(config)
    config.setdefault("units", units)
    app = QGuiApplication.instance() or QGuiApplication([])
    backend = build_backend(config)
    engine = build_engine(config, backend)
    if not engine.rootObjects():
        raise RuntimeError("Failed to load Main.qml")

    # Request fullscreen from the client so the toplevel is borderless under any
    # compositor (sway/labwc/weston) without relying on a kiosk shell to force
    # it. A fullscreen xdg-toplevel is decoration-free by protocol. Done here in
    # the device path (not in Main.qml) so the QML stays testable at a fixed
    # logical size.
    window = engine.rootObjects()[0]
    if hasattr(window, "showFullScreen"):
        window.showFullScreen()

    # Backlight sleep/wake driven by the backend's idle state machine.
    backlight = _make_backlight()

    def _apply_backlight():
        try:
            if backend.asleep:
                backlight.brightness = 0
                backlight.power = False
            else:
                backlight.power = True
                backlight.brightness = 100
        except Exception:
            pass

    backend.asleepChanged.connect(_apply_backlight)
    _apply_backlight()

    screen_power = ScreenPowerController("wayland")
    bind_backend_power(backend, screen_power)

    meta = read_generic_json(config["display_data_filename"]).get("metadata", {})
    framerate = meta.get("framerate", 20)
    timer = QTimer()
    timer.timeout.connect(backend.poll)
    timer.start(int(1000 / max(framerate, 1)))
    backend.poll()
    app.exec()
