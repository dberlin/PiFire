import pytest

from app import app as flask_app
from common.common import WriteKind
from common.datastore_accessors import (
    execute_control_writes,
    read_control,
    read_settings,
    write_control,
    write_settings_store,
)
from common.defaults import default_notify, default_probe_config
from common.modes import Mode

PROFILE_ID = "TWPS00"


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_probe_modules_lists_every_manifest_module(ds, client):
    resp = client.get("/api/probe_modules")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OK"
    modules = body["data"]["modules"]
    # 18 probe modules ship in wizard/wizard_manifest.json (verified 2026-07-26).
    assert len(modules) == 18
    assert "ds18b20" in modules and "virtual_average" in modules
    # The card components read exactly these keys.
    ds18b20 = modules["ds18b20"]
    assert ds18b20["friendly_name"]
    assert ds18b20["filename"] == "ds18b20"
    assert ds18b20["device_specific"]["ports"] == ["DS0"]
    assert isinstance(ds18b20["device_specific"]["config"], list)


def test_probe_modules_flags_which_modules_need_the_wizard(ds, client):
    body = client.get("/api/probe_modules").get_json()
    req = body["data"]["requires_install"]
    # The six dep-free modules, verified against the manifest 2026-07-26.
    for free in (
        "max31865",
        "prototype",
        "virtual_average",
        "virtual_highest",
        "virtual_lowest",
        "virtual_median",
    ):
        assert req[free] is False, free
    # ds18b20 has a py_dependency AND a command_list entry; bt_ibbq has all three.
    assert req["ds18b20"] is True
    assert req["bt_ibbq"] is True
    assert req["thermoworks_cloud"] is True
    assert set(req) == set(body["data"]["modules"])


def _profile():
    return read_settings()["probe_settings"]["probe_profiles"][PROFILE_ID].copy()


def _map(devices=None, probes=None):
    return {"probe_devices": devices or [], "probe_info": probes or []}


def _virtual_device(name="VirtDev", probes_list=()):
    # virtual_average declares no py/apt/command dependencies, so it is one of
    # the six modules addable without the wizard.
    return {
        "config": {"probes_list": list(probes_list)},
        "device": name,
        "module": "virtual_average",
        "module_filename": "virtual_average",
        "ports": ["VIRT0"],
    }


def _probe(name, device, port, probe_type="Primary"):
    return {
        "name": name,
        "label": name,
        "device": device,
        "port": port,
        "type": probe_type,
        "enabled": True,
        "profile": _profile(),
    }


def _set_mode(mode):
    control = read_control()
    control["mode"] = mode
    write_control(control, WriteKind.OVERWRITE, origin="test")


def _stop_mode():
    _set_mode(Mode.STOP)


def test_apply_writes_live_settings_and_flags_the_controller(ds, client):
    _stop_mode()
    new_map = _map([_virtual_device()], [_probe("Grill", "VirtDev", "VIRT0")])

    resp = client.post("/api/probe_map", json={"probe_map": new_map})

    assert resp.status_code == 200
    assert resp.get_json()["result"] == "success"
    stored = read_settings()["probe_settings"]["probe_map"]
    assert [d["device"] for d in stored["probe_devices"]] == ["VirtDev"]
    assert [p["label"] for p in stored["probe_info"]] == ["Grill"]
    # The flag the controller acts on (Task 3). save_settings_and_flag_update
    # QUEUES a named-flag delta (common/app.py:419) rather than overwriting
    # control:general, so the queue has to be drained before it is visible --
    # in production that drain is the control loop's own execute_control_writes.
    execute_control_writes()
    assert read_control()["probe_map_update"] is True


def test_apply_regenerates_the_history_probe_config(ds, client):
    _stop_mode()
    client.post(
        "/api/probe_map",
        json={"probe_map": _map([_virtual_device()], [_probe("Grill", "VirtDev", "VIRT0")])},
    )
    # wizard.py:230 does exactly this after writing the map; a probe map written
    # without it leaves the history chart configured for probes that are gone.
    assert set(read_settings()["history_page"]["probe_config"]) == {"Grill"}


