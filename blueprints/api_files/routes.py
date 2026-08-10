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
from common.common import WriteKind
from common.control_delta import control_delta
from common.datastore_accessors import read_control, write_control
from common.file_browser import browse_files, resolve_managed_file
from common.modes import Mode
from common.web_contracts.content import (
    AssetNamesData,
    ContentErrorEnvelope,
    CookFileAsset,
    CookFileAssetsData,
    CookFileChartData,
    CookFileComment,
    CookFileDetail,
    CookFileLabelData,
    FileListing,
    FilenameData,
    RecipeAssetAssignmentRequest,
    RecipeAsset,
    RecipeAssetsData,
    RecipeDetail,
    RecipeIngredientAddRequest,
    RecipeIngredientDeleteRequest,
    RecipeIngredientUpdateRequest,
    RecipeInstructionAddRequest,
    RecipeInstructionDeleteRequest,
    RecipeInstructionUpdateRequest,
    RecipeStep,
    RecipeStepDeleteRequest,
    RecipeStepInsertRequest,
    RecipeStepUpdateRequest,
    validated_content_json,
)
from file_mgmt.recipes import create_recipefile

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
    payload = api_response("Error", message, data or None)
    return jsonify(validated_content_json(ContentErrorEnvelope, payload)), status


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
    payload = browse_files(folder, extension, page=page, per_page=per_page, reverse=reverse)
    return jsonify(validated_content_json(FileListing, payload)), 200


@api_files_bp.route("/cookfiles/detail", methods=["GET"])
def cookfile_detail():
    name = request.args.get("file", "")
    struct, _path, err = _load_cookfile(name)
    if err:
        return err
    payload = cookfile_api.detail_payload(struct, name)
    return jsonify(validated_content_json(CookFileDetail, payload)), 200


@api_files_bp.route("/cookfiles/chart", methods=["GET"])
def cookfile_chart():
    name = request.args.get("file", "")
    struct, _path, err = _load_cookfile(name)
    if err:
        return err
    payload = cookfile_api.chart_payload(struct)
    return jsonify(validated_content_json(CookFileChartData, payload)), 200


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
    data = validated_content_json(FilenameData, {"filename": safe_name})
    return jsonify(api_response("OK", None, data)), 200


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
    data = validated_content_json(CookFileLabelData, {"new_label_safe": safe})
    return jsonify(api_response("OK", None, data)), 200


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
    data = None if entry is None else validated_content_json(CookFileComment, entry)
    return jsonify(api_response("OK", None, data)), 200


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
    data = validated_content_json(AssetNamesData, {"assets": stored})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/cookfiles/assets/upload", methods=["POST"])
def cookfile_asset_upload():
    path, err = require_file(request.form.get("file", ""), cookfile_folder())
    if err:
        return err
    added, problem = cookfile_api.upload_assets(path, request.files.getlist("assets"))
    if problem:
        return error(problem, 400, field="assets")
    assets = [validated_content_json(CookFileAsset, asset) for asset in added]
    data = validated_content_json(CookFileAssetsData, {"assets": assets})
    return jsonify(api_response("OK", None, data)), 200


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
    payload = recipes_api.detail_payload(struct, name)
    return jsonify(validated_content_json(RecipeDetail, payload)), 200


@api_files_bp.route("/recipes/create", methods=["POST"])
def recipe_create():
    """Flask's equivalent is `recipeedit` with an empty filename
    (blueprints/recipes/routes.py:136-147). The new file's bare name is
    returned so the client can navigate to it."""
    path = create_recipefile()
    data = validated_content_json(FilenameData, {"filename": os.path.basename(path)})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/download", methods=["GET"])
def recipe_download():
    path, err = require_file(request.args.get("file", ""), recipe_folder())
    if err:
        return err
    return send_file(path, as_attachment=True, max_age=0)


@api_files_bp.route("/recipes/upload", methods=["POST"])
def recipe_upload():
    storage = request.files.get("recipe")
    safe_name, problem = recipes_api.save_upload(storage)
    if problem:
        return error(problem, 400, field="recipe")
    #  Re-contain the FLATTENED name: secure_filename is a character filter,
    #  resolve_managed_file is the containment proof, and this is a write.
    path, err = require_file(safe_name, recipe_folder(), must_exist=False)
    if err:
        return err
    storage.save(path)
    data = validated_content_json(FilenameData, {"filename": safe_name})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/delete", methods=["POST"])
def recipe_delete_file():
    path, err = require_file(json_body().get("file", ""), recipe_folder())
    if err:
        return err
    os.remove(path)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/recipes/run", methods=["POST"])
