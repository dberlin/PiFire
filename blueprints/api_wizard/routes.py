import asyncio
import os

from flask import jsonify, request

from blueprints.wizard.wizard import (
    get_settings_dependencies_values,
    parse_bt_device_info,
    wizard_bus_kinds,
    wizardInstallInfoDefaults,
    wizardInstallInfoExisting,
)
from common.app import get_supported_cmds, get_system_command_output, process_command
from common.common import read_wizard
from common.datastore_accessors import (
    get_wizard_install_status,
    load_wizard_install_info,
    read_control,
    read_settings,
    set_wizard_install_status,
    store_wizard_install_info,
    write_settings,
)
from common.i2c_bus import (
    I2CBusConfigError,
    configured_bus_kinds,
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
    validate_bus_kinds,
)
from common.modes import Mode
from common.usb_serial import discover_usb_serial_devices
from probes.thermoworks_cloud import discover as _thermoworks_discover_impl
from thermoworks_cloud import AuthenticationError

from . import api_wizard_bp

_SECTIONS = ["grillplatform", "display", "distance", "probes"]
_DRAFT_KEY = "react_draft"  # marker key inside the wizard blob


def _thermoworks_discover(email, password):
    """Sync seam over the async ThermoWorks discovery, matching legacy
    _wizard_thermoworks_discover (blueprints/wizard/routes.py:162)."""
    return asyncio.run(_thermoworks_discover_impl(email, password))


def _load_draft():
    """`load_wizard_install_info()` does `json.loads(datastore.get_blob(...))`
    with no guard for a missing key -- on a fresh install (no `/wizard` GET,
    no `/api/wizard/draft` POST yet) the blob doesn't exist and this raises
    TypeError. There is no seeded default for this key (see
    tests/web/test_page_probeconfig.py's module docstring), so a fresh state
    request must tolerate that."""
    try:
        return load_wizard_install_info()
    except TypeError, ValueError:
        return None


def _build_state(settings, control):
    wizard_data = read_wizard()
    modules = wizard_data.get("modules", {})

    # Board default probe maps (the 4 PCB ids). The client reseeds probe_map
    # from these on a fresh-install platform switch (guarded). Boards without a
    # probe_map are skipped.
    board_probe_maps = {
        board_id: board["probe_map"]
        for board_id, board in wizard_data.get("boards", {}).items()
        if isinstance(board, dict) and isinstance(board.get("probe_map"), dict)
    }

    draft = _load_draft()
    has_draft = isinstance(draft, dict) and draft.get(_DRAFT_KEY) is True

    if has_draft:
        selections = draft.get("selections", {})
        settings_dep_values = draft.get("settings_dep_values", {})
        display_config = draft.get("display_config", {})
        probe_map = draft.get("probe_map") or {"probe_devices": [], "probe_info": []}
        probes_units = draft.get("probes_units") or settings["globals"].get("units", "F")
    else:
        # Compute from current settings/defaults (do NOT overwrite the blob here).
        if settings["globals"]["first_time_setup"]:
            info = wizardInstallInfoDefaults(wizard_data, settings)
        else:
            info = wizardInstallInfoExisting(wizard_data, settings)

        # profile_selected is ALWAYS a list (verified against
        # wizardInstallInfoDefaults/Existing and prepare_wizard_data in
        # blueprints/wizard/wizard.py) -- for grillplatform/display/distance
        # it holds exactly one module name (the wizard manifest marks only one
        # module per section as the default), or is empty when there's no
        # selection (e.g. wizardInstallInfoExisting()'s stale-module recovery
        # path); for probes it may hold several device modules.
        selections = {}
        for section in _SECTIONS:
            if section not in modules:
                continue
            pf = info["modules"].get(section, {}).get("profile_selected", [])
            # No selection is represented as None (JSON null), never an empty
            # string -- a "" sentinel is type-indistinguishable from a real
            # module name. profile_selected stays a list ([] when empty); this
            # derived per-section value is a single module name or None.
            selections[section] = pf[0] if pf else None

        settings_dep_values = {}
        for section in _SECTIONS:
            if section not in modules:
                continue
            sel = selections.get(section)
            mod_data = modules.get(section, {}).get(sel)
            settings_dep_values[section] = get_settings_dependencies_values(settings, mod_data) if mod_data else {}
        display_config = settings.get("display", {}).get("config", {})
        if settings["globals"]["first_time_setup"]:
            # Fresh install: seed from the default board's probe_map (which
            # wizardInstallInfoDefaults already computed into info["probe_map"])
            # instead of the live-settings map, so the default board's probes
            # show up-front and establish the reseed baseline.
            probe_map = info.get("probe_map") or {"probe_devices": [], "probe_info": []}
        else:
            probe_map = settings.get("probe_settings", {}).get("probe_map", {"probe_devices": [], "probe_info": []})
        probes_units = settings["globals"].get("units", "F")

    # probe_profiles is shipped as a LIST for the port form's picker. Live
    # settings store it as a dict keyed by id; flatten to the value objects.
    profiles_dict = settings.get("probe_settings", {}).get("probe_profiles", {})
    probe_profiles = list(profiles_dict.values())

    return {
        "modules_metadata": {s: modules.get(s, {}) for s in _SECTIONS if s in modules},
        "selections": selections,
        "settings_dep_values": settings_dep_values,
        "display_config": display_config,
        "probe_map": probe_map,
        "probe_profiles": probe_profiles,
        "probes_units": probes_units,
        "board_probe_maps": board_probe_maps,
        "control_mode": control.get("mode", "Stop"),
        "first_time_setup": bool(settings["globals"]["first_time_setup"]),
        "has_draft": has_draft,
    }


