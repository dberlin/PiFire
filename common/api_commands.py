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

from common import server_revision
from common.common import (
    MODE_MAP,
    WriteKind,
    convert_settings_units,
    epoch_to_time,
    is_float,
    notify_target_conversion_ops,
    write_log,
)
from common.control_delta import control_delta
from common.modes import Mode
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


def _write_control_delta(control, delta, kind, origin):
    """Queue `delta`, unless the caller explicitly asked for an OVERWRITE.

    `kind` is process_command's escape hatch for a caller that wants its write
    to land NOW rather than on the next drain: WriteKind.OVERWRITE replaces the
    control blob directly. A delta cannot honour that -- it is by construction a
    queued statement of intent -- so an OVERWRITE caller still gets the
    whole-dict write it asked for. That is pinned by the golden's
    `kind_overwrite_splus` (queued_writes == []).

    No PRODUCTION call site passes `kind`; every one of them takes the delta.
    """
    if kind is WriteKind.OVERWRITE:
        write_control(control, kind, origin=origin)
    else:
        write_control(delta, WriteKind.DELTA, origin=origin)


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

    Returns the manual members it ASSIGNED, so the caller can state exactly
    those in a delta. `pwm` is in the result only when it was actually reset:
    naming it unconditionally would let a fan toggle impose a stale pwm on a
    concurrent pwm change, which is the class of bug deltas exist to remove.
    (`control` is still mutated in place, because the write guard below reads
    control["manual"]["change"] back -- including the stale-value wart.)
    """
    assigned = {"change": pin_name}
    if arglist[2] == "toggle":
        status = read_status()
        if status["outpins"][pin_name]:
            arglist[2] = "false"
        else:
            arglist[2] = "true"
    if arglist[2] == "true":
        assigned["output"] = True
    else:
        assigned["output"] = False
        if reset_pwm_when_off:
            assigned["pwm"] = 100
    control["manual"].update(assigned)
    return assigned


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

    These come from the release manifest, so they move on a release and say
    nothing about whether THIS process is running current code. /api/get/revision
    answers that.
    """
    data["data"]["version"] = settings["versions"]["server"]
    data["data"]["build"] = settings["versions"]["build"]


def _cmd_get_revision(data, control, settings, arglist, origin, kind):
    """
    Get the source revision this server process is actually running
    /api/get/revision

    Returns:
    {
        'revision' : <git commit this process imported, or None>,
        'stale' : <True if loaded Python has changed since this process started>,
        'started_at' : <epoch seconds>,
        'newest_source_mtime' : <epoch seconds>
    }

    Separate from /api/get/versions on purpose. Two of these fields are clocks,
    so folding them into that response would make its golden fixture
    (tests/characterization) unpinnable, and would put an mtime walk on a path
    the mobile app polls. See common/server_revision.py.
    """
    data["data"].update(server_revision.status())


def _cmd_get_hopper(data, control, settings, arglist, origin, kind):
    """
    Get Hopper Level
    /api/get/hopper

    Returns:
    {
        'hopper' : <level>
    }

    Answers immediately, from the stored level. The control loop refreshes that
    every HOPPER_LEVEL_REFRESH_INTERVAL seconds (distance/intervals.py), so it
    is never more than a few seconds old.

    This used to raise hopper_check and then `time.sleep(3)` before reading --
    3 seconds being exactly how long the control loop would block forcing a
    fresh measurement, so the answer was ready by the time it read. Neither
    half of that arrangement exists any more: the loop never blocks on a
    sensor, so the sleep no longer buys a fresher reading, it just held a web
    worker hostage for 3 seconds per call. The flag is still raised, so a fresh
    sample is requested for the next refresh and for whoever reads next.
    """
    # A one-shot request flag. Under the old whole-dict write it was one of the
    # two flags (with settings_update) that a concurrent writer's stale snapshot
    # could revert to False before the control loop ever saw it -- i.e. the user
    # action simply never happened. Named, it cannot be.
    write_control(control_delta(set_values={"hopper_check": True}), WriteKind.DELTA, origin=origin)
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
        control["mode"] = Mode.HOLD
        if settings["globals"]["units"] == "F":
            control["primary_setpoint"] = int(float(arglist[1]))
        else:
            control["primary_setpoint"] = float(arglist[1])
        control["updated"] = True
        _write_control_delta(
            control,
            control_delta(
                set_values={
                    "mode": Mode.HOLD,
                    "primary_setpoint": control["primary_setpoint"],
                    "updated": True,
                }
            ),
            kind,
            origin,
        )
    else:
        data["result"] = "ERROR"
        data["message"] = f"Primary set point should be an integer or float in degrees {settings['globals']['units']}"


