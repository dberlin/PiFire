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
