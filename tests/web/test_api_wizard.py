import copy
import json

import pytest

from app import app as flask_app
from common.common import WriteKind
from common.datastore_accessors import read_settings, write_settings_store


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_state_fresh_returns_metadata_and_selections(ds, client):
    resp = client.get("/api/wizard/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["modules_metadata"].keys()) >= {"grillplatform", "display", "distance"}
    assert "display" in body["selections"]
    assert body["has_draft"] is False
    # display_config is a dict keyed by module name (may be empty on fresh)
    assert isinstance(body["display_config"], dict)
    assert "control_mode" in body and "first_time_setup" in body


def test_state_ships_board_probe_maps(ds, client):
    body = client.get("/api/wizard/state").get_json()
    assert isinstance(body["board_probe_maps"], dict)
    # the 4 PCB boards each carry a probe_map with the two probe arrays
    assert "pcb_4.x.x" in body["board_probe_maps"]
    board = body["board_probe_maps"]["pcb_4.x.x"]
    assert "probe_devices" in board and "probe_info" in board
    assert len(board["probe_devices"]) >= 1


def test_state_fresh_install_seeds_default_board_probe_map(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = True
    write_settings_store(settings)

    body = client.get("/api/wizard/state").get_json()
    # On a fresh install the returned probe_map is the DEFAULT board's map,
    # not the live-settings one -- it matches board_probe_maps[default_board].
    assert body["probe_map"] == body["board_probe_maps"]["pcb_4.x.x"]
    assert len(body["probe_map"]["probe_devices"]) >= 1


def test_draft_persists_and_state_resumes(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200 and r1.get_json()["result"] == "success"

    r2 = client.get("/api/wizard/state")
    body = r2.get_json()
    assert body["has_draft"] is True
    assert body["selections"]["display"] == "ili9341b"
    assert body["display_config"]["ili9341b"]["rotation"] == 90


def test_draft_clear_removes_draft_and_leaves_other_keys(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200

    r2 = client.post("/api/wizard/draft", data=json.dumps({"clear": True}), content_type="application/json")
    assert r2.status_code == 200 and r2.get_json()["result"] == "success"

    r3 = client.get("/api/wizard/state")
    body = r3.get_json()
    assert body["has_draft"] is False


def test_state_existing_stale_module_returns_empty_selection(ds, client):
    """Characterizes the (now-fixed) stale-module recovery path in
    wizardInstallInfoExisting() (blueprints/wizard/wizard.py): when
    settings["modules"]["dist"] names a module that is no longer in the
    wizard manifest (e.g. a distance sensor that was removed/renamed), the
    "stale module" recovery branch sets:

        wizardInstallInfo["modules"]["distance"]["profile_selected"] = []

    profile_selected is ALWAYS a list -- an invalid/stale saved module now
    means "no selection" ([]), not a bare string echoing the recovered
    fallback name. We deliberately use the "distance" section (not
    "display") to isolate this from a *second*, unrelated bug: the display
    branch of the same function indexes
    settings["display"]["config"][settings["modules"]["display"]] using the
    ORIGINAL (still-stale) settings value rather than the recovered
    `selected`, which would KeyError before we ever reach the
    profile_selected shape issue.

    _build_state()'s clean always-list extraction (`pf[0] if pf else None`)
    turns that empty list into a None selection (JSON null), never an empty
    string -- "no selection" is null, not a "" sentinel."""
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    settings["modules"]["dist"] = "totally_bogus_distance_module"
    write_settings_store(settings)

    resp = client.get("/api/wizard/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["selections"]["distance"] is None


def test_state_includes_probe_map_profiles_units(ds, client):
    resp = client.get("/api/wizard/state")
    body = resp.get_json()
    assert isinstance(body["probe_map"], dict)
    assert "probe_devices" in body["probe_map"] and "probe_info" in body["probe_map"]
    # probe_profiles is a LIST of profile objects (not the settings dict keyed by id)
    assert isinstance(body["probe_profiles"], list)
    assert all("id" in p and "name" in p for p in body["probe_profiles"])
    assert body["probes_units"] in ("F", "C")


def test_draft_persists_and_resumes_probe_map_and_units(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {},
        "display_config": {},
        "probe_map": {
            "probe_devices": [
                {
                    "device": "D1",
                    "module": "ads1115_adafruit",
                    "module_filename": "ads1115_adafruit",
                    "ports": ["ADC0"],
                    "config": {},
                }
            ],
            "probe_info": [],
        },
        "probes_units": "C",
    }
    r1 = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r1.status_code == 200
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    assert body["probe_map"]["probe_devices"][0]["device"] == "D1"
    assert body["probes_units"] == "C"


def test_draft_clear_drops_probe_map_and_units(ds, client):
    draft = {
        "selections": {},
        "settings_dep_values": {},
        "display_config": {},
        "probe_map": {"probe_devices": [{"device": "D1"}], "probe_info": []},
        "probes_units": "C",
    }
    client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    client.post("/api/wizard/draft", data=json.dumps({"clear": True}), content_type="application/json")
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is False
    # After clear, probe_map falls back to the computed value (from live settings), not the drafted one
    assert body["probe_map"]["probe_devices"][0]["device"] != "D1" or body["probe_map"]["probe_devices"] == []


def test_cancel_clears_first_time_setup(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = True
    write_settings_store(settings)

    resp = client.post("/api/wizard/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "success"
    # The React dashboard bounces back to /wizard while this flag is set
    # (web-react/src/components/DashboardRoute.tsx:23-35), so an exit that
    # leaves it True is an inescapable loop, not a partial fix.
    assert read_settings()["globals"]["first_time_setup"] is False
    assert client.get("/api/wizard/state").get_json()["first_time_setup"] is False


def test_cancel_preserves_the_draft(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")

    assert client.post("/api/wizard/cancel").status_code == 200

    # Legacy _wizard_cancel (blueprints/wizard/routes.py:71-74) does not touch
    # the install blob either, and the welcome step promises the draft is kept.
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    assert body["selections"]["display"] == "ili9341b"


def test_cancel_is_idempotent_when_not_a_fresh_install(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    write_settings_store(settings)

    assert client.post("/api/wizard/cancel").status_code == 200
    assert read_settings()["globals"]["first_time_setup"] is False


def test_cancel_does_not_flag_a_control_update(ds, client):
    """Legacy _wizard_cancel (blueprints/wizard/routes.py:71-74) uses a plain
    write_settings() -- NOT save_settings_and_flag_update() (common/app.py:401)
    -- because nothing about the running hardware changed: no install was
    started and no module configuration was applied. Flagging a settings/probe
    update here would make the control process needlessly reload its modules on
    a mere "never mind", so the control blob must come back untouched."""
    from common.datastore_accessors import read_control

    before = copy.deepcopy(read_control())

    assert client.post("/api/wizard/cancel").status_code == 200

    assert read_control() == before


def test_scan_extended_i2c_returns_groups(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(
        wr,
        "discover_extended_i2c_buses",
        lambda *a, **k: [{"bus_num": 1, "name": "i2c-1", "serial": "ABC"}],
    )
    resp = client.post(
        "/api/wizard/scan",
        data=json.dumps({"kind": "extended"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["error"] is None
    assert isinstance(body["groups"], list) and body["groups"]
    assert body["groups"][0]["items"][0]["value"]


def test_scan_no_results_returns_friendly_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(wr, "discover_extended_i2c_buses", lambda *a, **k: [])
    resp = client.post(
        "/api/wizard/scan",
        data=json.dumps({"kind": "extended"}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["error"] == "No devices found."


def test_finish_blocked_when_not_stopped(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))  # neutralize installer
    from common.datastore_accessors import read_control, write_control

    ctrl = read_control()
    ctrl["mode"] = "Hold"
    write_control(ctrl, WriteKind.OVERWRITE, origin="test")
    resp = client.post(
        "/api/wizard/finish",
        data=json.dumps({"selections": {}, "settings_dep_values": {}, "display_config": {}}),
        content_type="application/json",
    )
    assert resp.status_code == 409
    assert resp.get_json()["message"] == "system_active"
    assert fired == []  # installer must NOT fire


def test_finish_fires_installer_when_stopped(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    # control defaults to Stop in a fresh ds
    resp = client.post(
        "/api/wizard/finish",
        data=json.dumps(
            {
                "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "hcsr04"},
                "settings_dep_values": {},
                "display_config": {},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 200 and resp.get_json()["result"] == "success"
    assert fired and "wizard.py" in fired[0]

    st = client.get("/api/wizard/installstatus").get_json()
    assert st["percent"] == 0 and "Starting Install" in st["status"]


def test_finish_uses_probe_map_from_payload(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(wr, "store_wizard_install_info", lambda info: captured.update(info))
    payload = {
        "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "hcsr04"},
        "settings_dep_values": {},
        "display_config": {},
        "probe_map": {
            "probe_devices": [
                {
                    "device": "PAYLOAD_DEV",
                    "module": "ads1115_adafruit",
                    "module_filename": "ads1115_adafruit",
                    "ports": ["ADC0"],
                    "config": {},
                }
            ],
            "probe_info": [],
        },
        "probes_units": "C",
    }
    resp = client.post("/api/wizard/finish", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 200
    assert captured["probe_map"]["probe_devices"][0]["device"] == "PAYLOAD_DEV"
    assert captured["modules"]["probes"]["settings"]["units"] == "C"
    assert captured["modules"]["probes"]["profile_selected"] == ["ads1115_adafruit"]


def test_finish_sends_flat_display_config_for_selected_module(ds, client, monkeypatch):
    """The React client's display_config is module-keyed:
    {module: {option: value}}. The detached installer (wizard.py's
    run_wizard()) indexes
    WizardInstallInfo["modules"]["display"]["config"] as a FLAT
    {option: value} dict for the SELECTED module -- matching what legacy
    prepare_wizard_data built. Sending the module-keyed bag verbatim would
    nest it one level too deep and the installer would silently fall back to
    defaults instead of applying the user's chosen display options."""
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(wr, "store_wizard_install_info", lambda info: captured.update(info))
    payload = {
        "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "hcsr04"},
        "settings_dep_values": {},
        "display_config": {
            "ili9341b": {"rotation": 90},
            "other_module": {"rotation": 270},
        },
    }
    resp = client.post("/api/wizard/finish", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 200
    assert captured["modules"]["display"]["config"] == {"rotation": 90}
    assert fired and "wizard.py" in fired[0]


def test_finish_rejects_empty_selection(ds, client, monkeypatch):
    """The detached installer (wizard.py's run_wizard()) unconditionally
    indexes WizardInstallInfo["modules"]["grillplatform"|"display"|"distance"]
    ["profile_selected"][0] -- an empty selection (reachable when a
    stale-module draft is resumed and /finish is POSTed without
    re-selecting) would raise an unhandled IndexError in the detached
    process, silently sticking the install at "Starting Install..." forever.
    /finish is the last safety net before an irreversible real-hardware
    install, so it must reject this instead of firing."""
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    monkeypatch.setattr(wr, "wizard_bus_kinds", lambda *a, **k: {})
    monkeypatch.setattr(wr, "validate_bus_kinds", lambda *a, **k: None)
    resp = client.post(
        "/api/wizard/finish",
        data=json.dumps(
            {
                "selections": {"grillplatform": None, "display": "ili9341b", "distance": "hcsr04"},
                "settings_dep_values": {},
                "display_config": {},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["result"] == "error"
    assert body["message"] == "missing_selection"
    assert "grillplatform" in body.get("sections", [])
    assert fired == []  # installer must NOT fire


def test_finish_bus_conflict_returns_422(ds, client, monkeypatch):
    """Characterizes the existing bus_conflict (422) branch of wizard_finish():
    when validate_bus_kinds() raises I2CBusConfigError (e.g. a real
    basic+USB-HID conflict), /finish must surface a 422 without firing the
    installer."""
    import blueprints.api_wizard.routes as wr
    from common.i2c_bus import I2CBusConfigError

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))

    def _raise_conflict(*a, **k):
        raise I2CBusConfigError("'basic' I2C can't share a process with a USB-HID bus")

    monkeypatch.setattr(wr, "validate_bus_kinds", _raise_conflict)
    resp = client.post(
        "/api/wizard/finish",
        data=json.dumps(
            {
                "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "hcsr04"},
                "settings_dep_values": {},
                "display_config": {},
            }
        ),
        content_type="application/json",
    )
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["result"] == "error"
    assert body["message"] == "bus_conflict"
    assert fired == []  # installer must NOT fire


def test_scan_bluetooth_returns_rows(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(wr, "get_supported_cmds", lambda: ["scan_bluetooth"])
    monkeypatch.setattr(wr, "process_command", lambda **k: None)
    monkeypatch.setattr(
        wr,
        "get_system_command_output",
        lambda **k: {"result": "OK", "data": {"bt_devices": [{"name": "iBBQ", "hw_id": "AA:BB", "info": ""}]}},
    )
    monkeypatch.setattr(wr, "parse_bt_device_info", lambda devs: devs)
    resp = client.post("/api/wizard/scan/bluetooth", data=json.dumps({}), content_type="application/json")
    body = resp.get_json()
    assert body["error"] is None
    assert body["rows"][0]["hw_id"] == "AA:BB"


def test_scan_bluetooth_unsupported_is_friendly_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(wr, "get_supported_cmds", lambda: [])
    resp = client.post("/api/wizard/scan/bluetooth", data=json.dumps({}), content_type="application/json")
    body = resp.get_json()
    assert body["rows"] == []
    assert body["error"] == "No support for bluetooth scan command."


def test_scan_thermoworks_auth_error(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    from thermoworks_cloud import AuthenticationError
    from thermoworks_cloud.auth import AuthenticationErrorReason

    def _boom(*a, **k):
        # Real signature is (message, reason, details) -- the brief's
        # single-arg construction doesn't match the installed
        # thermoworks-cloud package and raises TypeError instead.
        raise AuthenticationError("bad creds", AuthenticationErrorReason.INVALID_PASSWORD, [])

    monkeypatch.setattr(wr, "_thermoworks_discover", _boom)
    resp = client.post(
        "/api/wizard/scan/thermoworks",
        data=json.dumps({"email": "x@y.z", "password": "nope"}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["rows"] == []
    assert "Could not log in" in body["error"]


def test_scan_thermoworks_returns_rows(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(
        wr,
        "_thermoworks_discover",
        lambda email, password: [{"label": "Signals", "type": "signals", "serial": "S1", "num_channels": 4}],
    )
    resp = client.post(
        "/api/wizard/scan/thermoworks",
        data=json.dumps({"email": "x@y.z", "password": "ok"}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["error"] is None
    assert body["rows"][0]["serial"] == "S1"


def test_validate_bus_kinds_clean(ds, client):
    devs = [
        {
            "device": "D1",
            "module": "ads1115_adafruit",
            "config": {"i2c_bus_kind": "basic"},
            "ports": ["ADC0"],
        }
    ]
    resp = client.post(
        "/api/wizard/probes/validate-bus-kinds",
        data=json.dumps({"probe_devices": devs}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_validate_bus_kinds_conflict(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr
    from common.i2c_bus import I2CBusConfigError

    def _boom(*a, **k):
        raise I2CBusConfigError("'basic' I2C can't share a process with a USB-HID bus")

    monkeypatch.setattr(wr, "validate_bus_kinds", _boom)
    resp = client.post(
        "/api/wizard/probes/validate-bus-kinds",
        data=json.dumps({"probe_devices": [{"device": "D1", "config": {"i2c_bus_kind": "basic"}}]}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert "USB-HID" in body["detail"]


def test_module_values_grillplatform_returns_live_settings(ds, client):
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "pcb_4.x.x"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    # settings is the dep-key -> value map for the module's settings_dependencies
    assert isinstance(body["settings"], dict)
    assert "system_type" in body["settings"]
    # grillplatform never has a config bag
    assert body["config"] == {}


def test_module_values_display_config_is_guarded(ds, client):
    # A display module that has never been persisted must not KeyError -- the
    # config bag falls back to {} (mirrors legacy _wizard_modulecard callout #2).
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "display", "module": "ili9341b"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body["settings"], dict)
    assert isinstance(body["config"], dict)  # dict, never a crash


def test_module_values_unknown_module_is_400(ds, client):
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "does_not_exist"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "unknown_module"


def test_module_values_unknown_section_is_400(ds, client):
    # "probes" has no module-card round-trip; anything outside the 3 non-probe
    # sections is rejected.
    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "probes", "module": "default"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["message"] == "unknown_module"


# ---------------------------------------------------------------------------
# usb_serial scan: which path the picker actually saves.
#
# The value the wizard writes into settings is whatever this endpoint puts in
# `value`. Saving the kernel name (/dev/ttyACM0) is what leaves a configured
# install pointing at a different device after a replug or a reboot, silently:
# the port opens, writes succeed, reads time out. When udev has given the board
# a stable alias, that is what has to be offered.
# ---------------------------------------------------------------------------


def _numato(stable=None, device="/dev/ttyACM1"):
    return {
        "device": device,
        "stable_device": stable,
        "description": "Numato Lab 4 Channel USB Relay",
        "manufacturer": "Numato Lab",
        "serial_number": "",
        "vid": 0x2A19,
        "pid": 0x0C0C,
    }


def _scan_usb_serial(client, devices, monkeypatch, body=None):
    import blueprints.api_wizard.routes as wr

    monkeypatch.setattr(wr, "discover_usb_serial_devices", lambda *a, **k: devices)
    resp = client.post(
        "/api/wizard/scan",
        data=json.dumps(body or {"kind": "usb_serial"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    return resp.get_json()


def test_usb_serial_scan_saves_the_stable_alias_when_there_is_one(ds, client, monkeypatch):
    body = _scan_usb_serial(client, [_numato(stable="/dev/pifire-numato")], monkeypatch)
    item = body["groups"][0]["items"][0]
    assert item["value"] == "/dev/pifire-numato"
    # The kernel name still appears, because that is what dmesg and every other
    # tool calls it -- and the label says which one is being saved.
    assert "/dev/ttyACM1" in item["label"]
    assert "/dev/pifire-numato" in item["label"]


def test_usb_serial_scan_falls_back_to_the_kernel_name(ds, client, monkeypatch):
    body = _scan_usb_serial(client, [_numato(stable=None)], monkeypatch)
    item = body["groups"][0]["items"][0]
    assert item["value"] == "/dev/ttyACM1"
    assert "saved as" not in item["label"]


def test_usb_serial_scan_passes_vid_pid_through_to_discovery(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    seen = {}

    def _capture(vid=None, pid=None):
        seen["vid"], seen["pid"] = vid, pid
        return [_numato()]

    monkeypatch.setattr(wr, "discover_usb_serial_devices", _capture)
    client.post(
        "/api/wizard/scan",
        data=json.dumps({"kind": "usb_serial", "vid": "0x2a19", "pid": "0x0c0c"}),
        content_type="application/json",
    )
    assert seen == {"vid": "0x2a19", "pid": "0x0c0c"}
