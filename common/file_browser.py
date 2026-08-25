"""
PiFire - Managed File Folder Browsing
=====================================

One implementation of the three things every "folder of PiFire archives"
surface needs: containment-checking a client-supplied name, listing a folder
by extension, and building a paginated {filename, title, thumbnail} listing.

Extracted from the two near-identical copies that already existed --
file_mgmt/recipes.py's get_recipefilelist/get_recipefilelist_details and
blueprints/cookfile/routes.py's _get_cookfilelist/_get_cookfilelist_details --
which differed only in the extension and the folder they read. Both now
delegate here, so the legacy pages and the new /api/files surface cannot drift.
"""

import os

from common.app import paginate_list
from file_mgmt.common import read_json_file_data
from file_mgmt.media import unpack_thumb


def resolve_managed_file(folder, name):
    """Resolve `name` against `folder` and require the result to stay inside it.

    Returns the resolved absolute path, or None if `name` is empty or would
    escape `folder` (via `../`, an absolute path, or a symlink pointing out).

    Deliberately NOT secure_filename: cook and recipe titles are user-chosen and
    may legitimately contain spaces, parentheses and `#`, which secure_filename
    mangles -- silently breaking opens/downloads/deletes for valid files. This
    validates the resulting PATH instead of the name's character set. Same
    reasoning, and same implementation, as blueprints/history/routes.py's
    _safe_history_path, which this supersedes for new code.

    Existence is NOT checked here: upload needs a contained destination for a
    file that does not exist yet. Callers that require an existing file assert
    os.path.isfile() themselves.
    """
    if not name:
        return None
    base = os.path.realpath(folder)
    candidate = os.path.realpath(os.path.join(folder, name))
    if candidate == base or not candidate.startswith(base + os.sep):
        return None
    return candidate


def list_managed_files(folder, extension):
    """Bare filenames in `folder` ending in `extension`.

    Creates `folder` if it is missing -- both callers this replaces did, and a
    first-boot install has neither ./history/ nor ./recipes/.
    """
    if not os.path.exists(folder):
        os.makedirs(folder, exist_ok=True)
    return [name for name in os.listdir(folder) if name.endswith(extension)]


def file_details(folder, filenames):
    """Read each archive's metadata.json for its title and unpack its thumbnail.

    A file that will not open reports title "ERROR" and no thumbnail rather than
    raising -- one corrupt archive must not blank the whole listing, which is
    the behaviour both replaced functions had.
    """
    out = []
    for name in filenames:
        path = os.path.join(folder, name)
        metadata, status = read_json_file_data(path, "metadata")
        if status != "OK":
            out.append({"filename": name, "title": "ERROR", "thumbnail": ""})
            continue
        thumbnail = unpack_thumb(metadata["thumbnail"], path, metadata["id"]) if "thumbnail" in metadata else ""
        out.append({"filename": name, "title": metadata["title"], "thumbnail": thumbnail})
    return out


def browse_files(folder, extension, *, page=1, per_page=10, reverse=True):
    """Paginated listing of a managed folder.

    Only the requested page's archives are opened -- paginate_list slices first,
    then file_details reads. That is the existing behaviour and it matters: a
    hundred-cook folder would otherwise unzip a hundred archives per request.
    """
    names = [{"filename": name} for name in list_managed_files(folder, extension)]
    total = len(names)
    pagination = paginate_list(names, "filename", reverse, per_page, page)
    return {
        "items": file_details(folder, [item["filename"] for item in pagination["displaydata"]]),
        "page": pagination["curpage"],
        "last_page": pagination["lastpage"],
        "per_page": pagination["itemspage"],
        "reverse": bool(reverse),
        "total": total,
    }
