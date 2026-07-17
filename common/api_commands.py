"""
==============================================================================
 PiFire API Commands
==============================================================================

Description: The /api command processor -- process_command() and the
  per-command handlers it dispatches to.

  Extracted from common/common.py; common/common.py re-imports these names
  for now so that existing `common.common.X` call sites keep resolving.

==============================================================================
"""

import json
import time

from common.common import MODE_MAP, WriteKind, convert_settings_units, epoch_to_time, is_float, write_log
from common.datastore_accessors import (
    read_control,
    read_current,
    read_pellet_db,
    read_settings,
    read_status,
    write_control,
    write_settings,
)
from common.sqlite_queue import SqliteQueue
from common.system import reboot_system, restart_scripts, shutdown_system


def _manual_toggle(control, pin_name, arglist, reset_pwm_when_off=False):
    """
    Apply a manual on/off/toggle action to a single manual-output pin
    (power, igniter, fan, or auger) within the 'set'/'manual' command.

    Mirrors the per-pin blocks that used to be inlined in process_command:
      - reads/writes control["manual"]["change"] and ["output"] for `pin_name`
      - a "toggle" request resolves against the live status pin state
      - `reset_pwm_when_off=True` additionally resets control["manual"]["pwm"]
        to 100 when the output is turned off (this only applied to the
        original "fan" branch; do not enable it for the others).
    """
    control["manual"]["change"] = pin_name
    if arglist[2] == "toggle":
        status = read_status()
        if status["outpins"][pin_name]:
            arglist[2] = "false"
        else:
            arglist[2] = "true"
    if arglist[2] == "true":
        control["manual"]["output"] = True
    else:
        control["manual"]["output"] = False
        if reset_pwm_when_off:
            control["manual"]["pwm"] = 100
    return control


def _cmd_get_uuid(data, control, settings, arglist, origin, kind):
    """
    Get Server Uuid
    /api/get/uuid

    Returns:
    {
        'uuid' : <Server Uuid>
    }
    """
    data["data"]["uuid"] = settings["server_info"]["uuid"]


def _cmd_get_versions(data, control, settings, arglist, origin, kind):
    """
    Get Server Versions
    /api/get/versions

    Returns:
    {
        'version' : <Server version>,
        'build' : <Server build>
    }
    """
    data["data"]["version"] = settings["versions"]["server"]
    data["data"]["build"] = settings["versions"]["build"]


def _cmd_get_hopper(data, control, settings, arglist, origin, kind):
    """
    Get Hopper Level
    /api/get/hopper

    Returns:
    {
        'hopper' : <level>
    }
    """
    control["hopper_check"] = True
    write_control(control, kind, origin=origin)
    time.sleep(3)
    pelletdb = read_pellet_db()
    data["data"]["hopper"] = pelletdb["current"]["hopper_level"]


def _cmd_get_timer(data, control, settings, arglist, origin, kind):
    """
    Get Timer Data
    /api/get/timer

    Returns:
    {
        'start' : control['timer']['start'],
        'paused' : control['timer']['paused'],
        'end' : control['timer']['end'],
        'shutdown' : control['notify_data'][]['shutdown'],
        'keep_warm' : control['notify_data'][]['keep_warm'],
    }
    """
    data["data"]["start"] = control["timer"]["start"]
    data["data"]["paused"] = control["timer"]["paused"]
    data["data"]["end"] = control["timer"]["end"]
    """ Get index of timer object """
    for index, notify_obj in enumerate(control["notify_data"]):
        if notify_obj["type"] == "timer":
            break
    data["data"]["shutdown"] = control["notify_data"][index]["shutdown"]
    data["data"]["keep_warm"] = control["notify_data"][index]["keep_warm"]


def _cmd_get_notify(data, control, settings, arglist, origin, kind):
    """
    Get Notify Data
    /api/get/notify

    Returns:
        [
            {
            "eta": null,
            "keep_warm": false,
            "label": "Grill",
            "name": "GrillMain",
            "req": false,
            "shutdown": false,
            "target": 0,
            "type": "probe"
            },
            ...
            {
            "keep_warm": false,
            "label": "Hopper",
            "last_check": 0,
            "req": true,
            "shutdown": false,
            "type": "hopper"
            }
        ]
    """
    data["data"] = control["notify_data"]


