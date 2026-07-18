"""Playwright coverage for the `probeconfig_page` route
(blueprints/probeconfig/routes.py), a single POST-dispatched route with two
`section` values ("devices" / "ports") and 5+4 `action` branches, plus the
base GET render.

Unlike blueprints/settings/routes.py (this suite's exemplar,
test_page_settings.py), probeconfig_page is never navigated to directly by a
user: it has no full HTML page of its own (no <!doctype>, no navbar) -- its
GET/POST handlers only ever render two small Jinja macro fragments
(render_probe_devices / render_probe_ports from
probeconfig/_macro_probes_config.html). In production those fragments are
either rendered inline into blueprints/wizard/templates/wizard/wizard.html on
first load, or re-fetched into the wizard page's #probeDevicesCard /
#probePortsCard divs via probeconfig.js's `$(...).load("/probeconfig",
{...})` calls -- and jQuery's `.load()` always issues a POST once a data
object is passed, exactly like a plain HTML form post. So "the real UI" for
every action in this route already *is* a raw POST with a flat form-data
body; there is no advantage to reverse-engineering wizard-page navigation
first. Every test below drives `/probeconfig/` directly via
`page.request.post`/`page.request.get`, using the exact field names read out
of probeconfig.js and probeconfig/_macro_probes_config.html (see the ~L255
`probe_config_` fields and ~L175 `probes_devspec_` fields).

The store: `wizardInstallInfo`, NOT live settings
--------------------------------------------------
Every action here reads/writes `load_wizard_install_info()` /
`store_wizard_install_info()` (SQLite key `wizard:install`), i.e. the
in-progress wizard configuration -- NOT `read_settings()["probe_settings"]`,
which holds the last-installed, running config. (settings is read once, for
`probe_profiles` lookups only.) `common/datastore.py`'s `get_blob()` returns
None for a missing key, and `load_wizard_install_info()` does
`json.loads(get_blob(...))` with no guard -- so unlike `read_settings()` /
`read_control()`, there is no seeded default for this key. conftest.py's
`_seed_fresh_db()` does not seed it either. Every test in this module must
therefore call `_seed_probe_map()` (below) before its first request, exactly
as tests/web/test_webapp_sqlite.py's
`test_probeconfig_add_usb_hid_probe_not_blocked_by_stale_platform_bus` does
for the same route.

Each test seeds its own preconditions via `_seed_probe_map()` since
`live_server` is module-scoped (see conftest.py) and this module's tests
share one datastore/DB.

The virtual-port ordering invariant (the main subject of this module)
----------------------------------------------------------------------
`add_probe`/`edit_probe` (routes.py ~L255-377) must keep a virtual/aggregate
probe's `probe_info` list entry AFTER the entries of the probes that feed it
(a "virtual" module device, e.g. virtual_average, has
`device["config"]["probes_list"]` = the labels of its input probes). Two
independent code paths enforce this on every edit, keyed off different
signals:

1. `"VIRT" in new_probe["port"]` -- the probe BEING EDITED is itself the
   virtual/aggregate probe (device_port selects the virtual device's own
   port, e.g. "VirtDev:VIRT0"). Walks `probe_info` **backwards** looking for
   whichever comes first scanning from the end: the virtual probe's own
   (still-current) entry -- meaning position is already OK -- or one of its
   input probes' entries -- meaning the virtual entry needs to move to right
   after that input.
2. `elif in_virtual_device != []` -- the probe BEING EDITED is one of a
   virtual device's INPUT probes (its old label appeared in some virtual
   device's `probes_list`). Walks `probe_info` **forwards**: if the edited
   probe's own old entry is reached before any entry whose `device` is one
   of the virtual device(s) it feeds, position is already OK (relabel in
   place). Otherwise, the virtual device's entry is reached first, so the
   edited probe's new entry is `insert`ed at that position (pushing the
   virtual entry back) and the old entry (now one slot later, at
   `found + 1`) is popped -- net effect: the edited probe moves to
   immediately before the virtual device's entry. This branch works exactly
   as described and is exercised twice below, once per sub-case (already in
   order / out of order), with the resulting `probe_info` list order
   asserted explicitly -- this is the ordering invariant most likely to
   regress under an upcoming refactor.

Both findings were verified against the real route (not inferred from
reading code alone) using disposable scripts driving a seeded live_server /
Flask test client before this test module was written.
"""

