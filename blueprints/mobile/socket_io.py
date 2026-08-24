#!/usr/bin/env python3

"""
==============================================================================
 PiFire SocketIO Module
==============================================================================

Description: This library provides socketio functions for app.py

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
from collections.abc import Mapping
import threading
import json
import math
import os
import time

from common.common import (
    ErrorKind,
    read_events_records,
    flush_events_records,
    read_generic_json,
    write_log,
    convert_settings_units,
    epoch_to_time,
)
from common.control_delta import NOTIFY_POST_KEYS, ControlDeltaError, control_delta, notify_ops_from_post
from common.persistence.control import (
    read_control,
    flush_control,
    enqueue_control_delta,
)
from common.persistence.runtime import (
    read_settings_store,
    seed_settings_store,
    read_pellets_store,
    seed_pellets_store,
    read_status,
    read_current,
    read_errors,
    read_warnings_snapshot,
    read_generic_key,
    read_control_heartbeat,
    CONTROL_HEARTBEAT_STALE_AFTER,
    write_pellet_db,
    write_connected_user,
    read_connected_users,
    flush_connected_users,
    remove_connected_user,
)
from common.persistence.history import flush_history, request_history_clear
from common.defaults import default_settings, default_control
from common.system import (
    reboot_system,
    shutdown_system,
    restart_control,
    restart_webapp,
    restart_scripts,
    gather_system_info,
)
from common.modes import Mode
from controller.learning_report import controller_learning_report_revision
from common.pellets_actions import clear_pellet_db, dispatch_pellet_action
from common.app import (
    CONTROL_DOWN_ERROR,
    create_ui_hash,
    update_probe_config,
    save_settings_and_flag_update,
    api_response,
)
from pydantic import ValidationError

from common.settings_schema import SettingsValidationError, apply_settings_delta
from common.web_contracts.control import ControlPatchRequest
from common.web_contracts.core import (
    DashSocketPayload,
    PelletSocketPayload,
    ThermocoupleHealthView,
)
from flask import request
from werkzeug.utils import secure_filename
from app import socketio
from config import Config
from file_mgmt.recipes import read_recipefile, get_recipefilelist
from base64 import b64encode
from threading import Event

thread_lock = threading.Lock()
thread_event = Event()
thread = None

# Whether the control process's heartbeat was fresh at the last check
# (_check_control_status, run by the broadcast loop every second). Consumed by
# _get_dash_data, which composes CONTROL_DOWN_ERROR into the payload it is
# already building.
#
# Deliberately in memory, and deliberately NOT in the errors blob. That blob is
# owned by the control process: every other writer of it (controller/runtime's
# devices.py / runner.py / controller.py, and common/extra_installer.py) records
# a failure that already happened and cannot un-happen, and its single clearer,
# flush_errors(), runs from control.py's boot path -- "errors accumulated since
# the control process started". Liveness is the opposite kind of fact: it is
# about right now, it is observed by THIS process, and it stops being true the
# moment control answers again. Filed in the blob it became permanent, because
# read_errors() is a plain non-destructive read (like `warnings` on the very
# same payload, but `warnings` has an explicit clear path -- POST
# /api/dismiss_warnings, keyed to a high-water mark -- while errors has none)
# and no route, socket action or API command could clear it -- so one missed
# answer, from a control process that was merely slow, rode every
# socket_dash_data frame until the control process restarted.
#
# Nor is it in the datastore: persisting a statement about "right now" is what
# made this sticky in the first place, and a persisted copy would outlive the
# web process that observed it. Starting each web process optimistic is correct
# -- the first check either confirms it or corrects it within a second.
#
# blueprints/dash/routes.py::dash_page needs no equivalent: it probes live on
# every render and appends to the local list it hands the template.
_control_alive = True

#: How long the broadcast loop waits between passes.
#:
#: This is the floor on how long a button press takes to become VISIBLE: the
#: control loop applies a command within ~50-100ms, but the result does not
#: reach the browser until the next pass reads it and emits. At the previous 1s
#: that floor was ~0.5s on average and 1s at worst, for a command the backend
#: had already finished -- which reads as "the UI is slow" even when nothing is.
#:
#: A pass is a handful of SQLite reads and, thanks to the change-dedup below,
#: emits nothing at all when nothing moved -- so the cost of shortening it is
#: reads, not traffic. Sharpening it further has diminishing returns: the reads
#: hit the same database the control loop uses to time the auger.
BROADCAST_INTERVAL = 0.25


def _set_control_alive(alive):
    global _control_alive
    _control_alive = alive


"""
==============================================================================
 Flush datastore and create Settings / PelletDB / Connected Users / Events
==============================================================================
"""
seed_settings_store()
seed_pellets_store()
flush_connected_users()
flush_events_records()

recipe_folder = Config.RECIPE_FOLDER

"""
==============================================================================
 Functions
==============================================================================
"""


@socketio.on("connect")
def handle_connect():
    client_id = request.sid
    write_connected_user(client_id)
    connected_users = read_connected_users()
    listen_app_data(force=True)
    _emit_app_data_to(client_id)
    print(f"User {client_id} connected. Current connected users: {connected_users}")


@socketio.on("disconnect")
def handle_disconnect():
    global thread
    client_id = request.sid
    remove_connected_user(client_id)
    connected_users = read_connected_users()
    print(f"User {client_id} disconnected. Current connected users: {connected_users}")
    if len(connected_users) == 0:
        thread_event.clear()
        with thread_lock:
            if thread is not None:
                thread.join()
                thread = None


@socketio.on("listen_app_data")
def listen_app_data(force=False):
    global thread

    with thread_lock:
        if thread is None:
            thread_event.set()
            thread = socketio.start_background_task(_emit_app_data, thread_event, force)

    return _response(result="OK")


@socketio.on("get_app_data")
def get_app_data(action=None, arg01=None, arg02=None):
    return _get_app_data(action, arg01, arg02)


@socketio.on("post_app_data")
def post_app_data(action=None, type=None, json_data=None):
    return _post_app_data(action, type, json_data)


"""
==============================================================================
 Supporting Functions
