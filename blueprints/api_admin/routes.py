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
from common.datastore_accessors import read_control, read_settings
from common.modes import Mode

from . import admin_api, api_admin_bp


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


@api_admin_bp.route("/state", methods=["GET"])
def admin_state():
    settings = read_settings()
    control = read_control()
    payload = admin_api.state_payload(settings, control, backup_folder())
    return jsonify(api_response("OK", None, payload)), 200
