import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QUrl
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
