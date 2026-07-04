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

from common import read_current, read_status, read_generic_json
from display.qtbackend import PiFireBackend

QML_DIR = Path(__file__).parent / 'qml'


def _fetch():
	return read_current(), read_status()


def build_engine(config, backend):
	"""Create and load the QML engine. Headless-testable (offscreen)."""
	engine = QQmlApplicationEngine()
	engine.addImportPath(str(QML_DIR))
	ctx = engine.rootContext()
	ctx.setContextProperty('backend', backend)
	meta = read_generic_json(config['display_data_filename']).get('metadata', {})
	ctx.setContextProperty('screenWidth', meta.get('screen_width', 1280))
	ctx.setContextProperty('screenHeight', meta.get('screen_height', 720))
	ctx.setContextProperty('splashImage', meta.get('splash_image', ''))
	ctx.setContextProperty('splashDelay', meta.get('splash_delay', 500))
	engine.load(QUrl.fromLocalFile(str(QML_DIR / 'Main.qml')))
	return engine


def build_backend(config):
	"""Construct the backend wired to the framework's data + command layer."""
	from display.qtquick_flex import Display

	dispatcher = Display.for_dispatch(config, config.get('units', 'F'))
	backend = PiFireBackend(_fetch, dispatcher._dispatch_command, config.get('probe_info', {}))
	backend._ip_address = config.get('ip_address', '') or backend.ipAddress
	return backend


def run_app(config, units):
	config = dict(config)
	config.setdefault('units', units)
	app = QGuiApplication.instance() or QGuiApplication([])
	backend = build_backend(config)
	engine = build_engine(config, backend)
	if not engine.rootObjects():
		raise RuntimeError('Failed to load Main.qml')

	meta = read_generic_json(config['display_data_filename']).get('metadata', {})
	framerate = meta.get('framerate', 20)
	timer = QTimer()
	timer.timeout.connect(backend.poll)
	timer.start(int(1000 / max(framerate, 1)))
	backend.poll()
	app.exec()
