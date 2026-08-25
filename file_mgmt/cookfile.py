"""
PiFire - File / Cookfile Functions
==================================

This file contains functions for file managing the coofile file format.

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

from common.common import (
    create_logger,
    generate_uuid,
    log_path,
    process_metrics,
    semantic_ver_is_lower,
    semantic_ver_to_list,
    unpack_history,
)
from common.cook_diagnostics import LearningReportProvider, collect_cook_learning_diagnostics
from common.defaults import default_probe_config
from common.persistence.history import (
    flush_history,
    read_all_metrics,
    read_history,
)
from common.persistence.runtime import (
    read_settings,
)
from file_mgmt.common import read_json_file_data, read_optional_json_file_data, update_json_file_data
from file_mgmt.downsample import select_indices

HISTORY_FOLDER = "./history/"  # Path to historical cook files

"""
Functions
=========
"""


def _default_cookfilestruct():
    settings = read_settings()

    cookfilestruct = {}

    cookfilestruct["metadata"] = {
        "title": "",
        "starttime": "",
        "endtime": "",
        "units": settings["globals"]["units"],
        "thumbnail": "",  # UUID of the thumbnail for this cook file - found in assets
        "id": generate_uuid(),
        "version": settings["versions"]["cookfile"],  #  PiFire Cook File Version
    }

    cookfilestruct["graph_data"] = {}

    cookfilestruct["raw_data"] = []

    cookfilestruct["graph_labels"] = {}

    cookfilestruct["events"] = []

    cookfilestruct["comments"] = []

    cookfilestruct["assets"] = []

    cookfilestruct["learning_diagnostics"] = None

    return cookfilestruct


def create_cookfile(*, cook_id: str | None, learning_report_provider: LearningReportProvider) -> None:
    """
    This function gathers all of the data from the previous cook
    from startup to stop mode, and saves this to a Cook File stored
    at ./history/

    The metrics and cook data are purged from memory, after stop mode is initiated.
    """
    # global cmdsts
    global HISTORY_FOLDER

    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s"
    )

    settings = read_settings()

    cook_file_struct = {}

    now = datetime.datetime.now()
    nowstring = now.strftime("%Y-%m-%d--%H%M")
    title = nowstring + "-CookFile"

    chart_data = prepare_chartdata(settings["history_page"]["probe_config"], num_items=0, reduce=False, data_points=0)
    raw_data = read_history()

    if len(chart_data["time_labels"]):
        starttime = chart_data["time_labels"][0]

        endtime = chart_data["time_labels"][-1]

        cook_file_struct = _default_cookfilestruct()

        cook_file_struct["metadata"]["title"] = title
        cook_file_struct["metadata"]["starttime"] = starttime
        cook_file_struct["metadata"]["endtime"] = endtime

        cook_file_struct["graph_data"] = {
            "time_labels": chart_data["time_labels"],
            "chart_data": chart_data["chart_data"],
            "probe_mapper": chart_data["probe_mapper"],
        }

        cook_file_struct["graph_labels"] = chart_data["graph_labels"]

        cook_file_struct["raw_data"] = raw_data

        metrics_rows = read_all_metrics()
        learning_diagnostics = collect_cook_learning_diagnostics(
            cook_id,
            learning_report_provider,
            warn=eventLogger.warning,
        )
        cook_file_struct["events"] = process_metrics(metrics_rows, augerrate=settings["globals"]["augerrate"])
        cook_file_struct["learning_diagnostics"] = learning_diagnostics.model_dump(mode="json")

        # 1. Create all JSON data files
        files_list = [
            "metadata",
            "graph_data",
            "raw_data",
            "graph_labels",
            "events",
            "comments",
            "assets",
            "learning_diagnostics",
        ]
        if not os.path.exists(HISTORY_FOLDER):
            os.mkdir(HISTORY_FOLDER)
        cook_file_path = f"{HISTORY_FOLDER}{title}"
        cook_file_name = f"{cook_file_path}.pifire"
        cook_file_duplicate = 0
        while os.path.exists(cook_file_name):
            # If file path exists, attempt to add a new path
            cook_file_duplicate += 1
            eventLogger.debug(
                f"{cook_file_name} exists, attempting to use {cook_file_path}-{cook_file_duplicate}.pifire"
            )
            cook_file_name = f"{cook_file_path}-{cook_file_duplicate}.pifire"

        os.mkdir(cook_file_path)  # Make temporary folder for all files
        for item in files_list:
            json_data_string = json.dumps(cook_file_struct[item], indent=2, sort_keys=True)
            filename = f"{cook_file_path}/{item}.json"
            with open(filename, "w+") as cook_file:
                cook_file.write(json_data_string)

        # 2. Create empty data folder(s) & add default data
        os.mkdir(f"{cook_file_path}/assets")
        os.mkdir(f"{cook_file_path}/assets/thumbs")
        # shutil.copy2('./static/img/pifire-cf-thumb.png', f'{HISTORY_FOLDER}{title}/assets/{thumbnail_UUID}.png')
        # shutil.copy2('./static/img/pifire-cf-thumb.png', f'{HISTORY_FOLDER}{title}/assets/thumbs/{thumbnail_UUID}.png')

        # 3. Create ZIP file of the folder
        directory = pathlib.Path(f"{cook_file_path}/")
        filename = cook_file_name

        with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as archive:
            for file_path in directory.rglob("*"):
                archive.write(file_path, arcname=file_path.relative_to(directory))

        eventLogger.debug(f"Wrote {cook_file_name} to {HISTORY_FOLDER}.")

        # 4. Cleanup temporary files
        shutil.rmtree(cook_file_path, ignore_errors=True)

    # Erase history, current and metrics now the cook is saved to a file.
    # (The separate flush_metrics() that used to follow this line was
    # redundant -- flush_history does it -- but that was invisible while the
    # call was spelled read_history(flushhistory=True).)
    flush_history()


def read_cookfile(filename):
    """
    Read FULL Cook File into Python Dictionary
    """
    settings = read_settings()

    cook_file_struct = {}
    status = "OK"
    json_types = ["metadata", "graph_data", "raw_data", "graph_labels", "events", "comments", "assets"]
    for jsonfile in json_types:
        cook_file_struct[jsonfile], status = read_json_file_data(filename, jsonfile)
        if status != "OK":
            break  # Exit loop and function, error string in status
        # settings["versions"]["cookfile"] is the MINIMUM file version this
        # build can load. The comparison used to test major/minor/patch
        # INDEPENDENTLY (`file[0] >= min[0] and file[1] >= min[1] and
        # file[2] >= min[2]`), which is not how semantic versions order:
        # against the shipped minimum of 1.5.0 a file written as 2.4.0
        # failed the `4 >= 5` term and was reported as an OLDER format, so
        # a file from a NEWER PiFire was routed to the repair/upgrade
        # prompt that would rewrite it backwards. semantic_ver_is_lower()
        # is the correct lexicographic comparison and was already in tree.
        if jsonfile == "metadata" and semantic_ver_is_lower(
            cook_file_struct["metadata"]["version"], settings["versions"]["cookfile"]
        ):
            status = "WARNING: Older cookfile version format! "
            break  # Exit loop and function, error string in status

    if status == "OK":
        cook_file_struct["learning_diagnostics"], status = read_optional_json_file_data(
            filename,
            "learning_diagnostics",
        )

    return (cook_file_struct, status)


def upgrade_cookfile(cookfilename, repair=False):
    settings = read_settings()

    status = "OK"
    cookfilestruct = _default_cookfilestruct()
    current_version = [0, 0, 0]

    json_types = ["metadata", "raw_data", "graph_data", "graph_labels", "events", "comments", "assets"]
    for jsonfile in json_types:
        jsondata, status = read_json_file_data(cookfilename, jsonfile, unpackassets=False)
        if status != "OK" and jsonfile == "raw_data":
            cookfilestruct["raw_data"] = []
            graph_data, status = read_json_file_data(cookfilename, "graph_data", unpackassets=False)
            list_length = len(graph_data["time_labels"])
            jsondata = []
            # Build out Raw Data Set
            for index in range(list_length):
                list_item = {
                    "T": graph_data["time_labels"][index],
                    "P": {"grill1": graph_data["grill1_temp"][index]},
                    "PSP": graph_data["grill1_setpoint"][index],
                    "F": {"probe1": graph_data["probe1_temp"][index], "probe2": graph_data["probe2_temp"][index]},
                    "NT": {
                        "grill1": graph_data["grill1_setpoint"][index],
                        "probe1": graph_data["probe1_setpoint"][index],
                        "probe2": graph_data["probe2_setpoint"][index],
                    },
                    "AUX": {},
                }
                jsondata.append(list_item)
            cookfilestruct[jsonfile] = jsondata
        elif status != "OK":
            break  # Exit loop and function, error string in status
        elif jsonfile == "metadata":
            # Update to the latest cookfile version
            current_version = semantic_ver_to_list(jsondata["version"])
            jsondata["version"] = settings["versions"]["cookfile"]
            cookfilestruct[jsonfile].update(jsondata)
        elif jsonfile == "comments":
            # Add assets list to each comment v1.0 -> v1.0.1+
            for index, comment in enumerate(jsondata):
                if not "assets" in comment:
                    jsondata[index]["assets"] = []
            cookfilestruct[jsonfile] = jsondata
        elif jsonfile == "assets" and jsondata == {}:
            # Some version 1.0 files may have an empty assets file with a dictionary instead of a list
            cookfilestruct[jsonfile] = []
        elif jsonfile == "graph_labels":
            # Convert prior to v1.5.0 versions of cookfile to new graph label format
            if current_version[0] <= 1 and current_version[1] < 5:
                cookfilestruct[jsonfile] = {
                    "primarysp": {"grill1": jsondata["grill1_label"] + " Set Point"},
                    "probes": {
                        "grill1": jsondata["grill1_label"],
                        "probe1": jsondata["probe1_label"],
                        "probe2": jsondata["probe2_label"],
                    },
                    "targets": {
                        "grill1": jsondata["grill1_label"] + " Target",
                        "probe1": jsondata["probe1_label"] + " Target",
                        "probe2": jsondata["probe2_label"] + " Target",
                    },
                }
            else:
                cookfilestruct[jsonfile] = jsondata
        elif jsonfile == "graph_data":
            # Convert prior to v1.5.0 versions of cookfile to new graph label format
            if current_version[0] <= 1 and current_version[1] < 5:
                probe_info = {
                    "probe_settings": {
                        "probe_map": {
                            "probe_info": [
                                {"name": "Grill", "label": "grill1", "type": "Primary", "enabled": True},
                                {"name": "Probe 1", "label": "probe1", "type": "Food", "enabled": True},
                                {"name": "Probe 2", "label": "probe2", "type": "Food", "enabled": True},
                            ]
                        }
                    },
                    # default_probe_config() unconditionally reads
                    # settings["history_page"]["probe_config"] to check for
                    # pre-existing per-probe color/config entries to reuse. This
                    # ad-hoc conversion dict has none (it's not real settings),
                    # so an empty dict here just means "build fresh defaults for
                    # every probe" -- required key, not optional.
                    "history_page": {"probe_config": {}},
                }
                probe_config = default_probe_config(probe_info)
                history = {
                    "T": jsondata["time_labels"],
                    "PSP": jsondata["grill1_setpoint"],
                    "P": {"grill1": jsondata["grill1_temp"]},
                    "F": {"probe1": jsondata["probe1_temp"], "probe2": jsondata["probe2_temp"]},
                    "NT": {
                        "grill1": jsondata["grill1_setpoint"],
                        "probe1": jsondata["probe1_setpoint"],
                        "probe2": jsondata["probe2_setpoint"],
                    },
                }
                cookfilestruct[jsonfile] = prepare_chartdata(probe_config, num_items=0, reduce=False, history=history)
            else:
                cookfilestruct[jsonfile] = jsondata
        else:
            cookfilestruct[jsonfile] = jsondata
        # Update the original file with new data
        update_json_file_data(cookfilestruct[jsonfile], cookfilename, jsonfile)

    return (cookfilestruct, status)


#: Which y-axis a dataset belongs to. Temperatures share the chart's original
#: axis; duty is a 0-100% control signal that cannot share a scale with a
#: 225-degree trace without being pinned flat to the floor.
#:
#: Stamped onto every dataset rather than left to a model default: the wire
#: payload is serialized with `exclude_unset=True`, so a field the producer
#: never set is dropped even when the model declares a default for it.
TEMP_AXIS = "temp"
DUTY_AXIS = "duty"

#: Duty series, in the order they are appended after the probe datasets.
#: Each entry is (history key, label, line colour, scale to percent).
#:
#: Colours are fixed here rather than read from probe_config -- duty is not a
#: probe and has no per-probe configuration -- and are chosen to read as what
#: they drive: ember for the auger (fuel), ice for the fan (air), amber for the
#: request the auger did not get. They mirror --color-accent-ember /
#: --color-accent-ice / --color-warn in web-react/src/theme.css. A canvas
#: stroke cannot read a CSS custom property, so the value travels with the data
#: exactly as every probe's line_color already does.
#:
#: CR (commanded cycle ratio) and RCR (realized cycle ratio -- what actually
#: reached the auger) are stored as a 0.0-1.0 ratio and converted to percent
#: HERE, so all three series arrive in one unit against one axis and no client
#: has to know which of them needed scaling.
#:
#: "Auger Delivered" is deliberately not called a duty: the point of drawing it
#: beside "Auger Duty" is the GAP between them, which is where a clamp acted --
#: the duty floor lifting a request too small to pulse, u_max capping one too
#: large, a lid-open pause pinning the auger off. Amber rather than ember so it
#: reads as a qualifier on the auger line rather than as a second one.
_DUTY_SERIES = (
    ("CR", "Auger Duty", "#ff8a2b", 100.0),
    ("RCR", "Auger Delivered", "#ffb020", 100.0),
    ("FD", "Fan Duty", "#3cc7d0", 1.0),
)

#: The same scales, keyed for the per-sample lookup in the row loop.
_DUTY_SCALES = {key: scale for key, _label, _color, scale in _DUTY_SERIES}


def _duty_change_indices(history, window_start, list_length):
    """Absolute indices where any duty series changes value, plus the endpoints.

    Under stepped rendering these are the only samples that carry information:
    every other one repeats the value already being drawn. Endpoints are
    included so a window that opens or closes mid-plateau still starts and ends
    at the right height.

    None participates as a value of its own, so the boundaries of a gap survive
    -- the transition from "recorded" to "not recorded" is exactly where the
    line has to stop.
    """
    changes = set()
    for source_key in _DUTY_SCALES:
        values = history.get(source_key) or []
        if not values:
            continue
        window = values[window_start:list_length]
        if not window:
            continue
        changes.add(window_start)
        changes.add(window_start + len(window) - 1)
        changes.update(
            window_start + offset for offset in range(1, len(window)) if window[offset] != window[offset - 1]
        )
    return changes


def prepare_chartdata(
    probe_config,
    chart_info=None,
    num_items=10,
    reduce=True,
    data_points=10000,
    history=None,
    tolerance=2.0,
    max_points=None,
):
    """Build Probe Mapper and Chart Data Struct"""
    chart_info = {} if chart_info is None else chart_info
    chart_data = []

    if chart_info == {}:
        chart_info = {
            "label": "",
            "fill": False,
            "lineTension": 0.1,
            "backgroundColor": "",
            "borderColor": "",
            "borderCapStyle": "butt",
            "borderDash": [],
            "borderDashOffset": 0.0,
            "borderJoinStyle": "miter",
            "pointBorderColor": "",
            "pointBackgroundColor": "#fff",
            "pointBorderWidth": 1,
            "pointHoverRadius": 10,
            "pointHoverBackgroundColor": "",
            "pointHoverBorderColor": "",
            "pointHoverBorderWidth": 2,
            "pointRadius": 1,
            "pointHitRadius": 10,
            "pointStyle": "line",
            "data": [],
            "spanGaps": False,
            "hidden": False,
            "axis": TEMP_AXIS,
        }

    index = 0
    probe_mapper = {"probes": {}, "targets": {}, "primarysp": {}}
    graph_labels = {"probes": {}, "targets": {}, "primarysp": {}}

    for probe in probe_config:
        """ First Object is Temperature Data for Probe """
        chart_obj = chart_info.copy()
        chart_obj["label"] = probe_config[probe]["name"]
        chart_obj["backgroundColor"] = probe_config[probe]["bg_color"]
        chart_obj["borderColor"] = probe_config[probe]["line_color"]
        chart_obj["borderDash"] = []
        chart_obj["pointBorderColor"] = probe_config[probe]["line_color"]
        chart_obj["pointHoverBackgroundColor"] = probe_config[probe]["bg_color"]
        chart_obj["pointHoverBorderColor"] = probe_config[probe]["line_color"]
        chart_obj["hidden"] = not probe_config[probe]["enabled"]
        chart_obj["data"] = []
        chart_data.append(chart_obj)
        probe_mapper["probes"][probe] = index
        graph_labels["probes"][probe] = probe_config[probe]["name"]
        """ Second Object is the Target Temperature Data for Probe """
        index += 1
        chart_obj = chart_info.copy()
        chart_obj["label"] = probe_config[probe]["name"] + " Target"
        chart_obj["backgroundColor"] = probe_config[probe]["bg_color_target"]
        chart_obj["borderColor"] = probe_config[probe]["line_color_target"]
        chart_obj["borderDash"] = [8, 4]
        chart_obj["pointBorderColor"] = probe_config[probe]["line_color_target"]
        chart_obj["pointHoverBackgroundColor"] = probe_config[probe]["bg_color_target"]
        chart_obj["pointHoverBorderColor"] = probe_config[probe]["line_color_target"]
        chart_obj["hidden"] = not probe_config[probe]["enabled"]
        chart_obj["data"] = []
        chart_data.append(chart_obj)
        probe_mapper["targets"][probe] = index
        graph_labels["targets"][probe] = probe_config[probe]["name"] + " Target"
        """ Third Object is the Primary Setpoint Temperature Data for Probe (if it is primary) """
        if probe_config[probe]["type"] == "Primary":
            index += 1
            chart_obj = chart_info.copy()
            chart_obj["label"] = probe_config[probe]["name"] + " Set Point"
            chart_obj["backgroundColor"] = probe_config[probe]["bg_color_setpoint"]
            chart_obj["borderColor"] = probe_config[probe]["line_color_setpoint"]
            chart_obj["borderDash"] = [8, 4]
            chart_obj["pointBorderColor"] = probe_config[probe]["line_color_setpoint"]
            chart_obj["pointHoverBackgroundColor"] = probe_config[probe]["bg_color_setpoint"]
            chart_obj["pointHoverBorderColor"] = probe_config[probe]["line_color_setpoint"]
            chart_obj["hidden"] = not probe_config[probe]["enabled"]
            chart_obj["data"] = []
            chart_data.append(chart_obj)
            probe_mapper["primarysp"][probe] = index
            graph_labels["primarysp"][probe] = probe_config[probe]["name"] + " Set Point"
        """ Increment Index """
        index += 1

    """ Populate history data into chart data """
    if history == None:
        history = read_history(num_items)
        if history != []:
            history = unpack_history(history)
            list_length = len(history["T"])  # Length of list(s)
        else:
            list_length = 0
    else:
        list_length = len(history["T"])  # Length of list(s)

    if (list_length < num_items) and (list_length > 0):
        num_items = list_length

    if num_items == 0:
        num_items = list_length

    # Duty datasets, appended AFTER every probe dataset so probe_mapper's
    # indices -- built above from the probe loop's own counter -- keep pointing
    # at the same slots they always have.
    #
    # Built here rather than up with the probe datasets because the decision
    # needs the data: a column that is entirely None has nothing to draw, and
    # offering an empty toggle for it is worse than not offering one. That is
    # the normal state for a cook recorded before duty existed (no key at all),
    # and for `RCR` until the Hold path reports the pre-clamp request.
    #
    # Gated on list_length because an empty read leaves `history` as the bare
    # `[]` read_history returned, never the unpacked dict -- there is nothing
    # to ask for a duty column on.
    duty_slots = []
    for source_key, label, color, _scale in _DUTY_SERIES if list_length > 0 else ():
        values = history.get(source_key) or []
        if not any(value is not None for value in values):
            continue
        chart_obj = chart_info.copy()
        chart_obj["label"] = label
        chart_obj["backgroundColor"] = color
        chart_obj["borderColor"] = color
        # Reassigned, not inherited: chart_info.copy() is shallow, so every
        # dataset would otherwise share one list object with the template.
        chart_obj["borderDash"] = []
        chart_obj["pointBorderColor"] = color
        chart_obj["pointHoverBackgroundColor"] = color
        chart_obj["pointHoverBorderColor"] = color
        chart_obj["axis"] = DUTY_AXIS
        # Off by default. Duty is a diagnostic overlay on a chart people open
        # to read temperatures, so it appears when asked for and the chart is
        # unchanged until then.
        chart_obj["hidden"] = True
        chart_obj["data"] = []
        chart_data.append(chart_obj)
        duty_slots.append((source_key, index))
        index += 1

    time_labels = []

    if list_length > 0:
        window_start = max(0, list_length - num_items)
        window = list(range(window_start, list_length))
        if reduce and window:
            # Fidelity-driven: keep the shape within `data_points`-gated tolerance
            # rather than keeping every Nth sample (which erased short events).
            # NT (targets) and PSP (primary setpoint) are step functions just
            # like P/F -- they share this same `window`, so they must share
            # the same fidelity check or a step edge can be smoothed into a
            # ramp that never happened. Guard each source for being absent
            # or empty (an all-Food probe_config has no PSP series to speak
            # of, e.g.) and drop any resulting empty slice before handing the
            # list to select_indices.
            series = [list(v[window_start:list_length]) for v in history["P"].values()]
            series += [list(v[window_start:list_length]) for v in history["F"].values()]
            series += [list(v[window_start:list_length]) for v in history.get("NT", {}).values()]
            psp = history.get("PSP") or []
            if psp:
                series.append(list(psp[window_start:list_length]))
            series = [s for s in series if s]
            times = [float(t) for t in history["T"][window_start:list_length]]
            chosen = select_indices(series, times, tolerance=tolerance, min_points=data_points, max_points=max_points)
            window = [window_start + i for i in chosen]

            # Duty is kept by its TRANSITIONS rather than by joining the
            # fidelity check above, because that check's error model does not
            # describe how duty is drawn.
            #
            # select_indices measures how far the drawn line strays from the
            # samples under LINEAR interpolation. Duty is a step function that
            # the chart draws as steps, so between two kept samples the drawn
            # value is the earlier one held flat -- which means keeping every
            # index where duty changes reproduces it EXACTLY, and keeping
            # anything else adds nothing.
            #
            # Handing it to the tolerance check instead is both less accurate
            # and far more expensive, because that check chases an error that
            # only exists under an interpolation the chart never performs.
            # Measured over a 30,000-sample window against a 55%-to-12% duty
            # step on a thermally flat hold -- the duty-floor case this series
            # exists to show: duty omitted keeps 1,000 points and misdraws the
            # step by 27 percentage points; duty added to the fidelity check
            # draws it perfectly but keeps all 30,000; duty transitions draw it
            # perfectly and keep 1,002.
            #
            # Skipped when `max_points` is set: that is an explicit instruction
            # to accept degradation for a hard ceiling, and no production caller
            # sets it.
            if max_points is None:
                window = sorted(set(window) | _duty_change_indices(history, window_start, list_length))

        # History rows are durable and name whatever probes were configured
        # when they were written, while probe_mapper is built from the CURRENT
        # probe_config. Rename, delete or re-module a probe and every older row
        # names a key the mapper no longer has -- missing data, not a server
        # error, so the unresolvable keys are dropped and the rest still draw.
        # The resolution happens once here rather than inside the row loop,
        # which runs over every sampled point of a window hundreds of rows wide.
        history_nt = history.get("NT") or {}
        history_psp = history.get("PSP") or []
        probe_slots = [(key, probe_mapper["probes"][key]) for key in history["P"] if key in probe_mapper["probes"]]
        food_slots = [(key, probe_mapper["probes"][key]) for key in history["F"] if key in probe_mapper["probes"]]
        target_slots = [(key, probe_mapper["targets"][key]) for key in history_nt if key in probe_mapper["targets"]]
        # primarysp is iterated from the mapper itself, so its slot always
        # resolves; what a row set can lack is the shared PSP column it reads.
        setpoint_slots = list(probe_mapper["primarysp"].values()) if history_psp else []

        dropped = sorted(
            {key for key in history["P"] if key not in probe_mapper["probes"]}
            | {key for key in history["F"] if key not in probe_mapper["probes"]}
            | {key for key in history_nt if key not in probe_mapper["targets"]}
        )
        if dropped:
            create_logger(
                "events",
                filename=log_path("events.log"),
                messageformat="%(asctime)s [%(levelname)s] %(message)s",
            ).warning(
                f"Dropped {len(dropped)} history series naming probes the current configuration "
                f"does not have: {', '.join(dropped)}."
            )

        # Build all lists from file data
        for index in window:
            timestamp = history["T"][index]
            for key, slot in probe_slots:
                chart_data[slot]["data"].append({"x": timestamp, "y": history["P"][key][index]})
            for key, slot in food_slots:
                chart_data[slot]["data"].append({"x": timestamp, "y": history["F"][key][index]})
            for key, slot in target_slots:
                chart_data[slot]["data"].append({"x": timestamp, "y": history_nt[key][index]})
            for slot in setpoint_slots:
                chart_data[slot]["data"].append({"x": timestamp, "y": history_psp[index]})
            for source_key, slot in duty_slots:
                value = history[source_key][index]
                # None stays None -- a sample from before duty was recorded is
                # a gap in the line, not a zero. Zero duty is a real reading.
                chart_data[slot]["data"].append(
                    {"x": timestamp, "y": None if value is None else value * _DUTY_SCALES[source_key]}
                )

            time_labels.append(timestamp)
    # No history: return empty series. This used to fabricate one point per
    # probe -- a literal 0 stamped at "now" -- which drew a reading that was
    # never taken, the same failure mode as the every-Nth decimation this
    # module now avoids. It also made `data` two different element types
    # ({"x", "y"} objects normally, bare ints here), which Chart.js tolerates
    # and a typed client cannot. Empty lists say "no data" honestly; both
    # Chart.js consumers assign time_labels/chart_data straight through and
    # render an empty chart.

    """ Create data structure to return """
    data_blob = {
        "time_labels": time_labels,
        "probe_mapper": probe_mapper,
        "chart_data": chart_data,
        "graph_labels": graph_labels,
    }

    return data_blob
