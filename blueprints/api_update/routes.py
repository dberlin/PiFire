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
import shlex

from flask import Response, jsonify, request

from common.app import api_response
from common.datastore_accessors import (
    get_updater_install_status,
    read_control,
    read_settings,
    set_updater_install_status,
)
from common.modes import Mode
from common.system import is_real_hardware
from common.web_ui_build import last_build_failed, read_build_log, web_ui_needs_rebuild
from updater import (
    REPO_ROOT,
    detached_head,
    get_available_updates,
    get_branch,
    get_log,
    get_update_data,
)

from . import api_update_bp


def _ok(data=None):
    return jsonify(api_response("OK", None, data)), 200


def _error(message, status, **data):
    return jsonify(api_response("Error", message, data or None)), status


def _python_exec(settings):
    return settings["globals"].get("python_exec", "python")


def _fire(settings, command):
    """Fire a detached updater.py process, ONLY on real hardware.

    Returns (started, error). `os.system` is the single seam tests neutralize;
    nothing else in this module shells out.

    Its exit status is CHECKED. The command ends in `&`, so the shell backgrounds
    the updater and reports 0 straight away -- a non-zero status therefore means
    the shell never got as far as running anything, and the caller is polling an
    install status that nothing will ever write to again. That is what a branch
    name interpolated unquoted into the command did on a detached HEAD: the
    parentheses in git's `(HEAD detached at abc1234)` placeholder are shell
    syntax, the shell refused the line, and the page sat on "Starting Update..."
    for good.
    """
    if not is_real_hardware(settings):
        return False, None
    if os.system(command) != 0:
        return False, "the updater could not be started -- see logs/update.log"
    return True, None


def _started(settings, command):
    """One mutation's response: an error envelope when the launch failed, so a
    refusal is never dressed up as a run in progress."""
    started, error = _fire(settings, command)
    if error:
        return _error(error, 500)
    return _ok({"started": started})


@api_update_bp.route("/state", methods=["GET"])
def update_state():
    d = get_update_data(read_settings())
    # The commit HEAD is parked on when this checkout is not on a branch, else
    # null. Everything the updater does is relative to `origin/<branch>`, so a
    # detached HEAD has nothing to update TO -- and the page has to say that
    # rather than offer an update that cannot work.
    detached = detached_head()
    return _ok(
        {
            "version": d["version"],
            # Empty rather than get_branch()'s error string: the page binds this
            # to the branch picker, and there is no current branch to bind.
            "branch": "" if detached else d["branch_target"],
            "detached": detached,
            "branches": d["branches"],
            "remote_url": d["remote_url"],
            "remote_version": d["remote_version"],
            # Whether the served React bundle is older than the sources on
            # disk. Drives the updater page's Rebuild Web UI control, which is
            # the way back from a pull whose rebuild did not run or failed.
            "web_ui_stale": web_ui_needs_rebuild(REPO_ROOT),
            # Whether the last rebuild attempt failed. Distinct from stale: a
            # forced rebuild of an already-current bundle can fail and leave
            # nothing out of date, and a stale bundle can simply never have
            # been built. This is what puts the build log on offer.
            "web_ui_build_failed": last_build_failed(),
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


@api_update_bp.route("/buildlog", methods=["GET"])
def update_build_log():
    """The last web UI rebuild's output, from `offset` bytes on.

    Scoped to the rebuild rather than serving logs/update.log whole: that file
    also carries git, apt and pip output from the update around it, and the
    question being asked here is why one build failed. A non-integer or absent
    offset reads the run from its start, which is what the client asks for the
    first time it opens the panel.
    """
    text, offset, reset = read_build_log(request.args.get("offset", type=int) or 0)
    return _ok({"text": text, "offset": offset, "reset": reset})


@api_update_bp.route("/buildlog/download", methods=["GET"])
def update_build_log_download():
    """The same transcript as a file, for attaching to a bug report.

    Its own route rather than a flag on the one above so the page can hand the
    URL straight to an <a download>, with no fetch and no envelope to unwrap.
    """
    text, _, _ = read_build_log()
    return Response(
        text,
        mimetype="text/plain",
        headers={"Content-Disposition": 'attachment; filename="web-ui-build.log"'},
    )


@api_update_bp.route("/status", methods=["GET"])
def update_status():
    percent, status, output = get_updater_install_status()
    return _ok({"percent": percent, "status": status, "output": output})


@api_update_bp.route("/branches/refresh", methods=["POST"])
def update_branches_refresh():
    settings = read_settings()
    set_updater_install_status(0, "Refreshing remote branches...", "")
    return _started(settings, f"{_python_exec(settings)} updater.py -r &")


@api_update_bp.route("/branch", methods=["POST"])
def update_branch():
    settings = read_settings()
    body = request.get_json(silent=True) or {}
    target = body.get("target")
    branches = get_update_data(settings)["branches"]
    if target not in branches:
        return _error("invalid_branch", 400, branches=branches)
    set_updater_install_status(0, "Starting Branch Change...", "")
    return _started(settings, f"{_python_exec(settings)} updater.py -b {shlex.quote(target)} &")


@api_update_bp.route("/pull", methods=["POST"])
def update_pull():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    branch, error_msg = get_branch()
    if error_msg:
        return _error(error_msg, 502)
    set_updater_install_status(0, "Starting Update...", "")
    return _started(settings, f"{_python_exec(settings)} updater.py -u {shlex.quote(branch)} -p &")


@api_update_bp.route("/rebuild-web-ui", methods=["POST"])
def update_rebuild_web_ui():
    """Rebuild the React bundle from the sources currently on disk.

    Unguarded by mode, unlike /pull and /upgrade: this touches nothing but
    web-react/dist, changes no Python the control process is running, and is
    the recovery path when a pull left the served bundle behind. Forced rather
    than conditional -- an explicit request builds even when the bundle looks
    current, because a half-finished build can look current and be broken.
    """
    settings = read_settings()
    set_updater_install_status(0, "Starting Web UI Rebuild...", "")
    return _started(settings, f"{_python_exec(settings)} updater.py -w &")


@api_update_bp.route("/upgrade", methods=["POST"])
def update_upgrade():
    settings = read_settings()
    if read_control().get("mode") != Mode.STOP:
        return _error("system_active", 409)
    set_updater_install_status(0, "Starting Upgrade...", "")
    return _started(settings, f"{_python_exec(settings)} updater.py -i &")