@api_wizard_bp.route("/state", methods=["GET"])
def wizard_state():
    settings = read_settings()
    control = read_control()
    return jsonify(_build_state(settings, control)), 200


@api_wizard_bp.route("/draft", methods=["POST"])
def wizard_draft():
    payload = request.get_json(silent=True) or {}
    info = _load_draft()
    if not isinstance(info, dict):
        info = {}

    if payload.get("clear"):
        # Drop the draft marker + working keys only -- any other persisted
        # keys (e.g. probe_map from a prior /finish) are left alone. A
        # subsequent /state then recomputes selections from settings/defaults
        # (has_draft == false) instead of resuming the cleared draft.
        info.pop(_DRAFT_KEY, None)
        info.pop("selections", None)
        info.pop("settings_dep_values", None)
        info.pop("display_config", None)
        info.pop("probe_map", None)
        info.pop("probes_units", None)
        store_wizard_install_info(info)
        return jsonify({"result": "success"}), 200

    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})
    info["probe_map"] = payload.get("probe_map", {"probe_devices": [], "probe_info": []})
    info["probes_units"] = payload.get("probes_units", "F")
    store_wizard_install_info(info)
    return jsonify({"result": "success"}), 200


@api_wizard_bp.route("/cancel", methods=["POST"])
def wizard_cancel():
    """Leave the wizard without installing anything -- the React counterpart of
    legacy `_wizard_cancel` (blueprints/wizard/routes.py:71-74, dispatched as
    ("POST", "cancel") at :265). Legacy does exactly three things: clear
    settings["globals"]["first_time_setup"], write_settings(), and
    redirect("/"). This ports the first two verbatim and returns JSON instead
    of the redirect -- the React client navigates itself.

    Clearing `first_time_setup` is the whole point, not a side effect: the React
    dashboard re-checks that flag after mount and navigates straight back to
    /wizard while it is True (web-react/src/components/DashboardRoute.tsx:26-38),
    so an exit that left it set would be an inescapable loop.

    Deliberately does NOT touch the wizard draft blob. The client POSTs /draft
    before calling this and /state resumes it on the next visit, which is what
    makes the welcome step's "your progress is saved as a draft" promise true.
    Legacy does not clear it either.

    Uses plain write_settings(), matching legacy -- NOT
    save_settings_and_flag_update() (common/app.py:401-413). No control
    update-flag is set because nothing about the running hardware changed;
    no install was started and no module configuration was applied.

    Note this route must exist as its own static rule: without it, the generic
    api blueprint's `/api/<action>/<arg0>` catch-all (blueprints/api/routes.py:291)
    swallows POST /api/wizard/cancel and answers 415, not 404.
    """
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    write_settings(settings)
    return jsonify({"result": "success"}), 200


