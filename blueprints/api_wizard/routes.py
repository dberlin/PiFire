from flask import jsonify, request

from blueprints.wizard.wizard import (
    get_settings_dependencies_values,
    wizardInstallInfoDefaults,
    wizardInstallInfoExisting,
)
from common.common import read_wizard
from common.datastore_accessors import (
    load_wizard_install_info,
    read_control,
    read_settings,
    store_wizard_install_info,
)
from common.i2c_bus import (
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
)
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
