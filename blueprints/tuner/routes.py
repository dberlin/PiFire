from flask import render_template, request, render_template_string, jsonify
from common.common import WriteKind
from common.modes import Mode
from common.control_delta import control_delta
from common.datastore_accessors import (
    read_settings,
    read_control,
    write_control,
    read_tr,
    read_autotune,
    autotune_length,
    flush_autotune,
    write_autotune,
    read_current,
)
from .tuner import calc_shh_coefficients, calc_shh_chart, calc_auto_tune_status

from . import tuner_bp


#: The macro fragments the tuner page may ask the server to render, mapped from
#: the name the client sends to the constant template string that answers it.
#:
#: This is an ALLOWLIST, not a validation step, and the difference is the point:
#: the name used to be concatenated into Jinja SOURCE and handed to
#: render_template_string, so `value` was parsed as template code rather than
#: escaped as data. Every string below is chosen by key -- no request value
#: reaches the renderer as source.
#:
#: render_manual_tool_card is deliberately absent: it is defined in
#: _macro_tuner.html but only ever called from inside render_manual_tool, and
#: the client never asks for it. Reachable is not the same as offered.
_RENDERABLE_FRAGMENTS = {
    name: (
        "{% from 'tuner/_macro_tuner.html' import render_" + name + " %}{{ render_" + name + "(settings, control) }}"
    )
    for name in (
        "manual_instruction_card",
        "manual_tool",
        "manual_finish_btn",
        "auto_instruction_card",
        "auto_tool",
        "auto_finish_btn",
    )
}


