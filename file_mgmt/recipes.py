"""
PiFire - File / Recipe Functions
================================

This file contains functions for file managing the recipe file format.

"""

"""
Imported Modules
================
"""
import datetime
import json
import os
import pathlib
import shutil
import zipfile

from flask import current_app

from common.common import convert_temp, generate_uuid
from common.file_browser import file_details, list_managed_files
from common.persistence.runtime import read_settings
from file_mgmt.common import read_json_file_data

RECIPE_FOLDER = "./recipes/"  # Path to recipe files

"""
Functions
=========
"""


def _default_recipe_metadata():
    settings = read_settings()
    metadata = {}
    metadata["author"] = ""
    metadata["username"] = ""
    metadata["id"] = generate_uuid()
    metadata["title"] = ""
    metadata["description"] = ""
    metadata["image"] = ""
    metadata["thumbnail"] = ""
    metadata["units"] = settings["globals"]["units"]
    metadata["prep_time"] = 0
    metadata["cook_time"] = 0
    metadata["rating"] = 5
    metadata["difficulty"] = "Easy"
    metadata["version"] = "1.1.0"
    metadata["food_probes"] = 2
    return metadata


def _default_recipe_ingredients():
    ingredients = []
    """
    ingredient = {
        "name" : "",
        "quantity" : "",
        "assets" : []
    }
    ingredients.append(ingredient)
    """
    return ingredients


def _default_recipe_instructions():
    instructions = []
    """
    instruction = {
      "text" : "",
      "ingredients" : [],
      "assets" : [],
      "step" : 0
    }
    instructions.append(instruction)
    """
    return instructions


def _default_recipe_comments():
    comments = []
    return comments


def _default_recipe_assets():
    assets = []
    return assets


def _default_recipe_steps():
    steps = []

    # Default Startup Step
    step = {
        "mode": "Startup",
        "trigger_temps": {"primary": 0, "food": [0, 0]},
        "hold_temp": 0,
        "timer": 0,
        "notify": False,
        "message": "",
        "pause": False,
    }
    steps.append(step)

    # Debug Step
    step = {
        "mode": "Hold",
        "trigger_temps": {"primary": 0, "food": [420, 0]},
        "hold_temp": 420,
        "timer": 0,
        "notify": True,
        "message": "Your meat is done, it's time to shutdown.",
        "pause": True,
    }
    steps.append(step)

    # Default Shutdown Step
    step = {
        "mode": "Shutdown",
        "trigger_temps": {"primary": 0, "food": [0, 0]},
        "hold_temp": 0,
        "timer": 0,
        "notify": False,
        "message": "",
        "pause": False,
    }
    steps.append(step)

    return steps


def create_recipefile():
    """
    This function creates an empty recipe file in the RECIPE_FOLDER
    """
    global RECIPE_FOLDER
    now = datetime.datetime.now()
    nowstring = now.strftime("%Y-%m-%d--%H%M")
    title = nowstring + "-Recipe"

    metadata = _default_recipe_metadata()

    recipe = {}
    recipe["ingredients"] = _default_recipe_ingredients()
    recipe["instructions"] = _default_recipe_instructions()
    recipe["steps"] = _default_recipe_steps()

    comments = _default_recipe_comments()
    assets = _default_recipe_assets()

    file_data = {}
    file_data["metadata"] = metadata
    file_data["recipe"] = recipe
    file_data["comments"] = comments
    file_data["assets"] = assets

    # 1. Create all JSON data files
    files_list = ["metadata", "recipe", "comments", "assets"]
    if not os.path.exists(RECIPE_FOLDER):
        os.mkdir(RECIPE_FOLDER)

    recipe_file_path = f"{RECIPE_FOLDER}{title}"
    recipe_file_name = f"{recipe_file_path}.pfrecipe"
    recipe_file_duplicate = 0
    while os.path.exists(recipe_file_name):
        # If file path exists, attempt to add a new path
        recipe_file_duplicate += 1
        recipe_file_name = f"{recipe_file_path}-{recipe_file_duplicate}.pfrecipe"

    os.mkdir(recipe_file_path)  # Make temporary folder for all recipe files

    for item in files_list:
        json_data_string = json.dumps(file_data[item], indent=2, sort_keys=True)
        filename = f"{recipe_file_path}/{item}.json"
        with open(filename, "w+") as recipe_file:
            recipe_file.write(json_data_string)

    # 2. Create empty data folder(s) & add default data
    os.mkdir(f"{recipe_file_path}/assets")
    os.mkdir(f"{recipe_file_path}/assets/thumbs")

    # 3. Create ZIP file of the folder
    directory = pathlib.Path(f"{recipe_file_path}/")
    filename = recipe_file_name

    with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in directory.rglob("*"):
            archive.write(file_path, arcname=file_path.relative_to(directory))

    # 4. Cleanup temporary files
    shutil.rmtree(recipe_file_path, ignore_errors=True)
    return filename


def read_recipefile(filename):
    """
    Read FULL Recipe File into Python Dictionary
    """
    file_data = {}
    status = "OK"
    json_types = ["metadata", "recipe", "comments", "assets"]
    for jsonfile in json_types:
        file_data[jsonfile], status = read_json_file_data(filename, jsonfile)
        if status != "OK":
            break  # Exit loop and function, error string in status

    return (file_data, status)


def _convert_setpoint(temp, units):
    """0 is the disabled sentinel for hold_temp and for both trigger_temps
    members, so it passes through unconverted: 0 F -> -17 C would arm every
    disabled trigger on the recipe."""
    return 0 if not temp else convert_temp(units, temp)


def convert_recipe_units(recipe, units):
    """Convert every temperature in a recipe's steps to `units`."""
    for step in recipe["steps"]:
        step["hold_temp"] = _convert_setpoint(step["hold_temp"], units)
        triggers = step["trigger_temps"]
        triggers["primary"] = _convert_setpoint(triggers["primary"], units)
        triggers["food"] = [_convert_setpoint(temp, units) for temp in triggers["food"]]
    return recipe


def get_recipefilelist(folder=None):
    if folder is None:
        folder = current_app.config["RECIPE_FOLDER"]
    # Grab list of Recipe Files
    return list_managed_files(folder, ".pfrecipe")


def get_recipefilelist_details(recipefilelist):
    #  RECIPE_FOLDER, not current_app.config: this is the module constant the
    #  original read, and tests/web/test_page_recipes.py patches BOTH. Changing
    #  which one is read here would silently move that fixture's target.
    return file_details(RECIPE_FOLDER, [item["filename"] for item in recipefilelist])
