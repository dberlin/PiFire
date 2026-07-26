"""Cook-file endpoint handlers for /api/files/cookfiles/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file. None
of them ever accepts a path, which is the single behavioural difference from
blueprints/cookfile/routes.py.
"""

import copy
import datetime
import os

from werkzeug.utils import secure_filename

from common.app import (
    allowed_file,
    classify_cookfile_error,
    create_safe_name,
    prepare_annotations,
    prepare_csv,
    prepare_event_totals,
    prepare_metrics_csv,
)
from common.common import epoch_to_time, generate_uuid
from file_mgmt.common import fixup_assets, read_json_file_data, update_json_file_data
from file_mgmt.cookfile import read_cookfile, upgrade_cookfile


def load(path):
    """read_cookfile + its status. Returns (struct, status)."""
    return read_cookfile(path)


def unreadable(status, error):
    """The uniform 422 for 'the file exists but will not load'. `errortype` is
    what cferror.html branches on and what the React page turns into an
    Attempt Repair / Attempt Conversion prompt."""
    return error(status, 422, errortype=classify_cookfile_error(status))


def _display_time(epoch_ms):
    """HH:MM:SS for a millisecond epoch, or "" for anything non-numeric.

    Hand-built and upgraded cook files do not all carry numeric epochs, and a
    detail read must not 500 on one.
    """
    if not isinstance(epoch_ms, (int, float)) or isinstance(epoch_ms, bool) or not epoch_ms:
        return ""
    return epoch_to_time(epoch_ms / 1000)


def detail_payload(struct, filename):
    """Reshape a cookfilestruct for the client.

    Deliberately NOT render_cookfile_page's reshape (common/app.py:283-306):
    that one MUTATES metadata in place, replacing the start/end epochs with
    HH:MM:SS strings, so the page can only ever show a time of day and never a
    date. Here the epochs are KEPT alongside the formatted strings -- the
    client can render either, and a second read of the same struct is not
    corrupted by the first.

    Comment text is also left ALONE. render_cookfile_page does
    `comment["text"].replace("\\n", "<br>")` (:287) because Jinja is about to
    emit it as HTML; React renders text nodes and `white-space: pre-wrap`, so
    injecting markup here would be both wrong and an XSS vector.
    """
    metadata = copy.deepcopy(struct["metadata"])
    start = metadata.get("starttime") or 0
    end = metadata.get("endtime") or 0
    metadata["starttime_epoch"] = start
    metadata["endtime_epoch"] = end
    metadata["starttime"] = _display_time(start)
    metadata["endtime"] = _display_time(end)

    events = struct["events"]
    #  prepare_event_totals indexes events[-1]/events[0]/events[-2]
    #  unconditionally (common/app.py:163-168), so a file with fewer than two
    #  events raises IndexError. The Flask page simply 500s on such a file;
    #  here an incomplete cook reports no totals and still renders.
    totals = prepare_event_totals(events) if len(events) >= 2 else {}

    return {
        "filename": filename,
        "metadata": metadata,
        "graph_labels": struct["graph_labels"],
        "events": events,
        "event_totals": totals,
        "comments": struct["comments"],
        "assets": struct["assets"],
    }


def chart_payload(struct):
    """The same four keys the legacy `full_graph` action returns
    (blueprints/cookfile/routes.py:42-47). `annotations` is keyed
    `event_<n>` -- a dict, not a list."""
    return {
        "chart_data": struct["graph_data"]["chart_data"],
        "time_labels": struct["graph_data"]["time_labels"],
        "probe_mapper": struct["graph_data"]["probe_mapper"],
        "annotations": prepare_annotations(0, struct["events"]),
    }


def build_export(path, name, kind):
    """Produce a CSV on disk and return (csv_path, status).

    `os.path.basename(name)` is what goes to the builders, NOT the full path:
    prepare_csv/prepare_metrics_csv both compose their output as
    `"/tmp/" + filename + ".csv"` after a `.replace("./history/", "")` that
    only matches the DEFAULT folder (common/app.py:175, :210). Handing them an
    absolute path under any other folder composes a /tmp path with embedded
    directories that do not exist, and open() raises. Passing the bare name
    makes this correct for every folder without touching common/app.py, which
    the legacy routes still call.
    """
    stem = os.path.basename(name)
    if kind == "data":
        struct, status = read_cookfile(path)
        if status != "OK":
            return None, status
        return prepare_csv(struct["raw_data"], stem), "OK"
    events, status = read_json_file_data(path, "events")
    if status != "OK":
        return None, status
    return prepare_metrics_csv(events, stem), "OK"