def recipe_run():
    """Start a recipe.

    Refuses unless the grill is stopped -- a deliberate divergence from Flask,
    which posts from any mode (static/recipes/js/recipes.js:270-293). It matches
    the guard POST /api/probe_map applies and it is the difference between a
    test suite that can exercise this route and one that cannot.

    start_step and step are sent explicitly because _api_post_control
    deep-merges: a bare {filename} inherits the previous run's step.
    """
    _struct, path, err = _load_recipe(json_body().get("file", ""))
    if err:
        return err
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return error("not_stopped", 409, mode=control.get("mode"))
    write_control(
        # The path rule (bare filenames) governs what the client SENDS; the
        # resolved absolute path is what gets stored here, because that is
        # what controller.py opens the recipe from.
        control_delta(
            set_values={
                "updated": True,
                "mode": Mode.RECIPE,
                "recipe": {"filename": path, "start_step": 0, "step": 0},
            }
        ),
        WriteKind.DELTA,
        origin="api-files",
    )
    data = validated_content_json(FilenameData, {"filename": os.path.basename(path)})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/metadata", methods=["POST"])
def recipe_metadata():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    fields = body.get("fields")
    if not isinstance(fields, dict):
        return error("bad_request", 400, field="fields")
    status, field = recipes_api.set_metadata(path, fields)
    if status == "bad_field":
        return error("bad_request", 400, field=field)
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/recipes/ingredients", methods=["POST"])
def recipe_ingredients():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    action = body.get("action")
    if action == "add":
        RecipeIngredientAddRequest.model_validate(
            {"file": body.get("file"), "action": action},
            strict=True,
        )
        status = recipes_api.add_ingredient(path)
    elif action == "update":
        index = body.get("index")
        name, quantity = body.get("name"), body.get("quantity")
        if not isinstance(index, int) or isinstance(index, bool):
            return error("bad_request", 400, field="index")
        if not isinstance(name, str):
            return error("bad_request", 400, field="name")
        if not isinstance(quantity, str):
            return error("bad_request", 400, field="quantity")
        mutation = RecipeIngredientUpdateRequest.model_validate(
            {
                "file": body.get("file"),
                "action": action,
                "index": index,
                "name": name,
                "quantity": quantity,
            },
            strict=True,
        )
        status = recipes_api.update_ingredient(path, mutation.index, mutation.name, mutation.quantity)
    elif action == "delete":
        index = body.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return error("bad_request", 400, field="index")
        mutation = RecipeIngredientDeleteRequest.model_validate(
            {"file": body.get("file"), "action": action, "index": index},
            strict=True,
        )
        status = recipes_api.delete_ingredient(path, mutation.index)
    else:
        return error("bad_request", 400, field="action")
    if status == "bad_index":
        return error("bad_request", 400, field="index")
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/recipes/instructions", methods=["POST"])
def recipe_instructions():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    action = body.get("action")
    if action == "add":
        RecipeInstructionAddRequest.model_validate(
            {"file": body.get("file"), "action": action},
            strict=True,
        )
        status = recipes_api.add_instruction(path)
    elif action == "update":
        index = body.get("index")
        text, ingredients, step = body.get("text"), body.get("ingredients"), body.get("step")
        if not isinstance(index, int) or isinstance(index, bool):
            return error("bad_request", 400, field="index")
        if not isinstance(text, str):
            return error("bad_request", 400, field="text")
        if not isinstance(ingredients, list) or not all(isinstance(name, str) for name in ingredients):
            return error("bad_request", 400, field="ingredients")
        if not isinstance(step, int) or isinstance(step, bool):
            return error("bad_request", 400, field="step")
        mutation = RecipeInstructionUpdateRequest.model_validate(
            {
                "file": body.get("file"),
                "action": action,
                "index": index,
                "text": text,
                "ingredients": ingredients,
                "step": step,
            },
            strict=True,
        )
        status = recipes_api.update_instruction(
            path,
            mutation.index,
            mutation.text,
            mutation.ingredients,
            mutation.step,
        )
    elif action == "delete":
        index = body.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return error("bad_request", 400, field="index")
        mutation = RecipeInstructionDeleteRequest.model_validate(
            {"file": body.get("file"), "action": action, "index": index},
            strict=True,
        )
        status = recipes_api.delete_instruction(path, mutation.index)
    else:
        return error("bad_request", 400, field="action")
    if status == "bad_index":
        return error("bad_request", 400, field="index")
    if status == "bad_ingredient":
        return error("bad_request", 400, field="ingredients")
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


#: The editor only offers Smoke/Hold, but Startup and Shutdown are seeded by
#: the recipe defaults and carried by every existing recipe, so a write must
#: still accept all four.
_STEP_MODES = ("Smoke", "Hold", "Startup", "Shutdown")


