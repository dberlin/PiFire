"""Playwright/HTTP characterization coverage for the setup-wizard page
(blueprints/wizard/routes.py's single `wizard_page` route, which handles the
`installstatus`, `cancel`, `finish`, `modulecard`, `bt_scan`,
`thermoworks_discover`, `i2c_bus_scan`, `usb_serial_scan` actions plus the
base render).

Follows the pattern established in test_page_settings.py / test_page_update.py;
see tests/web/conftest.py for the shared harness.

*** SAFETY-CRITICAL MODULE -- READ BEFORE EDITING ***

This route has the highest operational blast radius of any blueprint:

- `finish` (POST) runs ``os.system(f"{python_exec} wizard.py &")`` to kick off
  the REAL installer. In this harness `is_real_hardware()` is True (default
  settings ship ``platform.real_hw = True``) so ``python_exec`` is the seeded
  ``globals.python_exec`` -- i.e. an un-neutralized POST to ``/wizard/finish``
  would literally spawn ``<python_exec> wizard.py &`` as a background process.
- `bt_scan`, `thermoworks_discover`, `i2c_bus_scan`, `usb_serial_scan` all call
  real hardware / network discovery (bluetooth scan, ThermoWorks Cloud login,
  I2C/USB bus enumeration).

Every test in this module therefore runs under the `neutralize_wizard`
autouse fixture below, which:

1. Patches ``os.system`` **on the `os` module itself** to a recording fake that
   never executes -- proving interception (the recorded list is asserted), so
   nothing here ever spawns ``wizard.py``.
2. Patches, **on `blueprints.wizard.routes`** (these names are module-level
   ``from ... import ...`` bindings, so they must be patched on the wizard
   routes module, NOT their source modules -- patching the source would not
   rebind the already-imported name), the discovery entry points:
   ``discover`` (ThermoWorks, async), ``discover_extended_i2c_buses``,
   ``discover_mcp2221_devices``, ``discover_ft232h_devices``,
   ``discover_usb_serial_devices``, and for `bt_scan` ``get_supported_cmds`` +
   ``process_command`` + ``get_system_command_output``. Defaults return canned
   happy-path data; individual error-path tests override a single fake with a
   raising / empty variant via their own ``monkeypatch``.

These module-attribute patches are observed by `live_server`'s background
thread because it runs in THIS process against the SAME imported module objects
(see conftest.py's "thread-shared datastore" docs).

*** IMPORTANT FOR THE TASK-7b DISPATCH REFACTOR ***
The discovery / os.system patch targets above are anchored on
`blueprints.wizard.routes`. 7b MUST keep these handlers INLINE in that module
and keep ``import os`` (+ ``os.system``) and the discovery ``from ... import``
bindings at module level in `blueprints.wizard.routes`, or these mocks disarm
and a test run could spawn the real installer.

Actions covered: base GET render, `installstatus`, `cancel`, `finish`
(success / bus-conflict / blocked-when-active), `modulecard` (valid + invalid
section), `bt_scan` (happy + error), `thermoworks_discover` (happy + empty),
`i2c_bus_scan` (extended / mcp2221 / ft232h / unknown-kind), `usb_serial_scan`
(happy + empty).
"""

import pytest

from tests.web.conftest import apply_control, apply_settings, requires_chromium

pytestmark = requires_chromium


