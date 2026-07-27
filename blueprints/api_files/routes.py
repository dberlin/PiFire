"""Read-only and write JSON endpoints for PiFire's managed archive folders.

Why a new blueprint instead of pointing React at /cookfile and /recipes:
every mutating action in blueprints/cookfile/routes.py takes a FILESYSTEM PATH
from the client and uses it unvalidated -- `send_file(request.form
["dl_cookfile"])` at :162 is an arbitrary file read, and cookfile_update's four
branches feed a raw client path to update_json_file_data. The legacy pages keep
those routes; new code does not get to inherit them. Here, a client sends a BARE
FILENAME and the server resolves it through
common.file_browser.resolve_managed_file, which realpath-contains it to the
configured folder.

Two more reasons reuse was not on the table: the legacy download actions are
POST-only, so an <a href download> cannot use them; and the dev server proxies
only /socket.io, /api and /static/img (web-react/rsbuild.config.ts:27-37), so a
/cookfile URL does not even reach Flask in `bun run dev`.
"""

import os

from flask import current_app, jsonify, request, send_file
from werkzeug.exceptions import BadRequest

from common.app import api_response
from common.file_browser import browse_files, resolve_managed_file

from . import api_files_bp, cookfile_api, recipes_api

#: kind -> (app.config folder key, extension). The ONE place the two archive
#: kinds share behaviour; everything below this line is cookfile-specific.
_KINDS = {
    "cookfiles": ("HISTORY_FOLDER", ".pifire"),
    "recipes": ("RECIPE_FOLDER", ".pfrecipe"),
}

#: Mirrors the per-page dropdown the Flask lists offer
#: (cookfile/_cookfile_list.html, recipes/_recipefile_list.html). A whitelist,
#: not a range: an unbounded per_page is an unbounded number of archives to
#: unzip per request.
_PER_PAGE_CHOICES = (5, 10, 25, 50, 100)


def error(message, status, **data):
    """Uniform error envelope: {"result":"Error","message":...,"data":{...}}."""
    return jsonify(api_response("Error", message, data or None)), status


def _int_arg(name, default, *, minimum=1, choices=None):
    """Parse a query int, or raise ValueError carrying the offending field."""
    raw = request.args.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except TypeError, ValueError:
        raise ValueError(name)
    if value < minimum:
        raise ValueError(name)
    if choices is not None and value not in choices:
        raise ValueError(name)
    return value


def cookfile_folder():
    return current_app.config["HISTORY_FOLDER"]


def recipe_folder():
    return current_app.config["RECIPE_FOLDER"]


def require_file(name, folder, *, must_exist=True):
    """Resolve a client-supplied bare filename to a contained absolute path.

    `folder` is passed rather than looked up because two archive kinds now
    share this helper, and a default would let a new route silently resolve a
    recipe against the history folder.

    Returns (path, None) on success or (None, response) on failure, so callers
    read as `path, err = require_file(name, folder); if err: return err`.
    """
    if not name:
        return None, error("bad_request", 400, field="file")
    path = resolve_managed_file(folder, name)
    if path is None:
        return None, error("not_found", 404)
    if must_exist and not os.path.isfile(path):
        return None, error("not_found", 404)
    return path, None


def json_body():
    """request.json, or {} for a body that is absent or not JSON.

    A form-encoded body is deliberately not supported by these routes: one
    shape, one parser, so there is no second path a client can reach a write
    through.
    """
    try:
        return request.get_json(silent=True) or {}
    except BadRequest:
        return {}


def _load_cookfile(name):
    """require_file + read_cookfile. Returns (struct, path, None) or
    (None, None, response)."""
    path, err = require_file(name, cookfile_folder())
    if err:
        return None, None, err
    struct, status = cookfile_api.load(path)
    if status != "OK":
        return None, None, cookfile_api.unreadable(status, error)
    return struct, path, None


@api_files_bp.route("/<kind>", methods=["GET"])
def file_listing(kind):
    entry = _KINDS.get(kind)
    if entry is None:
        return error("not_found", 404, kind=kind)
    folder_key, extension = entry

    try:
        page = _int_arg("page", 1)
        per_page = _int_arg("per_page", 10, choices=_PER_PAGE_CHOICES)
    except ValueError as exc:
        return error("bad_request", 400, field=str(exc))

    reverse = request.args.get("reverse", "true").lower() != "false"
    folder = current_app.config[folder_key]
    return jsonify(browse_files(folder, extension, page=page, per_page=per_page, reverse=reverse)), 200


@api_files_bp.route("/cookfiles/detail", methods=["GET"])
def cookfile_detail():
    name = request.args.get("file", "")
    struct, _path, err = _load_cookfile(name)
    if err:
        return err
    return jsonify(cookfile_api.detail_payload(struct, name)), 200


@api_files_bp.route("/cookfiles/chart", methods=["GET"])
def cookfile_chart():
    name = request.args.get("file", "")
    struct, _path, err = _load_cookfile(name)
    if err:
        return err
    return jsonify(cookfile_api.chart_payload(struct)), 200


@api_files_bp.route("/cookfiles/download", methods=["GET"])
def cookfile_download():
    path, err = require_file(request.args.get("file", ""), cookfile_folder())
    if err:
        return err
    return send_file(path, as_attachment=True, max_age=0)


