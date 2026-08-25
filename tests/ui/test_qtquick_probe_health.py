import gc
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt, QUrl, qInstallMessageHandler
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

from display.qtbackend import PiFireBackend

QML_DIR = Path("display/qml").resolve()
SCREENSHOT_DIR = Path("/tmp/pifire-task5-screenshots")
PROBE_INFO = {
    "primary": {"name": "Grill", "label": "P0", "max_temp": 600},
    "food": [{"name": "Brisket", "label": "F0", "max_temp": 300}],
    "aux": [{"name": "Cabinet", "label": "A0"}],
}


def _app():
    return QGuiApplication.instance() or QGuiApplication([])


def _health(
    *,
    label="P0",
    display_name="Grill",
    role="Primary",
    state="healthy",
    faults=None,
    evidence=None,
    temperature_valid=True,
    source="software",
    policy="observe",
    outcome="none",
    current=True,
    age=0.25,
):
    return {
        "device": "max31856",
        "port": "TC0",
        "label": label,
        "displayName": display_name,
        "role": role,
        "report": {
            "state": state,
            "faults": [] if faults is None else faults,
            "evidence": ["stuck-response"] if evidence is None else evidence,
            "temperatureValid": temperature_valid,
            "detail": {"policy": policy},
        },
        "detector": {"source": source, "policy": policy},
        "outcome": outcome,
        "freshness": {"current": current, "lastReportedAgeS": age},
    }


def _backend(health):
    readings = {
        "P": {"P0": 225},
        "F": {"F0": 165},
        "AUX": {"A0": 90},
        "PSP": 250,
        "NT": {"F0": 203},
    }
    status = {
        "mode": "Hold",
        "units": "F",
        "outpins": {"fan": True, "auger": False, "igniter": False},
    }
    backend = PiFireBackend(lambda: (readings, status), lambda command, data: None, PROBE_INFO)
    backend.property("probeHealth").update(health)
    backend.poll()
    return backend


def _load_main(health, size=(1280, 720), rotation=0):
    _app()
    backend = _backend(health)
    warnings = []
    gc.collect()
    _app().processEvents()
    qInstallMessageHandler(lambda mode, context, message: warnings.append(str(message)))
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    context = engine.rootContext()
    context.setContextProperty("backend", backend)
    context.setContextProperty("screenWidth", size[0])
    context.setContextProperty("screenHeight", size[1])
    context.setContextProperty("screenRotation", rotation)
    context.setContextProperty("splashImage", "")
    context.setContextProperty("splashDelay", 0)
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    assert engine.rootObjects(), "Main.qml did not load"
    root = engine.rootObjects()[0]
    assert isinstance(root, QQuickWindow)
    QTest.qWait(500)
    return engine, backend, root, warnings


def _find(root, name):
    obj = root.findChild(QObject, name)
    if obj is None and isinstance(root, QQuickWindow):

        def walk(item: QQuickItem):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                found = walk(child)
                if found is not None:
                    return found
            return None

        obj = walk(root.contentItem())
    assert obj is not None, f"missing QML object {name}"
    return obj


def _text(root, name):
    return str(_find(root, name).property("text"))