@pytest.fixture(autouse=True)
def neutralize_wizard(monkeypatch):
    """See module docstring. Neutralizes os.system + every discovery entry
    point for the whole module, recording os.system invocations without ever
    executing them, and returns handles so tests can assert on / override the
    fakes."""
    import os

    import blueprints.wizard.routes as wr

    os_system_calls = []

    def fake_system(command):
        os_system_calls.append(command)
        return 0

    # os.system patched on the os module itself so the route's `import os;
    # os.system(...)` call site (in the live_server thread) resolves to this
    # recorder. Real os.system is thus never invoked anywhere in this module.
    monkeypatch.setattr(os, "system", fake_system)

    # ---- ThermoWorks discovery (async; called via asyncio.run) ----
    async def fake_discover(email, password):
        return [
            {"label": "Signals Probe 1", "type": "Signals", "serial": "TW-SERIAL-1", "num_channels": 4},
        ]

    monkeypatch.setattr(wr, "discover", fake_discover)

    # ---- I2C bus discovery ----
    monkeypatch.setattr(
        wr,
        "discover_extended_i2c_buses",
        lambda: [{"bus_num": 7, "name": "FT232H-i2c", "serial": "FTSERIAL7"}],
    )
    monkeypatch.setattr(wr, "discover_mcp2221_devices", lambda: [{"serial": "MCP-SERIAL-9"}])
    monkeypatch.setattr(
        wr,
        "discover_ft232h_devices",
        lambda: [{"url": "ftdi://ftdi:232h/1", "description": "FT232H Board"}],
    )

    # ---- USB serial discovery ----
    monkeypatch.setattr(
        wr,
        "discover_usb_serial_devices",
        lambda vid=None, pid=None: [
            {"device": "/dev/ttyUSB0", "description": "CP2102 UART", "serial_number": "USBSER123"},
        ],
    )

    # ---- Bluetooth scan plumbing (bt_scan) ----
    monkeypatch.setattr(wr, "get_supported_cmds", lambda: ["scan_bluetooth"])
    monkeypatch.setattr(wr, "process_command", lambda *a, **k: None)
    monkeypatch.setattr(
        wr,
        "get_system_command_output",
        lambda *a, **k: {
            "result": "OK",
            "data": {
                "bt_devices": [
                    {"name": "iGrill Mini", "hw_id": "AA:BB:CC:DD:EE:FF", "info": "signal ok "},
                ]
            },
        },
    )

    from types import SimpleNamespace

    return SimpleNamespace(os_system_calls=os_system_calls, wr=wr, monkeypatch=monkeypatch)


# --- GET actions --------------------------------------------------------


def test_installstatus_returns_seeded_status(live_server, page, neutralize_wizard):
    from common.datastore_accessors import set_wizard_install_status

    set_wizard_install_status(37, "Installing...", "some output line")

    resp = page.request.get(f"{live_server}/wizard/installstatus")

    assert resp.status == 200
    assert resp.json() == {"percent": 37, "status": "Installing...", "output": "some output line"}
    assert neutralize_wizard.os_system_calls == []


def test_default_render_returns_wizard_page(live_server, page, neutralize_wizard):
    resp = page.request.get(f"{live_server}/wizard/")

    assert resp.status == 200
    body = resp.text()
    assert "Config Wizard" in body  # wizard.html title/header
    assert neutralize_wizard.os_system_calls == []


# --- cancel -------------------------------------------------------------


def test_cancel_redirects_home_and_clears_first_time_setup(live_server, page, neutralize_wizard):
    apply_settings(lambda s: s["globals"].__setitem__("first_time_setup", True))

    resp = page.request.post(f"{live_server}/wizard/cancel", max_redirects=0)

    assert resp.status == 302
    assert resp.headers["location"] == "/"
    from tests.web.conftest import read_settings_from_server

    assert read_settings_from_server()["globals"]["first_time_setup"] is False
    assert neutralize_wizard.os_system_calls == []


# --- finish (the operationally dangerous action) ------------------------
#
# prepare_wizard_data() reads load_wizard_install_info(), so every finish test
# first GETs /wizard/ (whose tail render stores a valid wizardInstallInfo with
# a probe_map) before POSTing finish.


def _seed_wizard_install_info(live_server, page):
    resp = page.request.get(f"{live_server}/wizard/")
    assert resp.status == 200


_VALID_FINISH_FORM = {
    "grillplatformSelect": "custom",
    "displaySelect": "none",
    "distanceSelect": "none",
    "probes_units": "F",
}


