#!/usr/bin/env python3
"""Standalone FPS / fidelity preview for the redesigned PiFire dashboard.

Loads tools/qt_dashboard_preview.qml with a built-in simulator (no Redis, no
control stack). An on-screen counter reports rendered FPS; the terminal also
prints it once per second.

Needs only PySide6 (>=6.11) — no other PiFire deps. The system interpreter has
it (`/usr/bin/python3`); the project .venv does not.

Usage:
    /usr/bin/python3 tools/qt_dashboard_preview.py                 # windowed preview (1280x720)
    /usr/bin/python3 tools/qt_dashboard_preview.py --size 1920x1080  # scales the 1280x720 design to fit
    uv run --with pyside6 python tools/qt_dashboard_preview.py      # if PySide6 isn't on this interpreter
    /usr/bin/python3 tools/qt_dashboard_preview.py --check          # load + exit (offscreen syntax check)
    /usr/bin/python3 tools/qt_dashboard_preview.py --shot out.png   # render one frame to a PNG (offscreen)

Controls (in the window):
    click / M  cycle mode          A  cycle accent
    P          toggle probes       L  toggle lid-open alert
    F          toggle animation (isolate layout cost)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
QML = os.path.join(HERE, "qt_dashboard_preview.qml")


def main():
    check = "--check" in sys.argv
    shot = None
    if "--shot" in sys.argv:
        shot = sys.argv[sys.argv.index("--shot") + 1]
    if check or shot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    view_w, view_h = 1280, 720
    if "--size" in sys.argv:
        spec = sys.argv[sys.argv.index("--size") + 1]
        view_w, view_h = (int(v) for v in spec.lower().split("x"))

    from PySide6.QtCore import QUrl, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow  # noqa: F401 — registers the QQuickWindow wrapper

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("viewW", view_w)
    engine.rootContext().setContextProperty("viewH", view_h)
    engine.load(QUrl.fromLocalFile(QML))
    if not engine.rootObjects():
        print("ERROR: failed to load qt_dashboard_preview.qml", file=sys.stderr)
        return 1

    if check:
        print("OK: qt_dashboard_preview.qml loaded")
        # Give the scene one event-loop pass, then quit.
        QTimer.singleShot(0, app.quit)
        return app.exec()

    if shot:
        root = engine.rootObjects()[0]

        def _grab():
            img = root.grabWindow()
            img.save(shot)
            print(f"wrote {shot}", flush=True)
            app.quit()

        QTimer.singleShot(900, _grab)
        return app.exec()

    # Echo the on-screen FPS to the terminal once per second.
    from PySide6.QtCore import QObject

    root = engine.rootObjects()[0]
    fps_label = root.findChild(QObject, "fpsLabel")
    if fps_label is not None:
        ticker = QTimer()
        ticker.timeout.connect(lambda: print(fps_label.property("text"), flush=True))
        ticker.start(1000)
        globals()["_ticker"] = ticker  # keep a reference alive

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
