import asyncio
import hashlib
import os

from flask import jsonify, request
from pydantic import ValidationError
from thermoworks_cloud import AuthenticationError

from blueprints.wizard.wizard import (
    get_settings_dependencies_values,
    parse_bt_device_info,
    wizard_bus_kinds,
    wizardInstallInfoDefaults,
    wizardInstallInfoExisting,
)
from common.app import get_supported_cmds, get_system_command_output, process_command
from common.common import read_wizard, write_log
from common.i2c_bus import (
    I2CBusConfigError,
    configured_bus_kinds,
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
    validate_bus_kinds,
)
from common.install_log import read_install_log
from common.modes import Mode
from common.persistence.control import (
    read_control,
)
from common.persistence.install_state import (
    delete_wizard_install_info,
    get_wizard_install_status,
    load_wizard_install_info,
    set_wizard_install_status,
    store_wizard_install_info,
)
from common.persistence.runtime import (
    read_settings,
    write_settings,
)
from common.usb_serial import discover_usb_serial_devices
from common.web_contracts.wizard import (
    BtRowsResult,
    BusKindsValidationRequest,
    BusKindsValidationResponse,
    EmptyWizardRequest,
    InstallLog,
    InstallStatus,
    ModuleValues,
    ModuleValuesRequest,
    ScanRequest,
    ScanResult,
    ThermoworksRequest,
    ThermoworksRowsResult,
    WizardActionResponse,
    WizardDraftRequest,
    WizardFinishRequest,
    WizardState,
)
from probes.thermoworks_cloud import discover as _thermoworks_discover_impl

from . import api_wizard_bp

_SECTIONS = ["grillplatform", "display", "distance", "probes"]
_DRAFT_KEY = "react_draft"  # marker key inside the wizard blob
_STAMP_KEY = "manifest_fingerprint"  # which manifest shape a draft was written against


def _contract_response(model, payload, status=200):
    validated = model.model_validate(payload, strict=True)
    return (
        jsonify(validated.model_dump(mode="json", by_alias=True, exclude_unset=True)),
        status,
    )


def _invalid_request():
    return _contract_response(
        WizardActionResponse,
        {"result": "error", "message": "invalid_request"},
        400,
    )


def _request_contract(model, *, allow_absent=False):
    raw_body = request.get_data(cache=True)
    if not raw_body:
        if not allow_absent:
            return None, _invalid_request()
        payload = {}
    else:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return None, _invalid_request()
    try:
        validated = model.model_validate(payload, strict=True)
    except ValidationError:
        return None, _invalid_request()
    return validated, None


def _thermoworks_discover(email, password):
    """Synchronous wrapper over the async ThermoWorks discovery, matching legacy
    _wizard_thermoworks_discover (blueprints/wizard/routes.py:162)."""
    return asyncio.run(_thermoworks_discover_impl(email, password))


def _manifest_fingerprint(wizard_data):
    """Cheap identity for every manifest-declared key name a draft can bind
    to: each section/module/dependency name, plus each probes module's
    device_specific config labels (what a drafted probe device's `config`
    keys itself by). Entries carry a kind prefix so a dependency name can
    never collide with a probe config label.

    Two manifests declaring the same names -- even with different
    descriptions, defaults or option lists -- fingerprint the same. Only the
    names matter, because names are all a draft binds to."""
    modules = wizard_data.get("modules", {}) or {}
    names = sorted(
        f"dep:{section}/{module}/{dep}"
        for section, section_modules in modules.items()
        for module, module_data in (section_modules or {}).items()
        for dep in (module_data or {}).get("settings_dependencies", {}) or {}
    )
    names += sorted(
        f"probecfg:{module}/{option.get('label')}"
        for module, module_data in (modules.get("probes") or {}).items()
        for option in ((module_data or {}).get("device_specific") or {}).get("config") or []
    )
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


def _draft_is_stale(draft, wizard_data):
    """True when the draft was not written against this manifest.

    A draft keys its values by manifest dependency name, so one written
    against a different manifest binds its answers to nothing -- the wizard
    would render a field's default while the draft still claims to hold the
    operator's answer. A draft written before stamping existed carries no
    evidence of which manifest it came from, which is the same answer --
    reached without hashing the manifest to compare against nothing.

    This says nothing about a draft written against the current manifest and
    then corrupted by hand; only the manifest it was written against is
    judged here.
    """
    if _STAMP_KEY not in draft:
        return True
    return draft[_STAMP_KEY] != _manifest_fingerprint(wizard_data)