@api_files_bp.route("/cookfiles/export", methods=["GET"])
def cookfile_export():
    name = request.args.get("file", "")
    kind = request.args.get("kind", "")
    if kind not in ("data", "events"):
        return error("bad_request", 400, field="kind")
    path, err = require_file(name, cookfile_folder())
    if err:
        return err
    csv_path, status = cookfile_api.build_export(path, name, kind)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return send_file(csv_path, as_attachment=True, max_age=0)


@api_files_bp.route("/cookfiles/upload", methods=["POST"])
def cookfile_upload():
    storage = request.files.get("file")
    safe_name, problem = cookfile_api.save_upload(storage)
    if problem:
        return error(problem, 400, field="file")
    #  Re-contain the FLATTENED name: secure_filename is a character filter,
    #  resolve_managed_file is the containment proof, and this is a write.
    path, err = require_file(safe_name, cookfile_folder(), must_exist=False)
    if err:
        return err
    storage.save(path)
    return jsonify(api_response("OK", None, {"filename": safe_name})), 200


@api_files_bp.route("/cookfiles/delete", methods=["POST"])
def cookfile_delete():
    path, err = require_file(json_body().get("file", ""), cookfile_folder())
    if err:
        return err
    os.remove(path)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/title", methods=["POST"])
def cookfile_title():
    body = json_body()
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    title = body.get("title")
    if not isinstance(title, str):
        return error("bad_request", 400, field="title")
    status = cookfile_api.set_title(path, title)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/label", methods=["POST"])
def cookfile_label():
    body = json_body()
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    old_label, new_label = body.get("old_label"), body.get("new_label")
    if not isinstance(old_label, str) or not old_label:
        return error("bad_request", 400, field="old_label")
    if not isinstance(new_label, str) or not new_label.strip():
        return error("bad_request", 400, field="new_label")
    safe, problem = cookfile_api.rename_label(path, old_label, new_label)
    if problem == "label_exists":
        return error("label_exists", 409)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, {"new_label_safe": safe})), 200


@api_files_bp.route("/cookfiles/recover", methods=["POST"])
def cookfile_recover():
    body = json_body()
    action = body.get("action")
    if action not in ("upgrade", "repair"):
        return error("bad_request", 400, field="action")
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    status = cookfile_api.recover(path, action)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


_COMMENT_ACTIONS = ("add", "update", "delete")


@api_files_bp.route("/cookfiles/comments", methods=["POST"])
def cookfile_comments():
    body = json_body()
    action = body.get("action")
    if action not in _COMMENT_ACTIONS:
        return error("bad_request", 400, field="action")
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err

    if action == "add":
        text = body.get("text")
        if not isinstance(text, str):
            return error("bad_request", 400, field="text")
        entry, problem = cookfile_api.add_comment(path, text)
    elif action == "update":
        text, cid = body.get("text"), body.get("id")
        if not isinstance(cid, str) or not cid:
            return error("bad_request", 400, field="id")
        if not isinstance(text, str):
            return error("bad_request", 400, field="text")
        entry, problem = cookfile_api.update_comment(path, cid, text)
    else:
        cid = body.get("id")
        if not isinstance(cid, str) or not cid:
            return error("bad_request", 400, field="id")
        status = cookfile_api.delete_comment(path, cid)
        entry, problem = None, (None if status == "OK" else status)

    if problem == "comment_not_found":
        return error("comment_not_found", 404)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, entry)), 200


@api_files_bp.route("/cookfiles/comments/assets", methods=["POST"])
def cookfile_comment_assets():
    body = json_body()
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    cid = body.get("id")
    assets = body.get("assets")
    if not isinstance(cid, str) or not cid:
        return error("bad_request", 400, field="id")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    stored, problem = cookfile_api.set_comment_assets(path, cid, assets)
    if problem == "comment_not_found":
        return error("comment_not_found", 404)
    if problem:
        return cookfile_api.unreadable(problem, error)
    return jsonify(api_response("OK", None, {"assets": stored})), 200


@api_files_bp.route("/cookfiles/assets/upload", methods=["POST"])
def cookfile_asset_upload():
    path, err = require_file(request.form.get("file", ""), cookfile_folder())
    if err:
        return err
    added, problem = cookfile_api.upload_assets(path, request.files.getlist("assets"))
    if problem:
        return error(problem, 400, field="assets")
    return jsonify(api_response("OK", None, {"assets": added})), 200


@api_files_bp.route("/cookfiles/assets/delete", methods=["POST"])
def cookfile_asset_delete():
    body = json_body()
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    assets = body.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    status = cookfile_api.delete_assets(path, assets)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/thumbnail", methods=["POST"])
def cookfile_thumbnail():
    body = json_body()
    path, err = require_file(body.get("file", ""), cookfile_folder())
    if err:
        return err
    asset = body.get("asset")
    if not isinstance(asset, str) or not asset:
        return error("bad_request", 400, field="asset")
    status = cookfile_api.apply_thumbnail(path, asset)
    if status == "unknown_asset":
        return error("bad_request", 400, field="asset")
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


def _load_recipe(name):
    path, err = require_file(name, recipe_folder())
    if err:
        return None, None, err
    struct, status = recipes_api.load(path)
    if status != "OK":
        return None, None, recipes_api.unreadable(status, error)
    return struct, path, None


@api_files_bp.route("/recipes/detail", methods=["GET"])
def recipe_detail():
    name = request.args.get("file", "")
    struct, _path, err = _load_recipe(name)
    if err:
        return err
    return jsonify(recipes_api.detail_payload(struct, name)), 200