@tuner_bp.route("/", methods=["POST", "GET"])
def tuner_page():
    settings = read_settings()
    control = read_control()

    # This POST path will load/render portions of the tuner page
    if request.method == "POST" and ("form" in request.content_type):
        requestform = request.form
        if "command" in requestform.keys():
            if "render" in requestform["command"]:
                render_string = _RENDERABLE_FRAGMENTS.get(requestform.get("value", ""))
                if render_string is None:
                    return jsonify({"error": "unknown_fragment"}), 400
                return render_template_string(render_string, settings=settings, control=control)

    # This POST path provides data back to the page
    if request.method == "POST" and "json" in request.content_type:
        requestjson = request.json
        command = requestjson.get("command", None)
        if command == "stop_tuning":
            if control["tuning_mode"]:
                control["tuning_mode"] = False  # Disable tuning mode
                write_control(control_delta(set_values={"tuning_mode": False}), WriteKind.DELTA, origin="app")
            if control["mode"] == Mode.MONITOR:
                # If in Monitor Mode, stop
                control["mode"] = Mode.STOP  # Go to Stop mode
                control["updated"] = True
                write_control(
                    control_delta(set_values={"mode": Mode.STOP, "updated": True}),
                    WriteKind.DELTA,
                    origin="app",
                )
        if command == "read_tr":
            if not control["tuning_mode"]:
                control["tuning_mode"] = True  # Enable tuning mode
                write_control(control_delta(set_values={"tuning_mode": True}), WriteKind.DELTA, origin="app")

            if control["mode"] == Mode.STOP:
                # Turn on Monitor Mode if the system is stopped
                control["mode"] = Mode.MONITOR  # Enable monitor mode
                control["updated"] = True
                write_control(
                    control_delta(set_values={"mode": Mode.MONITOR, "updated": True}),
                    WriteKind.DELTA,
                    origin="app",
                )

            cur_probe_tr = read_tr()
            if requestjson["probe_selected"] in cur_probe_tr.keys():
                return jsonify({"trohms": cur_probe_tr[requestjson["probe_selected"]]})
            else:
                return jsonify({"trohms": 0})
        if command == "manual_finish" or command == "auto_finish":
            if control["tuning_mode"]:
                control["tuning_mode"] = False  # Disable tuning mode
                write_control(control_delta(set_values={"tuning_mode": False}), WriteKind.DELTA, origin="app")
            if control["mode"] == Mode.MONITOR:
                # If in Monitor Mode, stop
                control["mode"] = Mode.STOP  # Go to Stop mode
                control["updated"] = True
                write_control(
                    control_delta(set_values={"mode": Mode.STOP, "updated": True}),
                    WriteKind.DELTA,
                    origin="app",
                )

            tunerManualHighTemp = requestjson.get("tunerManualHighTemp", 0.1)
            tunerManualHighTemp = 0 if tunerManualHighTemp == "" else float(tunerManualHighTemp)
            tunerManualHighTr = requestjson.get("tunerManualHighTr", 0.1)
            tunerManualHighTr = 0 if tunerManualHighTr == "" else int(float(tunerManualHighTr))

            tunerManualMediumTemp = requestjson.get("tunerManualMediumTemp", 0.1)
            tunerManualMediumTemp = 0 if tunerManualMediumTemp == "" else float(tunerManualMediumTemp)
            tunerManualMediumTr = requestjson.get("tunerManualMediumTr", 0.1)
            tunerManualMediumTr = 0 if tunerManualMediumTr == "" else int(float(tunerManualMediumTr))

            tunerManualLowTemp = requestjson.get("tunerManualLowTemp", 0.1)
            tunerManualLowTemp = 0 if tunerManualLowTemp == "" else float(tunerManualLowTemp)
            tunerManualLowTr = requestjson.get("tunerManualLowTr", 0.1)
            tunerManualLowTr = 0 if tunerManualLowTr == "" else int(float(tunerManualLowTr))

            a, b, c = calc_shh_coefficients(
                tunerManualLowTemp,
                tunerManualMediumTemp,
                tunerManualHighTemp,
                tunerManualLowTr,
                tunerManualMediumTr,
                tunerManualHighTr,
                units=settings["globals"]["units"],
            )
            tr_points = [int(tunerManualHighTr), int(tunerManualMediumTr), int(tunerManualLowTr)]
            labels, chart_data = calc_shh_chart(
                a, b, c, units=settings["globals"]["units"], temp_range=220, tr_points=tr_points
            )
            return jsonify({"labels": labels, "chart_data": chart_data, "coefficients": {"a": a, "b": b, "c": c}})
        if command == "read_auto_status":
            first_run = False
            if not control["tuning_mode"]:
                control["tuning_mode"] = True  # Enable tuning mode
                write_control(control_delta(set_values={"tuning_mode": True}), WriteKind.DELTA, origin="app")
                flush_autotune()  # Flush autotune data
                first_run = True

            if control["mode"] == Mode.STOP:
                # Turn on Monitor Mode if the system is stopped
                control["mode"] = Mode.MONITOR  # Enable monitor mode
                control["updated"] = True
                write_control(
                    control_delta(set_values={"mode": Mode.MONITOR, "updated": True}),
                    WriteKind.DELTA,
                    origin="app",
                )

            status_data = {
                "current_tr": 0,
                "current_temp": 0,
                "high_tr": 0,
                "high_temp": 0,
                "medium_tr": 0,
                "medium_temp": 0,
                "low_tr": 0,
                "low_temp": 0,
                "ready": False,
            }

            # Get Tr Data from all probes
            cur_probe_tr = read_tr()
            if requestjson["probe_selected"] in cur_probe_tr.keys():
                status_data["current_tr"] = cur_probe_tr[requestjson["probe_selected"]]
            else:
                status_data["current_tr"] = -1

            # Get Temp Data from all probes
            cur_probe_temps = read_current()
            if requestjson["probe_reference"] in cur_probe_temps["P"].keys():
                status_data["current_temp"] = cur_probe_temps["P"][requestjson["probe_reference"]]
            elif requestjson["probe_reference"] in cur_probe_temps["F"].keys():
                status_data["current_temp"] = cur_probe_temps["F"][requestjson["probe_reference"]]
            elif requestjson["probe_reference"] in cur_probe_temps["AUX"].keys():
                status_data["current_temp"] = cur_probe_temps["AUX"][requestjson["probe_reference"]]
            else:
                status_data["current_temp"] = -1

            # Some probes (i.e. the DS18B20) may be slow to respond when Monitor mode starts, and may report 0 degrees
            # Thus we should ignore these first few data points if they are 0
            autotune_data_size = autotune_length()
            if (
                (autotune_data_size > 4 or status_data["current_temp"] > 0)
                and status_data["current_tr"] >= 0
                and status_data["current_temp"] >= 0
                and not first_run
            ):
                # Record Temperature / Tr Values in Auto-Tune Record
                data = {"ref_T": status_data["current_temp"], "probe_Tr": status_data["current_tr"]}
                write_autotune(data)

            data = read_autotune()
            if len(data) > 10:
                # If more than 10 datapoints, then calculate high / low / medium
                calc_auto_tune_status(data, settings["globals"]["units"], status_data)

            return jsonify(status_data)

    return render_template(
        "tuner/index.html",
        settings=settings,
        control=control,
    )