def _cmd_set_units(data, control, settings, arglist, origin, kind):
    """
    Units
    /api/set/units/{C/F}
    """
    if arglist[1] in ["C", "F"]:
        # Captured before convert_settings_units rewrites settings.globals.units.
        # A REAL change is the gate for the notify conversion below: units_change
        # is raised even on a redundant same-unit write, and convert_temp assumes
        # its input is in the OTHER unit, so converting on a no-op switch would
        # corrupt an already-correct target.
        units_changed = arglist[1] != settings["globals"]["units"]
        settings = convert_settings_units(arglist[1], settings)
        write_settings(settings)
        control["settings_update"] = True
        _write_control_delta(control, control_delta(set_values={"settings_update": True}), kind, origin)
        control["updated"] = True
        control["units_change"] = True
        # The second write states only ITS two members. The old whole-dict form
        # re-sent settings_update as well, which was harmless but was also the
        # shape that let any writer re-impose a flag it never set.
        _write_control_delta(control, control_delta(set_values={"updated": True, "units_change": True}), kind, origin)
        # Notify targets live in control["notify_data"], which convert_settings_units
        # cannot reach, so a 203 F target used to read as 203 after a switch to C.
        # Convert them here as addressed notify.set ops (never a whole-array replace,
        # which would revert a concurrent notify write). Emitted only when something
        # is armed, so a units change on default control adds no delta.
        if units_changed:
            notify_ops = notify_target_conversion_ops(control.get("notify_data", []), arglist[1])
            if notify_ops:
                _write_control_delta(control, control_delta(ops=notify_ops), kind, origin)
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
        _write_control_delta(
            control,
            control_delta(set_values={"mode": MODE_MAP[arglist[1]], "updated": True}),
            kind,
            origin,
        )
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
                    _write_control_delta(
                        control,
                        control_delta(
                            set_values={
                                "mode": control["mode"],
                                "prime_amount": control["prime_amount"],
                                "updated": True,
                                "next_mode": control["next_mode"],
                            }
                        ),
                        kind,
                        origin,
                    )
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
                _write_control_delta(
                    control,
                    control_delta(
                        set_values={
                            "mode": control["mode"],
                            "primary_setpoint": control["primary_setpoint"],
                            "updated": True,
                        }
                    ),
                    kind,
                    origin,
                )
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

    NOTE: always queues, ignoring the caller's `kind` (it hard-coded
    WriteKind.MERGE before the delta conversion). Preserved.
    """
    if arglist[1] is not None:
        if arglist[1].isdigit():
            if int(arglist[1]) >= 0 and int(arglist[1]) < 10:
                settings["cycle_data"]["PMode"] = int(arglist[1])
                write_settings(settings)
                control["settings_update"] = True
                write_control(control_delta(set_values={"settings_update": True}), WriteKind.DELTA, origin=origin)
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
    control["s_plus"] = arglist[1] == "true"
    _write_control_delta(control, control_delta(set_values={"s_plus": control["s_plus"]}), kind, origin)


def _cmd_set_lid_open(data, control, settings, arglist, origin, kind):
    """
    Lid Open Toggle
    /api/set/lid_open/toggle

    NOTE: lid_open_toggle is unconditionally set to True regardless of arglist[1],
    so no value can clear it. Preserved as-is.
    """
    control["lid_open_toggle"] = True

    _write_control_delta(control, control_delta(set_values={"lid_open_toggle": True}), kind, origin)


def _cmd_set_notify(data, control, settings, arglist, origin, kind):
    """
    Notify Settings
    /api/set/[notify:limit_high:limit_low]/{object}/ where object = probe label, 'Timer', 'Hopper'

    /api/set/notify/{object}/req/{true/false}
    /api/set/notify/{object}/target/{value}  (not valid for Timer or Hopper)
    /api/set/notify/{object}/shutdown/{true/false}
    /api/set/notify/{object}/keep_warm/{true/false}

    NOTE: always queues, ignoring the caller's `kind` (it hard-coded
    WriteKind.MERGE before the delta conversion). Preserved
    as-is.
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
            # Only the field this command actually set travels. `fields` stays
            # empty on the two ERROR branches below, which still queue a write
            # (an empty envelope) rather than none: preserving the "one command,
            # one queued write" observable the golden records, while saying
            # honestly that nothing was changed. The old code queued the whole
            # control dict there, which could revert a concurrent writer.
            fields = {}
            if arglist[2] in ["req", "shutdown", "keep_warm", "reignite"]:
                fields[arglist[2]] = arglist[3] == "true"
            elif arglist[2] == "target" and arglist[1] not in ["Timer", "Hopper"]:
                if is_float(arglist[3]):
                    if settings["globals"]["units"] == "F":
                        fields["target"] = int(float(arglist[3]))
                    else:
                        fields["target"] = float(arglist[3])
                else:
                    data["result"] = "ERROR"
                    data["message"] = f"Notify object target value invalid or missing."
            else:
                data["result"] = "ERROR"
                data["message"] = f"Notify object update failed."
            # The entry is addressed by (label, type) read off the entry the
            # loop above MATCHED. Not by a type derived from the subcommand:
            # `notify` matches on label alone, so for the Timer and Hopper
            # labels the matched entry's type is "timer"/"hopper", not "probe".
            entry = control["notify_data"][index]
            ops = (
                [{"op": "notify.set", "label": entry["label"], "type": entry["type"], "fields": fields}]
                if fields
                else None
            )
            write_control(control_delta(ops=ops), WriteKind.DELTA, origin=origin)
    else:
        data["result"] = "ERROR"
        data["message"] = f"Notify object label was not specified."


