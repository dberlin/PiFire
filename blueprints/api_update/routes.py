"""JSON endpoints for PiFire's software updater.

A thin JSON surface over updater.py, mirroring blueprints/api_tuner. Reads are
pure passthroughs; mutations fire the SAME detached `updater.py <flags> &`
command the Flask page fires (blueprints/update/routes.py), behind the same
is_real_hardware() gate plus a STOP-mode and branch-allowlist guard, and seed
the install-status row the client then polls. No rendered HTML -- so none of
Flask's render_template_string paths (the post-message / branch-change alerts)
come along, and their template-injection surface stays behind.
"""

import os

from flask import jsonify, request

from common.app import api_response
from common.datastore_accessors import (
    get_updater_install_status,
    read_control,
    read_settings,
    set_updater_install_status,
)
from common.modes import Mode
from common.system import is_real_hardware
from updater import get_available_updates, get_branch, get_log, get_update_data

from . import api_update_bp


def _ok(data=None):
    return jsonify(api_response("OK", None, data)), 200


def _error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def _python_exec(settings):
    return settings["globals"].get("python_exec", "python")


def _fire(settings, command):
    """Fire a detached updater.py process, ONLY on real hardware. Returns
    whether it fired. `os.system` is the single seam tests neutralize; nothing
    else in this module shells out."""
    if is_real_hardware(settings):
        os.system(command)
        return True
    return False


@api_update_bp.route("/state", methods=["GET"])
def update_state():
    d = get_update_data(read_settings())
    return _ok(
        {
            "version": d["version"],
            "branch": d["branch_target"],
            "branches": d["branches"],
            "remote_url": d["remote_url"],
            "remote_version": d["remote_version"],
        }
    )


@api_update_bp.route("/check", methods=["GET"])
def update_check():
    settings = read_settings()
    avail = get_available_updates()
    if not avail.get("success"):
        return _error(avail.get("message", "update check failed"), 502)
    return _ok({"current": settings["versions"]["server"], "behind": avail["commits_behind"]})


@api_update_bp.route("/log", methods=["GET"])
def update_log():
    commits = request.args.get("commits", "10")
    if not commits.isdigit() or int(commits) <= 0:
        return _error("commits must be a positive integer", 400)
    result, error_msg = get_log(num_commits=int(commits))
    if error_msg:
        return _error(error_msg, 502)
    return _ok({"output": result})


@api_update_bp.route("/status", methods=["GET"])
def update_status():
    percent, status, output = get_updater_install_status()
    return _ok({"percent": percent, "status": status, "output": output})


@api_update_bp.route("/branches/refresh", methods=["POST"])
def update_branches_refresh():
    settings = read_settings()
    set_updater_install_status(0, "Refreshing remote branches...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -r &")
    return _ok({"started": True})


@api_update_bp.route("/branch", methods=["POST"])
def update_branch():
    settings = read_settings()
    body = request.get_json(silent=True) or {}
    target = body.get("target")
    branches = get_update_data(settings)["branches"]
    if target not in branches:
        return _error("invalid_branch", 400, branches=branches)
    set_updater_install_status(0, "Starting Branch Change...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -b {target} &")
    return _ok({"started": True})


@api_update_bp.route("/pull", methods=["POST"])
def update_pull():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    branch, error_msg = get_branch()
    if error_msg:
        return _error(error_msg, 502)
    set_updater_install_status(0, "Starting Update...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -u {branch} -p &")
    return _ok({"started": True})


@api_update_bp.route("/upgrade", methods=["POST"])
def update_upgrade():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    set_updater_install_status(0, "Starting Upgrade...", "")
    _fire(settings, f"{_python_exec(settings)} updater.py -i &")
    return _ok({"started": True})