def _cmd_get_status(data, control, settings, arglist, origin, kind):
    """
    Get Status Information for Key Items
    /api/get/status

    Returns (Example):
    {
        "display_mode": "Stop",
        "lid_open_detected": false,
        "lid_open_endtime": 0,
        "mode": "Stop",
        "name": "Development",
        "outpins": {
            "auger": false,
            "fan": false,
            "igniter": false,
            "power": false
        },
        "p_mode": 0,
        "prime_amount": 0,
        "prime_duration": 0,
        "s_plus": false,
        "shutdown_duration": 10,
        "start_duration": 30,
        "start_time": 0,
        "startup_timestamp": 0,
        "status": "",
        "ui_hash": 5734093427135650890,
        "units": "F"
    }
    """
    status = read_status()

    data["data"]["mode"] = control["mode"]
    data["data"]["display_mode"] = status["mode"]
    data["data"]["status"] = control["status"]
    data["data"]["s_plus"] = control["s_plus"]
    data["data"]["units"] = settings["globals"]["units"]
    data["data"]["name"] = settings["globals"]["grill_name"]
    data["data"]["start_time"] = status["start_time"]
    data["data"]["start_duration"] = status["start_duration"]
    data["data"]["shutdown_duration"] = status["shutdown_duration"]
    data["data"]["prime_duration"] = status["prime_duration"]
    data["data"]["prime_amount"] = status["prime_amount"]
    data["data"]["lid_open_detected"] = status["lid_open_detected"]
    data["data"]["lid_open_endtime"] = status["lid_open_endtime"]
    data["data"]["p_mode"] = status["p_mode"]
    data["data"]["outpins"] = status["outpins"]
    data["data"]["startup_timestamp"] = status["startup_timestamp"]
    data["data"]["ui_hash"] = hash(json.dumps(settings["probe_settings"]["probe_map"]["probe_info"]))


def _cmd_get_temp(data, control, settings, arglist, origin, kind):
    """
    Get Temperature
    /api/get/temp/{probe label}

    Returns:
    {
        'temp' : <probe temperature>
        'result' : 'OK'
    }
    """
    current_temps = read_current()

    if arglist[1] in current_temps["P"].keys():
        data["data"]["temp"] = current_temps["P"][arglist[1]]
    elif arglist[1] in current_temps["F"].keys():
        data["data"]["temp"] = current_temps["F"][arglist[1]]
    elif arglist[1] in current_temps["AUX"].keys():
        data["data"]["temp"] = current_temps["AUX"][arglist[1]]
    else:
        data["result"] = "ERROR"
        data["message"] = f"Probe {arglist[1]} not found or not specified."


def _cmd_get_current(data, control, settings, arglist, origin, kind):
    """
    Get Current Temp Data Structure
    /api/get/current

    Returns (Example):
    {
        "AUX": {},
        "F": {
            "Probe1": 204,
            "Probe2": 206
        },
        "NT": {
            "Grill": 0,
            "Probe1": 0,
            "Probe2": 0
        },
        "P": {
            "Grill": 518
        },
        "PSP": 0,
        "TS": 1707345482984
    }
    """
    current_temps = read_current()

    data["data"] = current_temps


def _cmd_get_mode(data, control, settings, arglist, origin, kind):
    """
    Get Current Mode
    /api/get/mode

    Returns:
    {
        'mode' : <Current Mode>
    }
    """
    data["data"]["mode"] = control["mode"]