from common.datastore_accessors import load_wizard_install_info, store_wizard_install_info

from tests.web.conftest import read_settings_from_server, requires_chromium

pytestmark = requires_chromium


def _seed_probe_map(probe_devices=None, probe_info=None):
    """Seed the wizardInstallInfo store's probe_map directly (bypassing the
    UI), exactly like test_webapp_sqlite.py's probeconfig coverage does. This
    is the ONLY way to get a `wizard:install` row into a fresh DB -- see
    module docstring."""
    store_wizard_install_info({"probe_map": {"probe_devices": probe_devices or [], "probe_info": probe_info or []}})


def _probe_map():
    """Read back wizardInstallInfo['probe_map'] via the thread-shared
    datastore singleton (see conftest.py's "Thread-shared datastore" docs) --
    NOT read_settings_from_server(), which is the wrong store for this
    route."""
    return load_wizard_install_info()["probe_map"]


def _profile(profile_id="TWPS00"):
    return read_settings_from_server()["probe_settings"]["probe_profiles"][profile_id].copy()


def _probe(name, device, port, probe_type="Food", enabled=True, profile_id="TWPS00"):
    return {
        "name": name,
        "label": name,
        "device": device,
        "port": port,
        "type": probe_type,
        "enabled": enabled,
        "profile": _profile(profile_id),
    }


def _ds18b20_device(device_name, transient="False"):
    return {
        "config": {"transient": transient},
        "device": device_name,
        "module": "ds18b20",
        "module_filename": "ds18b20",
        "ports": ["DS0"],
    }


def _virtual_average_device(device_name, probes_list, transient=False):
    return {
        "config": {"probes_list": list(probes_list), "transient": transient},
        "device": device_name,
        "module": "virtual_average",
        "module_filename": "virtual_average",
        "ports": ["VIRT0"],
    }


def test_get_renders_probe_devices_and_ports_fragment(live_server, page):
    """Base GET: no `<!doctype>`/navbar (see module docstring) -- just the
    two macro fragments, seeded from wizardInstallInfo's probe_map."""
    _seed_probe_map(
        probe_devices=[_ds18b20_device("TempSensor")],
        probe_info=[_probe("Probe1", "TempSensor", "DS0")],
    )

    resp = page.goto(f"{live_server}/probeconfig/")

    assert resp.status == 200
    assert page.locator("#probeDevicesCard").count() == 1
    assert page.locator("#probePortsCard").count() == 1
    # The devices table (render_probe_devices) and ports table
    # (render_probe_ports) each show the seeded row.
    assert "TempSensor" in page.locator("#probeDevicesCard").inner_text()
    assert "Probe1" in page.locator("#probePortsCard").inner_text()


def test_add_config_renders_device_specific_form_without_persisting(live_server, page):
    """`add_config` (section=devices) only renders the device-specific
    settings form for the selected module into the Add Probe Device modal --
    it must NOT touch wizardInstallInfo."""
    _seed_probe_map()

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "devices", "action": "add_config", "module": "ds18b20"},
    )

    assert resp.status == 200
    body = resp.text()
    # Add-mode's unique-device-name field and the module's one
    # device_specific config field (see wizard_manifest.json's ds18b20 entry).
    assert 'id="probeDeviceNameAdd"' in body
    assert "probes_devspec_transient" in body
    assert _probe_map()["probe_devices"] == []


def test_add_device_via_direct_post(live_server, page):
    """`add_device` (section=devices): persists a new device into
    wizardInstallInfo's probe_map.probe_devices, including its
    `probes_devspec_*`-prefixed device-specific config."""
    _seed_probe_map()

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "devices",
            "action": "add_device",
            "name": "TempSensor",
            "module": "ds18b20",
            "probes_devspec_transient": "False",
        },
    )

    assert resp.status == 200
    devices = _probe_map()["probe_devices"]
    assert len(devices) == 1
    assert devices[0]["device"] == "TempSensor"
    assert devices[0]["module"] == "ds18b20"
    assert devices[0]["ports"] == ["DS0"]
    assert devices[0]["config"]["transient"] == "False"