def test_finish_success_starts_install_and_renders_finish_page(live_server, page, neutralize_wizard):
    """Control mode STOP + a form that (with validate_bus_kinds no-op'd) passes
    the whole-config bus check. Exercises the REAL prepare_wizard_data /
    store_wizard_install_info / set_wizard_install_status path; only
    validate_bus_kinds is stubbed to a no-op so the success branch is
    deterministic (as permitted by the task brief)."""
    apply_control(lambda c: c.__setitem__("mode", "Stop"))
    _seed_wizard_install_info(live_server, page)
    neutralize_wizard.monkeypatch.setattr(neutralize_wizard.wr, "validate_bus_kinds", lambda kinds: None)

    resp = page.request.post(f"{live_server}/wizard/finish", form=_VALID_FINISH_FORM)

    assert resp.status == 200
    body = resp.text()
    assert "Starting Install..." in body  # wizard-finish.html rendered
    assert "I2C Bus Configuration Error" not in body
    # The install kickoff was recorded (intercepted), never really spawned.
    assert len(neutralize_wizard.os_system_calls) == 1
    assert neutralize_wizard.os_system_calls[0].endswith("wizard.py &")


def test_finish_bus_conflict_renders_error_and_starts_no_install(live_server, page, neutralize_wizard):
    from common.i2c_bus import I2CBusConfigError

    apply_control(lambda c: c.__setitem__("mode", "Stop"))
    _seed_wizard_install_info(live_server, page)

    def raise_conflict(kinds):
        raise I2CBusConfigError("basic and usb-hid cannot share a bus")

    neutralize_wizard.monkeypatch.setattr(neutralize_wizard.wr, "validate_bus_kinds", raise_conflict)

    resp = page.request.post(f"{live_server}/wizard/finish", form=_VALID_FINISH_FORM)

    assert resp.status == 200
    body = resp.text()
    assert "I2C Bus Configuration Error" in body
    assert "basic and usb-hid cannot share a bus" in body
    # Conflict path returns BEFORE os.system -- no install kicked off.
    assert neutralize_wizard.os_system_calls == []


def test_finish_blocked_when_system_active_falls_through(live_server, page, neutralize_wizard):
    """control mode != STOP skips the whole finish block and falls through to
    the default wizard.html render carrying the 'cannot be run while the system
    is active' error, with NO os.system call. (This fall-through is behavior
    the 7b dispatch refactor must preserve -- pinned here.)"""
    apply_control(lambda c: c.__setitem__("mode", "Startup"))
    try:
        resp = page.request.post(f"{live_server}/wizard/finish", form=_VALID_FINISH_FORM)

        assert resp.status == 200
        assert "cannot be run while the system is active" in resp.text()
        assert neutralize_wizard.os_system_calls == []
    finally:
        # Module-scoped live_server: restore STOP so sibling tests aren't left
        # with an active-system datastore.
        apply_control(lambda c: c.__setitem__("mode", "Stop"))


# --- modulecard ---------------------------------------------------------


