import copy
import json

import pytest
from common.datastore_accessors import read_settings, write_settings_store
from common.web_contracts.wizard import (
    BusKindsValidationResponse,
    InstallLog,
    InstallStatus,
    ModuleValues,
    RowsResult,
    WizardActionResponse,
    ScanResult,
    WizardDraftRequest,
    WizardState,
)


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


# ---------------------------------------------------------------------------
# Stale draft discard: a draft is a snapshot of manifest-shaped keys, and it
# is served only when its stamp says it was written against the manifest in
# force now. When a module's dependencies are renamed or replaced (e.g. the
# *_i2c_bus_kind + *_i2c_bus_num split fields becoming one *_i2c_bus
# composite), a draft from before the change names keys that bind to
# nothing -- _load_draft() must discard it rather than serve it, or a
# resumed wizard silently shows Basic for hardware that isn't. A draft
# carrying no stamp at all cannot be shown to match, so it goes too.
# ---------------------------------------------------------------------------


def _seed_x86_numato_live_settings():
    """Live settings for a grill actually running x86_numato with two
    USB-I2C bridges (an emc2101 fan controller on one, a distance sensor's
    VL53L on the other) -- the migrated composite-field shape the manifest
    now expects."""
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    settings["platform"]["current"] = "x86_numato"
    settings["platform"]["fan_controller"]["i2c_bus"] = {"kind": "kernel", "serial": "0003171140"}
    settings["platform"]["devices"]["distance"]["i2c_bus"] = {"kind": "kernel", "serial": "0006634723"}
    write_settings_store(settings)
    return settings


def test_stale_draft_with_legacy_i2c_keys_is_discarded(ds, client):
    """The real regression: a draft saved before the I2C composite-field
    change named device_distance_i2c_bus_kind/_num and i2c_bus_kind/_num.
    It predates stamping, so it carries no manifest fingerprint and is
    discarded on that alone -- /state must fall through to (migrated) live
    settings, not serve Basic."""
    _seed_x86_numato_live_settings()
    from common.datastore_accessors import store_wizard_install_info

    store_wizard_install_info(
        {
            "react_draft": True,
            "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
            "settings_dep_values": {
                "grillplatform": {
                    "device_distance_i2c_bus_kind": "extended",
                    "device_distance_i2c_bus_num": "serial:0006634723",
                    "i2c_bus_kind": "extended",
                    "i2c_bus_num": "serial:0003171140",
                }
            },
            "display_config": {},
            "probe_map": {"probe_devices": [], "probe_info": []},
            "probes_units": "F",
        }
    )

    resp = client.get("/api/wizard/state")
    body = resp.get_json()
    assert body["has_draft"] is False
    assert body["settings_dep_values"]["grillplatform"]["device_distance_i2c_bus"] == {
        "kind": "kernel",
        "serial": "0006634723",
    }
    assert ds.get_blob("wizard:install") is None


def test_draft_with_matching_keys_is_not_stale_and_is_preferred(ds, client):
    _seed_x86_numato_live_settings()
    draft = {
        "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
        "settings_dep_values": {"grillplatform": {"i2c_bus": {"kind": "mcp2221", "serial": "DRAFT-VALUE"}}},
        "display_config": {},
        "probe_map": {"probe_devices": [], "probe_info": []},
        "probes_units": "F",
    }
    assert client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json").status_code == 200

    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    # The draft's value wins over the differently-configured live settings.
    assert body["settings_dep_values"]["grillplatform"]["i2c_bus"] == {"kind": "mcp2221", "serial": "DRAFT-VALUE"}
    assert ds.get_blob("wizard:install") is not None


def test_unstamped_draft_is_discarded_even_when_every_key_is_valid(ds, client):
    """The rule, stated directly: a draft with no manifest stamp cannot be
    shown to have been written against this manifest, so it goes -- however
    well its keys happen to line up."""
    _seed_x86_numato_live_settings()
    from common.datastore_accessors import store_wizard_install_info

    store_wizard_install_info(
        {
            "react_draft": True,
            "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
            "settings_dep_values": {"grillplatform": {"i2c_bus": {"kind": "mcp2221", "serial": "DRAFT-VALUE"}}},
            "display_config": {},
            "probe_map": {"probe_devices": [], "probe_info": []},
            "probes_units": "F",
        }
    )

    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is False
    assert ds.get_blob("wizard:install") is None