def _cmd_set_pwm(data, control, settings, arglist, origin, kind):
    """
    PWM Control

    /api/set/pwm/{true/false}
    """
    control["pwm_control"] = arglist[1] == "true"
    _write_control_delta(control, control_delta(set_values={"pwm_control": control["pwm_control"]}), kind, origin)


def _cmd_set_duty_cycle(data, control, settings, arglist, origin, kind):
    """
    Duty Cycle

    /api/set/duty_cycle/{0-100 percent}

    NOTE: always queues, ignoring the caller's `kind` (it hard-coded
    WriteKind.MERGE before the delta conversion). Preserved.
    """
    if is_float(arglist[1]):
        duty_cycle = int(arglist[1])
        if duty_cycle >= 0 and duty_cycle <= 100:
            control["duty_cycle"] = duty_cycle
            write_control(control_delta(set_values={"duty_cycle": duty_cycle}), WriteKind.DELTA, origin=origin)
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
    control["tuning_mode"] = arglist[1] == "true"
    _write_control_delta(control, control_delta(set_values={"tuning_mode": control["tuning_mode"]}), kind, origin)


""" The expiry flags /api/set/timer/start/{seconds}/{options} can name, in the
    order they are emitted back. Both live on the timer's notify_data object. """
_TIMER_EXPIRY_OPTIONS = ("shutdown", "keep_warm")

""" The option segment that names no flag at all. A path segment cannot be
    empty (an empty one collapses the URL back to the 3-argument form, which
    leaves both flags at whatever the previous cook set), so 'neither' has to
    have a spelling of its own. """
_TIMER_EXPIRY_NONE = "none"