def _validated_step_fields(body):
    """Pull a step payload out of `body["step"]` and validate its shape.

    Returns (fields, None) or (None, error_response). 0 is the disabled
    sentinel for hold_temp and both trigger_temps members -- a legal value,
    not a missing one -- so every check here is an isinstance check, never a
    truthiness check.
    """
    step = body.get("step")
    if not isinstance(step, dict):
        return None, error("bad_request", 400, field="step")
    mode = step.get("mode")
    if mode not in _STEP_MODES:
        return None, error("bad_request", 400, field="mode")
    message = step.get("message")
    if not isinstance(message, str):
        return None, error("bad_request", 400, field="message")
    hold_temp, timer = step.get("hold_temp"), step.get("timer")
    if not isinstance(hold_temp, int) or isinstance(hold_temp, bool):
        return None, error("bad_request", 400, field="hold_temp")
    if not isinstance(timer, int) or isinstance(timer, bool):
        return None, error("bad_request", 400, field="timer")
    notify, pause = step.get("notify"), step.get("pause")
    if not isinstance(notify, bool):
        return None, error("bad_request", 400, field="notify")
    if not isinstance(pause, bool):
        return None, error("bad_request", 400, field="pause")
    trigger_temps = step.get("trigger_temps")
    if not isinstance(trigger_temps, dict):
        return None, error("bad_request", 400, field="trigger_temps")
    primary = trigger_temps.get("primary")
    if not isinstance(primary, int) or isinstance(primary, bool):
        return None, error("bad_request", 400, field="trigger_temps")
    food = trigger_temps.get("food")
    if not isinstance(food, list) or not all(isinstance(t, int) and not isinstance(t, bool) for t in food):
        return None, error("bad_request", 400, field="trigger_temps")
    fields = validated_content_json(
        RecipeStep,
        {
            "mode": mode,
            "message": message,
            "hold_temp": hold_temp,
            "timer": timer,
            "notify": notify,
            "pause": pause,
            "trigger_temps": {"primary": primary, "food": food},
        },
    )
    return fields, None


@api_files_bp.route("/recipes/steps", methods=["POST"])
def recipe_steps():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    action = body.get("action")
    index = body.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        return error("bad_request", 400, field="index")
    if action == "insert":
        mutation = RecipeStepInsertRequest.model_validate(
            {"file": body.get("file"), "action": action, "index": index},
            strict=True,
        )
        status = recipes_api.insert_step(path, mutation.index)
    elif action == "update":
        fields, err = _validated_step_fields(body)
        if err:
            return err
        mutation = RecipeStepUpdateRequest.model_validate(
            {"file": body.get("file"), "action": action, "index": index, "step": fields},
            strict=True,
        )
        status = recipes_api.update_step(
            path,
            mutation.index,
            mutation.step.model_dump(mode="json", exclude_unset=True),
        )
    elif action == "delete":
        mutation = RecipeStepDeleteRequest.model_validate(
            {"file": body.get("file"), "action": action, "index": index},
            strict=True,
        )
        status = recipes_api.delete_step(path, mutation.index)
    else:
        return error("bad_request", 400, field="action")
    if status == "bad_index":
        return error("bad_request", 400, field="index")
    if status == "bad_food_probes":
        return error("bad_request", 400, field="trigger_temps")
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/recipes/assets/upload", methods=["POST"])
def recipe_asset_upload():
    path, err = require_file(request.form.get("file", ""), recipe_folder())
    if err:
        return err
    added, problem = recipes_api.upload_assets(path, request.files.getlist("assets"))
    if problem:
        return error(problem, 400, field="assets")
    assets = [validated_content_json(RecipeAsset, asset) for asset in added]
    data = validated_content_json(RecipeAssetsData, {"assets": assets})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/assets", methods=["POST"])
def recipe_assets():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    section = body.get("section")
    if section not in ("splash", "ingredients", "instructions"):
        return error("bad_request", 400, field="section")
    assets = body.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    if section == "splash":
        #  A single user-facing choice, not an arbitrary list: sets/clears
        #  metadata.image and metadata.thumbnail together.
        if len(assets) > 1:
            return error("bad_request", 400, field="assets")
        index = None
    else:
        index = body.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            return error("bad_request", 400, field="index")
    mutation_payload = {
        "file": body.get("file"),
        "section": section,
        "assets": assets,
        **({} if index is None else {"index": index}),
    }
    mutation = RecipeAssetAssignmentRequest.model_validate(mutation_payload, strict=True)
    stored, problem = recipes_api.set_assets(
        path,
        mutation.section,
        mutation.index,
        mutation.assets,
    )
    if problem == "bad_index":
        return error("bad_request", 400, field="index")
    if problem:
        return recipes_api.unreadable(problem, error)
    data = validated_content_json(AssetNamesData, {"assets": stored})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/assets/delete", methods=["POST"])
def recipe_asset_delete():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    assets = body.get("assets")
    if not isinstance(assets, list) or not all(isinstance(a, str) for a in assets):
        return error("bad_request", 400, field="assets")
    status = recipes_api.delete_assets(path, assets)
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200