def _wait_until(predicate, message, timeout_ms=2000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        _app().processEvents()
        if predicate():
            return
        QTest.qWait(10)
    assert predicate(), message


def _wait_for_active_details(root):
    _wait_until(
        lambda: root.findChild(QObject, "probeHealthScreen") is not None,
        "thermocouple health details were not created",
    )
    details = _find(root, "probeHealthScreen")
    stack = _find(root, "mainStack")
    _wait_until(
        lambda: not bool(stack.property("busy")) and stack.property("currentItem") == details,
        "thermocouple health details did not become the settled StackView item",
    )
    return details


def _open_details(root):
    QMetaObject.invokeMethod(root, "openHealthDetails", Qt.ConnectionType.DirectConnection)
    return _wait_for_active_details(root)


def _item_rect_in(item, ancestor):
    origin = item.mapToItem(ancestor, QPointF(0, 0))
    return origin.x(), origin.y(), float(item.property("width")), float(item.property("height"))


def _save(root, name, active_item=None):
    if active_item is not None:
        stack = _find(root, "mainStack")
        _wait_until(
            lambda: not bool(stack.property("busy")) and stack.property("currentItem") == active_item,
            "screenshot requested before the target StackView item settled",
        )
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"{name}.png"
    image = root.grabWindow()
    assert not image.isNull()
    assert image.save(str(path))
    assert path.stat().st_size > 1000
    return path


def _assert_no_qml_warnings(warnings):
    qInstallMessageHandler(None)
    assert warnings == []


def test_suspected_is_inline_only_and_keeps_numeric_reading():
    _engine, _, root, warnings = _load_main([_health(state="suspected")], (1024, 600))
    banner = _find(root, "probeHealthBanner")
    assert banner.property("active") is False
    assert _text(root, "primaryHealthStatus") == "CHECK PROBE"
    assert _text(root, "primaryTemperature") == "225"
    _save(root, "suspected-1024x600")
    _assert_no_qml_warnings(warnings)


def test_observe_primary_is_persistent_and_does_not_claim_heating_stopped():
    health = [_health(state="confirmed", outcome="notify_only", temperature_valid=True, faults=["malfunction"])]
    _engine, _, root, warnings = _load_main(health)
    banner = _find(root, "probeHealthBanner")
    assert banner.property("active") is True
    assert banner.property("dismissible") is False
    assert banner.property("opacity") == 1.0
    QTest.qWait(650)
    assert banner.property("opacity") == 1.0
    assert "FAULT" in str(banner.property("summaryText"))
    assert "Observe mode did not stop heating" in str(banner.property("summaryText"))
    assert _text(root, "primaryTemperature") == "225"
    _save(root, "observe-primary-1280x720")
    _assert_no_qml_warnings(warnings)


def test_stopped_primary_is_highest_priority_and_suppresses_temperature():
    health = [_health(state="confirmed", outcome="stopped", temperature_valid=False, faults=["open"], policy="enforce")]
    _engine, _, root, warnings = _load_main(health, (1024, 768))
    banner = _find(root, "probeHealthBanner")
    assert "CONTROL PROBE UNAVAILABLE" in str(banner.property("summaryText"))
    assert "PiFire stopped heating" in str(banner.property("summaryText"))
    assert _text(root, "primaryTemperature") == "—"
    assert _text(root, "primaryHealthStatus") == "CONTROL PROBE UNAVAILABLE"
    _save(root, "stopped-primary-1024x768")
    _assert_no_qml_warnings(warnings)


def test_secondary_unavailable_uses_reserved_card_status_and_em_dash():
    health = [
        _health(
            label="F0",
            display_name="Brisket",
            role="Food",
            state="confirmed",
            outcome="unavailable",
            temperature_valid=False,
            faults=["short"],
            source="hardware",
        )
    ]
    _engine, _, root, warnings = _load_main(health, (1024, 600))
    assert _text(root, "foodTemperature-Brisket") == "—"
    assert _text(root, "foodHealthStatus-Brisket") == "PROBE UNAVAILABLE"
    assert "Grill control continues" in str(_find(root, "probeHealthBanner").property("summaryText"))
    _save(root, "secondary-unavailable-1024x600")
    _assert_no_qml_warnings(warnings)


def test_multiple_faults_and_aux_are_available_in_scrollable_details():
    health = [
        _health(state="confirmed", outcome="notify_only", temperature_valid=True, faults=["malfunction"]),
        _health(
            label="F0",
            display_name="Brisket",
            role="Food",
            state="confirmed",
            outcome="unavailable",
            temperature_valid=False,
            faults=["open", "short"],
            source="mixed",
        ),
        _health(
            label="A0",
            display_name="Cabinet",
            role="Aux",
            state="confirmed",
            outcome="unavailable",
            temperature_valid=False,
            faults=["open"],
            source="hardware",
        ),
    ]
    _engine, backend, root, warnings = _load_main(health)
    banner = _find(root, "probeHealthBanner")
    assert "+2 more" in str(banner.property("summaryText"))
    assert isinstance(banner, QQuickItem)
    banner.forceActiveFocus()
    assert banner.property("activeFocus") is True
    backend.navEnter()
    details = _wait_for_active_details(root)
    assert details.property("visible") is True
    assert warnings == [], f"QML warnings: {warnings}"
    assert banner.property("visible") is True
    assert _text(root, "healthDetailName-Grill") == "Grill · Primary"
    assert _text(root, "healthDetailName-Brisket") == "Brisket · Food"
    assert _text(root, "healthDetailName-Cabinet") == "Cabinet · Aux"
    assert "open, short" in _text(root, "healthDetailTechnical-Brisket").lower()
    assert _find(root, "probeHealthDetailsFlick").property("contentHeight") >= _find(
        root, "probeHealthDetailsFlick"
    ).property("height")
    _save(root, "multiple-and-aux-details-1280x720", active_item=details)
    _assert_no_qml_warnings(warnings)


def test_last_reported_qualifies_retained_health_without_changing_priority():
    health = [
        _health(
            state="confirmed",
            outcome="stopped",
            temperature_valid=False,
            faults=["open"],
            current=False,
            age=47.0,
        )
    ]
    _engine, _, root, warnings = _load_main(health)
    summary = str(_find(root, "probeHealthBanner").property("summaryText"))
    assert summary.startswith("Last reported: Grill")
    assert "CONTROL PROBE UNAVAILABLE" in summary
    assert _text(root, "primaryHealthStatus").startswith("Last reported: ")
    _save(root, "last-reported-1280x720")
    _assert_no_qml_warnings(warnings)


def test_recovery_removes_banner_and_status_without_a_recovery_pill():
    health = [_health(state="confirmed", outcome="notify_only", faults=["malfunction"])]
    _engine, backend, root, warnings = _load_main(health)
    assert _find(root, "probeHealthBanner").property("active") is True
    backend.property("probeHealth").update([_health(state="healthy", evidence=[], faults=[])])
    QTest.qWait(30)
    assert _find(root, "probeHealthBanner").property("active") is False
    assert _text(root, "primaryHealthStatus") == ""
    _save(root, "recovery-1280x720")
    _assert_no_qml_warnings(warnings)


@pytest.mark.parametrize("state", ["healthy", "unmonitored"])
def test_quiet_states_do_not_create_status_pills(state):
    _engine, _, root, warnings = _load_main([_health(state=state, evidence=[], faults=[])])
    assert _find(root, "probeHealthBanner").property("active") is False
    assert _text(root, "primaryHealthStatus") == ""
    _assert_no_qml_warnings(warnings)


@pytest.mark.parametrize(
    ("size", "rotation"),
    [
        ((1024, 600), 0),
        ((1280, 720), 0),
        ((1024, 768), 0),
        ((1024, 600), 90),
        ((1024, 600), 180),
        ((1024, 600), 270),
    ],
)
def test_banner_and_details_fit_compact_and_rotated_viewports(size, rotation):
    health = [_health(state="confirmed", outcome="stopped", temperature_valid=False, faults=["open"])]
    _engine, _, root, warnings = _load_main(health, size, rotation)
    rotor = _find(root, "rotor")
    banner = _find(root, "probeHealthBanner")
    logical_width = float(rotor.property("width"))
    logical_height = float(rotor.property("height"))
    assert banner.property("width") <= logical_width - 24
    assert banner.property("height") >= 44
    assert banner.property("x") >= 0
    assert banner.property("y") >= 0
    assert banner.property("x") + banner.property("width") <= logical_width
    assert banner.property("y") + banner.property("height") <= logical_height
    details = _open_details(root)
    details_card = _find(root, "probeHealthDetailsCard")
    close_button = _find(root, "probeHealthCloseButton")
    assert details_card.property("width") <= logical_width - 24
    assert details_card.property("height") <= logical_height - 24
    close_x, close_y, close_width, close_height = _item_rect_in(close_button, rotor)
    banner_bottom = float(banner.property("y")) + float(banner.property("height"))
    assert close_width >= 44
    assert close_height >= 44
    assert close_y >= banner_bottom
    assert close_x >= 0
    assert close_x + close_width <= logical_width
    assert close_y + close_height <= logical_height
    _save(root, f"layout-{size[0]}x{size[1]}-r{rotation}", active_item=details)
    _assert_no_qml_warnings(warnings)


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_dashboard_reflows_to_vertical_scroll_without_horizontal_clipping(rotation):
    health = [_health(state="confirmed", outcome="stopped", temperature_valid=False, faults=["open"])]
    _engine, _, root, warnings = _load_main(health, (1024, 600), rotation)
    portrait = _find(root, "portraitDashFlick")
    landscape = _find(root, "landscapeDashBody")
    gauge_card = _find(root, "portraitGaugeCard")
    assert portrait.property("visible") is True
    assert landscape.property("visible") is False
    assert portrait.property("contentWidth") <= portrait.property("width")
    assert portrait.property("contentHeight") > portrait.property("height")
    assert gauge_card.property("x") >= 0
    assert gauge_card.property("x") + gauge_card.property("width") <= portrait.property("width")
    _save(root, f"dashboard-1024x600-r{rotation}")
    _assert_no_qml_warnings(warnings)


def test_banner_accessible_press_action_opens_settled_details():
    health = [_health(state="confirmed", outcome="notify_only", faults=["malfunction"])]
    _engine, _, root, warnings = _load_main(health)
    banner = _find(root, "probeHealthBanner")
    interface = QAccessible.queryAccessibleInterface(banner)
    assert interface is not None
    action = interface.actionInterface()
    assert action is not None
    assert QAccessibleActionInterface.pressAction() in action.actionNames()
    action.doAction(QAccessibleActionInterface.pressAction())
    details = _wait_for_active_details(root)
    assert details.property("visible") is True
    _assert_no_qml_warnings(warnings)


def test_natural_encoder_flow_opens_details_with_close_focused_and_closes_it():
    health = [_health(state="confirmed", outcome="notify_only", faults=["malfunction"])]
    _engine, backend, root, warnings = _load_main(health)
    banner = _find(root, "probeHealthBanner")
    assert isinstance(banner, QQuickItem)
    banner.forceActiveFocus()
    assert root.activeFocusItem() == banner
    backend.navEnter()
    details = _wait_for_active_details(root)
    close_button = _find(root, "probeHealthCloseButton")
    assert root.activeFocusItem() == close_button
    assert close_button.property("activeFocus") is True
    backend.navEnter()
    stack = _find(root, "mainStack")
    _wait_until(
        lambda: not bool(stack.property("busy")) and stack.property("currentItem") != details,
        "encoder close did not settle on the previous StackView item",
    )
    _assert_no_qml_warnings(warnings)


def test_banner_and_details_actions_have_accessible_metadata_and_focus_targets():
    health = [_health(state="confirmed", outcome="notify_only", faults=["malfunction"])]
    _engine, _, root, warnings = _load_main(health)
    banner = _find(root, "probeHealthBanner")
    interface = QAccessible.queryAccessibleInterface(banner)
    assert interface is not None
    assert interface.text(QAccessible.Text.Name) == "Thermocouple health alert"
    assert interface.text(QAccessible.Text.Description)
    assert interface.role() == QAccessible.Role.Button
    assert banner.property("activeFocusOnTab") is True
    details = _open_details(root)
    close_button = _find(root, "probeHealthCloseButton")
    assert close_button.property("width") >= 44
    assert close_button.property("height") >= 44
    close_interface = QAccessible.queryAccessibleInterface(close_button)
    assert close_interface is not None
    assert close_interface.text(QAccessible.Text.Name) == "Close thermocouple health details"
    assert close_interface.text(QAccessible.Text.Description)
    assert root.activeFocusItem() == close_button
    assert details.property("visible") is True
    _assert_no_qml_warnings(warnings)


@pytest.mark.parametrize(
    "scenario",
    [
        "suspected",
        "observe-primary",
        "stopped-primary",
        "secondary-unavailable",
        "multiple-faults",
        "aux-detail",
        "recovery",
        "stale-transport",
    ],
)
def test_dashboard_preview_loads_each_health_scenario_without_qml_warnings(scenario):
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [
            sys.executable,
            "tools/qt_dashboard_preview.py",
            "--check",
            "--health-scenario",
            scenario,
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"health scenario: {scenario}" in result.stdout
    assert result.stderr == ""


def test_aux_preview_details_render_real_line_breaks():
    _app()
    warnings = []
    qInstallMessageHandler(lambda mode, context, message: warnings.append(str(message)))
    engine = QQmlApplicationEngine()
    context = engine.rootContext()
    context.setContextProperty("viewW", 1280)
    context.setContextProperty("viewH", 720)
    context.setContextProperty("initialHealthScenario", "aux-detail")
    engine.load(QUrl.fromLocalFile(str(Path("tools/qt_dashboard_preview.qml").resolve())))
    assert engine.rootObjects()
    QTest.qWait(30)
    root = engine.rootObjects()[0]
    assert isinstance(root, QQuickWindow)
    texts = []
    pending = [root.contentItem()]
    while pending:
        item = pending.pop()
        pending.extend(item.childItems())
        text = item.property("text")
        if text is not None:
            texts.append(str(text))
    details = next(text for text in texts if text.startswith("CABINET"))
    assert "\\n" not in details
    assert "\n" in details
    _assert_no_qml_warnings(warnings)
