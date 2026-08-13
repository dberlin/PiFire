"""
Common PiFire WebApp Functions Shared Between Blueprints
"""

from common.common import (
    seconds_to_string,
    epoch_to_time,
    guard_none_metric_field,
    # Re-exported, not redefined: this module used to carry a byte-identical
    # second copy of get_system_command_output, so a fix to one silently left
    # the other (used by blueprints/mobile/socket_io.py) broken.
    get_system_command_output,
)
from common.modes import Mode
from common.control_delta import control_delta
from common.persistence.control import (
    enqueue_control_delta,
)
from common.persistence.runtime import (
    read_settings,
    write_settings,
)
from common.persistence.history import (
    read_all_metrics,
    read_history,
)
from common.defaults import metrics_items
from common.api_commands import process_command
from flask import current_app, render_template
from common.sqlite_queue import SqliteQueue
import time
import json
import datetime
import os

# Reported when the control process does not answer a `check_alive` probe
# within get_system_command_output()'s timeout. Both web-tier consumers put it
# in front of the user -- blueprints/dash/routes.py::dash_page renders it into
# the Jinja page's banner, blueprints/mobile/socket_io.py::_get_dash_data puts
# it in the socket_dash_data payload -- and neither PERSISTS it: liveness is an
# observation about right now, not one of the durable failures the control
# process records in the errors blob (see _check_control_status).
#
# It lives here, in one place, because the React dashboard identifies the
# condition by matching a substring of it (web-react/src/helpers/dashboard/
# health.ts::deriveControlAlive). Two copies of a string a client parses is one
# copy too many.
CONTROL_DOWN_ERROR = (
    "The control process did not respond to a request and may be stopped.  "
    "Try reloading the page or restarting the system.  Check logs for details."
)


def allowed_file(filename):
    ALLOWED_EXTENSIONS = current_app.config["ALLOWED_EXTENSIONS"]
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_supported_cmds():
    process_command(action="sys", arglist=["supported_commands"], origin="admin")  # Request supported commands
    data = get_system_command_output(requested="supported_commands")
    if data["result"] != "ERROR":
        return data["data"]["supported_cmds"]
    else:
        return data


def create_ui_hash(settings=None):
    if settings is None:
        settings = read_settings()
    return hash(json.dumps(settings["probe_settings"]["probe_map"]["probe_info"]))


