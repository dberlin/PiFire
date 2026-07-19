import os
from werkzeug.utils import secure_filename
from flask import render_template, request, current_app, send_file, jsonify, render_template_string
from common.datastore_accessors import read_settings, read_control
from common.modes import Mode
from common.app import paginate_list, allowed_file
from file_mgmt.common import update_json_file_data, remove_assets
from file_mgmt.media import add_asset
from file_mgmt.recipes import read_recipefile, create_recipefile, get_recipefilelist, get_recipefilelist_details

from . import recipes_bp


@recipes_bp.route("/", methods=["POST", "GET"])
def recipes_page():
    settings = read_settings()
    control = read_control()
    return render_template(
        "recipes/index.html",
        settings=settings,
        control=control,
    )


# ----------------------------------------------------------------------------
# recipes/_macro_recipes.html render_recipe_edit_X lookup table
#
# NOT uniform: render_recipe_edit_title takes an extra recipe_filename
# positional arg (and an extra recipe_filename render_template_string kwarg);
# the other five (description/metadata/ingredients/instructions/steps) take
# only recipe_data. `name` below is the dispatch key, not always the macro
# name suffix (kept 1:1 here for readability).
# ----------------------------------------------------------------------------

_RECIPE_EDIT_MACROS = {
    "title": "render_recipe_edit_title",
    "description": "render_recipe_edit_description",
    "metadata": "render_recipe_edit_metadata",
    "ingredients": "render_recipe_edit_ingredients",
    "instructions": "render_recipe_edit_instructions",
    "steps": "render_recipe_edit_steps",
}


def _render_recipe_edit(name, recipe_data, recipe_filename=None):
    macro = _RECIPE_EDIT_MACROS[name]
    if name == "title":
        render_string = (
            "{% from 'recipes/_macro_recipes.html' import "
            + macro
            + " %}{{ "
            + macro
            + "(recipe_data, recipe_filename) }}"
        )
        return render_template_string(render_string, recipe_data=recipe_data, recipe_filename=recipe_filename)
    render_string = "{% from 'recipes/_macro_recipes.html' import " + macro + " %}{{ " + macro + "(recipe_data) }}"
    return render_template_string(render_string, recipe_data=recipe_data)


# ----------------------------------------------------------------------------
# recipes_data form action handlers (request.content_type contains "form")
# Each handler reads request.form itself and returns a Response.
# ----------------------------------------------------------------------------


def _recipes_form_upload(RECIPE_FOLDER):
    # print(f'Files: {request.files}')
    remote_file = request.files["recipefile"]
    result = "error"
    if remote_file.filename != "":
        if remote_file and allowed_file(remote_file.filename):
            filename = secure_filename(remote_file.filename)
            remote_file.save(os.path.join(RECIPE_FOLDER, filename))
            result = "success"
    return jsonify({"result": result})


def _recipes_form_uploadassets(RECIPE_FOLDER):
    requestform = request.form
    # Assume we have request.files and localfile in response
    uploadedfiles = request.files.getlist("assetfiles")
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"

    errors = []
    for remotefile in uploadedfiles:
        if remotefile.filename != "":
            # Load the Recipe File
            recipe_data, status = read_recipefile(filepath)
            parent_id = recipe_data["metadata"]["id"]
            tmp_path = f"/tmp/pifire/{parent_id}"
            os.makedirs(tmp_path, exist_ok=True)

            if remotefile and allowed_file(remotefile.filename):
                asset_filename = secure_filename(remotefile.filename)
                pathfile = os.path.join(tmp_path, asset_filename)
                remotefile.save(pathfile)
                add_asset(filepath, tmp_path, asset_filename)
            else:
                errors.append("Disallowed File Upload.")
    if len(errors):
        status = "error"
    else:
        status = "success"
    return jsonify({"result": status, "errors": errors})


def _recipes_form_recipefilelist(RECIPE_FOLDER):
    requestform = request.form
    page = int(requestform["page"])
    reverse = True if requestform["reverse"] == "true" else False
    itemsperpage = int(requestform["itemsperpage"])
    filelist = get_recipefilelist()
    recipefilelist = []
    for filename in filelist:
        recipefilelist.append({"filename": filename, "title": "", "thumbnail": ""})
    paginated_recipefile = paginate_list(recipefilelist, "filename", reverse, itemsperpage, page)
    paginated_recipefile["displaydata"] = get_recipefilelist_details(paginated_recipefile["displaydata"])
    return render_template("recipes/_recipefile_list.html", pgntdrf=paginated_recipefile)