def _load_draft(wizard_data):
    """`load_wizard_install_info()` does `json.loads(datastore.get_blob(...))`
    with no guard for a missing key -- on a fresh install (no `/wizard` GET,
    no `/api/wizard/draft` POST yet) the blob doesn't exist and this raises
    TypeError. There is no seeded default for this key (see
    tests/web/test_page_probeconfig.py's module docstring), so a fresh state
    request must tolerate that.

    Also the single consumption point for a draft: a draft written against a
    manifest this version no longer matches (see _draft_is_stale) is
    discarded here rather than served, so a resumed setup can't silently
    overwrite working hardware with whatever a mismatched key's default
    renders as."""
    try:
        draft = load_wizard_install_info()
    except TypeError, ValueError:
        return None
    if isinstance(draft, dict) and _draft_is_stale(draft, wizard_data):
        delete_wizard_install_info()
        write_log(
            "Wizard: discarded a saved setup draft because it referenced settings this "
            "version no longer has -- showing your current configuration instead."
        )
        return None
    return draft


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

    draft = _load_draft(wizard_data)
    has_draft = isinstance(draft, dict) and draft.get(_DRAFT_KEY) is True

    if has_draft and isinstance(draft, dict):
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
    return _contract_response(WizardState, _build_state(settings, control))


