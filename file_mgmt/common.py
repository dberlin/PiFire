"""
PiFire - File / Common Functions
================================

This file contains common functions for various file formats (i.e. Cookfile and Recipe Files).

"""

"""
Imported Modules
================
"""
import json
import os
import shutil
import tempfile
import zipfile

from pydantic import JsonValue

HISTORY_FOLDER = "./history/"  # Path to historical cook files
RECIPE_FOLDER = "./recipes/"  # Path to recipe files

"""
Functions
=========
"""

# Process-wide private base directory for unpacked asset/thumbnail images.
# Lazily created as a 0700, app-owned, unique mkdtemp instead of the old
# predictable, world-writable shared temp path (symlink/pre-creation attack
# surface, no cleanup). The static symlink NAME (./static/img/tmp/{id}) and
# thus the browser-facing served URL are unchanged -- only the base moves.
_ASSET_TMP_BASE = None


def get_asset_tmp_base():
    """Return the process-wide private base dir for unpacked assets, creating
    it lazily (and re-creating it if it was removed out from under us)."""
    global _ASSET_TMP_BASE
    if _ASSET_TMP_BASE is None or not os.path.isdir(_ASSET_TMP_BASE):
        _ASSET_TMP_BASE = tempfile.mkdtemp(prefix="pifire-assets-")
    return _ASSET_TMP_BASE


def read_json_file_data(filename, jsonfile, unpackassets=True):
    """
    Read File JSON File data out of the zipped pifire file:
            Must specify the file name, and the jsonfile element to be extracted (without the .json extension)
    """
    status = "OK"

    try:
        with zipfile.ZipFile(filename, mode="r") as archive:
            json_string = archive.read(jsonfile + ".json")
            dictionary = json.loads(json_string)
            # If this is the assets file, load the assets into the temporary folder
            if jsonfile == "assets" and unpackassets:
                json_string = archive.read("metadata.json")
                metadata = json.loads(json_string)
                parent_id = metadata["id"]  # Get parent id for this file and store all images in parent_id folder

                for asset in range(len(dictionary)):
                    #  Get asset file information
                    mediafile = dictionary[asset]["filename"]
                    id = dictionary[asset]["id"]
                    filetype = dictionary[asset]["type"]
                    #  Read the file(s) into memory
                    data = archive.read(f"assets/{mediafile}")  # Read bytes into variable
                    thumb = archive.read(f"assets/thumbs/{mediafile}")  # Read bytes into variable
                    base = get_asset_tmp_base()
                    os.makedirs(os.path.join(base, parent_id, "thumbs"), mode=0o700, exist_ok=True)
                    #  Write fullsize image to disk
                    with open(os.path.join(base, parent_id, f"{id}.{filetype}"), "wb") as destination:
                        destination.write(data)
                    #  Write thumbnail image to disk
                    with open(os.path.join(base, parent_id, "thumbs", f"{id}.{filetype}"), "wb") as destination:
                        destination.write(thumb)

                    if not os.path.exists("./static/img/tmp"):
                        os.mkdir("./static/img/tmp")
                    if not os.path.exists(f"./static/img/tmp/{parent_id}"):
                        os.symlink(os.path.join(base, parent_id), f"./static/img/tmp/{parent_id}")

    except zipfile.BadZipFile as error:
        status = f"Error: {error}"
        dictionary = {}
    except json.decoder.JSONDecodeError:
        status = "Error: JSON Decoding Error."
        dictionary = {}
    except:
        if jsonfile == "assets":
            status = "Error: Error opening assets."
        else:
            status = "Error: Unspecified"
        dictionary = {}

    return (dictionary, status)


def read_optional_json_file_data(filename, member) -> tuple[JsonValue | None, str]:
    """Read an optional JSON archive member without hiding present-data errors."""
    try:
        with zipfile.ZipFile(filename, mode="r") as archive:
            json_string = archive.read(member + ".json")
            return (json.loads(json_string), "OK")
    except KeyError:
        return (None, "OK")
    except zipfile.BadZipFile as error:
        return (None, f"Error: {error}")
    except json.decoder.JSONDecodeError:
        return (None, "Error: JSON Decoding Error.")
    except Exception:
        return (None, "Error: Unspecified")