def test_apply_refuses_while_the_grill_is_running(ds, client):
    _set_mode(Mode.SMOKE)
    before = read_settings()["probe_settings"]["probe_map"]

    resp = client.post("/api/probe_map", json={"probe_map": _map()})

    assert resp.status_code == 409
    assert resp.get_json()["message"] == "system_active"
    assert read_settings()["probe_settings"]["probe_map"] == before


def test_apply_refuses_a_module_that_needs_the_installer(ds, client):
    _stop_mode()
    before = read_settings()["probe_settings"]["probe_map"]
    bt = {
        "config": {},
        "device": "MeaterProbe",
        "module": "bt_meater",
        "module_filename": "bt_meater",
        "ports": ["BT_Tip", "BT_Ambient"],
    }

    resp = client.post("/api/probe_map", json={"probe_map": _map([bt])})

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["message"] == "modules_require_install"
    assert body["modules"] == ["bt_meater"]
    assert read_settings()["probe_settings"]["probe_map"] == before


def test_apply_allows_a_module_that_is_already_installed(ds, client):
    """A module already in the LIVE map has necessarily been installed, so it
    may be re-sent even though its manifest declares dependencies."""
    _stop_mode()
    settings = read_settings()
    ds18b20 = {
        "config": {"transient": "False"},
        "device": "TempSensor",
        "module": "ds18b20",
        "module_filename": "ds18b20",
        "ports": ["DS0"],
    }
    settings["probe_settings"]["probe_map"] = _map([ds18b20])
    write_settings_store(settings)

    resp = client.post(
        "/api/probe_map",
        json={"probe_map": _map([ds18b20], [_probe("Grill", "TempSensor", "DS0")])},
    )

    assert resp.status_code == 200
    assert [p["label"] for p in read_settings()["probe_settings"]["probe_map"]["probe_info"]] == ["Grill"]


def test_apply_rejects_a_bus_kind_conflict(ds, client):
    """FULL cross-subsystem check here, unlike the wizard's in-progress
    settings=None check (api_wizard/routes.py:475-487): this writes LIVE
    config, so the live fan/distance kinds are exactly what it must consider."""
    _stop_mode()
    settings = read_settings()
    settings["platform"].setdefault("devices", {}).setdefault("distance", {})["i2c_bus"] = {"kind": "basic"}
    write_settings_store(settings)
    adc = {
        "config": {"i2c_bus": {"kind": "ft232h", "url": ""}},
        "device": "Adc",
        "module": "prototype",
        "module_filename": "prototype",
        "ports": ["ADC0", "ADC1", "ADC2", "ADC3"],
    }

    resp = client.post("/api/probe_map", json={"probe_map": _map([adc])})

    assert resp.status_code == 422
    assert resp.get_json()["message"] == "bus_conflict"
    assert "basic" in resp.get_json()["detail"]


def test_apply_rejects_a_malformed_map(ds, client):
    _stop_mode()
    for bad in (
        {"probe_map": None},
        {"probe_map": {"probe_devices": {}}},
        {"probe_map": {"probe_devices": [], "probe_info": "nope"}},
    ):
        resp = client.post("/api/probe_map", json=bad)
        assert resp.status_code == 400, bad
        assert resp.get_json()["message"] == "bad_probe_map"


"""Ruling 6 (2026-07-26, docs/superpowers/backlogs/react-migration-backlog.md):
renaming a probe must not leave a stale reference behind. The map used to be
swapped in with only history_page.probe_config regenerated, so every OTHER
structure keyed by a probe LABEL kept naming a probe that no longer exists."""