def test_draft_with_only_recognized_probe_config_keys_survives(ds, client):
    draft = {
        "selections": {"grillplatform": None, "display": None, "distance": None, "probes": "ads1115_adafruit"},
        "settings_dep_values": {},
        "display_config": {},
        "probe_map": {
            "probe_devices": [
                {
                    "device": "D1",
                    "module": "ads1115_adafruit",
                    "module_filename": "ads1115_adafruit",
                    "config": {"i2c_bus": {"kind": "basic"}, "i2c_bus_addr": "0x48"},
                    "ports": ["ADC0"],
                }
            ],
            "probe_info": [],
        },
        "probes_units": "F",
    }
    assert client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json").status_code == 200

    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    assert ds.get_blob("wizard:install") is not None


def _wizard_data_with_probe_config(label, description="I2C Bus", default=""):
    return {
        "modules": {
            "grillplatform": {"x86_numato": {"settings_dependencies": {"i2c_bus": {}}}},
            "probes": {
                "ads1115_adafruit": {
                    "settings_dependencies": {},
                    "device_specific": {"config": [{"label": label, "description": description, "default": default}]},
                }
            },
        }
    }


def test_manifest_fingerprint_covers_probe_config_labels():
    """A drafted probe device keys its config by these labels, and the stamp
    is the only thing watching them -- rename one and every draft bound to
    the old name must read as stale."""
    from blueprints.api_wizard.routes import _manifest_fingerprint

    assert _manifest_fingerprint(_wizard_data_with_probe_config("i2c_bus_addr")) != _manifest_fingerprint(
        _wizard_data_with_probe_config("i2c_bus_address")
    )
    # Only the names bind a draft's values, so everything around them is
    # deliberately outside the hash.
    assert _manifest_fingerprint(
        _wizard_data_with_probe_config("i2c_bus_addr", description="Address", default="0x48")
    ) == _manifest_fingerprint(
        _wizard_data_with_probe_config("i2c_bus_addr", description="Bus Address", default="0x49")
    )


def test_draft_stamp_written_by_save_draft_round_trips_and_is_not_stale(ds, client):
    _seed_x86_numato_live_settings()
    draft = {
        "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
        "settings_dep_values": {"grillplatform": {"i2c_bus": {"kind": "basic"}}},
        "display_config": {},
        "probe_map": {"probe_devices": [], "probe_info": []},
        "probes_units": "F",
    }
    r = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r.status_code == 200

    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True


def test_draft_with_wrong_stamp_is_stale(ds, client):
    from common.datastore_accessors import load_wizard_install_info, store_wizard_install_info

    draft = {
        "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
        "settings_dep_values": {"grillplatform": {"i2c_bus": {"kind": "basic"}}},
        "display_config": {},
        "probe_map": {"probe_devices": [], "probe_info": []},
        "probes_units": "F",
    }
    r = client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")
    assert r.status_code == 200

    info = load_wizard_install_info()
    info["manifest_fingerprint"] = "not-a-real-fingerprint"
    store_wizard_install_info(info)

    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is False
    assert ds.get_blob("wizard:install") is None


def test_stale_draft_discard_logs_an_operator_facing_line(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    logged = []
    monkeypatch.setattr(wr, "write_log", lambda event, *a, **k: logged.append(event))

    _seed_x86_numato_live_settings()
    from common.datastore_accessors import store_wizard_install_info

    store_wizard_install_info(
        {
            "react_draft": True,
            "selections": {"grillplatform": "x86_numato", "display": None, "distance": None, "probes": None},
            "settings_dep_values": {
                "grillplatform": {
                    "device_distance_i2c_bus_kind": "extended",
                    "device_distance_i2c_bus_num": "serial:0006634723",
                    "i2c_bus_kind": "extended",
                    "i2c_bus_num": "serial:0003171140",
                }
            },
            "display_config": {},
            "probe_map": {"probe_devices": [], "probe_info": []},
            "probes_units": "F",
        }
    )

    resp = client.get("/api/wizard/state")
    assert resp.get_json()["has_draft"] is False
    assert len(logged) == 1
    assert "draft" in logged[0].lower()
    assert "current configuration" in logged[0].lower()


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
        data=json.dumps({"kind": "kernel"}),
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
        data=json.dumps({"kind": "kernel"}),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["error"] == "No devices found."


def test_finish_blocked_when_not_stopped(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))  # neutralize installer
    from common.datastore_accessors import read_control, write_control_snapshot

    ctrl = read_control()
    ctrl["mode"] = "Hold"
    write_control_snapshot(ctrl, origin="test")
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
            "module_filename": "ads1115_adafruit",
            "config": {"i2c_bus": {"kind": "basic"}},
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
        data=json.dumps(
            {
                "probe_devices": [
                    {
                        "device": "D1",
                        "module": "ads1115_adafruit",
                        "module_filename": "ads1115_adafruit",
                        "config": {"i2c_bus": {"kind": "basic"}},
                        "ports": ["ADC0"],
                    }
                ]
            }
        ),
        content_type="application/json",
    )
    body = resp.get_json()
    assert body["ok"] is False
    assert "USB-HID" in body["detail"]