def _parse_timer_expiry_options(spec):
    """
    Parse the expiry-option segment of /api/set/timer/start/{seconds}/{options}.

    Accepts 'none', or a comma-separated list of distinct names drawn from
    _TIMER_EXPIRY_OPTIONS in any order -- e.g. 'shutdown', 'keep_warm',
    'shutdown,keep_warm'.

    :return: {'shutdown': bool, 'keep_warm': bool} with EVERY flag stated
             explicitly (an unnamed flag is False, not "leave it alone"), or
             None if the segment is not a valid option list.
    """
    tokens = [token.strip() for token in str(spec).split(",")]
    if tokens == [_TIMER_EXPIRY_NONE]:
        return dict.fromkeys(_TIMER_EXPIRY_OPTIONS, False)
    if len(set(tokens)) != len(tokens):
        return None
    if any(token not in _TIMER_EXPIRY_OPTIONS for token in tokens):
        return None
    return {name: name in tokens for name in _TIMER_EXPIRY_OPTIONS}


def _timer_start_with_options(data, control, arglist, index, now, kind):
    """
    Arm a NEW timer for a DURATION, with both expiry flags.

    /api/set/timer/start/{seconds}/{options}

    The client sends how LONG the timer should run; this function computes the
    absolute end from the server's own clock. That is the whole point of the
    form: the control process decides a timer has expired by comparing
    control.timer.end against its own time.time(), so an end computed on a
    client whose clock runs behind the Pi's arms an already-expired timer -- and
    an expired timer with 'shutdown' set shuts the grill down mid-cook.

    The form's ONE-WRITE rationale is gone, at both ends. It used to be
    load-bearing that both flags and the countdown travelled on one control
    dict: splitting them across requests meant the last write of a control
    cycle silently undid the earlier ones. Timer writers now queue an OP
    evaluated at drain time against live state (common/control_delta.py), so
    two timer gestures in one cycle compose instead of racing and nothing is
    won by bundling them.

    Two independent reasons keep the endpoint:
      * the server clock, above -- nothing about the write seam changes it;
      * the input rejections below (non-numeric / zero / negative duration, and
        a paused timer), which are request-time answers a queue cannot give.

    Deliberately does NOT unpause. The bare `start` form is also the resume
    command and ignores its seconds argument when the timer is paused; doing
    that here would silently discard the duration the caller asked for, which is
    the same "asked for X, got Y" failure this form exists to close. A paused
    timer is rejected: resume it with /api/set/timer/start/{seconds}, or clear
    it with /api/set/timer/stop first.

    Rejections write nothing.
    """
    options = _parse_timer_expiry_options(arglist[3])
    if options is None:
        data["result"] = "ERROR"
        data["message"] = (
            f"Timer expiry options [{arglist[3]}] not recognized. Expected "
            f"'{_TIMER_EXPIRY_NONE}' or a comma-separated list of: {', '.join(_TIMER_EXPIRY_OPTIONS)}."
        )
        return

    """ No silent 60-second substitution here (see the bare `start` form) and no
        zero/negative duration: a timer that is already expired the moment it is
        armed fires its expiry action immediately. """
    seconds = int(float(arglist[2])) if is_float(arglist[2]) else 0
    if seconds <= 0:
        data["result"] = "ERROR"
        data["message"] = f"Timer duration [{arglist[2]}] must be a number of seconds greater than zero."
        return

    if control["timer"]["paused"] != 0:
        data["result"] = "ERROR"
        data["message"] = "Timer is paused. Resume or stop it before starting a new timer."
        return

    write_log("Timer started.  Ends at: " + epoch_to_time(now + seconds))
    write_control(
        control_delta(
            ops=[
                {
                    "op": "timer.start_with_options",
                    "at": now,
                    "seconds": seconds,
                    "shutdown": options["shutdown"],
                    "keep_warm": options["keep_warm"],
                }
            ]
        ),
        WriteKind.DELTA,
        origin="app",
    )