def test_edit_config_renders_existing_device_values(live_server, page):
    """`edit_config` (section=devices) renders the Edit Probe Device modal
    pre-filled from the existing device's saved config -- no store
    mutation."""
    _seed_probe_map(probe_devices=[_ds18b20_device("TempSensor", transient="True")])

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "devices", "action": "edit_config", "name": "TempSensor"},
    )

    assert resp.status == 200
    body = resp.text()
    assert 'id="probeDeviceNameEdit"' in body
    assert 'value="TempSensor"' in body
    # Untouched: still one device, unchanged.
    devices = _probe_map()["probe_devices"]
    assert len(devices) == 1
    assert devices[0]["config"]["transient"] == "True"


def test_edit_device_via_direct_post_preserves_module_and_ports(live_server, page):
    """`edit_device` (section=devices): renames a device and replaces its
    config, but carries over `module`/`module_filename`/`ports` from the
    ORIGINAL device entry looked up by the old name (`r["name"]`) -- the POST
    body itself never supplies those three fields for this action (see
    probeconfig.js's probe_editSubmitDeviceConfig(), which only sends
    name/newname/module/probes_devspec_*, and routes.py's edit_device branch,
    which pulls ports + module_filename from the matched OLD device rather
    than from the request)."""
    _seed_probe_map(probe_devices=[_ds18b20_device("TempSensor", transient="False")])

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "devices",
            "action": "edit_device",
            "name": "TempSensor",
            "newname": "TempSensor2",
            "probes_devspec_transient": "True",
        },
    )

    assert resp.status == 200
    devices = _probe_map()["probe_devices"]
    assert len(devices) == 1
    assert devices[0]["device"] == "TempSensor2"
    assert devices[0]["module"] == "ds18b20"
    assert devices[0]["module_filename"] == "ds18b20"
    assert devices[0]["ports"] == ["DS0"]
    assert devices[0]["config"]["transient"] == "True"


def test_delete_device_via_direct_post_cascades_probe_info(live_server, page):
    """`delete_device` (section=devices): removes the device AND every
    probe_info entry that referenced it (a device and its probes are deleted
    together in one action, per the delProbeDeviceModal's own warning
    text)."""
    _seed_probe_map(
        probe_devices=[_ds18b20_device("TempSensor"), _ds18b20_device("OtherSensor")],
        probe_info=[
            _probe("Probe1", "TempSensor", "DS0"),
            _probe("Probe2", "OtherSensor", "DS0"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "devices", "action": "delete_device", "name": "TempSensor"},
    )

    assert resp.status == 200
    probe_map = _probe_map()
    assert [d["device"] for d in probe_map["probe_devices"]] == ["OtherSensor"]
    # Probe1 (on the deleted device) is gone; Probe2 (on the surviving
    # device) is untouched.
    assert [p["label"] for p in probe_map["probe_info"]] == ["Probe2"]


def test_config_action_renders_add_and_edit_probe_forms(live_server, page):
    """`config` (section=ports) renders the Add/Edit Probe modal body: blank
    defaults for `label=""` (Add), pre-filled from the matched probe_info
    entry for a non-empty `label` (Edit). Also populates the device_port
    dropdown from every probe_devices entry's device+port combinations."""
    _seed_probe_map(
        probe_devices=[_ds18b20_device("TempSensor")],
        probe_info=[_probe("Probe1", "TempSensor", "DS0")],
    )

    add_resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "ports", "action": "config", "label": ""},
    )
    assert add_resp.status == 200
    add_body = add_resp.text()
    assert 'id="probe_config_name"' in add_body
    # Device+port options are always populated, Add or Edit.
    assert "TempSensor:DS0" in add_body

    edit_resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "ports", "action": "config", "label": "Probe1"},
    )
    assert edit_resp.status == 200
    edit_body = edit_resp.text()
    assert 'value="Probe1"' in edit_body
    assert 'value="TempSensor:DS0" selected' in edit_body


def test_add_probe_via_direct_post(live_server, page):
    """`add_probe` (section=ports, `name=""` for a brand-new probe):
    persists a new probe_info entry built from the `probe_config_*` fields,
    with `label` derived from `name` (alnum-only) and `device`/`port` split
    out of the combined `device_port` value."""
    _seed_probe_map(probe_devices=[_ds18b20_device("TempSensor")])

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "ports",
            "action": "add_probe",
            "name": "",
            "probe_config_name": "Probe One",
            "probe_config_device_port": "TempSensor:DS0",
            "probe_config_type": "Food",
            "probe_config_profile_id": "TWPS00",
            "probe_config_enabled": "true",
        },
    )

    assert resp.status == 200
    probes = _probe_map()["probe_info"]
    assert len(probes) == 1
    assert probes[0]["name"] == "Probe One"
    assert probes[0]["label"] == "ProbeOne"  # non-alnum chars stripped
    assert probes[0]["device"] == "TempSensor"
    assert probes[0]["port"] == "DS0"
    assert probes[0]["enabled"] is True
    assert probes[0]["profile"]["id"] == "TWPS00"