==============================================================================
"""


def _emit_app_data(event, force_refresh):
    global thread

    previous_dash = ""
    previous_event = ""
    previous_pellet = ""

    try:
        while event.is_set():
            # Once per loop pass, not on a 30s timer: _check_control_status()
            # is now a single SELECT against a stamp the control loop keeps
            # fresh, so the every-30-seconds throttle it used to need (it
            # blocked for a full second waiting on an answer a stopped process
            # could never send) is gone -- and with it the up-to-30s lag before
            # a recovered control process was reported as back.
            _check_control_status()

            settings = read_settings_store()
            pelletdb = read_pellets_store()
            uuid = settings["server_info"]["uuid"]

            pellet_data = _get_pellet_socket_data(settings, pelletdb)

            event_data = {"uuid": uuid, "events": read_events_records()}

            dash_data = _get_dash_data(settings, pelletdb)

            if force_refresh:
                socketio.emit("socket_event_data", event_data)
                socketio.emit("socket_pellet_data", pellet_data)
                socketio.emit("socket_dash_data", dash_data)
                force_refresh = False
            else:
                if previous_event != event_data:
                    socketio.emit("socket_event_data", event_data)
                    previous_event = event_data

                if previous_pellet != pellet_data:
                    socketio.emit("socket_pellet_data", pellet_data)
                    previous_pellet = pellet_data

                if previous_dash != dash_data:
                    socketio.emit("socket_dash_data", dash_data)
                    previous_dash = dash_data

            socketio.sleep(BROADCAST_INTERVAL)
    finally:
        event.clear()
        thread = None


def _emit_app_data_to(client_id):
    """Send the current app data straight to one freshly-connected client.

    The broadcast loop is a single process-wide background task that re-emits
    a payload only when it differs from the last one it sent, and its
    force_refresh argument is consumed on the tick that starts the loop --
    `listen_app_data` starts the task at most once, so a client connecting
    while it is already running contributes a force flag that is discarded. On
    an idle grill nothing in the payload changes, so such a client can wait
    indefinitely for its first data.

    The Jinja pages hide this: they render the current values into the HTML,
    so the socket only ever has to deliver updates. A client that renders
    purely from the socket sits on its placeholder state instead.
    """
    settings = read_settings_store()
    pelletdb = read_pellets_store()
    uuid = settings["server_info"]["uuid"]

    socketio.emit("socket_event_data", {"uuid": uuid, "events": read_events_records()}, to=client_id)
    socketio.emit("socket_pellet_data", _get_pellet_socket_data(settings, pelletdb), to=client_id)
    socketio.emit("socket_dash_data", _get_dash_data(settings, pelletdb), to=client_id)


def _get_pellet_socket_data(settings, pelletdb):
    return PelletSocketPayload.model_validate(
        {"uuid": settings["server_info"]["uuid"], "pellets": pelletdb},
        strict=True,
    ).model_dump(mode="json", by_alias=True, exclude_none=False)


def _project_thermocouple_health(
    settings,
    probe_device_info,
    controller_mode,
    *,
    now=None,
):
    """Build the client health view without mutating persisted producer data."""
    if not isinstance(probe_device_info, list):
        return []

    health_settings = settings.get("thermocouple_health")
    probe_settings = settings.get("probe_settings")
    if not isinstance(health_settings, Mapping) or not isinstance(
        probe_settings, Mapping
    ):
        return []
    policy = health_settings.get("inference_policy")
    if policy not in {"off", "observe", "enforce"}:
        return []

    probe_map = probe_settings.get("probe_map")
    if not isinstance(probe_map, Mapping):
        return []
    configured_probes = probe_map.get("probe_info")
    if not isinstance(configured_probes, list):
        return []

    reports_by_probe = {}
    for device_info in probe_device_info:
        if not isinstance(device_info, Mapping):
            continue
        device = device_info.get("device")
        status = device_info.get("status")
        if not isinstance(device, str) or not isinstance(status, Mapping):
            continue
        reports = status.get("thermocouple_health")
        if not isinstance(reports, Mapping):
            continue
        for label, report in reports.items():
            if isinstance(label, str) and isinstance(report, Mapping):
                reports_by_probe[(device, label)] = report

    if now is None:
        now = time.monotonic()
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        return []
    now = float(now)

    projected = []
    for probe in configured_probes:
        if not isinstance(probe, Mapping):
            continue
        device = probe.get("device")
        port = probe.get("port")
        label = probe.get("label")
        display_name = probe.get("name")
        role = probe.get("type")
        if (
            not isinstance(device, str)
            or not isinstance(port, str)
            or not isinstance(label, str)
            or not isinstance(display_name, str)
            or role not in {"Primary", "Food", "Aux"}
        ):
            continue

        report = reports_by_probe.get((device, label))
        if report is None:
            continue
        observed_at = report.get("observed_at")
        detail = report.get("detail")
        evidence = report.get("evidence")
        if (
            isinstance(observed_at, bool)
            or not isinstance(observed_at, (int, float))
            or not math.isfinite(observed_at)
            or not isinstance(detail, Mapping)
            or not isinstance(evidence, list)
        ):
            continue

        age_s = max(0.0, now - float(observed_at))
        has_hardware = "hardware" in evidence
        has_software = any(item != "hardware" for item in evidence)
        source = (
            "mixed"
            if has_hardware and has_software
            else "hardware"
            if has_hardware
            else "software"
        )

        state = report.get("state")
        temperature_valid = report.get("temperature_valid")
        outcome = "none"
        if state == "confirmed":
            if role == "Primary" and temperature_valid is True:
                outcome = "notify_only"
            elif role == "Primary" and controller_mode == Mode.ERROR:
                outcome = "stopped"
            else:
                outcome = "unavailable"

        try:
            view = ThermocoupleHealthView.model_validate(
                {
                    "device": device,
                    "port": port,
                    "label": label,
                    "displayName": display_name,
                    "role": role,
                    "report": {
                        "state": state,
                        "faults": report.get("faults"),
                        "evidence": evidence,
                        "temperatureValid": temperature_valid,
                        "detail": dict(detail),
                    },
                    "detector": {
                        "source": source,
                        "policy": policy,
                    },
                    "outcome": outcome,
                    "freshness": {
                        "current": age_s <= CONTROL_HEARTBEAT_STALE_AFTER,
                        "lastReportedAgeS": age_s,
                    },
                },
                strict=True,
            )
        except ValidationError:
            continue
        projected.append(
            view.model_dump(mode="json", by_alias=True, exclude_none=False)
        )
    return projected


def _get_dash_data(settings, pelletdb):
    control = read_control()
    status = read_status()
    current = read_current()
    # The durable half comes from the store, where each producing process owns
    # its own ErrorKind -- any of them can restart and rewrite its own list
    # without erasing the others' banners. The liveness half is recomputed per
    # frame from the last check, so it clears itself the moment control answers
    # again. Copy rather than append: no other kind's rows are ours to write.
    errors = read_errors(ErrorKind.ALL) + ([] if _control_alive else [CONTROL_DOWN_ERROR])
    warnings_snapshot = read_warnings_snapshot()
    notify_data = control["notify_data"]
    probe_device_info = read_generic_key("probe_device_info")

    timer_notify_data = _get_timer_notify_data(notify_data)
    food_probes = _get_probe_data("Food", settings, current, probe_device_info, notify_data)
    primary_probe = _get_probe_data("Primary", settings, current, probe_device_info, notify_data)[0]

    dash_data = {
        "uuid": settings["server_info"]["uuid"],
        "errors": errors,
        "warnings": warnings_snapshot["warnings"],
        # High-water mark for the dismiss control: the client posts it back to
        # clear exactly the warnings it displayed (blueprints/api dismiss_warnings).
        "warningsMaxId": warnings_snapshot["max_id"],
        "status": control["status"],
        # The probe-map hash. A client compares it across frames and refetches
        # the settings blob when it moves: set_probe_map() rebuilds hidden_cards,
        # notify_data and history_page.probe_config off probe labels, none of
        # which the socket payload carries. Computed from the settings already
        # in hand, so the frame costs no extra read.
        "uiHash": create_ui_hash(settings),
        "criticalError": control["critical_error"],
        "grillName": settings["globals"]["grill_name"],
        "currentMode": control["mode"],
        "nextMode": control["next_mode"],
        "displayMode": status["mode"],
        "smokePlus": control["s_plus"],
        "pwmControl": control["pwm_control"],
        # Current manual DC-fan duty cycle (0-100), so the duty entry opens on
        # the real value rather than a guess. Distinct from pwmControl, which
        # is the automatic PWM-control enable flag.
        "manualPwm": control["manual"]["pwm"],
        "pMode": settings["cycle_data"]["PMode"],
        "hopperLevel": pelletdb["current"]["hopper_level"],
        "startupTimestamp": math.trunc(control["startup_timestamp"]),
        "modeStartTime": math.trunc(status["start_time"]),
        "lidOpenDetectEnabled": settings["cycle_data"]["LidOpenDetectEnabled"],
        "lidOpenDetected": status["lid_open_detected"],
        "lidOpenEndTime": math.trunc(status["lid_open_endtime"]),
        "startDuration": status["start_duration"],
        "shutdownDuration": status["shutdown_duration"],
        "primeDuration": status["prime_duration"],
        "primeAmount": status["prime_amount"],
        "tempUnits": settings["globals"]["units"],
        "hasDcFan": settings["platform"]["dc_fan"],
        "hasDistanceSensor": settings["modules"]["dist"] != "none",
        "startupCheck": settings["safety"]["startup_check"],
        "startToHoldPrompt": settings["startup"]["start_to_mode"]["start_to_hold_prompt"],
        "startupGotoTemp": settings["startup"]["start_to_mode"]["primary_setpoint"],
        "startupGotoMode": settings["startup"]["start_to_mode"]["after_startup_mode"],
        "allowManualOutputs": settings["safety"]["allow_manual_changes"],
        # The temperature above which the grill shuts down in any mode
        # (controller/runtime/logic/safety.py), carried in the units this
        # payload's tempUnits names -- common.py converts it with them. It
        # bounds every setpoint the dashboard offers; primaryProbe.maxTemp is
        # the gauge's ceiling, a display choice, and cannot stand in for it.
        "safetyMaxTemp": settings["safety"]["maxtemp"],
        # What the controller is asking of each actuator: the auger's share of
        # its cycle (0..1) and the fan's duty as a percentage. The dashboard
        # shows them in place of P-mode and Smoke+ while holding, where those
        # two describe nothing the grill is doing.
        "cycleRatio": status.get("cycle_ratio", 0) or 0,
        "fanDuty": status.get("fan_duty", 0) or 0,
        "timer": {
            "start": math.trunc(control["timer"]["start"]),
            "paused": math.trunc(control["timer"]["paused"]),
            "end": math.trunc(control["timer"]["end"]),
            "keepWarm": timer_notify_data["keep_warm"],
            "shutdown": timer_notify_data["shutdown"],
        },
        "outputs": {
            "fan": status["outpins"]["fan"],
            "auger": status["outpins"]["auger"],
            "igniter": status["outpins"]["igniter"],
            # The manual control panel toggles a power relay too (platform
            # outputs.power); the dashboard needs its live state to render the
            # button's on/off styling.
            "power": status["outpins"]["power"],
        },
        "recipeStatus": {
            "recipeMode": status["recipe"],
            "filename": control["recipe"]["filename"].split("/")[-1],
            "mode": status["mode"],
            "paused": status["recipe_paused"],
            "step": control["recipe"]["step"],
        },
        "foodProbes": food_probes,
        "primaryProbe": primary_probe,
        "modelLearningRevision": controller_learning_report_revision(settings["controller"]["selected"]),
        "thermocoupleHealth": _project_thermocouple_health(
            settings,
            probe_device_info,
            control["mode"],
        ),
    }
    return DashSocketPayload.model_validate(dash_data, strict=True).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )


def _get_app_data_settings_data(settings, arg01, arg02):
    return _response(result="OK", data=settings)


def _get_app_data_dash_data(settings, arg01, arg02):
    pelletdb = read_pellets_store()
    return _response(result="OK", data=_get_dash_data(settings, pelletdb))


def _get_app_data_pellets_data(settings, arg01, arg02):
    return _response(result="OK", data=_get_pellet_socket_data(settings, read_pellets_store()))


def _get_app_data_events_data(settings, arg01, arg02):
    return _response(result="OK", data={"uuid": settings["server_info"]["uuid"], "events": read_events_records()})


def _get_app_data_hopper_level(settings, arg01, arg02):
    return _response(result="OK", data=read_pellets_store()["current"]["hopper_level"])


def _get_app_data_info_data(settings, arg01, arg02):
    system_info = _get_system_info(read_control())
    return _response(
        result="OK",
        data={
            "uuid": settings["server_info"]["uuid"],
            "platformInfo": {
                "systemModel": system_info["hardware_info"]["cpu_info"]["model"],
                "cpuModel": system_info["hardware_info"]["cpu_info"]["model_name"],
                "cpuHardware": system_info["hardware_info"]["cpu_info"]["hardware"],
                "cpuCores": system_info["hardware_info"]["cpu_info"]["cores"],
                "cpuFrequency": system_info["hardware_info"]["cpu_info"]["frequency"],
                "totalRam": system_info["hardware_info"]["total_ram"],
                "availableRam": system_info["hardware_info"]["available_ram"],
            },
            "osInfo": {
                "prettyName": system_info["os_info"]["PRETTY_NAME"],
                "version": system_info["os_info"]["VERSION"],
                "codeName": system_info["os_info"]["VERSION_CODENAME"],
                "architecture": system_info["os_info"]["ARCHITECTURE"],
                "bits": system_info["os_info"]["BITS"],
            },
            "networkInfo": system_info["network_info"],
            "cpuThrottled": system_info["cpu_throttled"],
            "cpuUnderVolt": system_info["cpu_under_voltage"],
            "wifiQualityValue": system_info["wifi_quality_value"],
            "wifiQualityMax": system_info["wifi_quality_max"],
            "wifiQualityPercentage": system_info["wifi_quality_percentage"],
            "upTime": system_info["uptime"],
            "cpuTemp": system_info["cpu_temp"],
            "outPins": settings["platform"]["outputs"],
            "inPins": settings["platform"]["inputs"],
            "devPins": settings["platform"]["devices"],
            "serverVersion": settings["versions"]["server"],
            "serverBuild": settings["versions"]["build"],
            "platform": settings["modules"]["grillplat"],
            "display": settings["modules"]["display"],
            "distance": settings["modules"]["dist"],
            "pipList": read_generic_json("pip_list.json"),
            "dcFan": settings["platform"]["dc_fan"],
        },
    )


def _get_app_data_manual_data(settings, arg01, arg02):
    control = read_control()
    return _response(
        result="OK",
        data={
            "manual": read_status()["outpins"],
            "active": control["mode"] == Mode.MANUAL,
            "dcFan": settings["platform"]["dc_fan"],
        },
    )


def _get_app_data_recipe_data(settings, arg01, arg02):
    if arg01 is not None:
        if arg01 == "details":
            filelist = get_recipefilelist()
            recipedetailslist = []
            for filename in filelist:
                filepath = f"{recipe_folder}{filename}"
                recipe_data, status = read_recipefile(filepath)
                if status == "OK":
                    recipe_data = _encode_assets(recipe_data)
                    recipedetailslist.append({"filename": filename, "details": recipe_data})
            if recipedetailslist:
                return _response(
                    result="OK", data={"uuid": settings["server_info"]["uuid"], "recipe_details": recipedetailslist}
                )
            else:
                return _response(result="Error", message="Error: Recipes details not found")


_GET_APP_DATA_DISPATCH = {
    "settings_data": _get_app_data_settings_data,
    "dash_data": _get_app_data_dash_data,
    "pellets_data": _get_app_data_pellets_data,
    "events_data": _get_app_data_events_data,
    "hopper_level": _get_app_data_hopper_level,
    "info_data": _get_app_data_info_data,
    "manual_data": _get_app_data_manual_data,
    "recipe_data": _get_app_data_recipe_data,
}


def _get_app_data(action=None, arg01=None, arg02=None):
    settings = read_settings_store()

    handler = _GET_APP_DATA_DISPATCH.get(action)
    if handler is None:
        return _response(result="Error", message="Error: Received request without valid action")
    return handler(settings, arg01, arg02)


def _post_app_data_update(settings, type, request):
    if type == "settings":
        control = read_control()
        for key in request.keys():
            if key in settings.keys():
                settings = apply_settings_delta(settings, request)
                _write_settings(settings, control)
                return _response(result="OK", data=settings)
            else:
                return _response(result="Error", message="Error: Key not found in settings")
    elif type == "control":
        control = read_control()
        if "timer" in request:
            # start/paused/end are one countdown and the control code branches
            # on their combinations, so a timer state computed from a read that
            # cannot see the write queue is exactly the cross-writer race this
            # door used to feed. The timer_action commands queue ops the drain
            # resolves against live state; use those. Mirrors _api_post_control.
            return _response(
                result="Error",
                message="Error: control['timer'] cannot be set here; use the timer_action commands",
            )
        for key in request.keys():
            # NOTIFY_POST_KEYS widens the membership test because `notify_updates`
            # is a wire key, not a control member -- it addresses entries INSIDE
            # control["notify_data"] rather than naming a key of its own.
            if key in control.keys() or key in NOTIFY_POST_KEYS:
                # A posted patch is ALREADY a statement of intent -- the client
                # sent only what it means -- so it is WRAPPED as a delta, not
                # rewritten. The notify keys are the exception, and
                # notify_ops_from_post() is shared with _api_post_control so the
                # two doors cannot drift.
                try:
                    patch = ControlPatchRequest.model_validate(request, strict=True)
                    payload, ops = notify_ops_from_post(patch.model_dump(mode="python", exclude_unset=True))
                    enqueue_control_delta(control_delta(set_values=payload, ops=ops), origin="app-socketio")
                except (ControlDeltaError, ValidationError) as exc:
                    return _response(result="Error", message=f"Error: {exc}")
                return _response(result="OK", data=control)
            else:
                return _response(result="Error", message="Error: Key not found in control")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


def _post_app_data_admin(settings, type, request):
    if type == "clear_history":
        write_log("Clearing History Log.")
        request_history_clear()
        return _response(result="OK")
    elif type == "clear_events":
        write_log("Clearing Events Log.")
        os.system("rm ./logs/events.log")
        return _response(result="OK")
    elif type == "clear_pelletdb":
        write_log("Clearing Pellet Database.")
        clear_pellet_db()
        return _response(result="OK")
    elif type == "clear_pelletdb_log":
        pelletdb = read_pellets_store()
        pelletdb["log"].clear()
        write_pellet_db(pelletdb)
        write_log("Clearing Pellet Database Log.")
        return _response(result="OK")
    elif type == "factory_defaults":
        flush_history()
        flush_control()
        # This door never reset pellets, not even pre-SQLite -- only the admin
        # page had the `rm pelletdb.json`. Both say "factory defaults", so both
        # do the same thing, through the same clear_pellet_db().
        clear_pellet_db()
        settings = default_settings()
        control = default_control()
        _write_settings(settings, control)
        write_log("Resetting Settings, Control, History and Pellet Database to factory defaults.")
        return _response(result="OK")
    elif type == "reboot":
        write_log("Admin: Reboot")
        try:
            reboot_system()  # Use the improved function from common
        except Exception as e:
            write_log(f"Admin: Reboot failed: {e}")
            # Fallback to original method
            os.system("sleep 3 && sudo reboot &")
        return _response(result="OK")
    elif type == "shutdown":
        write_log("Admin: Shutdown")
        try:
            shutdown_system()  # Use the improved function from common
        except Exception as e:
            write_log(f"Admin: Shutdown failed: {e}")
            # Fallback to original method
            os.system("sleep 3 && sudo shutdown -h now &")
        return _response(result="OK")
    elif type == "restart_control":
        write_log("Admin: Restart Control")
        restart_control()
        return _response(result="OK")
    elif type == "restart_webapp":
        write_log("Admin: Restart WebApp")
        restart_webapp()
        return _response(result="OK")
    elif type == "restart_supervisor":
        write_log("Admin: Restart Supervisor")
        restart_scripts()
        return _response(result="OK")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


def _post_app_data_units(settings, type, request):
    if type == "f_units" and settings["globals"]["units"] == "C":
        settings = convert_settings_units("F", settings)
        control = read_control()
        _write_settings(settings, control)
        control["updated"] = True
        control["units_change"] = True
        enqueue_control_delta(control_delta(set_values={"updated": True, "units_change": True}), origin="app-socketio")
        write_log("Changed units to Fahrenheit")
        return _response(result="OK", data=settings)
    elif type == "c_units" and settings["globals"]["units"] == "F":
        settings = convert_settings_units("C", settings)
        control = read_control()
        _write_settings(settings, control)
        control["updated"] = True
        control["units_change"] = True
        enqueue_control_delta(control_delta(set_values={"updated": True, "units_change": True}), origin="app-socketio")
        write_log("Changed units to Celsius")
        return _response(result="OK", data=settings)
    else:
        return _response(result="Error", message="Error: Units could not be changed")


def _post_app_data_pellets(settings, type, request):
    pelletdb = read_pellets_store()
    return dispatch_pellet_action(
        pelletdb,
        type,
        request["pellets_action"],
        invalid_action_message="Error: Received request without valid type",
    )


def _post_app_data_timer(settings, type, request):
    control = read_control()
    index = None
    for i, notify_obj in enumerate(control["notify_data"]):
        if notify_obj["type"] == "timer":
            index = i
            break
    if index is None:
        return _response(result="Error", message="Error: No timer entry found")
    # This handler is the second, independent implementation of the same timer
    # grammar common/api_commands.py::_cmd_set_timer serves. Both now emit the
    # SAME ops (common/control_delta.py), so the two doors can no longer drift
    # and two timer gestures in one control cycle compose instead of racing.
    # The `index` lookup above survives only for its no-timer-entry guard; the
    # ops locate the entry by type themselves.
    if type == "start_timer":
        if control["timer"]["paused"] == 0:
            # The ranges are required for a FRESH start, and that answer is a
            # request-time one (it is in the payload, not in control state).
            if "hours_range" in request["timer_action"] and "minutes_range" in request["timer_action"]:
                now = time.time()
                seconds = request["timer_action"]["hours_range"] * 60 * 60
                seconds = seconds + request["timer_action"]["minutes_range"] * 60
                write_log("Timer started.  Ends at: " + epoch_to_time(now + seconds))
                enqueue_control_delta(
                    control_delta(
                        ops=[
                            {
                                "op": "timer.start_with_options",
                                "at": now,
                                "seconds": seconds,
                                "shutdown": request["timer_action"]["timer_shutdown"],
                                "keep_warm": request["timer_action"]["timer_keep_warm"],
                            }
                        ]
                    ),
                    origin="app-socketio",
                )
                return _response(result="OK")
            else:
                return _response(result="Error", message="Error: Start time not specified")
        else:
            # Unpause. As before, the ranges are ignored on this path; the drain
            # picks resume-vs-fresh-start from live state.
            now = time.time()
            write_log(
                "Timer unpaused.  Ends at: "
                + epoch_to_time((control["timer"]["end"] - control["timer"]["paused"]) + now)
            )
            enqueue_control_delta(
                control_delta(ops=[{"op": "timer.start_or_resume", "at": now, "seconds": None}]), origin="app-socketio"
            )
            return _response(result="OK")
    elif type == "pause_timer":
        write_log("Timer paused.")
        enqueue_control_delta(control_delta(ops=[{"op": "timer.pause", "at": time.time()}]), origin="app-socketio")
        return _response(result="OK")
    elif type == "stop_timer":
        write_log("Timer stopped.")
        enqueue_control_delta(control_delta(ops=[{"op": "timer.clear"}]), origin="app-socketio")
        return _response(result="OK")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


def _post_app_data_recipes(settings, type, request):
    if type == "recipe_delete":
        if request["recipes_action"]["filename"]:
            filename = request["recipes_action"]["filename"]
            # Guard against command injection / path traversal: only delete a
            # bare filename that resolves to a real file directly inside
            # recipe_folder. Mirrors blueprints/recipes/routes.py's
            # _recipes_json_deletefile, this handler's already-hardened HTTP
            # sibling.
            safe_name = secure_filename(filename)
            filepath = os.path.join(recipe_folder, safe_name)
            if safe_name and os.path.isfile(filepath):
                os.remove(filepath)
            return _response(result="OK")
    elif type == "recipe_start":
        if request["recipes_action"]["filename"]:
            filename = request["recipes_action"]["filename"]
            # recipe.filename is stated as a nested `set`, which deep-merges --
            # step/step_data are the control loop's and are not touched here.
            enqueue_control_delta(
                control_delta(
                    set_values={
                        "updated": True,
                        "mode": Mode.RECIPE,
                        "recipe": {"filename": recipe_folder + filename},
                    }
                ),
                origin="app-socketio",
            )
            return _response(result="OK")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


def _post_app_data_probes(settings, type, request):
    if type == "probe_update":
        if all(v in ("name", "label", "profile_id", "enabled") for v in request["probes_action"].keys()):
            control = read_control()
            return _update_probe_config(settings, control, request)
        else:
            return _response(result="Error", message="Error: Missing required argument, probe cannot be updated")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


def _post_app_data_notify(settings, type, request):
    if type == "notify_update":
        if "label" in request["notify_action"].keys():
            control = read_control()
            return _update_notify_data(control, request)
        else:
            return _response(result="Error", message="Error: Request missing probe label")
    else:
        return _response(result="Error", message="Error: Received request without valid type")


_POST_APP_DATA_DISPATCH = {
    "update_action": _post_app_data_update,
    "admin_action": _post_app_data_admin,
    "units_action": _post_app_data_units,
    "pellets_action": _post_app_data_pellets,
    "timer_action": _post_app_data_timer,
    "recipes_action": _post_app_data_recipes,
    "probes_action": _post_app_data_probes,
    "notify_action": _post_app_data_notify,
}


# pellets_action/timer_action/recipes_action/probes_action/notify_action
# subscript request["..._action"] unconditionally (not via .get()), so an
# empty dict would still raise KeyError. update_action/admin_action/
# units_action either don't touch `request` at all or only iterate its
# keys, so an empty dict degrades gracefully for them (see the pinned
# "empty settings/control request returns None" tests below).
_ACTIONS_REQUIRING_JSON_DATA = {
    "pellets_action",
    "timer_action",
    "recipes_action",
    "probes_action",
    "notify_action",
}


def _post_app_data(action=None, type=None, json_data=None):
    settings = read_settings_store()

    handler = _POST_APP_DATA_DISPATCH.get(action)
    if handler is None:
        return _response(result="Error", message="Error: Received request without valid action")

    if json_data is not None:
        request = json.loads(json_data)
    elif action in _ACTIONS_REQUIRING_JSON_DATA:
        # Bail out with the same Error envelope style used everywhere else
        # in this module, before ever calling a handler that would crash
        # trying to subscript a missing key.
        return _response(result="Error", message="Error: Received request without JSON data")
    else:
        request = {}

    try:
        return handler(settings, type, request)
    except SettingsValidationError as exc:
        # Single choke point for every _write_settings()/
        # save_settings_and_flag_update() call reachable from this dispatcher:
        # the settings tree failed strict validation and was NOT
        # persisted. Same {"result": "Error", "message": ...} envelope every
        # other failure path in this module already returns -- no crash back
        # to the socket.io client.
        return _response(result="Error", message="Error: Settings update rejected: " + "; ".join(exc.errors))


def _get_probe_data(probe_type, settings, current, probe_device_info, notify_data):
    probe_list = []
    # Read against the wall clock rather than current["TS"]: if the control
    # process stops writing, TS freezes and every age would freeze with it,
    # reporting a stale reading as fresh for as long as the outage lasts.
    now_ms = int(time.time() * 1000)

    # Determine section based on probe type
    if probe_type == "Primary":
        section = "P"
    elif probe_type == "Food":
        section = "F"
    else:
        section = "AUX"

    for probe in settings["probe_settings"]["probe_map"]["probe_info"]:
        if probe["type"] == probe_type and probe["enabled"] == True:
            probe_data = _get_probe_structure(probe_type, settings)
            probe_data["title"] = probe["name"]
            probe_data["label"] = probe["label"]
            probe_data["temp"] = current[section][probe["label"]]
            probe_data["device"] = probe["device"]
            # Both are published whether or not `temp` is None, so a client
            # never has to remember a previous frame to know how old a reading
            # is; while the probe is reporting they simply agree with `temp`.
            last = current.get("LAST", {}).get(probe["label"])
            if last is not None:
                probe_data["status"]["lastTemp"] = last["temp"]
                probe_data["status"]["lastReadingAge"] = max(0, int((now_ms - last["ts"]) / 1000))
            if probe_type == "Primary":
                probe_data["setTemp"] = current["PSP"]
            probe_list.append(probe_data)
    for probe in probe_list:
        for index, notify_obj in enumerate(notify_data):
            if notify_data[index]["label"] == probe["label"]:
                if notify_obj["type"] == "probe":
                    probe["eta"] = notify_obj["eta"]
                    probe["target"] = notify_obj["target"]
                    probe["targetShutdown"] = notify_obj["shutdown"]
                    probe["targetKeepWarm"] = notify_obj["keep_warm"]
                    probe["targetReq"] = notify_obj["req"]
                    if notify_obj["req"]:
                        probe["hasNotifications"] = True
                if notify_obj["type"] == "probe_limit_high":
                    probe["highLimitTemp"] = notify_obj["target"]
                    probe["highLimitReq"] = notify_obj["req"]
                    probe["highLimitShutdown"] = notify_obj["shutdown"]
                    probe["highLimitTriggered"] = notify_obj["triggered"]
                    if notify_obj["req"]:
                        probe["hasNotifications"] = True
                if notify_obj["type"] == "probe_limit_low":
                    probe["lowLimitTemp"] = notify_obj["target"]
                    probe["lowLimitReq"] = notify_obj["req"]
                    probe["lowLimitShutdown"] = notify_obj["shutdown"]
                    probe["lowLimitReignite"] = notify_obj["reignite"]
                    probe["lowLimitTriggered"] = notify_obj["triggered"]
                    if notify_obj["req"]:
                        probe["hasNotifications"] = True
        for device in probe_device_info:
            if device["device"] == probe["device"]:
                status = device.get("status", {})
                if "battery_charging" in status:
                    probe["status"]["batteryCharging"] = status["battery_charging"]
                if "battery_percentage" in status:
                    probe["status"]["batteryPercentage"] = status["battery_percentage"]
                if "battery_voltage" in status:
                    probe["status"]["batteryVoltage"] = status["battery_voltage"]
                if "connected" in status:
                    probe["status"]["connected"] = status["connected"]
                if "error" in status:
                    probe["status"]["error"] = status["error"]

    return probe_list


def _get_probe_structure(probe_type, settings):
    return {
        "title": "Probe",
        "label": "probe",
        "eta": 0,
        "temp": 0,
        "setTemp": 0,
        "maxTemp": _get_probe_max_temp(probe_type, settings),
        "target": 0,
        "lowLimitTemp": 0,
        "highLimitTemp": 0,
        "targetReq": False,
        "hasNotifications": False,
        "lowLimitReq": False,
        "highLimitReq": False,
        "highLimitShutdown": False,
        "highLimitTriggered": False,
        "lowLimitShutdown": False,
        "lowLimitReignite": False,
        "lowLimitTriggered": False,
        "targetShutdown": False,
        "targetKeepWarm": False,
        "status": {},
    }


def _get_probe_max_temp(probe_type, settings):
    config = settings["dashboard"]["dashboards"]["Default"]["config"]
    units = settings["globals"]["units"]
    if units == "F":
        if probe_type == "Primary":
            return config["max_primary_temp_F"]
        else:
            return config["max_food_temp_F"]
    else:
        if probe_type == "Primary":
            return config["max_primary_temp_C"]
        else:
            return config["max_food_temp_C"]


def _get_timer_notify_data(notify_data):
    timer_info = {"keep_warm": False, "shutdown": False}
    for index, notify_obj in enumerate(notify_data):
        if notify_obj["type"] == "timer":
            timer_info["keep_warm"] = notify_obj["keep_warm"]
            timer_info["shutdown"] = notify_obj["shutdown"]
    return timer_info


def _encode_assets(recipe_data):
    img_size = ["full", "thumb"]
    recipe_id = recipe_data["metadata"]["id"]
    for size in img_size:
        try:
            for asset in recipe_data["assets"]:
                if size == "full":
                    asset["encoded_image"] = _encode_img(recipe_id, asset["filename"])
                else:
                    asset["encoded_thumb"] = _encode_img(recipe_id, asset["filename"], True)
        except KeyError:
            continue
    return recipe_data


def _encode_img(recipe_id, asset_filename, thumb=False):
    filepath = f"./static/img/tmp/{recipe_id}/thumbs/" if thumb else f"./static/img/tmp/{recipe_id}/"
    try:
        with open(filepath + asset_filename, "rb") as img:
            buffer = img.read()
            asset_img = b64encode(buffer).decode("utf-8")
    except:
        asset_img = ""
    return asset_img


def _update_probe_config(settings, control, request):
    probe_config = request["probes_action"]
    probe_dto = probe_config

    settings, control, result = update_probe_config(settings, control, probe_dto)

    if result == "success":
        control["settings_update"] = True
        # update_probe_config (common/app.py) also raises probe_profile_update.
        # It used to ride along on the whole-dict write; now it has to be named.
        save_settings_and_flag_update(
            settings, control, "settings_update", "probe_profile_update", origin="app-socketio"
        )

        return _response(result="OK", data=settings)
    else:
        return _response(result="Error", message="Error: Probe was not found")


#: The notify_action DTO addresses ONE probe label and carries a flat bag of
#: per-type fields; this is that flattening written down once. Each entry type
#: maps to the DTO key whose PRESENCE means "set this entry" and to the
#: notify_data field each DTO key feeds.
_NOTIFY_DTO_FIELDS = {
    "probe": (
        "target_temp",
        {
            "target": "target_temp",
            "shutdown": "target_shutdown",
            "keep_warm": "target_keep_warm",
            "req": "target_req",
        },
    ),
    "probe_limit_high": (
        "high_limit_temp",
        {"target": "high_limit_temp", "shutdown": "high_limit_shutdown", "req": "high_limit_req"},
    ),
    "probe_limit_low": (
        "low_limit_temp",
        {
            "target": "low_limit_temp",
            "shutdown": "low_limit_shutdown",
            "reignite": "low_limit_reignite",
            "req": "low_limit_req",
        },
    ),
}

#: What each field becomes when the DTO omits its type's temp key -- the app
#: says "this alert is off" by leaving the temperature out.
_NOTIFY_CLEARED = {"target": 0, "shutdown": False, "keep_warm": False, "reignite": False, "req": False}


def _notify_fields_from_dto(entry_type, notify_dto):
    """The fields ONE notify_data entry of `entry_type` takes from the DTO.

    A missing companion key (target_temp without target_shutdown) raises
    KeyError, as it always has: the app sends the group or none of it, and a
    default here would silently disarm a shutdown the user asked for.
    """
    temp_key, dto_keys = _NOTIFY_DTO_FIELDS[entry_type]
    if temp_key not in notify_dto:
        return {field: _NOTIFY_CLEARED[field] for field in dto_keys}
    fields = {field: notify_dto[dto_key] for field, dto_key in dto_keys.items()}
    fields["target"] = int(fields["target"])
    return fields


def _update_notify_data(control, request):
    notify_dto = request["notify_action"]
    label = notify_dto["label"]
    # One notify.set per entry this DTO addresses, NOT a replace of the whole
    # array. The DTO names a single label, so every other entry -- the other
    # probes, the timer, the hopper -- is untouched, and a concurrent writer
    # that armed one of them inside this control cycle keeps its write. The
    # live read decides WHICH entries exist: notify.set appends a missing one,
    # and this handler must not conjure a limit alert the probe never had.
    ops = [
        {
            "op": "notify.set",
            "label": label,
            "type": entry["type"],
            "fields": _notify_fields_from_dto(entry["type"], notify_dto),
        }
        for entry in control["notify_data"]
        if entry["label"] == label and entry["type"] in _NOTIFY_DTO_FIELDS
    ]
    enqueue_control_delta(control_delta(ops=ops or None), origin="app-socketio")
    return _response(result="OK")


def _write_settings(settings, control):
    save_settings_and_flag_update(settings, control, "settings_update", origin="app-socketio")


def _check_control_status():
    """Record whether the control process's heartbeat is still fresh.

    The verdict is kept in `_control_alive` -- process-local, in memory -- and
    NOT written to the errors blob. See that global's comment for why.

    This used to push a "check_alive" system command and wait for the control
    process to answer it. That shape is self-defeating for a liveness check: a
    stopped process cannot answer, so the down case -- the one the check exists
    for -- always cost the caller a full get_system_command_output() timeout.
    Being expensive, it could only run every 30 seconds, which made BOTH
    detection and recovery take up to half a minute; the recovery half is what
    users notice, because a control process that has come back stays reported
    as down until the next probe lands.

    Reading a stamp the control loop already refreshes as it goes needs no
    cooperation from a process that may be gone, costs one SELECT, and lets
    this run every second. A restarted control process stamps on its first
    tick, so recovery is now immediate rather than eventual.
    """
    heartbeat = read_control_heartbeat()
    if heartbeat is None:
        # Never stamped: either a fresh datastore whose control process has not
        # completed a tick yet, or a control process too old to publish one.
        # Stay optimistic -- the same reason _control_alive starts True -- so an
        # upgrade in progress does not flash a control-down banner.
        return
    _set_control_alive((time.time() - heartbeat) < CONTROL_HEARTBEAT_STALE_AFTER)


# `_response` relocated to `common/app.py` as `api_response`.
# Kept as a thin local alias so the 67 call sites in this module don't churn.
_response = api_response


def _get_system_info(control):
    system_info, _ = gather_system_info(control, origin="app-socketio")

    info_details = {
        "wifi_quality_value": control["system"]["wifi_quality_value"],
        "wifi_quality_max": control["system"]["wifi_quality_max"],
        "wifi_quality_percentage": control["system"]["wifi_quality_percentage"],
        "cpu_throttled": control["system"]["cpu_throttled"],
        "cpu_under_voltage": control["system"]["cpu_under_voltage"],
        "cpu_temp": control["system"]["cpu_temp"],
        "network_info": system_info["network_info"],
        "hardware_info": system_info["hardware_info"],
        "os_info": system_info["os_info"],
        "uptime": system_info["uptime"],
    }

    return info_details
