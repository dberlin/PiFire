from flask import current_app, request, jsonify, abort
from common.common import WriteKind, write_log, deep_update
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
from common.app import get_system_command_output, create_ui_hash
from common.server_status import get_server_status
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


_API_GET_ACTIONS = {
    "settings": _api_get_settings,
    "server": _api_get_server,
    "control": _api_get_control,
    "current": _api_get_current,
    "hopper": _api_get_hopper,
    "wled_discover": _api_get_wled_discover,
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
    except:
        return jsonify(
            {
                "settings": "error",  # Keeping for compatibility
                "result": "error",
                "message": "Settings update failed.",
            }
        ), 201


def _api_post_control(settings, request_json):
    """
    Updating of control input data is now done in common.py > execute_commands()
    """
    try:
        # Update control data with request JSON
        write_control(request_json, WriteKind.MERGE, origin="app")
        return jsonify({"control": "success", "result": "success", "message": "Settings updated successfully."}), 201
    except:
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


_API_POST_ACTIONS = {
    "settings": _api_post_settings,
    "control": _api_post_control,
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
