from flask import request, jsonify, abort
from common.common import WriteKind, write_log, deep_update, read_generic_json
from common.control_delta import ControlDeltaError, control_delta, notify_ops_from_post
from common.datastore_accessors import (
    read_settings,
    write_settings,
    read_control,
    write_control,
    read_pellet_db,
    read_current,
    read_status,
    read_probe_status,
)
from common.api_commands import process_command
from common.app import get_system_command_output, create_ui_hash, save_settings_and_flag_update, api_response
from common.pellets_actions import PELLETS_DISPATCH
from common.server_status import get_server_status
from common.settings_schema import SettingsValidationError, validate_partial_settings
from common.controller_deps import guard_controller_selection
from . import api_bp


def _build_current_status(settings, control, display, probe_status):
    status = {}
    status["mode"] = control["mode"]
    status["display_mode"] = display["mode"]
    status["status"] = control["status"]
    status["s_plus"] = control["s_plus"]
    status["units"] = settings["globals"]["units"]
    status["name"] = settings["globals"]["grill_name"]
    status["start_time"] = display["start_time"]
    status["start_duration"] = display["start_duration"]
    status["shutdown_duration"] = display["shutdown_duration"]
    status["prime_duration"] = display["prime_duration"]
    status["prime_amount"] = display["prime_amount"]
    status["lid_open_detected"] = display["lid_open_detected"]
    status["lid_open_endtime"] = display["lid_open_endtime"]
    status["p_mode"] = display["p_mode"]
    status["outpins"] = display["outpins"]
    status["startup_timestamp"] = display["startup_timestamp"]
    status["ui_hash"] = create_ui_hash()
    status["probe_status"] = probe_status
    status["critical_error"] = control.get("critical_error", False)
    return status


def _api_get_settings(settings, server_status):
    return jsonify({"settings": settings}), 201


def _api_get_server(settings, server_status):
    return jsonify({"server_status": server_status}), 201


def _api_get_control(settings, server_status):
    control = read_control()
    return jsonify({"control": control}), 201


def _api_get_current(settings, server_status):
    """Only fetch data from the datastore or locally available, to improve performance"""
    current_temps = read_current()  # Get current temperatures
    control = read_control()  # Get status of control
    display = read_status()  # Get status of display items
    probe_status = read_probe_status(settings["probe_settings"]["probe_map"]["probe_info"])

    """ Create string of probes that can be hashed to ensure UI integrity """
    probe_string = ""
    for group in current_temps:
        if group in ["P", "F"]:
            for probe in current_temps[group]:
                probe_string += probe
    probe_string += settings["globals"]["units"]

    notify_data = control["notify_data"]

    status = _build_current_status(settings, control, display, probe_status)
    return jsonify({"current": current_temps, "notify_data": notify_data, "status": status}), 201


def _api_get_hopper(settings, server_status):
    pelletdb = read_pellet_db()
    pelletlevel = pelletdb["current"]["hopper_level"]
    pelletid = pelletdb["current"]["pelletid"]
    pellets = f"{pelletdb['archive'][pelletid]['brand']} {pelletdb['archive'][pelletid]['wood']}"
    return jsonify({"hopper_level": pelletlevel, "hopper_pellets": pellets})


def _api_get_pellets(settings, server_status):
    """Whole pellet database over REST.

    The live UI reads this over socket_pellet_data (socket_io.py:174, :224);
    this route exists so a test can assert store state without going through
    the UI it is testing, and so a client with no socket can cold-start.
    """
    return jsonify(
        api_response(
            result="OK",
            data={"uuid": settings["server_info"]["uuid"], "pellets": read_pellet_db()},
        )
    ), 200


