"""Smoke Plus (S+) selectability and toggle behavior in the QT Quick UI.

Guards the full path: the "Smoke+" item lives in the active-mode main menu
(so it is reachable), its cmd_splus dispatch toggles the control s_plus flag
in both directions, and the backend exposes smokePlus for the dash pill.
"""

import json

from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent

import display.qtquick_flex as qmod
from tests.conftest import QML_DIR


def _eval_js(engine, expr):
    qml = 'import QtQuick\nimport "Menus.js" as Menus\nItem { property string result: JSON.stringify(%s) }' % expr
    comp = QQmlComponent(engine)
    comp.setData(qml.encode(), QUrl.fromLocalFile(str(QML_DIR / "_probe.qml")))
    obj = comp.create()
    assert obj is not None, comp.errorString()
    obj.setParent(engine)
    return json.loads(obj.property("result"))


def test_smoke_plus_reachable_in_active_menu(qml_engine):
    # In an active cook (e.g. Smoke) the main menu is main_active_normal, and it
    # must offer Smoke+ wired to cmd_splus so the user can toggle it.
    assert _eval_js(qml_engine, 'Menus.mainVariantForMode("Smoke")') == "main_active_normal"
    items = _eval_js(qml_engine, "Menus.menuFor('main_active_normal').items")
    actions = [it["action"] for it in items]
    assert "cmd_splus" in actions
    label = next(it["label"] for it in items if it["action"] == "cmd_splus")
    assert label == "Smoke+"


def test_cmd_splus_toggles_s_plus_both_directions(monkeypatch):
    writes = []
    monkeypatch.setattr(qmod, "write_control", lambda data, kind=None, origin=None: writes.append(data))
    disp = qmod.Display.for_dispatch({"display_data_filename": "./display/qtquick_dsi_1280x720t.json"}, "F")

    monkeypatch.setattr(qmod, "read_status", lambda: {"s_plus": False})
    disp._dispatch_command("cmd_splus", 0)
    assert writes[-1] == {"s_plus": True}

    monkeypatch.setattr(qmod, "read_status", lambda: {"s_plus": True})
    disp._dispatch_command("cmd_splus", 0)
    assert writes[-1] == {"s_plus": False}


def test_backend_exposes_smoke_plus_from_status():
    from display.qtbackend import PiFireBackend

    b = PiFireBackend(
        lambda: (
            {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}},
            {"mode": "Smoke", "units": "F", "s_plus": True, "outpins": {}},
        ),
        lambda c, v=0: None,
        {"primary": {"name": "Grill", "max_temp": 600}, "food": [], "aux": []},
    )
    b.poll()
    assert b.smokePlus is True
