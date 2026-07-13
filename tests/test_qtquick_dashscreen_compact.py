import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QUrl, Property
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

REPO = Path(__file__).resolve().parents[1]
QML_DIR = REPO / 'display' / 'qml'


class _StubBackend(QObject):
	# Minimal surface DashScreen reads at construction time.
	def __init__(self):
		super().__init__()

	mode = Property(str, lambda self: 'Stop', constant=True)
	foodProbeCount = Property(int, lambda self: 0, constant=True)
	foodProbes = Property('QVariantList', lambda self: [], constant=True)
	units = Property(str, lambda self: 'F', constant=True)
	primaryTemp = Property(float, lambda self: 0.0, constant=True)
	primarySetpoint = Property(float, lambda self: 0.0, constant=True)
	primaryNotifyTarget = Property(float, lambda self: 0.0, constant=True)
	primaryMax = Property(float, lambda self: 600.0, constant=True)
	primaryName = Property(str, lambda self: 'Grill', constant=True)
	modeText = Property(str, lambda self: 'STOP', constant=True)
	lidOpen = Property(bool, lambda self: False, constant=True)
	recipe = Property(bool, lambda self: False, constant=True)
	recipePaused = Property(bool, lambda self: False, constant=True)
	augerDuty = Property(int, lambda self: 0, constant=True)
	fanDuty = Property(int, lambda self: 0, constant=True)
	pMode = Property(int, lambda self: 2, constant=True)
	smokePlus = Property(bool, lambda self: False, constant=True)
	fanOn = Property(bool, lambda self: False, constant=True)


def _dash(width):
	QGuiApplication.instance() or QGuiApplication([])
	engine = QQmlApplicationEngine()
	engine.addImportPath(str(QML_DIR))
	backend = _StubBackend()
	engine.rootContext().setContextProperty('backend', backend)
	qml = 'import QtQuick\nimport "screens"\nDashScreen { width: %d; height: %d }' % (
		width,
		600 if width <= 1100 else 720,
	)
	comp = QQmlComponent(engine)
	comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / '_probe.qml')))
	obj = comp.create()
	assert obj is not None, comp.errorString()
	obj.setParent(engine)
	# Keep the engine (and its context-property backend) alive for as long as
	# the caller holds `obj` — otherwise Python GC drops the last reference to
	# `engine` when this function returns, deleting the underlying C++ engine
	# (and the DashScreen it owns) out from under the caller's assertions.
	obj._engine = engine
	obj._backend = backend
	return obj


def test_compact_true_at_1024():
	assert _dash(1024).property('compact') is True


def test_compact_false_at_1280():
	assert _dash(1280).property('compact') is False