def _api_get_wled_discover(settings, server_status):
    """Discover WLED devices on the network (mDNS/zeroconf, in-process)"""
    try:
        # Imported lazily so zeroconf is only loaded when discovery runs.
        # Runs in-process now that the webapp uses the gthread worker (no
        # eventlet/gevent monkey-patching), so no subprocess is needed.
        from notify.wled_discovery import discover_wled_devices

        # Get timeout from query parameter, default to 10 seconds
        timeout = request.args.get("timeout", 10, type=int)
        timeout = max(5, min(30, timeout))  # Clamp between 5-30 seconds

        devices = discover_wled_devices(timeout)
        return jsonify({"result": "success", "message": f"Found {len(devices)} WLED devices", "devices": devices}), 200

    except Exception as e:
        return jsonify({"result": "error", "message": f"WLED discovery failed: {str(e)}", "devices": []}), 500


def _api_get_controller_metadata(settings, server_status):
    return jsonify(read_generic_json("./controller/controllers.json")), 201


_API_GET_ACTIONS = {
    "settings": _api_get_settings,
    "server": _api_get_server,
    "control": _api_get_control,
    "current": _api_get_current,
    "hopper": _api_get_hopper,
    "pellets": _api_get_pellets,
    "wled_discover": _api_get_wled_discover,
    "controller_metadata": _api_get_controller_metadata,
}


def _api_post_settings(settings, request_json):
    try:
        settings = deep_update(settings, request_json)
        write_settings(settings)
        return jsonify(
            {
                "settings": "success",  # Keeping for compatibility
                "result": "success",
                "message": "Settings updated successfully.",
            }
        ), 201
    except Exception:
        return jsonify(
            {
                "settings": "error",  # Keeping for compatibility
                "result": "error",
                "message": "Settings update failed.",
            }
        ), 201


_SETTINGS_UPDATE_ALLOWED_FLAGS = {
    "settings_update",
    "controller_update",
    "distance_update",
    "probe_profile_update",
}


def _api_post_settings_update(settings, request_json):
    """
    JSON settings write that ALSO sets control-update flags so the running
    control loop re-reads. Mirrors save_settings_and_flag_update.
    body: { "settings": <partial settings dict>, "flags": ["settings_update", ...] }

    `flags` is restricted to _SETTINGS_UPDATE_ALLOWED_FLAGS -- an unknown flag
    (e.g. a typo'd "mode") would otherwise be set as control[flag] = True,
    clobbering unrelated control keys, so requests with any unknown flag are
    rejected outright without writing settings or control.

    Two-layer rejection:
      1. The DELTA itself is FIELD-level strict-validated against
         PartialSettingsSchema before anything is touched -- catches a
         structurally-bad delta (e.g. a section replaced with a scalar, or a
         field of the wrong type) with a precise dotted-path message, same
         format as SettingsValidationError.errors. This layer deliberately
         does NOT enforce cross-field/cross-section model_validator rules
         (e.g. startup.pwm_duty_cycle vs. pwm.min/max_duty_cycle) -- on a
         sparse delta those would run against sections' STATIC DEFAULTS
         rather than the store's real values and could falsely reject a
         valid delta (see validate_partial_settings()'s docstring in
         common/settings_schema.py for the full rationale + discriminator).
      2. The delta is merged onto the current tree and handed to
         save_settings_and_flag_update() -> write_settings(), which
         strict-validates the FULL merged tree and raises
         SettingsValidationError on any violation -- including the
         cross-field constraints Layer 1 skips, now checked against real
         values everywhere. Caught here and turned into the same error
         envelope; the store is left untouched (write_settings() validates
         before persisting). This layer is authoritative.
    """
    delta = request_json.get("settings", {})
    flags = request_json.get("flags", []) or []
    for flag in flags:
        if flag not in _SETTINGS_UPDATE_ALLOWED_FLAGS:
            return jsonify({"result": "error", "message": f"Unknown flag: {flag}", "data": {}}), 200

    layer1_errors = validate_partial_settings(delta)
    if layer1_errors:
        message = "; ".join(layer1_errors)
        return jsonify({"result": "error", "message": f"Settings update failed: {message}", "data": {}}), 200

    try:
        settings = deep_update(settings, delta)
        # Layer 3: the selected controller must be constructible on THIS install.
        # Evaluated on the MERGED tree (so it sees the selection the save would
        # actually produce) and before any write, so a refusal leaves the store
        # untouched exactly like the two layers above -- the controller currently
        # running the user's cook is not disturbed. Kicks off the missing extra's
        # background install; see common/controller_deps.py.
        blocked = guard_controller_selection(settings)
        if blocked:
            return jsonify({"result": "error", "message": blocked, "data": {}}), 200
        control = read_control()
        save_settings_and_flag_update(settings, control, *flags, origin="api")
        return jsonify({"result": "success", "message": "Settings updated.", "data": settings}), 200
    except SettingsValidationError as exc:
        message = "; ".join(exc.errors)
        return jsonify({"result": "error", "message": f"Settings update failed: {message}", "data": {}}), 200
    except Exception as e:
        return jsonify({"result": "error", "message": f"Settings update failed: {e}", "data": {}}), 200