def _cmd_set_psp(data, control, settings, arglist, origin, kind):
    """
    Primary Setpoint
    /api/set/psp/{integer/float temperature}
    """
    if is_float(arglist[1]):
        control["mode"] = "Hold"
        if settings["globals"]["units"] == "F":
            control["primary_setpoint"] = int(float(arglist[1]))
        else:
            control["primary_setpoint"] = float(arglist[1])
        control["updated"] = True
        write_control(control, kind, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Primary set point should be an integer or float in degrees {settings['globals']['units']}"


def _cmd_set_units(data, control, settings, arglist, origin, kind):
    """
    Units
    /api/set/units/{C/F}
    """
    if arglist[1] in ["C", "F"]:
        settings = convert_settings_units(arglist[1], settings)
        write_settings(settings)
        control["settings_update"] = True
        write_control(control, kind, origin=origin)
        control["updated"] = True
        control["units_change"] = True
        write_control(control, kind, origin=origin)
        # print(f'Settings Units Changed to {arglist[1]}')
    else:
        data["result"] = "ERROR"
        data["message"] = f"Set Units {arglist[1]} not recognized."


def _cmd_set_mode(data, control, settings, arglist, origin, kind):
    """
    Mode
    /api/set/mode/{mode} where mode = 'startup', 'smoke', 'shutdown', 'stop', 'reignite', 'monitor', 'error'
    /api/set/mode/prime/{prime amount in grams}[/{next mode}]
    /api/set/mode/hold/{integer/float temperature}
    """
    if arglist[1] in ["startup", "smoke", "shutdown", "stop", "reignite", "monitor", "error", "manual"]:
        control["mode"] = MODE_MAP[arglist[1]]
        control["updated"] = True
        write_control(control, kind, origin=origin)
    elif arglist[1] == "prime":
        try:
            if arglist[2] is not None:
                if arglist[2].isdigit():
                    control["mode"] = MODE_MAP[arglist[1]]
                    control["prime_amount"] = int(arglist[2])
                    control["updated"] = True
                    if arglist[3] in ["startup", "monitor"]:
                        control["next_mode"] = MODE_MAP[arglist[3]]
                    else:
                        control["next_mode"] = "Stop"
                    write_control(control, kind, origin=origin)
                else:
                    data["result"] = "ERROR"
                    data["message"] = f"Prime amount should be an integer in grams."
            else:
                data["result"] = "ERROR"
                data["message"] = f"Prime amount not specified."
        except:
            data["result"] = "ERROR"
            data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} caused an exception."
    elif arglist[1] == "hold":
        if arglist[2] is not None:
            if is_float(arglist[2]):
                control["mode"] = MODE_MAP[arglist[1]]
                if settings["globals"]["units"] == "F":
                    control["primary_setpoint"] = int(float(arglist[2]))
                else:
                    control["primary_setpoint"] = float(arglist[2])
                control["updated"] = True
                write_control(control, kind, origin=origin)
            else:
                data["result"] = "ERROR"
                data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} failed [not a number]."
        else:
            data["result"] = "ERROR"
            data["message"] = f"Set Mode {arglist[1]} with {arglist[2]} failed [no hold temp specified]."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Get API Argument: {arglist[2]} not recognized."


def _cmd_set_pmode(data, control, settings, arglist, origin, kind):
    """
    PMode
    /api/set/pmode/{pmode value} where pmode value is between 0-9

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Preserved.
    """
    if arglist[1] is not None:
        if arglist[1].isdigit():
            if int(arglist[1]) >= 0 and int(arglist[1]) < 10:
                settings["cycle_data"]["PMode"] = int(arglist[1])
                write_settings(settings)
                control["settings_update"] = True
                write_control(control, WriteKind.MERGE, origin=origin)
            else:
                data["result"] = "ERROR"
                data["message"] = f"Set PMode out of range(0-9): {arglist[1]}"
        else:
            data["result"] = "ERROR"
            data["message"] = f"Set PMode invalid value."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Set PMode invalid arguments."