# NOTE: the log line is still computed HERE, from this request's (possibly
# stale) read, while the STATE change is computed in the drain from live state.
# They can disagree: two timer commands in one control cycle can log "Timer
# unpaused" and then correctly take the start branch. That is deliberate --
# moving the logging into the drain would move it into a different PROCESS and
# flip `log_calls` in six golden entries, for a diagnostic line. The drain logs
# the op it actually applied at DEBUG (common/control_delta.py).
def _cmd_set_timer(data, control, settings, arglist, origin, kind):
    """
    Timer Control

    /api/set/timer/start/{seconds}
    /api/set/timer/start/{seconds}/{options}
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

    if arglist[1] == "start" and arglist[3] is not None:
        """ The 4-argument form: server-computed end + both expiry flags. Kept
            separate from the 3-argument form below, which other clients (the
            Flask dashboard, mobile) still use and which doubles as the unpause
            command. """
        _timer_start_with_options(data, control, arglist, index, now, kind)
    elif arglist[1] == "start":
        seconds = int(float(arglist[2])) if is_float(arglist[2]) else None
        # The BRANCH is not decided here. `start` is also the unpause command and
        # which one it is depends on control["timer"]["paused"] -- a value this
        # read_control() cannot see the queue behind. The drain decides, against
        # live state; the clock still comes from here, as `at`.
        if control["timer"]["paused"] == 0:
            write_log("Timer started.  Ends at: " + epoch_to_time(now + (seconds if seconds is not None else 60)))
        else:
            write_log(
                "Timer unpaused.  Ends at: "
                + epoch_to_time((control["timer"]["end"] - control["timer"]["paused"]) + now)
            )
        write_control(
            control_delta(ops=[{"op": "timer.start_or_resume", "at": now, "seconds": seconds}]),
            WriteKind.DELTA,
            origin="app",
        )
    elif arglist[1] == "pause":
        if control["timer"]["start"] != 0:
            write_log("Timer paused.")
        else:
            write_log("Timer cleared.")
        write_control(control_delta(ops=[{"op": "timer.pause", "at": now}]), WriteKind.DELTA, origin="app")
    elif arglist[1] == "stop":
        write_log("Timer stopped.")
        write_control(control_delta(ops=[{"op": "timer.clear"}]), WriteKind.DELTA, origin="app")
    elif arglist[1] in ("shutdown", "keep_warm"):
        write_control(
            control_delta(
                ops=[
                    {
                        "op": "notify.set",
                        "label": control["notify_data"][index]["label"],
                        "type": "timer",
                        "fields": {arglist[1]: arglist[2] == "true"},
                    }
                ]
            ),
            WriteKind.DELTA,
            origin=origin,
        )
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

    if control["mode"] == Mode.MANUAL or settings["safety"]["allow_manual_changes"]:
        assigned = None
        if arglist[1] == "power":
            assigned = _manual_toggle(control, "power", arglist)
        elif arglist[1] == "igniter":
            assigned = _manual_toggle(control, "igniter", arglist)
        elif arglist[1] == "fan":
            assigned = _manual_toggle(control, "fan", arglist, reset_pwm_when_off=True)
        elif arglist[1] == "auger":
            assigned = _manual_toggle(control, "auger", arglist)
        elif arglist[1] == "pwm" and is_float(arglist[2]):
            assigned = {"change": "pwm", "output": True, "pwm": int(float(arglist[2]))}
            control["manual"].update(assigned)
        else:
            data["result"] = "ERROR"
            data["message"] = f"Manual command not recognized or contained an error."
        # The guard is UNCHANGED, wart included: it sits outside the if/elif
        # chain, so a rejected request still writes when control["manual"]
        # ["change"] holds a stale value from a previous command. What it writes
        # is now an envelope naming nothing (`assigned` is None on that path)
        # rather than a whole stale control dict -- so the wart survives as a
        # queued no-op instead of as something that can revert another writer.
        if control["manual"]["change"] in ["power", "igniter", "fan", "auger", "pwm"]:
            _write_control_delta(
                control,
                control_delta(set_values={"manual": assigned} if assigned else None),
                kind,
                origin,
            )

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
    ("get", "revision"): _cmd_get_revision,
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


def process_command(action=None, arglist=None, origin="unknown", kind=WriteKind.MERGE):
    """
    Process incoming command from API or elsewhere
    """
    if arglist is None:
        arglist = []

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