@api_wizard_bp.route("/scan", methods=["POST"])
def wizard_scan():
    """Hardware discovery delegation for the wizard's probe-config module
    forms. Mirrors blueprints/wizard/routes.py's _wizard_i2c_bus_scan /
    _wizard_usb_serial_scan grouping logic, but returns JSON
    ({groups: [{title, items: [{value, label}]}], error}) instead of a
    rendered HTML fragment, for the React client.

    Discovery-function return shapes (reconciled against the real
    implementations, not guessed):
      - discover_extended_i2c_buses() -> [{'bus_num': int, 'name': str,
        'serial': str | None}, ...]                    (common/i2c_bus.py)
      - discover_mcp2221_devices() -> [{'serial': str, 'path': ...}, ...]
        ('serial' is only ever a truthy string -- the function filters out
        entries with no serial_number)                 (grillplat/mcp2221.py)
      - discover_ft232h_devices() -> [{'url': str, 'serial': str | None,
        'description': str | None}, ...]               (grillplat/ft232h.py)
      - discover_usb_serial_devices(vid, pid) -> [{'device': str,
        'description': str, ...}, ...] ('description' defaults to '', so a
        falsy check -- not a `.get(..., default)` missing-key check -- is
        needed for the label fallback)                 (common/usb_serial.py)
    """
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    groups = []
    error = None
    try:
        if kind == "extended":
            adapters = discover_extended_i2c_buses()
            groups = [
                {
                    "title": "By Bus Number",
                    "items": [
                        {"value": str(a["bus_num"]), "label": f"{a['name']} (bus {a['bus_num']})"} for a in adapters
                    ],
                },
                {
                    "title": "By Serial",
                    "items": [
                        {"value": a["serial"], "label": f"{a['name']} [{a['serial']}]"}
                        for a in adapters
                        if a.get("serial")
                    ],
                },
            ]
        elif kind == "mcp2221":
            devs = discover_mcp2221_devices()
            groups = [
                {
                    "title": "MCP2221 Devices",
                    "items": [{"value": d["serial"], "label": d["serial"]} for d in devs],
                }
            ]
        elif kind == "ft232h":
            devs = discover_ft232h_devices()
            groups = [
                {
                    "title": "FT232H Devices",
                    "items": [{"value": d["url"], "label": d.get("description") or d["url"]} for d in devs],
                }
            ]
        elif kind == "usb_serial":
            devs = discover_usb_serial_devices(payload.get("vid"), payload.get("pid"))
            groups = [
                {
                    "title": "USB Serial Devices",
                    "items": [{"value": d["device"], "label": d.get("description") or d["device"]} for d in devs],
                }
            ]
        else:
            error = f"Unknown scan kind: {kind}"
        if not error and not any(g["items"] for g in groups):
            error = "No devices found."
    except Exception as e:  # discovery hits hardware libs; surface failures as a friendly error
        error = f"Scan failed: {e}"
        groups = []
    return jsonify({"groups": groups, "error": error}), 200


@api_wizard_bp.route("/module-values", methods=["POST"])
def wizard_module_values():
    """Return a module's settings-dependency values (+ display config bag) for
    the wizard's client-side module-switch, mirroring the legacy
    _wizard_modulecard round-trip (blueprints/wizard/routes.py:105-119).

    `settings` come from the LIVE settings tree via
    get_settings_dependencies_values -- NOT manifest defaults -- so a switch
    reproduces legacy behavior exactly. `config` is display-only and
    guarded with .get(module, {}) because a display module may never have been
    configured -- legacy indexes it unguarded and KeyErrors."""
    payload = request.get_json(silent=True) or {}
    section = payload.get("section")
    module = payload.get("module")
    if section not in ("grillplatform", "display", "distance"):
        return jsonify({"result": "error", "message": "unknown_module"}), 400
    wizard_data = read_wizard()
    module_data = wizard_data.get("modules", {}).get(section, {}).get(module)
    if not isinstance(module_data, dict):
        return jsonify({"result": "error", "message": "unknown_module"}), 400
    settings = read_settings()
    dep_values = get_settings_dependencies_values(settings, module_data)
    if section == "display":
        config = settings.get("display", {}).get("config", {}).get(module, {})
    else:
        config = {}
    return jsonify({"settings": dep_values, "config": config}), 200