def _seed_two_probes():
    """A known map (Grill + Probe1) with every derived structure consistent
    with it, so these tests never depend on the developer-machine settings.json
    the `ds` fixture seeds from."""
    settings = read_settings()
    settings["probe_settings"]["probe_map"] = _map(
        [_virtual_device()],
        [_probe("Grill", "VirtDev", "VIRT0"), _probe("Probe1", "VirtDev", "VIRT0", "Food")],
    )
    settings["history_page"]["probe_config"] = default_probe_config(settings)
    settings["recipe"]["probe_map"] = {"primary": "Grill", "food": ["Probe1"]}
    settings["dashboard"]["dashboards"]["Default"]["custom"]["hidden_cards"] = ["Probe1", "hopper"]
    write_settings_store(settings)

    control = read_control()
    control["mode"] = Mode.STOP
    control["notify_data"] = default_notify(settings)
    for entry in control["notify_data"]:
        if entry["label"] == "Grill" and entry["type"] == "probe":
            entry["req"] = True
            entry["target"] = 225
    write_control(control, WriteKind.OVERWRITE, origin="test")
    return settings


def _renamed_map():
    """The same map with Probe1 renamed to Brisket -- the only edit."""
    return _map(
        [_virtual_device()],
        [_probe("Grill", "VirtDev", "VIRT0"), _probe("Brisket", "VirtDev", "VIRT0", "Food")],
    )


def test_rename_leaves_no_stale_notify_entry(ds, client):
    _seed_two_probes()

    assert client.post("/api/probe_map", json={"probe_map": _renamed_map()}).status_code == 200

    execute_control_writes()
    probe_labels = {e["label"] for e in read_control()["notify_data"] if e["type"].startswith("probe")}
    # Probe1 is gone, so nothing may still be notifying on it -- and Brisket,
    # which is what the user now sees, must be notifiable at all.
    assert probe_labels == {"Grill", "Brisket"}


def test_rename_keeps_the_untouched_probes_notification(ds, client):
    """Reconciliation is not a factory reset: an edit that did not touch Grill
    must not silently clear the target the user set on it."""
    _seed_two_probes()

    client.post("/api/probe_map", json={"probe_map": _renamed_map()})

    execute_control_writes()
    grill = next(e for e in read_control()["notify_data"] if e["label"] == "Grill" and e["type"] == "probe")
    assert grill["req"] is True
    assert grill["target"] == 225


def test_rename_updates_the_recipe_probe_map(ds, client):
    """controller/runtime/controller.py:158 keys a recipe step's trigger_temps
    off these labels, so a stale one is a step waiting on a probe that never
    reports."""
    _seed_two_probes()

    client.post("/api/probe_map", json={"probe_map": _renamed_map()})

    assert read_settings()["recipe"]["probe_map"] == {"primary": "Grill", "food": ["Brisket"]}


def test_rename_drops_the_stale_hidden_card(ds, client):
    """hidden_cards holds card ids, and a probe card's id IS its label
    (blueprints/dash/templates/basic/dash_basic.html:18). Only ids the OLD map
    had as labels are pruned, so "hopper" survives."""
    _seed_two_probes()

    client.post("/api/probe_map", json={"probe_map": _renamed_map()})

    hidden = read_settings()["dashboard"]["dashboards"]["Default"]["custom"]["hidden_cards"]
    assert hidden == ["hopper"]


def test_removing_a_probe_takes_its_notify_entries_with_it(ds, client):
    _seed_two_probes()

    client.post(
        "/api/probe_map",
        json={"probe_map": _map([_virtual_device()], [_probe("Grill", "VirtDev", "VIRT0")])},
    )

    execute_control_writes()
    notify_data = read_control()["notify_data"]
    assert {e["label"] for e in notify_data if e["type"].startswith("probe")} == {"Grill"}
    # The non-probe entries default_notify() always appends are not probes and
    # must survive every map edit.
    assert {e["label"] for e in notify_data if not e["type"].startswith("probe")} == {"Timer", "Hopper", "Test"}


def test_apply_rejects_an_empty_body_before_any_handler(ds, client):
    """An empty JSON object never reaches _api_post_probe_map: api_page's POST
    branch does `if not request.json: abort(400)` (blueprints/api/routes.py),
    which is a bare Werkzeug 400 with an HTML body -- so this asserts the status
    only. Pinned because the four-guard order in the handler reads as if {} were
    its own "bad_probe_map" case, and it is not.
    """
    resp = client.post("/api/probe_map", json={})
    assert resp.status_code == 400
    assert resp.get_json() is None