def test_finish_rejects_a_real_basic_plus_ft232h_conflict(ds, client, monkeypatch):
    # A probe on the FT232H beside a distance sensor left on 'basic' -- the one
    # unworkable combo (Blinka's board backend is process-global) -- must be
    # caught by the REAL (unmocked) wizard_bus_kinds/validate_bus_kinds path at
    # /finish, not just by a test that monkeypatches the check away.
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    payload = {
        "selections": {"grillplatform": "custom", "display": "ili9341b", "distance": "vl53l0x"},
        "settings_dep_values": {"grillplatform": {"device_distance_i2c_bus": {"kind": "basic"}}},
        "display_config": {},
        "probe_map": {
            "probe_devices": [
                {
                    "device": "D1",
                    "module": "ads1115_adafruit",
                    "module_filename": "ads1115_adafruit",
                    "config": {"i2c_bus": {"kind": "ft232h", "url": ""}},
                    "ports": ["ADC0"],
                }
            ],
            "probe_info": [],
        },
    }
    resp = client.post("/api/wizard/finish", data=json.dumps(payload), content_type="application/json")
    assert resp.status_code == 422
    body = resp.get_json()
    assert body["message"] == "bus_conflict"
    assert "process-global" in body["detail"]
    assert fired == []  # installer must NOT fire


