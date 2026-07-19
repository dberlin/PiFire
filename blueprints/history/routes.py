import os
import time
from flask import render_template, request, current_app, jsonify, send_file, redirect
from common.modes import Mode
from common.datastore_accessors import read_settings, read_control, read_current, write_settings
from common.app import (
    create_ui_hash,
    prepare_annotations,
    prepare_csv,
    render_cookfile_page,
    classify_cookfile_error,
)
from file_mgmt.cookfile import read_cookfile, prepare_chartdata

from . import history_bp


def _history_stream(settings, control, HISTORY_FOLDER, errors):
    # GET - Read current temperatures and set points for history streaming
    control = read_control()
    json_response = {}
    if control["mode"] in [Mode.STOP, Mode.ERROR]:
        json_response["current"] = read_current(zero_out=True)  # Probe Temps Zero'd Out
    else:
        json_response["current"] = read_current()  # Probe Temps Zero'd Out

    # Calculate Displayed Start Time
    displayed_starttime = time.time() - (settings["history_page"]["minutes"] * 20)
    json_response["annotations"] = prepare_annotations(displayed_starttime)
    json_response["mode"] = control["mode"]
    json_response["ui_hash"] = create_ui_hash()
    json_response["timestamp"] = int(time.time() * 1000)

    return jsonify(json_response)


def _history_refresh(settings, control, HISTORY_FOLDER, errors):
    # POST - Get number of minutes into the history to refresh the history chart
    control = read_control()
    request_json = request.json
    if "num_mins" in request_json:
        num_items = (
            int(request_json["num_mins"]) * 20 if int(request_json["num_mins"]) > 0 else 20
        )  # Calculate number of items requested
        settings["history_page"]["minutes"] = int(request_json["num_mins"]) if int(request_json["num_mins"]) > 0 else 1
        write_settings(settings)
    elif "zoom" in request_json:
        num_items = int(request_json["zoom"]) * 20
    else:
        num_items = int(settings["history_page"]["minutes"] * 20)

    # Get Chart Data Structures
    json_response = prepare_chartdata(
        settings["history_page"]["probe_config"],
        num_items=num_items,
        reduce=True,
        data_points=settings["history_page"]["datapoints"],
    )
    json_response["ui_hash"] = create_ui_hash()
    # Calculate Displayed Start Time
    displayed_starttime = time.time() - (int(num_items / 20) * 60)
    json_response["annotations"] = prepare_annotations(displayed_starttime)
    """
    json_response = {
        'annotations' : [],
        'time_labels' : time_labels,
        'probe_mapper' : probe_mapper,
        'chart_data' : chart_data
    }
    """
    return jsonify(json_response)


def _history_cookfile(settings, control, HISTORY_FOLDER, errors):
    if request.method != "POST":
        return None
    response = request.form
    if "delcookfile" in response:
        filename = "./history/" + response["delcookfile"]
        os.remove(filename)
        return redirect("/history")
    if "opencookfile" in response:
        cookfilename = HISTORY_FOLDER + response["opencookfile"]
        cookfilestruct, status = read_cookfile(cookfilename)
        if status == "OK":
            filenameonly = response["opencookfile"]
            return render_cookfile_page(cookfilestruct, settings, cookfilename, filenameonly, errors)
        else:
            errors.append(status)
            errortype = classify_cookfile_error(status)
            return render_template(
                "cferror.html",
                settings=settings,
                cookfilename=cookfilename,
                errortype=errortype,
                errors=errors,
            )
    if "dlcookfile" in response:
        filename = "./history/" + response["dlcookfile"]
        return send_file(filename, as_attachment=True, max_age=0)
    return None


def _history_setmins(settings, control, HISTORY_FOLDER, errors):
    if request.method != "POST":
        return None
    response = request.form
    if "minutes" in response:
        if response["minutes"] != "":
            num_items = int(response["minutes"]) * 20
            settings["history_page"]["minutes"] = int(response["minutes"])
            write_settings(settings)
    return None


def _history_export(settings, control, HISTORY_FOLDER, errors):
    if request.method != "GET":
        return None
    exportfilename = prepare_csv()
    return send_file(exportfilename, as_attachment=True, max_age=0)


_HISTORY_DISPATCH = {
    "stream": _history_stream,
    "refresh": _history_refresh,
    "cookfile": _history_cookfile,
    "setmins": _history_setmins,
    "export": _history_export,
}


@history_bp.route("/<action>", methods=["POST", "GET"])
@history_bp.route("/", methods=["POST", "GET"])
def history_page(action=None):
    settings = read_settings()
    control = read_control()
    HISTORY_FOLDER = current_app.config["HISTORY_FOLDER"]
    errors = []

    handler = _HISTORY_DISPATCH.get(action)
    if handler is not None:
        result = handler(settings, control, HISTORY_FOLDER, errors)
        if result is not None:
            return result

    return render_template(
        "history/index.html",
        settings=settings,
        control=control,
    )
