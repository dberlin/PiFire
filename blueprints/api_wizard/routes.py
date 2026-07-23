import os

from flask import jsonify, request

from blueprints.wizard.wizard import (
    get_settings_dependencies_values,
    wizard_bus_kinds,
    wizardInstallInfoDefaults,
    wizardInstallInfoExisting,
)
from common.common import read_wizard
from common.datastore_accessors import (
    get_wizard_install_status,
    load_wizard_install_info,
    read_control,
    read_settings,
    set_wizard_install_status,
    store_wizard_install_info,
)
from common.i2c_bus import (
    I2CBusConfigError,
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
    validate_bus_kinds,
)
from common.modes import Mode
from common.usb_serial import discover_usb_serial_devices

from . import api_wizard_bp

_SECTIONS = ["grillplatform", "display", "distance", "probes"]
_DRAFT_KEY = "react_draft"  # marker key inside the wizard blob


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

    draft = _load_draft()
    has_draft = isinstance(draft, dict) and draft.get(_DRAFT_KEY) is True

    if has_draft:
        selections = draft.get("selections", {})
        settings_dep_values = draft.get("settings_dep_values", {})
        display_config = draft.get("display_config", {})
    else:
        # Compute from current settings/defaults (do NOT overwrite the blob here).
        if settings["globals"]["first_time_setup"]:
            info = wizardInstallInfoDefaults(wizard_data, settings)
        else:
            info = wizardInstallInfoExisting(wizard_data, settings)

        # profile_selected is ALWAYS a list (verified against
        # wizardInstallInfoDefaults/Existing and prepare_wizard_data in
        # blueprints/wizard/wizard.py) -- for grillplatform/display/distance
        # it holds exactly one module name (matches Task 1's single-default
        # manifest), or is empty when there's no selection (e.g.
        # wizardInstallInfoExisting()'s stale-module recovery path); for
        # probes it may hold several device modules.
        selections = {}
        for section in _SECTIONS:
            if section not in modules:
                continue
            pf = info["modules"].get(section, {}).get("profile_selected", [])
            selections[section] = pf[0] if pf else ""

        settings_dep_values = {}
        for section in _SECTIONS:
            if section not in modules:
                continue
            sel = selections.get(section)
            mod_data = modules.get(section, {}).get(sel)
            settings_dep_values[section] = get_settings_dependencies_values(settings, mod_data) if mod_data else {}
        display_config = settings.get("display", {}).get("config", {})

    return {
        "modules_metadata": {s: modules.get(s, {}) for s in _SECTIONS if s in modules},
        "selections": selections,
        "settings_dep_values": settings_dep_values,
        "display_config": display_config,
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
    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})
    store_wizard_install_info(info)
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

    probe_map is carried over from whatever is already persisted in the
    shared "wizard:install" blob (populated by the legacy probeconfig
    blueprint's direct reads/writes of wizardInstallInfo["probe_map"], or by
    a prior full wizard run) rather than rebuilt from the payload -- the
    React draft shape has no per-probe-device fields (just a single flat
    `selections["probes"]` string, per _build_state's `pf[0] if pf else ""`
    extraction), so the persisted probe_map is the only source of truth for
    the per-device i2c_bus_kind configuration that wizard_bus_kinds() needs
    to catch a real basic+USB-HID conflict.
    """
    selections = payload.get("selections", {}) or {}
    settings_dep_values = payload.get("settings_dep_values", {}) or {}
    display_config = payload.get("display_config", {}) or {}

    probe_map = existing.get("probe_map") if isinstance(existing, dict) else None
    if not isinstance(probe_map, dict):
        probe_map = {}
    probe_devices = probe_map.get("probe_devices") or []

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
        "config": display_config,
    }
    modules["probes"] = {
        # One entry per already-configured probe device, matching
        # wizardInstallInfoDefaults/Existing's `.append(device["module"])`
        # pattern -- profile_selected is always a list, never scalar.
        "profile_selected": [d.get("module") for d in probe_devices if d.get("module")],
        "settings": settings_dep_values.get("probes", {}) or {},
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
