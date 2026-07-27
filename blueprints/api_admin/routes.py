"""JSON endpoints for PiFire's admin surface.

Why a new blueprint rather than pointing React at /admin: that blueprint renders
a page, mutates a shared `_AdminActionContext` in place, and reaches a shell in
two handlers. It also takes a client-supplied filename for restore and joins it
by string concatenation. New code does not get to inherit any of that.

Every destructive endpoint here refuses unless the grill is stopped. Flask
offers them from any mode; this matches the guard POST /api/probe_map and
POST /api/files/recipes/run already apply. It is a SECOND line of defence behind
test stubbing, never a replacement for it -- see tests/web/test_api_admin_system.py.
"""

from flask import current_app, jsonify, request
from werkzeug.exceptions import BadRequest

from common.app import api_response
from common.common import WriteKind, write_log
from common.control_delta import control_delta
from common.datastore_accessors import (
    flush_control,
    flush_history,
    read_control,
    read_pellet_db,
    read_settings,
    write_control,
    write_pellet_db,
    write_settings,
)
from common.defaults import default_control, default_settings
from common.modes import Mode
from common.pellets_actions import clear_pellet_db
from common.server_status import set_server_status
from common.system import reboot_system, restart_scripts, shutdown_system

from . import admin_api, api_admin_bp

#: action -> (the call, the server_status to record before making it).
#: Imported by value above, which is what tests/web/test_api_admin_system.py
#: patches -- patching common.system would leave these bindings pointing at the
#: real functions.
_SYSTEM_ACTIONS = {
    "reboot": (lambda: reboot_system(), "rebooting"),
    "shutdown": (lambda: shutdown_system(), "shutdown"),
    "restart": (lambda: restart_scripts(), "restarting"),
}


def _clear_pelletdb_log():
    pelletdb = read_pellet_db()
    pelletdb["log"].clear()
    write_pellet_db(pelletdb)


#: Destructive, but recoverable -- none of these can stop a cook or the machine.
_MAINTENANCE_ACTIONS = {
    "clear_history": lambda: flush_history(),
    "clear_events": lambda: admin_api.clear_events_log(),
    "clear_pelletdb": lambda: clear_pellet_db(),
    "clear_pelletdb_log": _clear_pelletdb_log,
}


def error(message, status, **data):
    """Uniform error envelope: {"result":"Error","message":...,"data":{...}}."""
    return jsonify(api_response("Error", message, data or None)), status


def json_body():
    """request.json, or {} for a body that is absent or not JSON.

    A form-encoded body is deliberately not supported: one shape, one parser, so
    there is no second path a client can reach a write through.
    """
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}


def backup_folder():
    return current_app.config["BACKUP_PATH"]


def require_stopped():
    """None if the grill is stopped, else a 409 response refusing the action.

    Deliberately re-reads control rather than taking it as an argument: the
    caller's copy may predate the request, and this is the guard standing
    between a web request and a power-off.
    """
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return error("not_stopped", 409, mode=control.get("mode"))
    return None


@api_admin_bp.route("/system", methods=["POST", "GET"])
def admin_system():
    """Reboot, shut down, or restart.

    POST only, and not by accident: /api/cmd/reboot was reachable by a bare GET
    until 2026-07-27, which made any link or prefetch enough to power the box
    off. Flask's own /admin/reboot is a POST form.

    GET is accepted by the rule and refused in the body rather than left off
    `methods`, because blueprints/api's generic /api/<action>/<arg0> rule also
    matches /api/admin/<anything>. Registered POST-only, a GET here does not
    404 from this blueprint -- it falls through to that catch-all and 404s from
    somewhere else entirely, which makes the refusal an accident of routing
    order rather than a statement.

    Calls the existing common/system.py implementations rather than
    reimplementing them -- there is exactly one place in the tree that knows how
    to power the machine off, and it stays that way.
    """
    if request.method != "POST":
        return error("method_not_allowed", 405, field="method")

    action = json_body().get("action")
    entry = _SYSTEM_ACTIONS.get(action)
    if entry is None:
        return error("bad_request", 400, field="action")

    refusal = require_stopped()
    if refusal:
        return refusal

    call, status = entry
    write_log(f"Admin: {action} requested via /api/admin/system")
    set_server_status(status)
    #  Resolved from module globals at call time, so the tests' patches on this
    #  module's own bindings intercept here.
    call()
    return jsonify(api_response("OK", None, {"action": action})), 200


@api_admin_bp.route("/factory-reset", methods=["POST"])
def admin_factory_reset():
    """Reset settings, control, history and the pellet database to defaults.

    Mirrors _admin_setting_factorydefaults step for step. Two of those steps
    look redundant and are not:

      * clear_pellet_db() -- pre-SQLite, `os.system("rm pelletdb.json")` WAS how
        a factory reset cleared pellets. Removing that dead rm preserved the
        accident it left behind, a reset that kept every profile and log entry.
        Clearing them is a ruling, not an oversight.
      * the control reseed after flush_control() -- flush already wrote
        default_control(), and this restates it as named intent so a write
        queued alongside cannot clobber it. timer and notify_data are stated as
        ops because they are only expressible that way.
    """
    refusal = require_stopped()
    if refusal:
        return refusal

    write_log("Admin: factory reset via /api/admin/factory-reset")
    flush_history()
    flush_control()
    clear_pellet_db()
    write_settings(default_settings())

    control = default_control()
    notify_entries = control.pop("notify_data")
    control.pop("timer")
    write_control(
        control_delta(
            set_values=control,
            ops=[{"op": "timer.clear"}, {"op": "notify.replace", "entries": notify_entries}],
        ),
        WriteKind.DELTA,
        origin="api-admin",
    )
    set_server_status("restarting")
    restart_scripts()
    return jsonify(api_response("OK", None, {"action": "factory_reset"})), 200


@api_admin_bp.route("/maintenance", methods=["POST"])
def admin_maintenance():
    """The four destructive-but-not-fatal clears.

    Deliberately NOT gated on Stop mode: clearing a pellet log mid-cook is
    recoverable, and Flask offers all four from any mode. The system actions
    above are the ones that need the guard.
    """
    action = json_body().get("action")
    if action not in _MAINTENANCE_ACTIONS:
        return error("bad_request", 400, field="action")

    write_log(f"Admin: {action} via /api/admin/maintenance")
    _MAINTENANCE_ACTIONS[action]()
    return jsonify(api_response("OK", None, {"action": action})), 200


@api_admin_bp.route("/settings", methods=["POST"])
def admin_settings():
    """The two admin toggles.

    debug_mode also raises the settings_update control flag, matching
    _admin_setting_debugenabled: without it the running control process never
    learns the setting changed.
    """
    body = json_body()
    unknown = set(body) - {"debug_mode", "boot_to_monitor"}
    if unknown:
        return error("bad_request", 400, field=sorted(unknown)[0])
    if not body:
        return error("bad_request", 400, field="debug_mode")
    for key, value in body.items():
        if not isinstance(value, bool):
            return error("bad_request", 400, field=key)

    settings = read_settings()
    settings["globals"].update(body)
    write_settings(settings)
    if "debug_mode" in body:
        write_control(control_delta(set_values={"settings_update": True}), WriteKind.DELTA, origin="api-admin")
        write_log(f"Debug Mode {'Enabled' if body['debug_mode'] else 'Disabled'}.")
    return jsonify(api_response("OK", None, body)), 200


@api_admin_bp.route("/state", methods=["GET"])
def admin_state():
    settings = read_settings()
    control = read_control()
    payload = admin_api.state_payload(settings, control, backup_folder())
    return jsonify(api_response("OK", None, payload)), 200