def _api_post_control(settings, request_json):
    """Queue a client-supplied control patch as a delta.

    A posted patch is ALREADY a statement of intent -- the client sent only what
    it means -- so it needs no reduction and no client change; it is wrapped, not
    rewritten. Three members are special, all handled by notify_ops_from_post()
    so this door and the Socket.IO one cannot drift:

      * `notify_updates` is the way to change notifications: one patch per
        addressed entry ({label, type, fields}), applied against live state, so
        a concurrent writer touching a different entry or field survives;
      * `notify_data` travels WHOLE (an omitted entry is a deletion, not
        silence), so it becomes an explicit notify.replace op rather than an
        implicit array swap. It cannot express which fields the client meant,
        so it CLOBBERS a concurrent write to the same array -- retained only
        for clients that already speak it; post `notify_updates` instead;
      * `timer` is refused. start/paused/end are one countdown and the control
        code branches on their combinations, so a value computed from a read
        that cannot see the write queue is exactly the race this endpoint used
        to feed. Use /api/set/timer/{start,pause,stop}, which queue ops the
        drain resolves against live state.
    """
    if "timer" in request_json:
        return jsonify(
            {
                "control": "error",
                "result": "error",
                "message": "control['timer'] cannot be set through /api/control; use /api/set/timer/...",
            }
        ), 400
    try:
        members, ops = notify_ops_from_post(request_json)
        write_control(control_delta(set_values=members, ops=ops), WriteKind.DELTA, origin="app")
        return jsonify({"control": "success", "result": "success", "message": "Settings updated successfully."}), 201
    except ControlDeltaError as exc:
        # A malformed patch is the CLIENT's error and is named as such, rather
        # than falling into the generic 201 below where a caller cannot tell a
        # rejected request from an accepted one.
        return jsonify({"control": "error", "result": "error", "message": str(exc)}), 400
    except Exception:
        return jsonify({"control": "error", "result": "error", "message": "Settings update failed."}), 201


def _api_post_wled_push_profiles(settings, request_json):
    """Push PiFire profiles to WLED device"""
    try:
        from notify.wled_profiles import WLEDProfileManager

        device_address = request_json.get("device_address", "").strip()
        profile_numbers = request_json.get("profile_numbers", {})

        if not device_address:
            return jsonify({"result": "error", "message": "Device address is required"}), 400

        # Create profile manager and push profiles
        profile_manager = WLEDProfileManager(device_address, settings)
        result = profile_manager.push_all_profiles(custom_profile_numbers=profile_numbers)

        if result["success"]:
            return jsonify(
                {
                    "result": "success",
                    "message": f"Successfully pushed {result['profiles_pushed']} profiles",
                    "profiles_pushed": result["profiles_pushed"],
                    "profiles": result["profiles"],
                }
            ), 200
        else:
            return jsonify({"result": "error", "message": result["message"]}), 500

    except Exception as e:
        return jsonify({"result": "error", "message": f"Failed to push profiles: {str(e)}"}), 500


