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

from flask import current_app, jsonify, request

from common.app import api_response
from common.file_browser import browse_files

from . import api_files_bp

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
