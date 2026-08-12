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
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest

from common.app import api_response
from common.control_delta import control_delta
from common.datastore_accessors import enqueue_control_delta, read_control
from common.file_browser import browse_files, resolve_managed_file
from common.modes import Mode
from common.web_contracts.content import (
    AssetNamesData,
    ContentErrorEnvelope,
    CookFileAsset,
    CookFileAssetsData,
    CookFileChartData,
    CookFileCommentAddRequest,
    CookFileCommentAssetsRequest,
    CookFileCommentDeleteRequest,
    CookFileCommentUpdateRequest,
    CookFileComment,
    CookFileDetail,
    CookFileLabelData,
    CookFileLabelRequest,
    CookFileRecoverRequest,
    CookFileThumbnailRequest,
    CookFileTitleRequest,
    EmptyContentRequest,
    FileAssetsRequest,
    FileRequest,
    FileListing,
    FilenameData,
    RecipeAsset,
    RecipeAssetsData,
    RecipeIndexedAssetAssignmentRequest,
    RecipeDetail,
    RecipeIngredientAddRequest,
    RecipeIngredientDeleteRequest,
    RecipeIngredientUpdateRequest,
    RecipeInstructionAddRequest,
    RecipeInstructionDeleteRequest,
    RecipeInstructionUpdateRequest,
    RecipeMetadataUpdateRequest,
    RecipeSplashAssetAssignmentRequest,
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


def _validated_request(model, body, fallback_field, *, collapse_fields=()):
    """Strictly validate one JSON request and retain the existing 400 envelope."""
    try:
        return model.model_validate(body, strict=True), None
    except ValidationError as exc:
        detail = exc.errors()[0]
        location = detail["loc"]
        field = None
        if detail["type"] != "extra_forbidden":
            field = next((candidate for candidate in collapse_fields if candidate in location), None)
        if field is None:
            field = next((part for part in reversed(location) if isinstance(part, str)), fallback_field)
        return None, error("bad_request", 400, field=field or fallback_field)


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
    payload, validation_err = _validated_request(FileRequest, json_body(), "file")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    os.remove(path)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/title", methods=["POST"])
def cookfile_title():
    payload, validation_err = _validated_request(CookFileTitleRequest, json_body(), "title")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    status = cookfile_api.set_title(path, payload.title)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/label", methods=["POST"])
def cookfile_label():
    payload, validation_err = _validated_request(CookFileLabelRequest, json_body(), "new_label")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    if not payload.old_label:
        return error("bad_request", 400, field="old_label")
    if not payload.new_label.strip():
        return error("bad_request", 400, field="new_label")
    safe, problem = cookfile_api.rename_label(path, payload.old_label, payload.new_label)
    if problem == "label_exists":
        return error("label_exists", 409)
    if problem:
        return cookfile_api.unreadable(problem, error)
    data = validated_content_json(CookFileLabelData, {"new_label_safe": safe})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/cookfiles/recover", methods=["POST"])
def cookfile_recover():
    payload, validation_err = _validated_request(CookFileRecoverRequest, json_body(), "action")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    status = cookfile_api.recover(path, payload.action)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/comments", methods=["POST"])
def cookfile_comments():
    body = json_body()
    action = body.get("action")
    request_model = {
        "add": CookFileCommentAddRequest,
        "update": CookFileCommentUpdateRequest,
        "delete": CookFileCommentDeleteRequest,
    }.get(action)
    if request_model is None:
        return error("bad_request", 400, field="action")
    payload, validation_err = _validated_request(request_model, body, "action")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err

    if action == "add":
        entry, problem = cookfile_api.add_comment(path, payload.text)
    elif action == "update":
        if not payload.id:
            return error("bad_request", 400, field="id")
        entry, problem = cookfile_api.update_comment(path, payload.id, payload.text)
    else:
        if not payload.id:
            return error("bad_request", 400, field="id")
        status = cookfile_api.delete_comment(path, payload.id)
        entry, problem = None, (None if status == "OK" else status)

    if problem == "comment_not_found":
        return error("comment_not_found", 404)
    if problem:
        return cookfile_api.unreadable(problem, error)
    data = None if entry is None else validated_content_json(CookFileComment, entry)
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/cookfiles/comments/assets", methods=["POST"])
def cookfile_comment_assets():
    payload, validation_err = _validated_request(
        CookFileCommentAssetsRequest,
        json_body(),
        "assets",
    )
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    if not payload.id:
        return error("bad_request", 400, field="id")
    stored, problem = cookfile_api.set_comment_assets(path, payload.id, payload.assets)
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
    payload, validation_err = _validated_request(FileAssetsRequest, json_body(), "assets")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    status = cookfile_api.delete_assets(path, payload.assets)
    if status != "OK":
        return cookfile_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200


@api_files_bp.route("/cookfiles/thumbnail", methods=["POST"])
def cookfile_thumbnail():
    payload, validation_err = _validated_request(CookFileThumbnailRequest, json_body(), "asset")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, cookfile_folder())
    if err:
        return err
    if not payload.asset:
        return error("bad_request", 400, field="asset")
    status = cookfile_api.apply_thumbnail(path, payload.asset)
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
    _payload, validation_err = _validated_request(EmptyContentRequest, json_body(), "body")
    if validation_err:
        return validation_err
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
    payload, validation_err = _validated_request(FileRequest, json_body(), "file")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, recipe_folder())
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
    payload, validation_err = _validated_request(FileRequest, json_body(), "file")
    if validation_err:
        return validation_err
    assert payload is not None
    _struct, path, err = _load_recipe(payload.file)
    if err:
        return err
    control = read_control()
    if control.get("mode") != Mode.STOP:
        return error("not_stopped", 409, mode=control.get("mode"))
    enqueue_control_delta(
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
        origin="api-files",
    )
    data = validated_content_json(FilenameData, {"filename": os.path.basename(path)})
    return jsonify(api_response("OK", None, data)), 200