def _wizard_install_info_from_payload(payload, existing):
    """Translate the React draft payload ({selections, settings_dep_values,
    display_config} -- the same shape /draft persists) into the
    wizardInstallInfo *module* shape that wizard_bus_kinds() validates and
    that the real detached installer (top-level wizard.py's run_wizard(),
    invoked by the os.system call below) reads back via
    load_wizard_install_info(). These are NOT the same shape: the draft
    shape has no "modules"/"probe_map" keys at all, so persisting it
    verbatim (as /draft does, for its own resume-only purpose) would crash
    the installer's `WizardInstallInfo["modules"][...]` indexing. Mirrors
    the {"modules": {...}, "probe_map": {...}} shape built by
    blueprints/wizard/wizard.py's wizardInstallInfoDefaults /
    wizardInstallInfoExisting / prepare_wizard_data.

    probe_map is client-held (owned by the React probe reducer, not part of
    {selections, settings_dep_values, display_config}) and is preferred
    straight from `payload["probe_map"]` when present; it falls back to
    whatever is already persisted in the shared "wizard:install" blob
    (populated by the legacy probeconfig blueprint's direct reads/writes of
    wizardInstallInfo["probe_map"], or by a prior full wizard run) only when
    the payload omits it -- this keeps the per-device i2c_bus_kind
    configuration that wizard_bus_kinds() needs to catch a real
    basic+USB-HID conflict.
    """
    selections = payload.get("selections", {}) or {}
    settings_dep_values = payload.get("settings_dep_values", {}) or {}
    display_config = payload.get("display_config", {}) or {}

    # probe_map is client-held (the React probe reducer): prefer the payload,
    # fall back to whatever is persisted only when the payload omits it.
    probe_map = payload.get("probe_map")
    if not isinstance(probe_map, dict):
        probe_map = existing.get("probe_map") if isinstance(existing, dict) else None
    if not isinstance(probe_map, dict):
        probe_map = {"probe_devices": [], "probe_info": []}
    probe_devices = probe_map.get("probe_devices") or []
    probes_units = payload.get("probes_units") or ""

    modules = {}
    for section in ("grillplatform", "distance"):
        selected = selections.get(section) or ""
        modules[section] = {
            "profile_selected": [selected] if selected else [],
            "settings": settings_dep_values.get(section, {}) or {},
            "config": {},
        }
    display_selected = selections.get("display") or ""
    modules["display"] = {
        "profile_selected": [display_selected] if display_selected else [],
        "settings": settings_dep_values.get("display", {}) or {},
        # display_config is the React client's module-keyed bag
        # {module: {option: value}}, but the detached installer
        # (wizard.py's run_wizard) indexes
        # WizardInstallInfo["modules"]["display"]["config"] as a FLAT
        # {option: value} dict for the SELECTED module only -- matching what
        # legacy prepare_wizard_data built via its
        # `config.startswith(module_ + "config_")` loop. Passing the
        # module-keyed bag here would nest it one level too deep and the
        # installer would silently fall back to defaults.
        "config": display_config.get(display_selected, {}) if display_selected else {},
    }
    probes_settings = dict(settings_dep_values.get("probes", {}) or {})
    if probes_units:
        probes_settings["units"] = probes_units
    modules["probes"] = {
        # One entry per already-configured probe device, matching
        # wizardInstallInfoDefaults/Existing's `.append(device["module"])`
        # pattern -- profile_selected is always a list, never scalar.
        "profile_selected": [d.get("module") for d in probe_devices if d.get("module")],
        "settings": probes_settings,
        "config": {},
    }
    return {"modules": modules, "probe_map": probe_map}