def save_upload(storage):
    """Vet an uploaded archive's filename.

    TWO guards, both required and neither sufficient alone:
      1. allowed_file() gates the extension against config.py's
         ALLOWED_EXTENSIONS -- the same gate the legacy route uses.
      2. secure_filename() flattens the name, and the CALLER then re-resolves
         the flattened name through resolve_managed_file. secure_filename alone
         is a character-set filter, not a containment proof, and this is a
         write: a name that survives it but still escapes would create a file
         outside the folder.
    Returns (safe_name, None) or (None, error_message).
    """
    if storage is None or not storage.filename:
        return None, "bad_request"
    if not allowed_file(storage.filename):
        return None, "disallowed_file"
    safe_name = secure_filename(storage.filename)
    if not safe_name:
        return None, "bad_request"
    return safe_name, None


def set_title(path, title):
    metadata, status = read_json_file_data(path, "metadata")
    if status != "OK":
        return status
    metadata["title"] = title
    return update_json_file_data(metadata, path, "metadata")


def rename_label(path, old_label, new_label):
    """Rename a probe's display label across graph_labels.json AND
    graph_data.json.

    Ported from blueprints/cookfile/routes.py's _rename_graph_label (:499-554)
    rather than imported, because that helper answers "already exists" and
    "read failed" with the same jsonify({"result": "ERROR"}) -- the client
    cannot tell a user mistake from a corrupt file. Here the two are distinct
    return values so the route can answer 409 vs 422. The collision check is
    also a separate pass: the original renames and checks in ONE loop, so by
    the time it discovers the collision it has already mutated the dict it is
    about to throw away.

    Returns (safe_label, None) or (None, "label_exists" | <read/write status>).
    """
    labels, status = read_json_file_data(path, "graph_labels")
    if status != "OK":
        return None, status

    safe = create_safe_name(new_label)
    for category in labels:
        if safe in labels[category]:
            return None, "label_exists"

    for category in labels:
        if old_label in labels[category]:
            labels[category].pop(old_label)
            labels[category][safe] = new_label

    status = update_json_file_data(labels, path, "graph_labels")
    if status != "OK":
        return None, status

    graph, status = read_json_file_data(path, "graph_data")
    if status != "OK":
        return None, status
    for category in graph["probe_mapper"]:
        mapper = graph["probe_mapper"][category]
        if old_label not in mapper:
            continue
        mapper[safe] = mapper.pop(old_label)
        #  The three series a probe contributes get three different suffixes
        #  (file_mgmt/cookfile.py:372, :388) and the chart label must keep them.
        addendum = {"targets": " Target", "primarysp": " Set Point"}.get(category, "")
        graph["chart_data"][mapper[safe]]["label"] = new_label + addendum

    status = update_json_file_data(graph, path, "graph_data")
    if status != "OK":
        return None, status
    return safe, None


def recover(path, action):
    """upgrade | repair. `repair` is upgrade(repair=True) followed by
    fixup_assets, matching blueprints/cookfile/routes.py:271-303."""
    struct, status = upgrade_cookfile(path, repair=(action == "repair"))
    if status != "OK":
        return status
    if action == "repair":
        struct, status = read_cookfile(path)
        if status != "OK":
            return status
        struct, status = fixup_assets(path, struct)
    return status


def _load_comments(path):
    """comments.json, or a status. A corrupt archive reads back as {}, and the
    legacy branch appends to it -- AttributeError, HTTP 500 -- so the isinstance
    check is the fix, not a formality."""
    comments, status = read_json_file_data(path, "comments")
    if status != "OK":
        return None, status
    if not isinstance(comments, list):
        return None, "Error: comments unreadable."
    return comments, None


def add_comment(path, text):
    comments, problem = _load_comments(path)
    if problem:
        return None, problem
    now = datetime.datetime.now()
    entry = {
        "text": text,
        "id": generate_uuid(),
        "edited": "",
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "assets": [],
    }
    comments.append(entry)
    status = update_json_file_data(comments, path, "comments")
    return (entry, None) if status == "OK" else (None, status)


def update_comment(path, comment_id, text):
    comments, problem = _load_comments(path)
    if problem:
        return None, problem
    for entry in comments:
        if entry["id"] != comment_id:
            continue
        entry["text"] = text
        entry["edited"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        status = update_json_file_data(comments, path, "comments")
        return (entry, None) if status == "OK" else (None, status)
    return None, "comment_not_found"


def delete_comment(path, comment_id):
    comments, problem = _load_comments(path)
    if problem:
        return problem
    for entry in comments:
        if entry["id"] == comment_id:
            comments.remove(entry)
            return update_json_file_data(comments, path, "comments")
    return "comment_not_found"


def set_comment_assets(path, comment_id, assets):
    """Replace a comment's asset list wholesale.

    Flask toggles ONE asset and infers add-vs-remove from a client-supplied
    `state` string (routes.py:566-586): if the client's view of "selected" is
    stale, the toggle does the OPPOSITE of what the user clicked. A whole-list
    write states the intent and cannot invert.
    """
    comments, problem = _load_comments(path)
    if problem:
        return None, problem
    for entry in comments:
        if entry["id"] == comment_id:
            entry["assets"] = list(assets)
            status = update_json_file_data(comments, path, "comments")
            return (entry["assets"], None) if status == "OK" else (None, status)
    return None, "comment_not_found"