def _api_post_wled_test_profile(settings, request_json):
    """Test a WLED profile"""
    try:
        import requests

        device_address = request_json.get("device_address", "").strip()
        profile_number = request_json.get("profile_number", 1)

        if not device_address:
            return jsonify({"result": "error", "message": "Device address is required"}), 400

        # Clean device address
        if "http://" in device_address:
            device_address = device_address.replace("http://", "")
        if "https://" in device_address:
            device_address = device_address.replace("https://", "")
        device_address = device_address.strip().rstrip("/")

        # Send test command to WLED
        url = f"http://{device_address}/json/state"
        payload = {"on": True, "bri": 128, "ps": profile_number}

        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()

        return jsonify({"result": "success", "message": f"Profile {profile_number} activated successfully"}), 200

    except requests.RequestException as e:
        return jsonify({"result": "error", "message": f"Failed to communicate with WLED device: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"result": "error", "message": f"Failed to test profile: {str(e)}"}), 500


def _api_post_pellets(settings, request_json):
    """One pellet action per request. body: {"action": <name>, "data": {...}}

    The action name travels in the BODY, not the path: api_page's POST branch
    calls handler(settings, request_json) and never forwards arg0.

    INTENT ONLY -- see common/pellets_actions.py's module docstring. This route
    must never grow a "here is the whole pellet database" form.
    """
    handler = PELLETS_DISPATCH.get(request_json.get("action"))
    if handler is None:
        return jsonify(api_response(result="Error", message="Error: Received request without valid action")), 200
    pelletdb = read_pellet_db()
    # `or {}`: add_profile subscripts action_data["brand_name"] without .get(),
    # so a missing `data` must arrive as a dict and raise KeyError inside the
    # handler rather than TypeError on None. Mirrors socket_io.py's
    # _ACTIONS_REQUIRING_JSON_DATA.
    return jsonify(handler(pelletdb, request_json.get("data") or {})), 200


_API_POST_ACTIONS = {
    "settings": _api_post_settings,
    "settings_update": _api_post_settings_update,
    "control": _api_post_control,
    "pellets": _api_post_pellets,
    "wled_push_profiles": _api_post_wled_push_profiles,
    "wled_test_profile": _api_post_wled_test_profile,
}


@api_bp.route("/", methods=["POST", "GET"])
@api_bp.route("/<action>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>/<arg2>", methods=["POST", "GET"])
@api_bp.route("/<action>/<arg0>/<arg1>/<arg2>/<arg3>", methods=["POST", "GET"])
def api_page(action=None, arg0=None, arg1=None, arg2=None, arg3=None):
    settings = read_settings()
    # Get current server status
    server_status = get_server_status()

    if action in ["get", "set", "cmd", "sys"]:
        # print(f'action={action}\narg0={arg0}\narg1={arg1}\narg2={arg2}\narg3={arg3}')
        arglist = []
        arglist.extend([arg0, arg1, arg2, arg3])

        data = process_command(action=action, arglist=arglist, origin="api")

        if action == "sys":
            """ If system command, wait for output from control """
            data = get_system_command_output(requested=arg0)

        return jsonify(data), 201

    elif request.method == "GET":
        handler = _API_GET_ACTIONS.get(action)
        if handler is not None:
            return handler(settings, server_status)
        return jsonify({"Error": "Received GET request, without valid action"}), 404

    elif request.method == "POST":
        if not request.json:
            event = "Local API Call Failed"
            write_log(event)
            abort(400)
        else:
            request_json = request.json
            handler = _API_POST_ACTIONS.get(action)
            if handler is not None:
                return handler(settings, request_json)
            return jsonify({"Error": "Received POST request no valid action."}), 404
    else:
        return jsonify({"Error": "Received undefined/unsupported request."}), 404