@api_wizard_bp.route("/finish", methods=["POST"])
def wizard_finish():
    settings = read_settings()
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return jsonify({"result": "error", "message": "system_active"}), 409

    payload = request.get_json(silent=True) or {}
    existing = _load_draft()
    if not isinstance(existing, dict):
        existing = {}
    wizard_install_info = _wizard_install_info_from_payload(payload, existing)

    # The detached installer (wizard.py's run_wizard()) unconditionally indexes
    # WizardInstallInfo["modules"][section]["profile_selected"][0] for these
    # three sections (probes is separately guarded there) -- an empty
    # selection here (e.g. a stale-module draft resumed and /finish POSTed
    # without re-selecting) would raise an unhandled IndexError in the
    # detached process, silently sticking the install at "Starting
    # Install..." forever. /finish is the last safety net before an
    # irreversible real-hardware install, so reject instead of firing.
    missing_sections = [
        section
        for section in ("grillplatform", "display", "distance")
        if not wizard_install_info["modules"].get(section, {}).get("profile_selected")
    ]
    if missing_sections:
        return jsonify({"result": "error", "message": "missing_selection", "sections": missing_sections}), 400

    wizard_data = read_wizard()
    try:
        validate_bus_kinds(wizard_bus_kinds(wizard_install_info, wizard_data))
    except I2CBusConfigError as exc:
        return jsonify({"result": "error", "message": "bus_conflict", "detail": str(exc)}), 422

    store_wizard_install_info(wizard_install_info)
    set_wizard_install_status(0, "Starting Install...", "")
    python_exec = settings["globals"].get("python_exec", "python")
    os.system(f"{python_exec} wizard.py &")  # Kickoff Installation (mirrors _wizard_finish)
    return jsonify({"result": "success"}), 200


@api_wizard_bp.route("/installstatus", methods=["GET"])
def wizard_installstatus():
    percent, status, output = get_wizard_install_status()
    return jsonify({"percent": percent, "status": status, "output": output}), 200


@api_wizard_bp.route("/scan/bluetooth", methods=["POST"])
def wizard_scan_bluetooth():
    """Bluetooth peripheral discovery for probe device forms. Hardware-mediated:
    routes scan_bluetooth through the control process (6s timeout). Mirrors
    blueprints/wizard/routes.py::_wizard_bt_scan but returns JSON rows."""
    rows = []
    error = None
    try:
        if "scan_bluetooth" in get_supported_cmds():
            process_command(action="sys", arglist=["scan_bluetooth"], origin="admin")
            data = get_system_command_output(requested="scan_bluetooth", timeout=6)
            if data["result"] != "OK":
                error = data["message"]
            else:
                rows = parse_bt_device_info(data["data"]["bt_devices"])
                if rows == []:
                    error = "No bluetooth devices found."
        else:
            error = "No support for bluetooth scan command."
    except Exception as e:  # never 500 -- surface as a friendly banner
        error = f"Something bad happened: {e}"
        rows = []
    return jsonify({"rows": rows, "error": error}), 200


@api_wizard_bp.route("/probes/validate-bus-kinds", methods=["POST"])
def wizard_probes_validate_bus_kinds():
    """Per-device bus-kind coexistence check for the in-progress probe device
    set only (settings=None) -- deliberately excludes the live fan/distance
    kinds so a mid-wizard edit doesn't false-positive against stale settings.
    The FULL cross-subsystem check still runs at /finish."""
    payload = request.get_json(silent=True) or {}
    probe_devices = payload.get("probe_devices") or []
    try:
        validate_bus_kinds(configured_bus_kinds(None, {"probe_devices": probe_devices}))
    except I2CBusConfigError as exc:
        return jsonify({"ok": False, "detail": str(exc)}), 200
    return jsonify({"ok": True}), 200


@api_wizard_bp.route("/scan/thermoworks", methods=["POST"])
def wizard_scan_thermoworks():
    """ThermoWorks Cloud account discovery for the thermoworks_cloud device.
    Blocking network auth; distinguishes bad-creds from generic failure.
    Mirrors blueprints/wizard/routes.py::_wizard_thermoworks_discover."""
    payload = request.get_json(silent=True) or {}
    email = payload.get("email", "")
    password = payload.get("password", "")
    rows = []
    error = None
    try:
        rows = _thermoworks_discover(email, password)
        if rows == []:
            error = "No ThermoWorks Cloud devices found for this account."
    except AuthenticationError as e:
        error = f"Could not log in to ThermoWorks Cloud: {e}"
        rows = []
    except Exception as e:
        error = f"Something bad happened: {e}"
        rows = []
    return jsonify({"rows": rows, "error": error}), 200