def _cmd_set_splus(data, control, settings, arglist, origin, kind):
    """
    Smoke Plus
    /api/set/splus/{true/false}
    """
    if arglist[1] == "true":
        control["s_plus"] = True
    else:
        control["s_plus"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_lid_open(data, control, settings, arglist, origin, kind):
    """
    Lid Open Toggle
    /api/set/lid_open/toggle

    NOTE: both branches of the if/else set lid_open_toggle to True, so no value
    can clear it. Preserved as-is.
    """
    if arglist[1] == "toggle":
        control["lid_open_toggle"] = True
    else:
        control["lid_open_toggle"] = True

    write_control(control, kind, origin=origin)


def _cmd_set_notify(data, control, settings, arglist, origin, kind):
    """
    Notify Settings
    /api/set/[notify:limit_high:limit_low]/{object}/ where object = probe label, 'Timer', 'Hopper'

    /api/set/notify/{object}/req/{true/false}
    /api/set/notify/{object}/target/{value}  (not valid for Timer or Hopper)
    /api/set/notify/{object}/shutdown/{true/false}
    /api/set/notify/{object}/keep_warm/{true/false}

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Also, the
    'target' path under units == 'C' writes control['primary_setpoint'] rather
    than the notify object's target. Both preserved as-is.
    """
    if arglist[1] is not None:
        if arglist[0] == "limit_high":
            limit = "probe_limit_high"
        elif arglist[0] == "limit_low":
            limit = "probe_limit_low"
        else:
            limit = None
        found = False
        for index, object in enumerate(control["notify_data"]):
            if object["label"] == arglist[1]:
                if limit is not None:
                    if object["type"] == limit:
                        found = True
                        break
                else:
                    found = True
                    break

        if not found:
            data["result"] = "ERROR"
            data["message"] = f"Notify object label {arglist[1]} was not found."
        else:
            # print(f'{object["label"]} FOUND')
            if arglist[2] in ["req", "shutdown", "keep_warm", "reignite"]:
                if arglist[3] == "true":
                    control["notify_data"][index][arglist[2]] = True
                else:
                    control["notify_data"][index][arglist[2]] = False
            elif arglist[2] == "target" and arglist[1] not in ["Timer", "Hopper"]:
                if is_float(arglist[3]):
                    if settings["globals"]["units"] == "F":
                        control["notify_data"][index]["target"] = int(float(arglist[3]))
                    else:
                        control["primary_setpoint"] = float(arglist[3])
                else:
                    data["result"] = "ERROR"
                    data["message"] = f"Notify object target value invalid or missing."
            else:
                data["result"] = "ERROR"
                data["message"] = f"Notify object update failed."
            write_control(control, WriteKind.MERGE, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Notify object label was not specified."


def _cmd_set_pwm(data, control, settings, arglist, origin, kind):
    """
    PWM Control

    /api/set/pwm/{true/false}
    """
    if arglist[1] == "true":
        control["pwm_control"] = True
    else:
        control["pwm_control"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_duty_cycle(data, control, settings, arglist, origin, kind):
    """
    Duty Cycle

    /api/set/duty_cycle/{0-100 percent}

    NOTE: hard-codes WriteKind.MERGE, ignoring the caller's `kind`. Preserved.
    """
    if is_float(arglist[1]):
        duty_cycle = int(arglist[1])
        if duty_cycle >= 0 and duty_cycle <= 100:
            control["duty_cycle"] = duty_cycle
            write_control(control, WriteKind.MERGE, origin=origin)
        else:
            data["result"] = "ERROR"
            data["message"] = f"Duty cycle must be an integer between 0-100."
    else:
        data["result"] = "ERROR"
        data["message"] = f"Duty cycle must be specified as an integer between 0-100 percent."


def _cmd_set_tuning_mode(data, control, settings, arglist, origin, kind):
    """
    Tuning Mode Enable

    /api/set/tuning_mode/{true/false}
    """
    if arglist[1] == "true":
        control["tuning_mode"] = True
    else:
        control["tuning_mode"] = False
    write_control(control, kind, origin=origin)


def _cmd_set_timer(data, control, settings, arglist, origin, kind):
    """
    Timer Control

    /api/set/timer/start/{seconds}
    /api/set/timer/pause
    /api/set/timer/stop
    /api/set/timer/shutdown/{true/false}
    /api/set/timer/keep_warm/{true/false}

    NOTE: the start/pause/stop paths hard-code origin='app', ignoring the
    caller's `origin`; shutdown/keep_warm honor it. Preserved as-is.
    """

    """ Get index of timer object """
    for index, notify_obj in enumerate(control["notify_data"]):
        if notify_obj["type"] == "timer":
            break
    """ Get timestamp """
    now = time.time()

    if arglist[1] == "start":
        control["notify_data"][index]["req"] = True
        # If starting new timer
        if control["timer"]["paused"] == 0:
            control["timer"]["start"] = now
            if is_float(arglist[2]):
                seconds = int(float(arglist[2]))
                control["timer"]["end"] = now + seconds
            else:
                control["timer"]["end"] = now + 60
            write_log("Timer started.  Ends at: " + epoch_to_time(control["timer"]["end"]))
            write_control(control, kind, origin="app")
        else:  # If Timer was paused, restart with new end time.
            control["timer"]["end"] = (control["timer"]["end"] - control["timer"]["paused"]) + now
            control["timer"]["paused"] = 0
            write_log("Timer unpaused.  Ends at: " + epoch_to_time(control["timer"]["end"]))
            write_control(control, kind, origin="app")
    elif arglist[1] == "pause":
        if control["timer"]["start"] != 0:
            control["notify_data"][index]["req"] = False
            control["timer"]["paused"] = now
            write_log("Timer paused.")
            write_control(control, kind, origin="app")
        else:
            control["notify_data"][index]["req"] = False
            control["timer"]["start"] = 0
            control["timer"]["end"] = 0
            control["timer"]["paused"] = 0
            control["notify_data"][index]["shutdown"] = False
            control["notify_data"][index]["keep_warm"] = False
            write_log("Timer cleared.")
            write_control(control, kind, origin="app")
    elif arglist[1] == "stop":
        control["notify_data"][index]["req"] = False
        control["timer"]["start"] = 0
        control["timer"]["end"] = 0
        control["timer"]["paused"] = 0
        control["notify_data"][index]["shutdown"] = False
        control["notify_data"][index]["keep_warm"] = False
        write_log("Timer stopped.")
        write_control(control, kind, origin="app")
    elif arglist[1] == "shutdown":
        if arglist[2] == "true":
            control["notify_data"][index]["shutdown"] = True
        else:
            control["notify_data"][index]["shutdown"] = False
        write_control(control, kind, origin=origin)
    elif arglist[1] == "keep_warm":
        if arglist[2] == "true":
            control["notify_data"][index]["keep_warm"] = True
        else:
            control["notify_data"][index]["keep_warm"] = False
        write_control(control, kind, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Timer command not recognized."


def _cmd_set_manual(data, control, settings, arglist, origin, kind):
    """
    Manual Control
    Note: Must already be in Manual mode (see set/mode command)
    /api/set/manual/power/{true/false/toggle}
    /api/set/manual/igniter/{true/false/toggle}
    /api/set/manual/fan/{true/false/toggle}
    /api/set/manual/auger/{true/false/toggle}
    /api/set/manual/pwm/{speed}

    NOTE: the write_control below is outside the if/elif chain, so a rejected
    (ERROR) request still writes control when control['manual']['change'] holds
    a stale value from a previous command. Preserved as-is.
    """

    if control["mode"] == "Manual" or settings["safety"]["allow_manual_changes"]:
        if arglist[1] == "power":
            control = _manual_toggle(control, "power", arglist)
        elif arglist[1] == "igniter":
            control = _manual_toggle(control, "igniter", arglist)
        elif arglist[1] == "fan":
            control = _manual_toggle(control, "fan", arglist, reset_pwm_when_off=True)
        elif arglist[1] == "auger":
            control = _manual_toggle(control, "auger", arglist)
        elif arglist[1] == "pwm" and is_float(arglist[2]):
            control["manual"]["change"] = "pwm"
            control["manual"]["output"] = True
            control["manual"]["pwm"] = int(float(arglist[2]))
        else:
            data["result"] = "ERROR"
            data["message"] = f"Manual command not recognized or contained an error."
        if control["manual"]["change"] in ["power", "igniter", "fan", "auger", "pwm"]:
            write_control(control, kind, origin=origin)

    else:
        data["result"] = "ERROR"
        data["message"] = f"Before changing manual outputs, system must be put into Manual mode."


def _cmd_cmd_restart(data, control, settings, arglist, origin, kind):
    """
    Restart Scripts
    /api/cmd/restart
    """
    restart_scripts()


def _cmd_cmd_reboot(data, control, settings, arglist, origin, kind):
    """
    Reboot System
    /api/cmd/reboot
    """
    reboot_system()


def _cmd_cmd_shutdown(data, control, settings, arglist, origin, kind):
    """
    Shutdown System
    /api/cmd/shutdown
    """
    shutdown_system()


def _cmd_sys(data, control, settings, arglist, origin, kind):
    """
    System Control Commands

    Unlike get/set/cmd, this action has no subcommand ladder: any arglist is
    pushed to the system queue verbatim. Note that the arglist pushed here is
    the PADDED one, so trailing Nones leak into the queue payload -- e.g.
    ['restart'] is pushed as ['restart', None, None, None]. Preserved as-is.
    """
    system_command_queue = SqliteQueue("queue_systemq")
    system_command_queue.push(arglist)


def _process_command_unknown(data, action, arglist):
    """
    Fallback for a command with no registered handler.

    Reproduces the four distinct error paths of the original if/elif ladder
    exactly. Note the inconsistent formatting, preserved as-is: the `get` path
    brackets the offending argument, while `set` and `cmd` do not.

    `arglist` has already been padded to `max_args`, so `arglist[0]` is always
    subscriptable here and is None when the caller passed no arguments -- the
    same value the original ladder's `else` branches interpolated.
    """
    data["result"] = "ERROR"
    if action == "get":
        data["message"] = f"Get API Argument: [{arglist[0]}] not recognized."
    elif action == "set":
        data["message"] = f"Set API Argument: {arglist[0]} not recognized."
    elif action == "cmd":
        data["message"] = f"CMD API Argument: {arglist[0]} not recognized."
    else:
        data["message"] = f"Action [{action}] not valid/recognized."


_COMMAND_DISPATCH = {
    ("get", "temp"): _cmd_get_temp,
    ("get", "current"): _cmd_get_current,
    ("get", "mode"): _cmd_get_mode,
    ("get", "uuid"): _cmd_get_uuid,
    ("get", "versions"): _cmd_get_versions,
    ("get", "hopper"): _cmd_get_hopper,
    ("get", "timer"): _cmd_get_timer,
    ("get", "notify"): _cmd_get_notify,
    ("get", "status"): _cmd_get_status,
    ("set", "psp"): _cmd_set_psp,
    ("set", "units"): _cmd_set_units,
    ("set", "mode"): _cmd_set_mode,
    ("set", "pmode"): _cmd_set_pmode,
    ("set", "splus"): _cmd_set_splus,
    ("set", "lid_open"): _cmd_set_lid_open,
    ("set", "notify"): _cmd_set_notify,
    ("set", "limit_high"): _cmd_set_notify,
    ("set", "limit_low"): _cmd_set_notify,
    ("set", "pwm"): _cmd_set_pwm,
    ("set", "duty_cycle"): _cmd_set_duty_cycle,
    ("set", "tuning_mode"): _cmd_set_tuning_mode,
    ("set", "timer"): _cmd_set_timer,
    ("set", "manual"): _cmd_set_manual,
    ("cmd", "restart"): _cmd_cmd_restart,
    ("cmd", "reboot"): _cmd_cmd_reboot,
    ("cmd", "shutdown"): _cmd_cmd_shutdown,
}


_ACTION_DISPATCH = {
    "sys": _cmd_sys,
}


def process_command(action=None, arglist=[], origin="unknown", kind=WriteKind.MERGE):
    """
    Process incoming command from API or elsewhere
    """
    data = {}
    data["result"] = "OK"
    data["message"] = "Command was accepted successfully."
    data["data"] = {}

    control = read_control()
    settings = read_settings()

    """ Populate any empty args with None just in case """
    num_args = len(arglist)
    max_args = 4  # Needs updating if API adds deeper number of arguments

    for _ in range(max_args - num_args):
        arglist.append(None)

    """ Subcommand lookup first, then the action-only table for actions (sys)
        that dispatch on the action alone. """
    handler = _COMMAND_DISPATCH.get((action, arglist[0]))
    if handler is None:
        handler = _ACTION_DISPATCH.get(action)

    if handler is None:
        _process_command_unknown(data, action, arglist)
    else:
        handler(data, control, settings, arglist, origin, kind)

    return data