def paginate_list(datalist, sortkey="", reversesortorder=False, itemsperpage=10, page=1):
    if sortkey != "":
        #  Sort list if key is specified
        tempdatalist = sorted(datalist, key=lambda d: d[sortkey], reverse=reversesortorder)
    else:
        #  If no key, reverse list if specified, or keep order
        if reversesortorder:
            datalist.reverse()
        tempdatalist = datalist.copy()
    listlength = len(tempdatalist)
    if listlength <= itemsperpage:
        curpage = 1
        prevpage = 1
        nextpage = 1
        lastpage = 1
        displaydata = tempdatalist.copy()
    else:
        lastpage = (listlength // itemsperpage) + ((listlength % itemsperpage) > 0)
        if lastpage < page:
            curpage = lastpage
            prevpage = curpage - 1 if curpage > 1 else 1
            nextpage = curpage + 1 if curpage < lastpage else lastpage
        else:
            curpage = page if page > 0 else 1
            prevpage = curpage - 1 if curpage > 1 else 1
            nextpage = curpage + 1 if curpage < lastpage else lastpage
        #  Calculate starting / ending position and create list with that data
        start = itemsperpage * (curpage - 1)  # Get starting position
        end = start + itemsperpage  # Get ending position
        displaydata = tempdatalist.copy()[start:end]

    reverse = "true" if reversesortorder else "false"

    pagination = {
        "displaydata": displaydata,
        "curpage": curpage,
        "prevpage": prevpage,
        "nextpage": nextpage,
        "lastpage": lastpage,
        "reverse": reverse,
        "itemspage": itemsperpage,
    }

    return pagination


def prepare_annotations(displayed_starttime, metrics_data=[]):
    if metrics_data == []:
        metrics_data = read_all_metrics()
    annotation_json = {}
    # Process Additional Metrics Information for Display
    for index in range(0, len(metrics_data)):
        # Guard against a poisoned row (None starttime) the same way
        # process_metrics does -- update_metrics' "amend last record" path can
        # leave a row with a None starttime, which crashes the `>` comparison
        # below (TypeError: unsupported between NoneType and int/float).
        starttime = guard_none_metric_field(metrics_data, index, "starttime", "prepare_annotations")
        # Check if metric falls in the displayed time window
        if starttime > displayed_starttime:
            # Convert Start Time
            # starttime = epoch_to_time(metrics_data[index]['starttime']/1000)
            mode = metrics_data[index]["mode"]
            color = "blue"
            if mode == Mode.STARTUP:
                color = "green"
            elif mode == Mode.STOP:
                color = "red"
            elif mode == Mode.SHUTDOWN:
                color = "black"
            elif mode == Mode.REIGNITE:
                color = "orange"
            elif mode == Mode.ERROR:
                color = "red"
            elif mode == Mode.HOLD:
                color = "blue"
            elif mode == Mode.SMOKE:
                color = "grey"
            elif mode in [Mode.MONITOR, Mode.MANUAL]:
                color = "purple"
            annotation = {
                "type": "line",
                "xMin": metrics_data[index]["starttime"],
                "xMax": metrics_data[index]["starttime"],
                "borderColor": color,
                "borderWidth": 2,
                "label": {
                    "backgroundColor": color,
                    "borderColor": "black",
                    "color": "white",
                    "content": mode,
                    "enabled": True,
                    "position": "end",
                    "rotation": 0,
                },
                "display": True,
            }
            annotation_json[f"event_{index}"] = annotation

    return annotation_json


def prepare_event_totals(events):
    settings = read_settings()
    auger_time = 0
    for index in range(0, len(events)):
        auger_time += events[index]["augerontime"]
    auger_time = int(auger_time)

    event_totals = {}
    event_totals["augerontime"] = seconds_to_string(auger_time)

    grams = int(auger_time * settings["globals"]["augerrate"])
    pounds = round(grams * 0.00220462, 2)
    ounces = round(grams * 0.03527392, 2)
    event_totals["estusage_m"] = f"{grams} grams"
    event_totals["estusage_i"] = f"{pounds} pounds ({ounces} ounces)"

    seconds = int((events[-1]["starttime"] / 1000) - (events[0]["starttime"] / 1000))

    event_totals["cooktime"] = seconds_to_string(seconds)

    event_totals["pellet_level_start"] = events[0]["pellet_level_start"]
    event_totals["pellet_level_end"] = events[-2]["pellet_level_end"]

    return event_totals


def _export_temp_path(filename, suffix):
    """Compose the /tmp path a CSV export is written to.

    This used to be `filename.replace("./history/", "")` followed by
    `"/tmp/" + ...`. That `.replace` only matched the DEFAULT HISTORY_FOLDER
    literal, so under any other configured folder the full source path
    survived and produced a path under a directory that does not exist
    (`/tmp//srv/cooks/X.pifire-...csv`), which open() cannot create -- which
    is why the legacy dl_eventfile/dl_graphfile branches had never worked
    outside the default install. `filename` also arrives straight off a form
    field, and plain concatenation let `../..` escape /tmp altogether.
    os.path.basename() is correct for any folder and closes both.
    """
    stem = os.path.basename(filename.replace(".json", ""))
    return os.path.join("/tmp", stem + suffix)


def prepare_metrics_csv(metrics_data, filename):
    filename = _export_temp_path(filename, "-PiFire-Metrics-Export.csv")

    csvfile = open(filename, "w")

    list_length = len(metrics_data)  # Length of list

    if list_length > 0:
        # Build the header row
        writeline = ""
        for item in range(0, len(metrics_items)):
            writeline += f"{metrics_items[item][0]}, "
        writeline += "\n"
        csvfile.write(writeline)
        for index in range(0, list_length):
            writeline = ""
            for item in range(0, len(metrics_items)):
                writeline += f"{metrics_data[index][metrics_items[item][0]]}, "
            writeline += "\n"
            csvfile.write(writeline)
    else:
        writeline = "No Data\n"
        csvfile.write(writeline)

    csvfile.close()
    return filename


def prepare_csv(data=[], filename=""):
    # Create filename if no name specified
    if filename == "":
        now = datetime.datetime.now()
        exportfilename = _export_temp_path(now.strftime("%Y%m%d-%H%M"), "-PiFire-Export.csv")
    else:
        exportfilename = _export_temp_path(filename, "-Pifire-Export.csv")

    # Open CSV File for editing
    csvfile = open(exportfilename, "w")

    if data == []:
        data = read_history()

    # Get the length of the data (number of captured events)
    list_length = len(data)

    if list_length > 0:
        exd_data = True if "EXD" in data[0].keys() else False

        # Set Standard Labels
        labels = "Time, "
        primary_key = list(data[0]["P"].keys())[0]
        labels += f"{primary_key} Temp, {primary_key} Set Point, {primary_key} Notify Target"
        for key in data[0]["F"]:
            labels += f", {key} Temp, {key} Notify Target"
        for key in data[0]["AUX"]:
            labels += f", {key} Temp"
        if exd_data:
            for key in data[0]["EXD"]:
                labels += f", {key}"

        # End the labels line
        labels += "\n"

        writeline = labels
        csvfile.write(writeline)

        for index in range(0, list_length):
            converted_dt = datetime.datetime.fromtimestamp(int(data[index]["T"]) / 1000)
            timestr = converted_dt.strftime("%Y-%m-%d %H:%M:%S")
            writeline = (
                f"{timestr}, {data[index]['P'][primary_key]}, {data[index]['PSP']}, {data[index]['NT'][primary_key]}"
            )
            for key in data[index]["F"]:
                writeline += f", {data[index]['F'][key]}, {data[index]['NT'][key]}"
            for key in data[index]["AUX"]:
                writeline += f", {data[index]['AUX'][key]}"
            # Add any additional data if keys exist
            if exd_data:
                for key in data[index]["EXD"]:
                    writeline += f", {data[index]['EXD'][key]}"
            # Write line to file
            csvfile.write(writeline + "\n")
    else:
        writeline = "No Data\n"
        csvfile.write(writeline)

    csvfile.close()

    return exportfilename


def render_cookfile_page(cookfilestruct, settings, cookfilename, filenameonly, errors):
    """
    Shared cook-file page renderer. Reshapes a freshly-read `cookfilestruct`
    (mutating it in place: `\\n`->`<br>` on comment text, epoch->display-time on
    metadata start/end) and returns the `cookfile/index.html` render Response.

    Extracted from six byte-identical copies of the
    reshape+render block: `blueprints/cookfile/routes.py` (thumbSelected,
    ulmedia/ulthumb, repairCF, upgradeCF, delmedialist) and
    `blueprints/history/routes.py` (opencookfile). The six copies differed only
    in the already-computed `cookfilename`/`filenameonly` expressions fed in as
    kwargs, so both are parameters here.
    """
    events = cookfilestruct["events"]
    event_totals = prepare_event_totals(events)
    comments = cookfilestruct["comments"]
    for comment in comments:
        comment["text"] = comment["text"].replace("\n", "<br>")
    metadata = cookfilestruct["metadata"]
    metadata["starttime"] = epoch_to_time(metadata["starttime"] / 1000)
    metadata["endtime"] = epoch_to_time(metadata["endtime"] / 1000)
    labels = cookfilestruct["graph_labels"]
    assets = cookfilestruct["assets"]

    return render_template(
        "cookfile/index.html",
        settings=settings,
        cookfilename=cookfilename,
        filenameonly=filenameonly,
        events=events,
        event_totals=event_totals,
        comments=comments,
        metadata=metadata,
        labels=labels,
        assets=assets,
        errors=errors,
    )


def classify_cookfile_error(status):
    """
    Shared cook-file error classifier. Extracted from
    five byte-identical copies of the `errortype` if/elif/else in
    `blueprints/cookfile/routes.py` (repairCF x2, upgradeCF, delmedialist) and
    `blueprints/history/routes.py` (opencookfile). Returns the errortype string
    used by the `cferror.html` templates.
    """
    if "version" in status:
        return "version"
    elif "asset" in status:
        return "asset"
    else:
        return "other"


def create_safe_name(name):
    return "".join([x for x in name if x.isalnum()])


def update_probe_config(settings, control, probe_dto):
    """
    Shared probe-config-update helper for `settings_page`'s `probe_config_save`
    action and socket_io's `_update_probe_config`.

    `probe_dto` is a normalized dict: `label` plus any of `name`/`type`/`port`/
    `device`/`enabled`/`profile_id`, already resolved by the caller (e.g. the
    `enabled` coercion differs per caller and must be resolved BEFORE calling
    this helper). Mutates and returns `settings`/`control` (sets
    `control["probe_profile_update"] = True` on success); does not write
    anything to disk and does not build a response envelope - both remain the
    caller's responsibility.

    Returns (settings, control, result) where result is "success" or
    "label_not_found".
    """
    label = probe_dto.get("label", "")
    probe_edited = {}

    for index, probe in enumerate(settings["probe_settings"]["probe_map"]["probe_info"]):
        if probe["label"] == label:
            probe_edited["label"] = probe["label"]
            probe_edited["name"] = probe_dto.get(
                "name", settings["probe_settings"]["probe_map"]["probe_info"][index]["name"]
            )
            probe_edited["type"] = probe_dto.get(
                "type", settings["probe_settings"]["probe_map"]["probe_info"][index]["type"]
            )
            probe_edited["port"] = probe_dto.get(
                "port", settings["probe_settings"]["probe_map"]["probe_info"][index]["port"]
            )
            probe_edited["device"] = probe_dto.get(
                "device", settings["probe_settings"]["probe_map"]["probe_info"][index]["device"]
            )
            probe_edited["enabled"] = probe_dto.get(
                "enabled", settings["probe_settings"]["probe_map"]["probe_info"][index]["enabled"]
            )
            profile_id = probe_dto.get(
                "profile_id", settings["probe_settings"]["probe_map"]["probe_info"][index]["profile"]["id"]
            )
            if profile_id != probe["profile"]["id"]:
                probe_edited["profile"] = settings["probe_settings"]["probe_profiles"].get(
                    profile_id, settings["probe_settings"]["probe_map"]["probe_info"][index]["profile"]
                )
            else:
                probe_edited["profile"] = settings["probe_settings"]["probe_map"]["probe_info"][index]["profile"]
            break

    if probe_edited:
        settings["probe_settings"]["probe_map"]["probe_info"][index] = probe_edited
        settings["history_page"]["probe_config"][label]["name"] = probe_edited["name"]
        control["probe_profile_update"] = True
        return settings, control, "success"
    else:
        return settings, control, "label_not_found"


def save_settings_and_flag_update(settings, control, *flags, origin="app", ops=None):
    """
    Shared "write settings + set one-or-more control update-flags + queue a
    control write" helper for the repeated persistence tail used by several
    settings/admin/socket_io actions.

    Writes `settings` to disk, sets `control[flag] = True` for each flag name in
    `flags`, then queues a delta naming ONLY those flags. Mutates `control` in
    place.

    These are one-shot REQUEST flags: the control loop acts on them and clears
    them. Under the old whole-dict write, a concurrent writer's stale snapshot
    could set one back to False before the loop ever saw it -- the settings were
    saved but the reload never happened, so the user's change appeared to do
    nothing at all. A named flag cannot be reverted by a writer that never
    mentions it.

    `control` remains a parameter, and is still mutated in place, even though
    the delta no longer needs it: nine call sites pass it and may observe that
    mutation, and two of those files are shared with other in-flight work.
    Removing it would be a rename with no behavioural benefit.

    `ops` rides along in the SAME envelope for the callers whose settings write
    also has to say something about `timer`/`notify_data`, which
    common/control_delta.py:34 forbids under `set` (both need addressing, not
    assignment). One envelope rather than two writes, so the flags and the ops
    cannot be drained a batch apart.
    """
    write_settings(settings)
    for flag in flags:
        control[flag] = True
    enqueue_control_delta(control_delta(set_values={flag: True for flag in flags}, ops=ops), origin=origin)


def api_response(result, message=None, data=None):
    """
    Shared Socket.IO response envelope. Relocated from
    blueprints/mobile/socket_io.py's `_response` so it can
    be shared by any future Socket.IO consumer.

    Returns a bare dict (no jsonify/status) with the same key order as the
    original: {"data", "result", "message"}.
    """
    return {"data": data, "result": result, "message": message}