def _recipes_form_recipeview(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    return render_template(
        "recipes/_recipe_view.html", recipe_data=recipe_data, recipe_filename=filename, recipe_filepath=filepath
    )


def _recipes_form_recipeedit(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    if filename == "":
        filepath = create_recipefile()
        filename = filepath.replace(RECIPE_FOLDER, "")
    else:
        filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    return render_template(
        "recipes/_recipe_edit.html", recipe_data=recipe_data, recipe_filename=filename, recipe_filepath=filepath
    )


def _recipes_form_update(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    if requestform["update"] in ["metadata"]:
        field = requestform["field"]
        if field in ["prep_time", "cook_time", "rating"]:
            recipe_data["metadata"][field] = int(requestform["value"])
        elif field == "food_probes":
            food_probes = int(requestform["value"])
            recipe_data["metadata"][field] = food_probes
            for index, step in enumerate(recipe_data["recipe"]["steps"]):
                while len(step["trigger_temps"]["food"]) > food_probes:
                    recipe_data["recipe"]["steps"][index]["trigger_temps"]["food"].pop()
                while len(step["trigger_temps"]["food"]) < food_probes:
                    recipe_data["recipe"]["steps"][index]["trigger_temps"]["food"].append(0)
            update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        else:
            recipe_data["metadata"][field] = requestform["value"]
        update_json_file_data(recipe_data["metadata"], filepath, "metadata")
        if field == "title":
            return _render_recipe_edit("title", recipe_data, filename)
        elif field == "description":
            return _render_recipe_edit("description", recipe_data)
        else:
            return _render_recipe_edit("metadata", recipe_data)
    elif requestform["update"] == "ingredients":
        recipe = recipe_data["recipe"]
        ingredient_index = int(requestform["index"])
        if recipe["ingredients"][ingredient_index]["name"] != requestform["name"]:
            # Go Fixup any Instruction Step that includes this Ingredient First
            for index, direction in enumerate(recipe["instructions"]):
                if recipe["ingredients"][ingredient_index]["name"] in recipe["instructions"][index]["ingredients"]:
                    recipe["instructions"][index]["ingredients"].remove(recipe["ingredients"][ingredient_index]["name"])
                    recipe["instructions"][index]["ingredients"].append(requestform["name"])
        recipe["ingredients"][ingredient_index]["name"] = requestform["name"]
        recipe["ingredients"][ingredient_index]["quantity"] = requestform["quantity"]
        recipe_data["recipe"] = recipe
        update_json_file_data(recipe, filepath, "recipe")
        return _render_recipe_edit("ingredients", recipe_data)
    elif requestform["update"] == "instructions":
        instruction_index = int(requestform["index"])
        if "ingredients[]" in requestform:
            ingredients = request.form.getlist("ingredients[]")
        else:
            ingredients = []
        recipe_data["recipe"]["instructions"][instruction_index]["ingredients"] = ingredients
        recipe_data["recipe"]["instructions"][instruction_index]["text"] = requestform["text"]
        recipe_data["recipe"]["instructions"][instruction_index]["step"] = int(requestform["step"])
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("instructions", recipe_data)
    elif requestform["update"] == "steps":
        step_index = int(requestform["index"])
        food = request.form.getlist("food[]")
        for i in range(0, len(food)):
            food[i] = int(food[i])
        recipe_data["recipe"]["steps"][step_index]["hold_temp"] = int(requestform["hold_temp"])
        recipe_data["recipe"]["steps"][step_index]["timer"] = int(requestform["timer"])
        recipe_data["recipe"]["steps"][step_index]["mode"] = requestform["mode"]
        recipe_data["recipe"]["steps"][step_index]["trigger_temps"]["primary"] = int(requestform["primary"])
        recipe_data["recipe"]["steps"][step_index]["trigger_temps"]["food"] = food
        recipe_data["recipe"]["steps"][step_index]["pause"] = True if requestform["pause"] == "true" else False
        recipe_data["recipe"]["steps"][step_index]["notify"] = True if requestform["notify"] == "true" else False
        recipe_data["recipe"]["steps"][step_index]["message"] = requestform["message"]

        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("steps", recipe_data)
    else:
        return '<strong color="red">No Data</strong>'


def _recipes_form_delete(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    if requestform["delete"] == "ingredients":
        recipe = recipe_data["recipe"]
        ingredient_index = int(requestform["index"])
        # Go Fixup any Instruction Step that includes this Ingredient First
        for index, direction in enumerate(recipe["instructions"]):
            if recipe["ingredients"][ingredient_index]["name"] in recipe["instructions"][index]["ingredients"]:
                recipe["instructions"][index]["ingredients"].remove(recipe["ingredients"][ingredient_index]["name"])
        recipe["ingredients"].pop(ingredient_index)
        recipe_data["recipe"] = recipe
        update_json_file_data(recipe, filepath, "recipe")
        return _render_recipe_edit("ingredients", recipe_data)
    elif requestform["delete"] == "instructions":
        instruction_index = int(requestform["index"])
        recipe_data["recipe"]["instructions"].pop(instruction_index)
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("instructions", recipe_data)
    elif requestform["delete"] == "steps":
        step_index = int(requestform["index"])
        recipe_data["recipe"]["steps"].pop(step_index)
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("steps", recipe_data)
    else:
        return '<strong color="red">No Data</strong>'


def _recipes_form_add(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    if requestform["add"] == "ingredients":
        new_ingredient = {"name": "", "quantity": "", "assets": []}
        recipe_data["recipe"]["ingredients"].append(new_ingredient)
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("ingredients", recipe_data)
    elif requestform["add"] == "instructions":
        new_instruction = {"text": "", "ingredients": [], "assets": [], "step": 0}
        recipe_data["recipe"]["instructions"].append(new_instruction)
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("instructions", recipe_data)
    elif requestform["add"] == "steps":
        step_index = int(requestform["index"])
        food_list = []
        for count in range(0, recipe_data["metadata"]["food_probes"]):
            food_list.append(0)
        new_step = {
            "hold_temp": 0,
            "message": "",
            "mode": "Smoke",
            "notify": False,
            "pause": False,
            "timer": 0,
            "trigger_temps": {"primary": 0, "food": food_list},
        }
        recipe_data["recipe"]["steps"].insert(step_index, new_step)
        update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        return _render_recipe_edit("steps", recipe_data)
    else:
        return '<strong color="red">No Data</strong>'


def _recipes_form_refresh(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    refresh_value = requestform["refresh"]
    if refresh_value in _RECIPE_EDIT_MACROS and refresh_value != "title":
        return _render_recipe_edit(refresh_value, recipe_data)
    return None


def _recipes_form_reciperunstatus(RECIPE_FOLDER):
    requestform = request.form
    control = read_control()
    if control["mode"] != Mode.RECIPE:
        filename = requestform["filename"]
        filepath = f"{RECIPE_FOLDER}{filename}"
    else:
        filepath = control["recipe"]["filename"]
        filename = filepath.replace(RECIPE_FOLDER, "")

    recipe_data, status = read_recipefile(filepath)
    return render_template(
        "recipes/_recipe_status.html",
        control=control,
        recipe_data=recipe_data,
        recipe_filename=filename,
        recipe_filepath=filepath,
    )


def _recipes_form_recipeassetmanager(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    section = requestform["section"]
    section_index = int(requestform["index"])
    if section == "splash":
        assets_selected = [recipe_data["metadata"]["image"]]
    elif section in ["ingredients", "instructions"]:
        assets_selected = recipe_data["recipe"][section][section_index]["assets"]
    elif section == "comments":
        assets_selected = recipe_data["comments"][section_index]["assets"]
    else:
        assets_selected = []
    return render_template(
        "recipes/_recipe_assets.html",
        recipe_data=recipe_data,
        recipe_filename=filename,
        recipe_filepath=filepath,
        section=section,
        section_index=section_index,
        selected=assets_selected,
    )


def _recipes_form_recipeshowasset(RECIPE_FOLDER):
    requestform = request.form
    filename = requestform["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    section = requestform["section"]
    section_index = int(requestform["section_index"])
    selected_asset = requestform["asset"]
    if section == "metadata":
        assets = [recipe_data["metadata"]["title"]]
    else:
        assets = recipe_data["recipe"][section][section_index]["assets"]
    recipe_id = recipe_data["metadata"]["id"]
    render_string = "{% from 'recipes/_macro_recipes.html' import render_recipe_asset_viewer %}{{ render_recipe_asset_viewer(assets, recipe_id, selected_asset) }}"
    return render_template_string(render_string, assets=assets, recipe_id=recipe_id, selected_asset=selected_asset)


_RECIPES_FORM_DISPATCH = {
    "upload": _recipes_form_upload,
    "uploadassets": _recipes_form_uploadassets,
    "recipefilelist": _recipes_form_recipefilelist,
    "recipeview": _recipes_form_recipeview,
    "recipeedit": _recipes_form_recipeedit,
    "update": _recipes_form_update,
    "delete": _recipes_form_delete,
    "add": _recipes_form_add,
    "refresh": _recipes_form_refresh,
    "reciperunstatus": _recipes_form_reciperunstatus,
    "recipeassetmanager": _recipes_form_recipeassetmanager,
    "recipeshowasset": _recipes_form_recipeshowasset,
}


# ----------------------------------------------------------------------------
# recipes_data JSON action handlers (request.content_type contains "json")
# ----------------------------------------------------------------------------


def _recipes_json_deletefile(RECIPE_FOLDER):
    requestjson = request.json
    filename = requestjson["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    os.system(f"rm {filepath}")
    return jsonify({"result": "success"})


def _recipes_json_assetchange(RECIPE_FOLDER):
    requestjson = request.json
    filename = requestjson["filename"]
    filepath = f"{RECIPE_FOLDER}{filename}"
    recipe_data, status = read_recipefile(filepath)
    section = requestjson["section"]
    section_index = requestjson["index"]
    asset_name = requestjson["asset_name"]
    asset_id = requestjson["asset_id"]
    action = requestjson["action"]
    if action == "add":
        if section in ["ingredients", "instructions"]:
            if asset_name not in recipe_data["recipe"][section][section_index]["assets"]:
                recipe_data["recipe"][section][section_index]["assets"].append(asset_name)
                update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        elif section in ["splash"]:
            recipe_data["metadata"]["image"] = asset_name
            recipe_data["metadata"]["thumbnail"] = asset_name
            update_json_file_data(recipe_data["metadata"], filepath, "metadata")
        elif section in ["delete"]:
            remove_assets(filepath, [asset_name], filetype="recipefile")
    elif action == "remove":
        if section in ["ingredients", "instructions"]:
            if asset_name in recipe_data["recipe"][section][section_index]["assets"]:
                recipe_data["recipe"][section][section_index]["assets"].remove(asset_name)
                update_json_file_data(recipe_data["recipe"], filepath, "recipe")
        elif section in ["splash"]:
            recipe_data["metadata"]["image"] = ""
            recipe_data["metadata"]["thumbnail"] = ""
            update_json_file_data(recipe_data["metadata"], filepath, "metadata")
        elif section in ["delete"]:
            remove_assets(filepath, [asset_name], filetype="recipefile")
    return jsonify({"result": "success"})


_RECIPES_JSON_DISPATCH = {
    "deletefile": _recipes_json_deletefile,
    "assetchange": _recipes_json_assetchange,
}


@recipes_bp.route("/data", methods=["POST", "GET"])
@recipes_bp.route("/data/upload", methods=["POST", "GET"])
@recipes_bp.route("/data/download/<filename>", methods=["GET"])
def recipes_data(filename=None):
    settings = read_settings()
    control = read_control()
    RECIPE_FOLDER = current_app.config["RECIPE_FOLDER"]

    if (request.method == "GET") and (filename is not None):
        filepath = f"{RECIPE_FOLDER}{filename}"
        # print(f'Sending: {filepath}')
        return send_file(filepath, as_attachment=True, max_age=0)

    if (request.method == "POST") and ("form" in request.content_type):
        requestform = request.form
        # print(f'Request FORM: {requestform}')
        for key, handler in _RECIPES_FORM_DISPATCH.items():
            if key in requestform:
                result = handler(RECIPE_FOLDER)
                if result is not None:
                    return result

    """ AJAX POST JSON Type Method Handler """
    if (request.method == "POST") and ("json" in request.content_type):
        requestjson = request.json
        # print(f'Request JSON: {requestjson}')
        for key, handler in _RECIPES_JSON_DISPATCH.items():
            if key in requestjson:
                result = handler(RECIPE_FOLDER)
                if result is not None:
                    return result

    return jsonify({"result": "error"})
