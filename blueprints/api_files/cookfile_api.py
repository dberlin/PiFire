"""Cook-file endpoint handlers for /api/files/cookfiles/*.

Every handler here takes a BARE FILENAME resolved by routes.require_file. None
of them ever accepts a path, which is the single behavioural difference from
blueprints/cookfile/routes.py.
"""

import copy

from common.app import classify_cookfile_error, prepare_annotations, prepare_event_totals
from common.common import epoch_to_time
from file_mgmt.cookfile import read_cookfile


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
