#!/usr/bin/env python3
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
import os
import json
import shutil
import zipfile
import pathlib

from common.common import (
    generate_uuid,
    process_metrics,
    semantic_ver_is_lower,
    semantic_ver_to_list,
    unpack_history,
    create_logger,
)
from common.datastore_accessors import (
    read_settings,
    read_history,
    flush_history,
    read_all_metrics,
)
from common.defaults import default_probe_config
from file_mgmt.common import read_json_file_data, update_json_file_data
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

    return cookfilestruct


def create_cookfile():
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

        cook_file_struct["events"] = process_metrics(read_all_metrics(), augerrate=settings["globals"]["augerrate"])

        # 1. Create all JSON data files
        files_list = ["metadata", "graph_data", "raw_data", "graph_labels", "events", "comments", "assets"]
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
        if jsonfile == "metadata":
            # settings["versions"]["cookfile"] is the MINIMUM file version this
            # build can load. The comparison used to test major/minor/patch
            # INDEPENDENTLY (`file[0] >= min[0] and file[1] >= min[1] and
            # file[2] >= min[2]`), which is not how semantic versions order:
            # against the shipped minimum of 1.5.0 a file written as 2.4.0
            # failed the `4 >= 5` term and was reported as an OLDER format, so
            # a file from a NEWER PiFire was routed to the repair/upgrade
            # prompt that would rewrite it backwards. semantic_ver_is_lower()
            # is the correct lexicographic comparison and was already in tree.
            if semantic_ver_is_lower(cook_file_struct["metadata"]["version"], settings["versions"]["cookfile"]):
                status = "WARNING: Older cookfile version format! "
                break  # Exit loop and function, error string in status

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
            for index in range(0, list_length):
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
                if not "assets" in comment.keys():
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


def prepare_chartdata(
    probe_config,
    chart_info={},
    num_items=10,
    reduce=True,
    data_points=10000,
    history=None,
    tolerance=2.0,
    max_points=None,
):
    """Build Probe Mapper and Chart Data Struct"""
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

        # Build all lists from file data
        for index in window:
            for key, value in history["P"].items():
                chart_data[probe_mapper["probes"][key]]["data"].append(
                    {"x": history["T"][index], "y": history["P"][key][index]}
                )
            for key, value in history["F"].items():
                chart_data[probe_mapper["probes"][key]]["data"].append(
                    {"x": history["T"][index], "y": history["F"][key][index]}
                )
            for key, value in history["NT"].items():
                chart_data[probe_mapper["targets"][key]]["data"].append(
                    {"x": history["T"][index], "y": history["NT"][key][index]}
                )
            for key in probe_mapper["primarysp"]:
                chart_data[probe_mapper["primarysp"][key]]["data"].append(
                    {"x": history["T"][index], "y": history["PSP"][index]}
                )

            time_labels.append(history["T"][index])
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