def test_delete_probe_via_direct_post_removes_from_virtual_device_list(live_server, page):
    """`delete_probe` (section=ports): removes the probe_info entry, and if
    that probe fed a virtual device, also removes its label from that
    device's `config.probes_list`."""
    _seed_probe_map(
        probe_devices=[
            _ds18b20_device("TempSensor"),
            _virtual_average_device("VirtDev", ["Probe1", "Probe2"]),
        ],
        probe_info=[
            _probe("Probe1", "TempSensor", "DS0"),
            _probe("Probe2", "TempSensor", "DS0"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={"section": "ports", "action": "delete_probe", "label": "Probe1"},
    )

    assert resp.status == 200
    probe_map = _probe_map()
    assert [p["label"] for p in probe_map["probe_info"]] == ["Probe2"]
    vdev = next(d for d in probe_map["probe_devices"] if d["device"] == "VirtDev")
    assert vdev["config"]["probes_list"] == ["Probe2"]


# --- The virtual-port ordering invariant (the highest-value coverage here) -


def test_edit_probe_input_probe_already_in_order_stays_in_place(live_server, page):
    """Editing an input probe that ALREADY sits before its virtual device's
    entry in probe_info: the `elif in_virtual_device != []` branch's own
    "current location is OK" path relabels it in place without moving
    anything -- list order is unchanged, only the label is updated
    (including inside the virtual device's probes_list)."""
    _seed_probe_map(
        probe_devices=[
            _ds18b20_device("TempSensor"),
            _virtual_average_device("VirtDev", ["Input1", "Input2"]),
        ],
        probe_info=[
            _probe("Input1", "TempSensor", "DS0"),
            _probe("Input2", "TempSensor", "DS0"),
            _probe("VirtProbe", "VirtDev", "VIRT0", probe_type="Aux"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "ports",
            "action": "edit_probe",
            "name": "Input1",  # original label, identifies which probe is being edited
            "probe_config_name": "Input1Renamed",
            "probe_config_device_port": "TempSensor:DS0",
            "probe_config_type": "Food",
            "probe_config_profile_id": "TWPS00",
            "probe_config_enabled": "true",
        },
    )

    assert resp.status == 200
    probe_map = _probe_map()
    # Order preserved: Input1 (renamed) still comes before VirtProbe.
    assert [p["label"] for p in probe_map["probe_info"]] == ["Input1Renamed", "Input2", "VirtProbe"]
    vdev = next(d for d in probe_map["probe_devices"] if d["device"] == "VirtDev")
    assert vdev["config"]["probes_list"] == ["Input1Renamed", "Input2"]


def test_edit_probe_input_probe_out_of_order_reorders_before_virtual_probe(live_server, page):
    """THE ordering-invariant pin: an input probe that is currently AFTER its
    virtual device's entry in probe_info gets moved back to just before it
    on edit (the `elif in_virtual_device != []` branch's insert-then-pop
    path). Seeded order is deliberately "wrong" (virtual entry first) to
    force the move rather than the no-op "already OK" path exercised by
    test_edit_probe_input_probe_already_in_order_stays_in_place above.

    Verified against the real route before writing this assertion (see
    module docstring): editing "Input1" out of
    [VirtProbe, Input1, Input2] with routes.py's insert(index=0,
    new_probe) + pop(found=1 + 1=2) produces
    [Input1Renamed, VirtProbe, Input2] -- Input1 moves to the front (ahead
    of VirtProbe), Input2 (untouched) stays put after it.
    """
    _seed_probe_map(
        probe_devices=[
            _ds18b20_device("TempSensor"),
            _virtual_average_device("VirtDev", ["Input1", "Input2"]),
        ],
        probe_info=[
            _probe("VirtProbe", "VirtDev", "VIRT0", probe_type="Aux"),
            _probe("Input1", "TempSensor", "DS0"),
            _probe("Input2", "TempSensor", "DS0"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "ports",
            "action": "edit_probe",
            "name": "Input1",
            "probe_config_name": "Input1Renamed",
            "probe_config_device_port": "TempSensor:DS0",
            "probe_config_type": "Food",
            "probe_config_profile_id": "TWPS00",
            "probe_config_enabled": "true",
        },
    )

    assert resp.status == 200
    probe_map = _probe_map()
    assert [p["label"] for p in probe_map["probe_info"]] == ["Input1Renamed", "VirtProbe", "Input2"]
    vdev = next(d for d in probe_map["probe_devices"] if d["device"] == "VirtDev")
    assert vdev["config"]["probes_list"] == ["Input1Renamed", "Input2"]


def test_editing_the_virtual_probe_itself_succeeds_and_stays_in_place(live_server, page):
    """Characterizes the OTHER ordering branch (`"VIRT" in new_probe["port"]`
    -- editing the virtual/aggregate probe's own entry, as opposed to one of
    its inputs). This branch's backward walk used to start at the
    out-of-bounds index `len(probe_info)` (an off-by-one -> IndexError ->
    HTTP 500 on every call, regardless of whether reordering was even
    needed) and identified "this is my own entry" by comparing the list
    entry's label to the *new* (possibly renamed) label -- which never
    matches when renaming, corrupting the list instead of leaving it alone.
    Fixed: the walk starts at `len(probe_info) - 1` and identifies its own
    entry by index (`probe == found`), which is rename-safe. Seeded already
    in the correct order (virtual entry last, after both its inputs), so
    the "current position is OK" path fires immediately on the very first
    (now in-bounds) iteration: the entry is replaced in place and renamed,
    and the list order is unchanged."""
    _seed_probe_map(
        probe_devices=[
            _ds18b20_device("TempSensor"),
            _virtual_average_device("VirtDev", ["Input1", "Input2"]),
        ],
        probe_info=[
            _probe("Input1", "TempSensor", "DS0"),
            _probe("Input2", "TempSensor", "DS0"),
            _probe("VirtProbe", "VirtDev", "VIRT0", probe_type="Aux"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "ports",
            "action": "edit_probe",
            "name": "VirtProbe",
            "probe_config_name": "VirtProbeRenamed",
            "probe_config_device_port": "VirtDev:VIRT0",
            "probe_config_type": "Aux",
            "probe_config_profile_id": "TWPS00",
            "probe_config_enabled": "true",
        },
    )

    assert resp.status == 200
    probe_map = _probe_map()
    assert [p["label"] for p in probe_map["probe_info"]] == ["Input1", "Input2", "VirtProbeRenamed"]


def test_editing_the_virtual_probe_itself_out_of_order_reorders_after_inputs(live_server, page):
    """Companion to the above for the "insert" sub-path of the same branch:
    the virtual entry is seeded OUT of order (ahead of its own inputs), so
    the backward walk hits an input probe before it hits the virtual's own
    entry, forcing a reorder. The edited entry must land AFTER every input
    probe belonging to its device, not merely before the one that was found
    scanning backwards -- inserting at `probe` (the found input's own
    index) rather than `probe + 1` would leave the last input probe after
    the virtual entry, violating the invariant."""
    _seed_probe_map(
        probe_devices=[
            _ds18b20_device("TempSensor"),
            _virtual_average_device("VirtDev", ["Input1", "Input2"]),
        ],
        probe_info=[
            _probe("VirtProbe", "VirtDev", "VIRT0", probe_type="Aux"),
            _probe("Input1", "TempSensor", "DS0"),
            _probe("Input2", "TempSensor", "DS0"),
        ],
    )

    resp = page.request.post(
        f"{live_server}/probeconfig/",
        form={
            "section": "ports",
            "action": "edit_probe",
            "name": "VirtProbe",
            "probe_config_name": "VirtProbeRenamed",
            "probe_config_device_port": "VirtDev:VIRT0",
            "probe_config_type": "Aux",
            "probe_config_profile_id": "TWPS00",
            "probe_config_enabled": "true",
        },
    )

    assert resp.status == 200
    probe_map = _probe_map()
    assert [p["label"] for p in probe_map["probe_info"]] == ["Input1", "Input2", "VirtProbeRenamed"]
