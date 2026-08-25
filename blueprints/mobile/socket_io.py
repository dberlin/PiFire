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
import math
import threading
import time
from base64 import b64encode
from collections.abc import Mapping
from threading import Event

from flask import request
from pydantic import ValidationError

from app import socketio
from common.app import (
    CONTROL_DOWN_ERROR,
    api_response,
    create_ui_hash,
    save_settings_and_flag_update,
    update_probe_config,
)
from common.common import (
    ErrorKind,
    flush_events_records,
)
from common.control_delta import control_delta
from common.persistence.control import (
    enqueue_control_delta,
    read_control,
)
from common.persistence.runtime import (
    CONTROL_HEARTBEAT_STALE_AFTER,
    flush_connected_users,
    read_connected_users,
    read_control_heartbeat,
    read_current,
    read_errors,
    read_generic_key,
    read_pellets_store,
    read_settings_store,
    read_status,
    read_warnings_snapshot,
    remove_connected_user,
    seed_pellets_store,
    seed_settings_store,
    write_connected_user,
)
from common.system import (
    gather_system_info,
)
from common.web_contracts.core import (
    DashSocketPayload,
    PelletSocketPayload,
    ThermocoupleHealthView,
    project_thermocouple_health_outcome,
)
from config import Config
from controller.learning_report import controller_learning_report_revision

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


"""
==============================================================================
 Supporting Functions
==============================================================================
"""


def _emit_app_data(event, force_refresh):
    global thread

    previous_dash = ""
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

            pellet_data = _get_pellet_socket_data(settings, pelletdb)

            dash_data = _get_dash_data(settings, pelletdb)

            if force_refresh:
                socketio.emit("socket_pellet_data", pellet_data)
                socketio.emit("socket_dash_data", dash_data)
                force_refresh = False
            else:
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

    socketio.emit("socket_pellet_data", _get_pellet_socket_data(settings, pelletdb), to=client_id)
    socketio.emit("socket_dash_data", _get_dash_data(settings, pelletdb), to=client_id)


def _get_pellet_socket_data(settings, pelletdb):
    return PelletSocketPayload.model_validate(
        {"uuid": settings["server_info"]["uuid"], "pellets": pelletdb},
        strict=True,
    ).model_dump(mode="json", by_alias=True, exclude_none=False)


def _finite_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError, ValueError:
        return None
    return number if math.isfinite(number) else None


def _project_thermocouple_health(
    settings,
    probe_device_info,
    *,
    now=None,
):
    """Build the client health view without mutating persisted producer data."""
    if not isinstance(probe_device_info, list):
        return []
    probe_settings = settings.get("probe_settings")
    if not isinstance(probe_settings, Mapping):
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
    now = _finite_float(now)
    if now is None:
        return []

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
        observed_at = _finite_float(report.get("observed_at"))
        detail = report.get("detail")
        evidence = report.get("evidence")
        if observed_at is None or not isinstance(detail, Mapping) or not isinstance(evidence, list):
            continue
        policy = detail.get("policy")
        if policy not in {"off", "observe", "enforce"}:
            continue

        age_s = max(0.0, now - observed_at)
        has_hardware = "hardware" in evidence
        has_software = any(item != "hardware" for item in evidence)
        source = "mixed" if has_hardware and has_software else "hardware" if has_hardware else "software"

        state = report.get("state")
        temperature_valid = report.get("temperature_valid")
        outcome = project_thermocouple_health_outcome(role, state, evidence, detail)

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
        projected.append(view.model_dump(mode="json", by_alias=True, exclude_none=False))
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
        ),
    }
    return DashSocketPayload.model_validate(dash_data, strict=True).model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )


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
            if notify_obj["label"] == probe["label"]:
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