@api_wizard_bp.route("/draft", methods=["POST"])
def wizard_draft():
    request_payload, error_response = _request_contract(WizardDraftRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    payload = request_payload.model_dump(mode="json", by_alias=True, exclude_unset=True)
    wizard_data = read_wizard()
    info = _load_draft(wizard_data)
    if not isinstance(info, dict):
        info = {}

    if request_payload.clear:
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
        info.pop(_STAMP_KEY, None)
        store_wizard_install_info(info)
        return _contract_response(WizardActionResponse, {"result": "success"})

    info[_DRAFT_KEY] = True
    info["selections"] = payload.get("selections", {})
    info["settings_dep_values"] = payload.get("settings_dep_values", {})
    info["display_config"] = payload.get("display_config", {})
    info["probe_map"] = payload.get("probe_map", {"probe_devices": [], "probe_info": []})
    info["probes_units"] = payload.get("probes_units", "F")
    # Stamp with the manifest's current key names so a future load can tell,
    # cheaply and totally, whether this draft still applies.
    info[_STAMP_KEY] = _manifest_fingerprint(wizard_data)
    store_wizard_install_info(info)
    return _contract_response(WizardActionResponse, {"result": "success"})


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
    _, error_response = _request_contract(EmptyWizardRequest, allow_absent=True)
    if error_response is not None:
        return error_response
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    write_settings(settings)
    return _contract_response(WizardActionResponse, {"result": "success"})


def _usb_serial_label(dev):
    """One picker row for a discovered USB serial device.

    Names the description, the kernel device, and -- when the value being saved
    is a stable alias rather than that kernel device -- says so explicitly.
    Without the last part the picker would silently write a path the user never
    saw, which is the sort of surprise that gets "fixed" by typing the kernel
    name back in and reintroducing the problem the alias exists to solve.
    """
    device = dev["device"]
    description = dev.get("description") or device
    stable = dev.get("stable_device")
    if stable and stable != device:
        return f"{description} — {device} (saved as {stable})"
    return f"{description} — {device}" if description != device else device


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
        'serial': str | None}, ...]. The 'kernel' kind returns three groups
        from this, one per way a KernelBus can be addressed: bus number,
        adapter name, and serial.                      (common/i2c_bus.py)
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
    request_payload, error_response = _request_contract(ScanRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    kind = request_payload.kind
    groups = []
    error = None
    try:
        if kind == "kernel":
            adapters = discover_extended_i2c_buses()
            groups = [
                {
                    "title": "By Bus Number",
                    "items": [
                        {"value": str(a["bus_num"]), "label": f"{a['name']} (bus {a['bus_num']})"} for a in adapters
                    ],
                },
                {
                    "title": "By Adapter Name",
                    "items": [{"value": a["name"], "label": f"{a['name']} (bus {a['bus_num']})"} for a in adapters],
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
            devs = discover_usb_serial_devices(request_payload.vid, request_payload.pid)
            # Offer the STABLE alias as the value to save when the device has
            # one (common/usb_serial.py::_stable_device_path). The kernel name
            # this scan is built from -- /dev/ttyACM0 -- is assigned in USB
            # enumeration order, so saving it is what leaves a configured
            # install pointing at some other device after a replug or a reboot,
            # silently. The label still shows the kernel name, because that is
            # what the user sees in dmesg and in every other tool.
            groups = [
                {
                    "title": "USB Serial Devices",
                    "items": [
                        {
                            "value": d.get("stable_device") or d["device"],
                            "label": _usb_serial_label(d),
                        }
                        for d in devs
                    ],
                }
            ]
        else:
            error = f"Unknown scan kind: {kind}"
        if not error and not any(g["items"] for g in groups):
            error = "No devices found."
    except Exception as e:  # discovery hits hardware libs; surface failures as a friendly error
        error = f"Scan failed: {e}"
        groups = []
    return _contract_response(ScanResult, {"groups": groups, "error": error})


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
    request_payload, error_response = _request_contract(ModuleValuesRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    section = request_payload.section
    module = request_payload.module
    if section not in ("grillplatform", "display", "distance"):
        return _contract_response(
            WizardActionResponse,
            {"result": "error", "message": "unknown_module"},
            400,
        )
    wizard_data = read_wizard()
    module_data = wizard_data.get("modules", {}).get(section, {}).get(module)
    if not isinstance(module_data, dict):
        return _contract_response(
            WizardActionResponse,
            {"result": "error", "message": "unknown_module"},
            400,
        )
    settings = read_settings()
    dep_values = get_settings_dependencies_values(settings, module_data)
    if section == "display":
        config = settings.get("display", {}).get("config", {}).get(module, {})
    else:
        config = {}
    return _contract_response(ModuleValues, {"settings": dep_values, "config": config})


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
    the payload omits it -- this keeps the per-device i2c_bus
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
    request_payload, error_response = _request_contract(WizardFinishRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    settings = read_settings()
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return _contract_response(
            WizardActionResponse,
            {"result": "error", "message": "system_active"},
            409,
        )

    payload = request_payload.model_dump(mode="json", by_alias=True, exclude_unset=True)
    wizard_data = read_wizard()
    existing = _load_draft(wizard_data)
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
        return _contract_response(
            WizardActionResponse,
            {"result": "error", "message": "missing_selection", "sections": missing_sections},
            400,
        )

    try:
        validate_bus_kinds(wizard_bus_kinds(wizard_install_info, wizard_data))
    except I2CBusConfigError as exc:
        return _contract_response(
            WizardActionResponse,
            {"result": "error", "message": "bus_conflict", "detail": str(exc)},
            422,
        )

    store_wizard_install_info(wizard_install_info)
    set_wizard_install_status(0, "Starting Install...", "")
    python_exec = settings["globals"].get("python_exec", "python")
    os.system(f"{python_exec} wizard.py &")  # Kickoff Installation (mirrors _wizard_finish)
    return _contract_response(WizardActionResponse, {"result": "success"})


@api_wizard_bp.route("/installstatus", methods=["GET"])
def wizard_installstatus():
    percent, status, output = get_wizard_install_status()
    return _contract_response(
        InstallStatus,
        {"percent": percent, "status": status, "output": output},
    )


@api_wizard_bp.route("/installlog", methods=["GET"])
def wizard_installlog():
    """The running install's command output, from `offset` bytes on.

    Serves the log file rather than the install-status blob: that blob holds
    one line, overwritten as fast as apt and uv emit output, so it can say what
    the installer is doing but never what it has done. A non-integer or absent
    offset reads from the start of the current run, which is also what the
    client asks for when it first opens the panel.
    """
    text, offset, reset = read_install_log(request.args.get("offset", type=int) or 0)
    return _contract_response(InstallLog, {"text": text, "offset": offset, "reset": reset})


@api_wizard_bp.route("/scan/bluetooth", methods=["POST"])
def wizard_scan_bluetooth():
    """Bluetooth peripheral discovery for probe device forms. Hardware-mediated:
    routes scan_bluetooth through the control process (6s timeout). Mirrors
    blueprints/wizard/routes.py::_wizard_bt_scan but returns JSON rows."""
    _, error_response = _request_contract(EmptyWizardRequest)
    if error_response is not None:
        return error_response
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
    return _contract_response(BtRowsResult, {"rows": rows, "error": error})


@api_wizard_bp.route("/probes/validate-bus-kinds", methods=["POST"])
def wizard_probes_validate_bus_kinds():
    """Per-device bus-kind coexistence check for the in-progress probe device
    set only (settings=None) -- deliberately excludes the live fan/distance
    kinds so a mid-wizard edit doesn't false-positive against stale settings.
    The FULL cross-subsystem check still runs at /finish."""
    request_payload, error_response = _request_contract(BusKindsValidationRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    probe_devices = request_payload.model_dump(mode="json", by_alias=True)["probe_devices"]
    try:
        validate_bus_kinds(configured_bus_kinds(None, {"probe_devices": probe_devices}))
    except I2CBusConfigError as exc:
        return _contract_response(
            BusKindsValidationResponse,
            {"ok": False, "detail": str(exc)},
        )
    return _contract_response(BusKindsValidationResponse, {"ok": True})


@api_wizard_bp.route("/scan/thermoworks", methods=["POST"])
def wizard_scan_thermoworks():
    """ThermoWorks Cloud account discovery for the thermoworks_cloud device.
    Blocking network auth; distinguishes bad-creds from generic failure.
    Mirrors blueprints/wizard/routes.py::_wizard_thermoworks_discover."""
    request_payload, error_response = _request_contract(ThermoworksRequest)
    if error_response is not None:
        return error_response
    assert request_payload is not None
    rows = []
    error = None
    try:
        rows = _thermoworks_discover(request_payload.email, request_payload.password)
        if rows == []:
            error = "No ThermoWorks Cloud devices found for this account."
    except AuthenticationError as e:
        error = f"Could not log in to ThermoWorks Cloud: {e}"
        rows = []
    except Exception as e:
        error = f"Something bad happened: {e}"
        rows = []
    return _contract_response(ThermoworksRowsResult, {"rows": rows, "error": error})