def update_json_file_data(filedata, filename, jsonfile):
    """
    Write an update to the recipe file
    """
    status = "OK"
    jsonfilename = jsonfile + ".json"

    # Borrowed from StackOverflow https://stackoverflow.com/questions/25738523/how-to-update-one-file-inside-zip-file
    # Submitted by StackOverflow user Sebdelsol

    # Start by creating a temporary file without the jsonfile that is being edited
    tmpfd, tmpname = tempfile.mkstemp(dir=os.path.dirname(filename))
    os.close(tmpfd)
    try:
        # Create a temp copy of the archive without filename
        with zipfile.ZipFile(filename, "r") as zin, zipfile.ZipFile(tmpname, "w") as zout:
            zout.comment = zin.comment  # Preserve the zip metadata comment
            for item in zin.infolist():
                if item.filename != jsonfilename:
                    zout.writestr(item, zin.read(item.filename))
        # Replace original with the temp archive
        os.remove(filename)
        os.rename(tmpname, filename)
        # Now add updated JSON file with its new data
        with zipfile.ZipFile(filename, mode="a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(jsonfilename, json.dumps(filedata, indent=2, sort_keys=True))

    except zipfile.BadZipFile as error:
        status = f"Error: {error}"
    except:
        status = "Error: Unspecified"

    return status


def fixup_assets(filename, jsondata):
    jsondata["assets"], status = read_json_file_data(filename, "assets", unpackassets=False)

    # Loop through assets list, check actual files exist, remove from assets list if not
    #   - Get file list from cookfile / assets
    assetlist = []
    thumblist = []
    with zipfile.ZipFile(filename, mode="r") as archive:
        for item in archive.infolist():
            if "assets" in item.filename:
                if item.filename == "assets/" or item.filename == "assets.json" or item.filename == "assets/thumbs/":
                    pass
                elif "thumbs" in item.filename:
                    thumblist.append(item.filename.replace("assets/thumbs/", ""))
                else:
                    assetlist.append(item.filename.replace("assets/", ""))

    #   - Loop through asset list / compare with file list
    for asset in jsondata["assets"]:
        if asset["filename"] not in assetlist:
            jsondata["assets"].remove(asset)
        else:
            for item in assetlist:
                if asset["filename"] in item:
                    assetlist.remove(item)
                    break

    # Loop through remaining files in assets list and populate
    for filename in assetlist:
        asset = {
            "id": filename.rsplit(".", 1)[0].lower(),
            "filename": filename.replace(HISTORY_FOLDER, ""),
            "type": filename.rsplit(".", 1)[1].lower(),
        }
        jsondata["assets"].append(asset)

    # Check Metadata Thumbnail if asset exists
    thumbnail = jsondata["metadata"]["thumbnail"]
    assetlist = []
    for asset in jsondata["assets"]:
        assetlist.append(asset["filename"])

    if thumbnail != "" and thumbnail not in assetlist:
        jsondata["metadata"]["thumbnail"] = ""

    # Loop through comments and check if asset lists contain valid assets, remove if not
    comments = jsondata["comments"]
    for index, comment in enumerate(comments):
        for asset in comment["assets"]:
            if asset not in assetlist:
                jsondata["comments"][index]["assets"].remove(asset)

    update_json_file_data(jsondata["assets"], filename, "assets")
    status = "OK"
    return (jsondata, status)


def remove_assets(filename, assetlist, filetype="cookfile"):
    status = "OK"

    if filetype == "recipefile":
        recipe, status = read_json_file_data(filename, "recipe")

    metadata, status = read_json_file_data(filename, "metadata")
    comments, status = read_json_file_data(filename, "comments")
    assets, status = read_json_file_data(filename, "assets", unpackassets=False)

    # Check Thumbnail against assetlist
    if metadata["thumbnail"] in assetlist:
        metadata["thumbnail"] = ""
        if filetype == "recipefile":
            metadata["image"] = ""
        update_json_file_data(metadata, filename, "metadata")

    # Check comment.json assets against assetlist
    modified = False
    for index, comment in enumerate(comments):
        for asset in comment["assets"]:
            if asset in assetlist:
                comment["assets"].remove(asset)
                modified = True
    if modified:
        update_json_file_data(comments, filename, "comments")

    # Check recipe.json assets against assetlist
    if filetype == "recipefile":
        modified = False
        for index, ingredient in enumerate(recipe["ingredients"]):
            for asset in ingredient["assets"]:
                if asset in assetlist:
                    recipe["ingredients"][index]["assets"].remove(asset)
                    modified = True
        for index, instruction in enumerate(recipe["instructions"]):
            for asset in instruction["assets"]:
                if asset in assetlist:
                    recipe["instructions"][index]["assets"].remove(asset)
                    modified = True
        if modified:
            update_json_file_data(recipe, filename, "recipe")

    # Check asset.json against assetlist
    modified = False
    tempassets = assets.copy()
    for asset in tempassets:
        if asset["filename"] in assetlist:
            assets.remove(asset)
            modified = True
    if modified:
        update_json_file_data(assets, filename, "assets")

    # Traverse list of asset files from the compressed file, remove asset and thumb
    try:
        tmpdir = os.path.join(get_asset_tmp_base(), metadata["id"])
        os.makedirs(tmpdir, mode=0o700, exist_ok=True)
        with zipfile.ZipFile(filename, mode="r") as archive:
            new_archive = zipfile.ZipFile(f"{tmpdir}/new.pifire", "w", zipfile.ZIP_DEFLATED)
            for item in archive.infolist():
                remove = False
                for asset in assetlist:
                    if asset in item.filename:
                        remove = True
                        break
                if not remove:
                    buffer = archive.read(item.filename)
                    new_archive.writestr(item, buffer)
            new_archive.close()

        os.remove(filename)
        shutil.move(f"{tmpdir}/new.pifire", filename)
    except:
        status = "Error:  Error removing assets from file."

    return status
