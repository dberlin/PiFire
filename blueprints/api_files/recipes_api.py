"""Recipe endpoint handlers for /api/files/recipes/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file
against routes.recipe_folder(). None of them ever accepts a path, which is the
single behavioural difference from blueprints/recipes/routes.py.

comments.json is deliberately absent from every payload: no UI in either app has
ever written a recipe comment (tests/web/test_page_recipes.py:33-38). The member
is preserved on every write because update_json_file_data rewrites one member
and copies the rest.
"""

from werkzeug.utils import secure_filename

from common.app import api_response, classify_cookfile_error
from file_mgmt.common import read_json_file_data, update_json_file_data
from file_mgmt.recipes import read_recipefile


def load(path):
    """read_recipefile, with the (struct, status) shape the routes branch on."""
    return read_recipefile(path)


def unreadable(status, error):
    """422 for an archive that exists but will not open."""
    return error("unreadable", 422, errortype=classify_cookfile_error(status))


def detail_payload(struct, filename):
    """Everything the React editor needs, and nothing it does not."""
    return {
        "filename": filename,
        "metadata": struct["metadata"],
        "recipe": struct["recipe"],
        "assets": struct["assets"],
    }


def save_upload(storage):
    """Vet an uploaded recipe archive's filename.

    Only `.pfrecipe` is accepted here -- config.py's app-wide
    ALLOWED_EXTENSIONS also permits images and logs, which have no business
    being uploaded as a recipe. secure_filename() then flattens the name, and
    the CALLER re-resolves the flattened name through require_file(...,
    must_exist=False): secure_filename alone is a character-set filter, not a
    containment proof, and this is a write.
    Returns (safe_name, None) or (None, error_message).
    """
    if storage is None or not storage.filename:
        return None, "bad_request"
    if not storage.filename.lower().endswith(".pfrecipe"):
        return None, "disallowed_file"
    safe_name = secure_filename(storage.filename)
    if not safe_name:
        return None, "bad_request"
    return safe_name, None


_INT_FIELDS = ("prep_time", "cook_time", "rating", "food_probes")
_STR_FIELDS = ("title", "author", "description", "difficulty", "units")


def set_metadata(path, fields):
    """Apply a whole-metadata patch: {field: value, ...}.

    Flask writes one field per request (blueprints/recipes/routes.py:155-176);
    this takes a patch because the React editor's SaveBar saves a form, not a
    keystroke. food_probes is the only field that also touches recipe.json:
    it is structural, so every step's trigger_temps.food must carry exactly
    one entry per probe -- padded with 0, or truncated from the end -- or the
    controller's probe_map remap (controller.py:156-163) indexes past the
    end. That reshape is written before metadata, and recipe.json is opened
    only when food_probes is actually present in the patch.

    An unknown field name is rejected rather than written: Flask's endpoint
    accepts any field name, but a typed client has no reason to send one it
    does not recognise.

    Returns ("OK", None), ("bad_field", field), or (<read/write status>, None).
    """
    unknown = [name for name in fields if name not in _INT_FIELDS and name not in _STR_FIELDS]
    if unknown:
        return "bad_field", unknown[0]

    converted = {}
    for name, value in fields.items():
        if name in _INT_FIELDS:
            try:
                converted[name] = int(value)
            except TypeError, ValueError:
                return "bad_field", name
        else:
            converted[name] = str(value)

    metadata, status = read_json_file_data(path, "metadata")
    if status != "OK":
        return status, None

    if "food_probes" in converted:
        recipe, status = read_json_file_data(path, "recipe")
        if status != "OK":
            return status, None
        food_probes = converted["food_probes"]
        for step in recipe["steps"]:
            food = step["trigger_temps"]["food"]
            while len(food) < food_probes:
                food.append(0)
            while len(food) > food_probes:
                food.pop()
        status = update_json_file_data(recipe, path, "recipe")
        if status != "OK":
            return status, None

    metadata.update(converted)
    return update_json_file_data(metadata, path, "metadata"), None


def add_ingredient(path):
    """Append a blank ingredient, mirroring Flask's add/ingredients branch."""
    recipe, status = read_json_file_data(path, "recipe")
    if status != "OK":
        return status
    recipe["ingredients"].append({"name": "", "quantity": "", "assets": []})
    return update_json_file_data(recipe, path, "recipe")


def update_ingredient(path, index, name, quantity):
    """Rename/requantify ingredient `index`.

    instructions[].ingredients holds ingredient NAME STRINGS, not indices, so
    a rename that does not cascade orphans every instruction that referenced
    the old name. The cascade runs before the name is overwritten, since that
    is the last point at which the old name is still known.

    Returns "OK", "bad_index" (index out of range), or a write status.
    """
    recipe, status = read_json_file_data(path, "recipe")
    if status != "OK":
        return status
    ingredients = recipe["ingredients"]
    if not 0 <= index < len(ingredients):
        return "bad_index"
    old_name = ingredients[index]["name"]
    if old_name != name:
        for instruction in recipe["instructions"]:
            if old_name in instruction["ingredients"]:
                instruction["ingredients"].remove(old_name)
                instruction["ingredients"].append(name)
    ingredients[index]["name"] = name
    ingredients[index]["quantity"] = quantity
    return update_json_file_data(recipe, path, "recipe")


def delete_ingredient(path, index):
    """Remove ingredient `index`, cascading the removal into every
    instruction BEFORE the pop -- once popped, the name is gone and there is
    nothing left to match on.
    """
    recipe, status = read_json_file_data(path, "recipe")
    if status != "OK":
        return status
    ingredients = recipe["ingredients"]
    if not 0 <= index < len(ingredients):
        return "bad_index"
    name = ingredients[index]["name"]
    for instruction in recipe["instructions"]:
        if name in instruction["ingredients"]:
            instruction["ingredients"].remove(name)
    ingredients.pop(index)
    return update_json_file_data(recipe, path, "recipe")
