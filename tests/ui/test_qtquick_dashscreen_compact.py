import pytest
from PySide6.QtCore import QObject, QUrl, Property
from PySide6.QtQml import QQmlComponent

from tests.conftest import QML_DIR


class _StubBackend(QObject):
    # Minimal surface DashScreen reads at construction time.
    def __init__(self, mode="Stop"):
        super().__init__()
        self._mode = mode

    mode = Property(str, lambda self: self._mode, constant=True)
    foodProbeCount = Property(int, lambda self: 0, constant=True)
    foodProbes = Property("QVariantList", lambda self: [], constant=True)
    units = Property(str, lambda self: "F", constant=True)
    primaryTemp = Property(float, lambda self: 0.0, constant=True)
    primarySetpoint = Property(float, lambda self: 0.0, constant=True)
    primaryNotifyTarget = Property(float, lambda self: 0.0, constant=True)
    primaryMax = Property(float, lambda self: 600.0, constant=True)
    primaryHasTemp = Property(bool, lambda self: True, constant=True)
    primaryStale = Property(str, lambda self: "", constant=True)
    primaryName = Property(str, lambda self: "Grill", constant=True)
    modeText = Property(str, lambda self: "STOP", constant=True)
    lidOpen = Property(bool, lambda self: False, constant=True)
    recipe = Property(bool, lambda self: False, constant=True)
    recipePaused = Property(bool, lambda self: False, constant=True)
    augerDuty = Property(int, lambda self: 40, constant=True)
    fanDuty = Property(int, lambda self: 100, constant=True)
    pMode = Property(int, lambda self: 2, constant=True)
    # Held ON so a mode that must not show SMOKE+ cannot pass by carrying a
    # value that happened to be false.
    smokePlus = Property(bool, lambda self: True, constant=True)
    fanOn = Property(bool, lambda self: False, constant=True)


def _dash(engine, width, mode="Stop"):
    backend = _StubBackend(mode)
    engine.rootContext().setContextProperty("backend", backend)
    qml = 'import QtQuick\nimport "screens"\nDashScreen { width: %d; height: %d }' % (
        width,
        600 if width <= 1100 else 720,
    )
    comp = QQmlComponent(engine)
    comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / "_probe.qml")))
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


def test_compact_true_at_1024(qml_engine):
    assert _dash(qml_engine, 1024).property("compact") is True


def test_compact_false_at_1280(qml_engine):
    assert _dash(qml_engine, 1280).property("compact") is False


def _pills(dash):
    """The two DutyPills, left to right.

    DutyPill is the only thing under DashScreen carrying all three of
    label/value/highlighted, and findChildren walks in construction order,
    which is the order they are declared in the RowLayout.
    """
    return [
        c
        for c in dash.findChildren(QObject)
        if c.property("label") is not None and c.property("value") is not None and c.property("highlighted") is not None
    ]


def _duty_pills(dash):
    return [(p.property("label"), p.property("value")) for p in _pills(dash)]


def test_duty_pills_show_p_mode_and_smoke_plus_in_smoke(qml_engine):
    assert _duty_pills(_dash(qml_engine, 1280, "Smoke")) == [("P-MODE", "P-2"), ("SMOKE+", "ON")]


@pytest.mark.parametrize("mode", ["Stop", "Hold", "Startup", "Reignite", "Prime", "Shutdown", "Monitor", "Manual"])
def test_duty_pills_show_the_duties_everywhere_but_smoke(qml_engine, mode):
    assert _duty_pills(_dash(qml_engine, 1280, mode)) == [("AUGER DUTY", "40%"), ("FAN DUTY", "100%")]


def test_the_p_mode_pill_opens_the_p_mode_menu(qml_engine):
    dash = _dash(qml_engine, 1280, "Smoke")
    left = _pills(dash)[0]
    assert left.property("label") == "P-MODE"
    assert left.property("clickable") is True

    opened = []
    dash.requestMenu.connect(opened.append)
    left.tapped.emit()

    # "pmode" is the menu Menus.js defines with the ten P-Mode entries; Main.qml
    # routes a named requestMenu straight to openMenu.
    assert opened == ["pmode"]


def test_the_pill_is_inert_where_it_reads_the_auger_duty(qml_engine):
    # Same pill object, different mode: a tap here must not offer to set a
    # P-Mode, because the number shown is not one.
    for mode in ["Hold", "Stop", "Startup", "Shutdown"]:
        left = _pills(_dash(qml_engine, 1280, mode))[0]
        assert left.property("label") == "AUGER DUTY", mode
        assert left.property("clickable") is False, mode
