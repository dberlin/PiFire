#!/usr/bin/env python3
"""Standalone FPS / fidelity preview for the redesigned PiFire dashboard.

Loads tools/qt_dashboard_preview.qml with a built-in simulator (no Redis, no
control stack). An on-screen counter reports rendered FPS; the terminal also
prints it once per second.

Usage:
    python tools/qt_dashboard_preview.py           # windowed preview
    python tools/qt_dashboard_preview.py --check    # load + exit (CI syntax check, offscreen)

Controls (in the window):
    click / M  cycle mode          A  cycle accent
    P          toggle probes       L  toggle lid-open alert
    F          toggle animation (isolate layout cost)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QML = os.path.join(HERE, 'qt_dashboard_preview.qml')


def main():
	check = '--check' in sys.argv
	if check:
		os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

	from PySide6.QtCore import QUrl, QTimer
	from PySide6.QtGui import QGuiApplication
	from PySide6.QtQml import QQmlApplicationEngine

	app = QGuiApplication(sys.argv)
	engine = QQmlApplicationEngine()
	engine.load(QUrl.fromLocalFile(QML))
	if not engine.rootObjects():
		print('ERROR: failed to load qt_dashboard_preview.qml', file=sys.stderr)
		return 1

	if check:
		print('OK: qt_dashboard_preview.qml loaded')
		# Give the scene one event-loop pass, then quit.
		QTimer.singleShot(0, app.quit)
		return app.exec()

	# Echo the on-screen FPS to the terminal once per second.
	from PySide6.QtCore import QObject

	root = engine.rootObjects()[0]
	fps_label = root.findChild(QObject, 'fpsLabel')
	if fps_label is not None:
		ticker = QTimer()
		ticker.timeout.connect(lambda: print(fps_label.property('text'), flush=True))
		ticker.start(1000)
		globals()['_ticker'] = ticker  # keep a reference alive

	return app.exec()


if __name__ == '__main__':
	sys.exit(main())