def test_finish_accepts_mcp2221_pwm_when_distance_is_disabled(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    fired = []
    monkeypatch.setattr(wr.os, "system", lambda cmd: fired.append(cmd))
    payload = {
        "selections": {
            "grillplatform": "mcp2221_relay",
            "display": "ili9341b",
            "distance": "none",
        },
        "settings_dep_values": {
            "grillplatform": {
                "fan_mode": "EMC2101",
                "fan_i2c_bus": {"kind": "mcp2221", "serial": ""},
                "device_distance_i2c_bus": {"kind": "basic"},
            }
        },
        "display_config": {},
        "probe_map": {"probe_devices": [], "probe_info": []},
    }

    resp = client.post("/api/wizard/finish", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    assert resp.get_json() == {"result": "success"}
    assert len(fired) == 1


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


def test_module_values_grillplatform_imposes_the_selected_platform(ds, client):
    # Reported: picking FT232H IO-Triggered Relay came back as "custom" on the
    # next wizard run, and the controller loaded the prototype platform. The
    # switch handed back platform.current/system_type as they stood for the
    # platform being left, and both are hidden, so nothing in the wizard ever
    # corrected them before the installer wrote them straight back.
    settings = read_settings()
    settings["platform"]["current"] = "custom"
    settings["platform"]["system_type"] = "prototype"
    write_settings_store(settings)

    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "ft232h_relay"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    deps = resp.get_json()["settings"]
    assert deps["current"] == "ft232h_relay"
    assert deps["system_type"] == "ft232h_relay"


def test_module_values_mcp2221_relay_uses_distinct_pins_and_keeps_serial(ds, client):
    settings = read_settings()
    settings["platform"]["mcp2221"]["serial"] = "RELAY-B"
    settings["platform"]["outputs"] = {
        "power": 17,
        "igniter": 27,
        "auger": 23,
        "fan": 18,
    }
    write_settings_store(settings)

    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "mcp2221_relay"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    deps = resp.get_json()["settings"]
    assert deps["mcp2221_serial"] == "RELAY-B"
    assert {output: deps[f"output_{output}"] for output in ("power", "igniter", "auger", "fan")} == {
        "power": "GP0",
        "igniter": "GP1",
        "auger": "GP2",
        "fan": "GP3",
    }


def test_module_values_grillplatform_imposes_the_boards_own_pins(ds, client):
    # The same switch in the other direction: FT232H addresses outputs by name
    # ("C0"), a PCB board by BCM number, so leaving the live value in place
    # would write FT232H pin names onto a Raspberry Pi board.
    settings = read_settings()
    settings["platform"]["outputs"]["auger"] = "C2"
    settings["platform"]["system_type"] = "ft232h_relay"
    write_settings_store(settings)

    resp = client.post(
        "/api/wizard/module-values",
        data=json.dumps({"section": "grillplatform", "module": "pcb_4.x.x"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    deps = resp.get_json()["settings"]
    assert deps["output_auger"] == "23"
    # system_type's options are the two this board runs on; ft232h_relay is not
    # one of them, so the board's own first option wins.
    assert deps["system_type"] == "raspberry_pi_all"


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


def test_installstatus_before_any_install_preserves_null_fields(ds, client):
    response = client.get("/api/wizard/installstatus")

    assert response.status_code == 200
    body = response.get_json()
    assert body == {"percent": None, "status": None, "output": None}
    assert InstallStatus.model_validate(body, strict=True).model_dump(mode="json") == body


def test_installlog_serves_the_current_run_incrementally(ds, client, monkeypatch):
    """The panel behind "Show output" polls this while an install runs. It reads
    the log file rather than the install-status blob, because that blob holds one
    line at a time and a 250ms poll of it samples the output instead of
    transcribing it."""
    import blueprints.api_wizard.routes as wr

    seen = []

    def _fake_read(offset):
        seen.append(offset)
        return "Resolved 12 packages\n", 512, False

    monkeypatch.setattr(wr, "read_install_log", _fake_read)
    body = client.get("/api/wizard/installlog?offset=128").get_json()

    assert seen == [128]
    assert body == {"text": "Resolved 12 packages\n", "offset": 512, "reset": False}


def test_installlog_without_an_offset_reads_from_the_start_of_the_run(ds, client, monkeypatch):
    import blueprints.api_wizard.routes as wr

    seen = []
    monkeypatch.setattr(wr, "read_install_log", lambda offset: (seen.append(offset), ("", 0, False))[1])

    client.get("/api/wizard/installlog")
    # A junk offset must not 500 -- type=int yields None, same as absent.
    client.get("/api/wizard/installlog?offset=banana")

    assert seen == [0, 0]


def test_installlog_before_any_install_is_empty_rather_than_an_error(ds, client):
    """First-time setup reaches this endpoint with no wizard.log on disk."""
    body = client.get("/api/wizard/installlog").get_json()

    assert body["text"] == ""
    assert body["offset"] == 0


def test_scan_kernel_offers_all_three_ways_to_address_an_adapter(client, monkeypatch):
    monkeypatch.setattr(
        "blueprints.api_wizard.routes.discover_extended_i2c_buses",
        lambda: [{"bus_num": 7, "name": "CP2112 SMBus Bridge", "serial": "AB12"}],
    )
    body = client.post("/api/wizard/scan", json={"kind": "kernel"}).get_json()
    titles = [group["title"] for group in body["groups"]]
    assert titles == ["By Bus Number", "By Adapter Name", "By Serial"]
    by_title = {group["title"]: group["items"] for group in body["groups"]}
    assert by_title["By Bus Number"][0]["value"] == "7"
    assert by_title["By Adapter Name"][0]["value"] == "CP2112 SMBus Bridge"
    assert by_title["By Serial"][0]["value"] == "AB12"


def test_scan_no_longer_answers_to_the_old_kind_name(client):
    body = client.post("/api/wizard/scan", json={"kind": "extended"}).get_json()
    assert body["groups"] == []


def _assert_wire_round_trip(model, payload):
    validated = model.model_validate(payload, strict=True)
    assert validated.model_dump(mode="json", by_alias=True, exclude_unset=True) == payload


def test_state_response_round_trips_through_the_wizard_contract(ds, client):
    body = client.get("/api/wizard/state").get_json()

    _assert_wire_round_trip(WizardState, body)


@pytest.mark.parametrize(
    "bus",
    [
        {"kind": "basic"},
        {"kind": "kernel", "bus_num": 7},
        {"kind": "kernel", "bus_num": None},
        {"kind": "kernel", "adapter": "CP2112"},
        {"kind": "kernel", "serial": "AB12"},
        {"kind": "ft232h", "url": ""},
        {"kind": "mcp2221", "serial": ""},
    ],
)
def test_draft_contract_preserves_every_i2c_discriminator_variant(ds, client, bus):
    payload = {
        "settings_dep_values": {
            "grillplatform": {
                "i2c_bus": bus,
            },
        },
    }

    assert client.post("/api/wizard/draft", json=payload).status_code == 200
    body = client.get("/api/wizard/state").get_json()
    assert body["settings_dep_values"]["grillplatform"]["i2c_bus"] == bus
    _assert_wire_round_trip(WizardState, body)


def test_draft_contract_distinguishes_absent_empty_and_null_members(ds, client):
    absent = client.post("/api/wizard/draft", json={})
    assert absent.status_code == 200
    assert client.get("/api/wizard/state").get_json()["settings_dep_values"] == {}

    empty = client.post("/api/wizard/draft", json={"settings_dep_values": {}})
    assert empty.status_code == 200
    assert client.get("/api/wizard/state").get_json()["settings_dep_values"] == {}

    null = client.post("/api/wizard/draft", json={"settings_dep_values": None})
    assert null.status_code == 400
    assert null.get_json()["message"] == "invalid_request"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("draft", {"clear": "yes"}),
        ("cancel", {"unexpected": True}),
        ("scan", {"kind": 1}),
        ("module-values", {"section": "display", "module": 1}),
        ("finish", {"selections": []}),
        ("scan/bluetooth", {"unexpected": True}),
        ("probes/validate-bus-kinds", {"probe_devices": {}}),
        ("scan/thermoworks", {"email": [], "password": "secret"}),
    ],
)
def test_wizard_post_routes_reject_invalid_json_contracts(ds, client, path, payload):
    response = client.post(f"/api/wizard/{path}", json=payload)

    assert response.status_code == 400
    assert response.get_json()["message"] == "invalid_request"


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("null", "application/json"),
        ("[1]", "application/json"),
        ("{", "application/json"),
        ("not-json", "text/plain"),
    ],
)
def test_present_invalid_bodies_are_rejected_before_cancel_side_effects(ds, client, body, content_type):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = True
    write_settings_store(settings)

    response = client.post("/api/wizard/cancel", data=body, content_type=content_type)

    assert response.status_code == 400
    assert response.get_json() == {"result": "error", "message": "invalid_request"}
    assert read_settings()["globals"]["first_time_setup"] is True


@pytest.mark.parametrize("path", ["draft", "scan", "scan/thermoworks"])
def test_defaulted_wizard_requests_reject_a_genuinely_absent_body(ds, client, path):
    response = client.post(f"/api/wizard/{path}")

    assert response.status_code == 400
    assert response.get_json()["message"] == "invalid_request"


def test_cancel_retains_legacy_absent_body_compatibility(ds, client):
    response = client.post("/api/wizard/cancel")

    assert response.status_code == 200
    assert response.get_json()["result"] == "success"


def test_static_wizard_response_contracts_pin_null_and_omission():
    _assert_wire_round_trip(
        ScanResult,
        {"groups": [{"title": "USB Serial Devices", "items": []}], "error": None},
    )
    _assert_wire_round_trip(
        ModuleValues,
        {"settings": {"device": None}, "config": {}},
    )
    _assert_wire_round_trip(
        InstallStatus,
        {"percent": 0, "status": "Starting Install...", "output": ""},
    )
    _assert_wire_round_trip(
        InstallLog,
        {"text": "", "offset": 0, "reset": False},
    )
    _assert_wire_round_trip(
        RowsResult,
        {"rows": [{"name": "IBBQ", "hw_id": "AA:BB", "info": ""}], "error": None},
    )
    _assert_wire_round_trip(BusKindsValidationResponse, {"ok": True})
    _assert_wire_round_trip(WizardActionResponse, {"result": "success"})
    _assert_wire_round_trip(
        WizardActionResponse,
        {"result": "error", "message": "bus_conflict", "detail": "mixed buses", "sections": []},
    )


def test_draft_request_contract_rejects_non_finite_plugin_values():
    with pytest.raises(ValueError):
        WizardDraftRequest.model_validate(
            {"display_config": {"display": {"brightness": float("nan")}}},
            strict=True,
        )