@api_files_bp.route("/recipes/metadata", methods=["POST"])
def recipe_metadata():
    body = json_body()
    mutation, validation_err = _validated_request(RecipeMetadataUpdateRequest, body, "fields")
    if validation_err:
        return validation_err
    path, err = require_file(mutation.file, recipe_folder())
    if err:
        return err
    fields = mutation.fields.model_dump(mode="json", exclude_unset=True)
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
        mutation, validation_err = _validated_request(RecipeIngredientAddRequest, body, "action")
        if validation_err:
            return validation_err
        status = recipes_api.add_ingredient(path)
    elif action == "update":
        mutation, validation_err = _validated_request(RecipeIngredientUpdateRequest, body, "action")
        if validation_err:
            return validation_err
        status = recipes_api.update_ingredient(path, mutation.index, mutation.name, mutation.quantity)
    elif action == "delete":
        mutation, validation_err = _validated_request(RecipeIngredientDeleteRequest, body, "action")
        if validation_err:
            return validation_err
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
        mutation, validation_err = _validated_request(RecipeInstructionAddRequest, body, "action")
        if validation_err:
            return validation_err
        status = recipes_api.add_instruction(path)
    elif action == "update":
        mutation, validation_err = _validated_request(RecipeInstructionUpdateRequest, body, "action")
        if validation_err:
            return validation_err
        status = recipes_api.update_instruction(
            path,
            mutation.index,
            mutation.text,
            mutation.ingredients,
            mutation.step,
        )
    elif action == "delete":
        mutation, validation_err = _validated_request(RecipeInstructionDeleteRequest, body, "action")
        if validation_err:
            return validation_err
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


@api_files_bp.route("/recipes/steps", methods=["POST"])
def recipe_steps():
    body = json_body()
    path, err = require_file(body.get("file", ""), recipe_folder())
    if err:
        return err
    action = body.get("action")
    if not isinstance(action, str):
        return error("bad_request", 400, field="action")
    request_model = {
        "insert": RecipeStepInsertRequest,
        "update": RecipeStepUpdateRequest,
        "delete": RecipeStepDeleteRequest,
    }.get(action)
    if request_model is None:
        return error("bad_request", 400, field="action")
    mutation, validation_err = _validated_request(
        request_model,
        body,
        "action",
        collapse_fields=("trigger_temps",),
    )
    if validation_err:
        return validation_err
    assert mutation is not None
    if action == "insert":
        status = recipes_api.insert_step(path, mutation.index)
    elif action == "update":
        status = recipes_api.update_step(
            path,
            mutation.index,
            mutation.step.model_dump(mode="json", exclude_unset=True),
        )
    else:
        status = recipes_api.delete_step(path, mutation.index)
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
    if section == "splash":
        mutation, validation_err = _validated_request(
            RecipeSplashAssetAssignmentRequest,
            body,
            "section",
        )
        if validation_err:
            return validation_err
        # A single user-facing choice, not an arbitrary list: sets/clears
        # metadata.image and metadata.thumbnail together.
        if len(mutation.assets) > 1:
            return error("bad_request", 400, field="assets")
        index = None
    elif section in ("ingredients", "instructions"):
        mutation, validation_err = _validated_request(
            RecipeIndexedAssetAssignmentRequest,
            body,
            "section",
        )
        if validation_err:
            return validation_err
        index = mutation.index
    else:
        return error("bad_request", 400, field="section")
    stored, problem = recipes_api.set_assets(
        path,
        mutation.section,
        index,
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
    payload, validation_err = _validated_request(FileAssetsRequest, json_body(), "assets")
    if validation_err:
        return validation_err
    assert payload is not None
    path, err = require_file(payload.file, recipe_folder())
    if err:
        return err
    status = recipes_api.delete_assets(path, payload.assets)
    if status != "OK":
        return recipes_api.unreadable(status, error)
    return jsonify(api_response("OK")), 200