def test_modulecard_valid_section_renders_card(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/modulecard",
        form={"module": "custom", "section": "grillplatform"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "Custom Build" in body  # friendly_name of grillplatform/custom
    assert body.strip() != '<strong color="red">No Data</strong>'
    assert neutralize_wizard.os_system_calls == []


def test_modulecard_invalid_section_returns_no_data(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/modulecard",
        form={"module": "custom", "section": "bogus_section"},
    )

    assert resp.status == 200
    assert resp.text() == '<strong color="red">No Data</strong>'
    assert neutralize_wizard.os_system_calls == []


# --- bt_scan ------------------------------------------------------------


def test_bt_scan_happy_path_renders_device_table(live_server, page, neutralize_wizard):
    resp = page.request.post(f"{live_server}/wizard/bt_scan", form={"itemID": "probe0"})

    assert resp.status == 200
    body = resp.text()
    assert "iGrill Mini" in body
    assert "AA:BB:CC:DD:EE:FF" in body
    assert "alert-danger" not in body
    assert neutralize_wizard.os_system_calls == []


def test_bt_scan_error_path_when_unsupported(live_server, page, neutralize_wizard):
    neutralize_wizard.monkeypatch.setattr(neutralize_wizard.wr, "get_supported_cmds", lambda: [])

    resp = page.request.post(f"{live_server}/wizard/bt_scan", form={"itemID": "probe0"})

    assert resp.status == 200
    body = resp.text()
    assert "alert-danger" in body
    assert "No support for bluetooth scan command." in body
    assert neutralize_wizard.os_system_calls == []


# --- thermoworks_discover -----------------------------------------------


def test_thermoworks_discover_happy_path(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/thermoworks_discover",
        form={"email": "a@b.c", "password": "x", "serialID": "sid", "numProbesID": "nid"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "Signals Probe 1" in body
    assert "TW-SERIAL-1" in body
    assert "alert-danger" not in body
    assert neutralize_wizard.os_system_calls == []


def test_thermoworks_discover_empty_reports_error(live_server, page, neutralize_wizard):
    async def fake_discover_empty(email, password):
        return []

    neutralize_wizard.monkeypatch.setattr(neutralize_wizard.wr, "discover", fake_discover_empty)

    resp = page.request.post(
        f"{live_server}/wizard/thermoworks_discover",
        form={"email": "a@b.c", "password": "x", "serialID": "sid", "numProbesID": "nid"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "alert-danger" in body
    assert "No ThermoWorks Cloud devices found for this account." in body
    assert neutralize_wizard.os_system_calls == []


# --- i2c_bus_scan -------------------------------------------------------


def test_i2c_bus_scan_extended_renders_groups(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/i2c_bus_scan",
        form={"itemID": "probe0", "kind": "extended"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "By Bus Number" in body
    assert "i2c-7 (FT232H-i2c)" in body
    assert "By Serial" in body
    assert "alert-danger" not in body
    assert neutralize_wizard.os_system_calls == []


def test_i2c_bus_scan_mcp2221_renders_group(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/i2c_bus_scan",
        form={"itemID": "probe0", "kind": "mcp2221"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "MCP2221 Devices" in body
    assert "MCP2221 serial MCP-SERIAL-9" in body
    assert neutralize_wizard.os_system_calls == []


def test_i2c_bus_scan_ft232h_renders_group(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/i2c_bus_scan",
        form={"itemID": "probe0", "kind": "ft232h"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "FT232H Devices" in body
    assert "FT232H Board" in body
    assert neutralize_wizard.os_system_calls == []


def test_i2c_bus_scan_unknown_kind_reports_error(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/i2c_bus_scan",
        form={"itemID": "probe0", "kind": "bogus"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "alert-danger" in body
    assert "Unknown I2C bus kind" in body
    assert neutralize_wizard.os_system_calls == []


# --- usb_serial_scan ----------------------------------------------------


def test_usb_serial_scan_happy_path(live_server, page, neutralize_wizard):
    resp = page.request.post(
        f"{live_server}/wizard/usb_serial_scan",
        form={"itemID": "probe0"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "/dev/ttyUSB0" in body
    assert "CP2102 UART" in body
    assert "All Serial Devices" in body  # no vid/pid filter
    assert "alert-danger" not in body
    assert neutralize_wizard.os_system_calls == []


def test_usb_serial_scan_empty_reports_error(live_server, page, neutralize_wizard):
    neutralize_wizard.monkeypatch.setattr(
        neutralize_wizard.wr, "discover_usb_serial_devices", lambda vid=None, pid=None: []
    )

    resp = page.request.post(
        f"{live_server}/wizard/usb_serial_scan",
        form={"itemID": "probe0"},
    )

    assert resp.status == 200
    body = resp.text()
    assert "alert-danger" in body
    assert "No serial devices found." in body
    assert neutralize_wizard.os_system_calls == []
