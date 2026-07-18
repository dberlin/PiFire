#!/usr/bin/env python3

"""
==============================================================================
 PiFire Notifications Module
==============================================================================

Description: This library provides notification functions for
   control.py

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""
import datetime
import time
import requests
import json
import apprise
import logging
import math
from common.common import WriteKind, create_logger
from common.datastore_accessors import (
    write_settings,
    write_control,
    read_history,
    read_settings,
    read_control,
    read_pellet_db,
)

"""
==============================================================================
 Functions
==============================================================================
"""


def check_notify(settings, control, in_data=None, pelletdb=None, grill_platform=None, pid_data=None, update_eta=False):
    """
    Check for any pending notifications

    :param in_data: In Data (Probe Temps)
    :param control: Control
    :param settings: Settings
    :param pelletdb: Pellet DB
    :param grill_platform: Grill Platform
    """
    # Forward to mqtt if enabled.
    if settings["notify_services"].get("mqtt") != None and settings["notify_services"]["mqtt"]["enabled"] == True:
        _send_mqtt_notification(control, settings, pelletdb, in_data, grill_platform, pid_data)

    # If pelletdb or grill_platform is not populated, exit
    if not pelletdb or not grill_platform:
        return

    if settings["notify_services"]["influxdb"]["url"] != "" and settings["notify_services"]["influxdb"]["enabled"]:
        _send_influxdb_notification("GRILL_STATE", control, settings, pelletdb, in_data, grill_platform)

    if settings["notify_services"]["wled"]["device_address"] != "" and settings["notify_services"]["wled"]["enabled"]:
        _send_wled_notification("GRILL_STATE", control, settings)

    """ Get simple list of temperatures key:value pairs """
    probe_temp_list = {}
    if in_data is not None:
        for group in in_data["probe_history"]:
            if group != "tr":
                for probe in in_data["probe_history"][group]:
                    probe_temp_list[probe] = in_data["probe_history"][group][probe]

    """ Process all registered notification items """
    for index, item in enumerate(control["notify_data"]):
        if item["req"]:
            if item["type"] in ["probe", "probe_limit_low", "probe_limit_high"] and in_data is not None:
                # Update the ETA, if requested for any active probe
                if item["type"] == "probe" and update_eta:
                    num_minutes = 20  # Number of minutes of history to grab
                    num_seconds = num_minutes * 60
                    time_interval = 3  # 3-Second Time Intervals
                    # Get temperature history for this probe
                    temperatures = []
                    history = read_history(num_items=(num_seconds // time_interval))
                    for datapoint in history:
                        if item["label"] in datapoint["F"]:
                            temperatures.append(datapoint["F"][item["label"]])
                        elif item["label"] in datapoint["P"]:
                            temperatures.append(datapoint["P"][item["label"]])
                    # Call extrapolate ETA
                    # print(f'DEBUG: ETA: Interpolating {item["name"]}')
                    eta_seconds = _estimate_eta(
                        temperatures, item["target"], interval_seconds=time_interval, max_history_minutes=num_minutes
                    )
                    # Write to control
                    control["notify_data"][index]["eta"] = eta_seconds
                # If target temperature meets the condition, send notification and clear request/data
                if _check_condition(item["condition"], probe_temp_list[item["label"]], item["target"]):
                    if item["type"] == "probe":
                        send_notifications(
                            "Probe_Temp_Achieved", label=item["label"], target=in_data["notify_targets"][item["label"]]
                        )
                        if control["mode"] == "Recipe":
                            if control["recipe"]["step_data"]["trigger_temps"][item["label"]] > 0:
                                control["recipe"]["step_data"]["triggered"] = True
                        control["notify_data"][index]["req"] = False
                        control["notify_data"][index]["target"] = 0
                        control["notify_data"][index]["eta"] = None
                    if item["type"] in ["probe_limit_high", "probe_limit_low"] and not item["triggered"]:
                        send_notifications("Probe_Temp_Limit_Alarm", label=item["label"], target=item["target"])
                        control["notify_data"][index]["triggered"] = True
                elif item["type"] in ["probe_limit_high", "probe_limit_low"] and item["triggered"]:
                    # If the temperature goes back into the right range, reset the 'triggered' flag
                    control["notify_data"][index]["triggered"] = False

            elif item["type"] == "timer":
                if time.time() >= control["timer"]["end"]:
                    send_notifications("Timer_Expired")
                    if control["mode"] == "Recipe":
                        if control["recipe"]["step_data"]["timer"] > 0:
                            control["recipe"]["step_data"]["triggered"] = True
                    control["timer"]["start"] = 0
                    control["timer"]["paused"] = 0
                    control["timer"]["end"] = 0
                    control["notify_data"][index]["req"] = False

            elif item["type"] == "hopper":
                if (time.time() - item["last_check"]) > (settings["pelletlevel"]["warning_time"] * 60):
                    if pelletdb["current"]["hopper_level"] <= settings["pelletlevel"]["warning_level"]:
                        send_notifications("Pellet_Level_Low")
                        control["notify_data"][index]["last_check"] = time.time()

            elif item["type"] == "test":
                send_notifications("Test_Notify")
                control["notify_data"][index]["last_check"] = time.time()
                control["notify_data"][index]["req"] = False

            """ Do Shutdown or Keep Warm if Requested """
            if (
                item["shutdown"]
                and control["mode"] in ("Reignite", "Startup", "Smoke", "Hold")
                and not control["notify_data"][index]["req"]
            ):
                control["mode"] = "Shutdown"
                control["updated"] = True
                control["notify_data"][index]["shutdown"] = False
            elif (
                item["keep_warm"] and control["mode"] in ("Smoke", "Hold") and not control["notify_data"][index]["req"]
            ):
                control["mode"] = "Hold"
                control["primary_setpoint"] = settings["keep_warm"]["temp"]
                control["s_plus"] = settings["keep_warm"]["s_plus"]
                control["updated"] = True
                control["notify_data"][index]["keep_warm"] = False
            elif item.get("reignite", False) and item.get("triggered", False) and control["mode"] in ("Smoke", "Hold"):
                control["safety"]["reignitelaststate"] = control["mode"]
                control["mode"] = "Reignite"
                control["updated"] = True

            write_control(control, WriteKind.OVERWRITE, origin="notifications")

    return control


def _event_context(settings, control, label, target):
    """Assemble the shared runtime values every EVENTS builder may read."""
    date = datetime.datetime.now()
    return {
        "settings": settings,
        "control": control,
        "label": label,
        "target": target,
        "unit": settings["globals"]["units"],
        "now": date.strftime("%m-%d %H:%M"),
        "time": date.strftime("%H:%M"),
        "day": date.strftime("%m/%d"),
    }


def _evt_probe_achieved(ctx):
    title = f"{ctx['label']} Target Achieved"
    body = f"{ctx['label']} target of {ctx['target']}{ctx['unit']} achieved at {ctx['time']} on {ctx['day']}"
    return title, body, "pifire_temp_alerts", {"value1": True}


def _evt_probe_limit(ctx):
    title = f"{ctx['label']} Limit Reached"
    body = f"{ctx['label']} limit of {ctx['target']}{ctx['unit']} exceeded at {ctx['time']} on {ctx['day']}"
    return title, body, "pifire_temp_alerts", {"value1": True}


def _evt_timer_expired(ctx):
    return (
        "Timer Complete",
        "Your timer has expired, time to check your cook!",
        "pifire_timer_alerts",
        {"value1": "Your timer has expired."},
    )


def _evt_pellet_low(ctx):
    pelletdb = read_pellet_db()
    body = f"Your pellet level is currently at {pelletdb['current']['hopper_level']}%"
    return "Low Pellet Level", body, "pifire_pellet_alerts", {"value1": body}


def _evt_grill_error_01(ctx):
    maxtemp = ctx["settings"]["safety"]["maxtemp"]
    body = (
        "Grill exceeded maximum temperature limit of "  # typo fix: "exceded" -> "exceeded" (deliberate, see Task 1 note)
        + str(maxtemp)
        + ctx["unit"]
        + "! Shutting down. "
        + str(ctx["now"])
    )
    return "Grill Error!", body, "pifire_error_alerts", {"value1": str(maxtemp)}


def _evt_grill_error_02(ctx):
    startuptemp = ctx["control"]["safety"]["startuptemp"]
    body = (
        "Grill temperature dropped below minimum startup temperature of "
        + str(startuptemp)
        + ctx["unit"]
        + "! Shutting down to prevent firepot overload. "
        + str(ctx["now"])
    )
    return "Grill Error!", body, "pifire_error_alerts", {"value1": str(startuptemp)}


def _evt_grill_error_03(ctx):
    startuptemp = ctx["control"]["safety"]["startuptemp"]
    body = (
        "Grill temperature dropped below minimum startup temperature of "
        + str(startuptemp)
        + ctx["unit"]
        + "! Starting a re-ignite attempt, per user settings."
    )
    return "Grill Error!", body, "pifire_error_alerts", {"value1": str(startuptemp)}


def _evt_recipe_step(ctx):
    message = ctx["control"]["recipe"]["step_data"]["message"]
    body = message + str(ctx["now"])
    return "Recipe Message", body, "pifire_recipe_message", {"value1": message}


def _evt_test_notify(ctx):
    return (
        "Test Notification",
        "This is a test notification from PiFire.",
        "pifire_test_message",
        {"value1": "This is a test notification from PiFire."},
    )


def _evt_control_stopped(ctx):
    return (
        "Control Process Stopped!",
        "The control process has encountered an issue and has been stopped. "
        "Check on your grill as soon as possible to prevent damage!",
        "pifire_error_alerts",
        {"value1": "Control Process Stopped"},
    )


# Exact-key mapping of event string -> builder(ctx) -> (title, body, channel, query_args).
# Looked up with EVENTS.get(notify_event); a miss falls through to the Unknown-Notification
# fallback. Every caller passes an exact literal key (verified repo-wide -- no concatenation),
# so exact-key lookup is behavior-identical to the old `"<key>" in notify_event` substring
# ladder. Order is irrelevant for a dict .get().
#
# NOTE: "Grill_Error_00" and "Grill_Warning" are intentionally omitted -- both are dead,
# never-emitted events (verified zero emitters repo-wide); they now route to the
# Unknown-Notification fallback (approved behavior change, see plan Task 2).
EVENTS = {
    "Probe_Temp_Achieved": _evt_probe_achieved,
    "Probe_Temp_Limit_Alarm": _evt_probe_limit,
    "Timer_Expired": _evt_timer_expired,
    "Pellet_Level_Low": _evt_pellet_low,
    "Grill_Error_01": _evt_grill_error_01,
    "Grill_Error_02": _evt_grill_error_02,
    "Grill_Error_03": _evt_grill_error_03,
    "Recipe_Step_Message": _evt_recipe_step,
    "Test_Notify": _evt_test_notify,
    "Control_Process_Stopped": _evt_control_stopped,
}


def send_notifications(notify_event, label="Probe", target=0):
    """
    Build and send notification based on notify_event and write to log.

    :param notify_event: String Event
    :param label: Label
    :param target: Target Value
    """
    settings = read_settings()
    control = read_control()
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
    )
    ctx = _event_context(settings, control, label, target)

    builder = EVENTS.get(notify_event)
    if builder is not None:
        title_message, body_message, channel, query_args = builder(ctx)
        eventLogger.info(body_message)
    else:
        now = ctx["now"]
        title_message = "PiFire: Unknown Notification issue"
        body_message = "Whoops! PiFire had the following unhandled notify event: " + notify_event + " at " + str(now)
        channel = "default"
        query_args = {"value1": "Unknown Notification issue"}
        eventLogger.error(body_message)

    if settings["notify_services"]["apprise"]["locations"] != "" and settings["notify_services"]["apprise"]["enabled"]:
        _send_apprise_notifications(settings, title_message, body_message)
    if settings["notify_services"]["ifttt"]["APIKey"] != "" and settings["notify_services"]["ifttt"]["enabled"]:
        _send_ifttt_notification(settings, notify_event, query_args)
    if (
        settings["notify_services"]["pushbullet"]["APIKey"] != ""
        and settings["notify_services"]["pushbullet"]["enabled"]
    ):
        _send_pushbullet_notification(settings, title_message, body_message)
    if (
        settings["notify_services"]["pushover"]["APIKey"] != ""
        and settings["notify_services"]["pushover"]["UserKeys"] != ""
        and settings["notify_services"]["pushover"]["enabled"]
    ):
        _send_pushover_notification(settings, title_message, body_message)
    if settings["notify_services"]["onesignal"]["app_id"] != "" and settings["notify_services"]["onesignal"]["enabled"]:
        _send_onesignal_notification(settings, title_message, body_message, channel)
    if settings["notify_services"]["mqtt"]["broker"] != "" and settings["notify_services"]["mqtt"]["enabled"]:
        control = read_control()
        _send_mqtt_notification(control, settings, notify_event=title_message)
    if settings["notify_services"]["wled"]["device_address"] != "" and settings["notify_services"]["wled"]["enabled"]:
        _send_wled_notification(notify_event, control, settings)


def _send_apprise_notifications(settings, title_message, body_message):
    """
    Send Apprise Notifications

    :param settings: Settings
    :param title_message: Message Title
    :param body_message: Message Body
    """
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
    )
    if len(settings["notify_services"]["apprise"]["locations"]):
        eventLogger.info(
            "Sending Apprise Notifications: " + ", ".join(settings["notify_services"]["apprise"]["locations"])
        )
        appriseHandler = apprise.Apprise()

        for location in settings["notify_services"]["apprise"]["locations"]:
            appriseHandler.add(location)

        result = appriseHandler.notify(title=title_message, body=body_message)
    else:
        eventLogger.warning("No Apprise Locations Configured")


def _send_pushover_notification(settings, title_message, body_message):
    """
    Send Pushover Notifications

    :param settings: Settings
    :param title_message: Message Title
    :param body_message: Message Body
    """
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s"
    )
    if settings["globals"]["debug_mode"]:
        eventLogger.setLevel(logging.DEBUG)
    else:
        eventLogger.setLevel(logging.INFO)

    appriseHandler = apprise.Apprise()

    token = settings["notify_services"]["pushover"]["APIKey"]
    public_url = settings["notify_services"]["pushover"]["PublicURL"]

    for user in settings["notify_services"]["pushover"]["UserKeys"].split(","):
        user_id = user.strip()
        apprise_url = f"pover://{user_id}@{token}?url={public_url}"
        appriseHandler.add(apprise_url)

    try:
        result = appriseHandler.notify(title=title_message, body=body_message)

        if result:
            eventLogger.debug(f"Pushover Notification to {user} was a success!")
        else:
            eventLogger.warning(f"Pushover Notification to {user} failed!")

    except Exception as e:
        eventLogger.warning(f"Pushover Notification to {user} failed: {e}")
    except:
        eventLogger.warning(f"Pushover Notification to {user} failed for unknown reason.")


def _send_pushbullet_notification(settings, title_message, body_message):
    """
    Send PushBullet Notifications

    :param settings: Settings
    :param title_message: Message Title
    :param body_message: Message Body
    :return:
    """
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s"
    )
    if settings["globals"]["debug_mode"]:
        eventLogger.setLevel(logging.DEBUG)
    else:
        eventLogger.setLevel(logging.INFO)

    appriseHandler = apprise.Apprise()

    api_key = settings["notify_services"]["pushbullet"]["APIKey"]
    public_url = settings["notify_services"]["pushbullet"]["PublicURL"]

    apprise_url = f"pbul://{api_key}@{api_key}?url={public_url}"
    appriseHandler.add(apprise_url)

    try:
        result = appriseHandler.notify(title=title_message, body=body_message)

        if result:
            eventLogger.debug(f"Push Bullet Notification to {api_key} was a success!")
        else:
            eventLogger.warning(f"Push Bullet Notification to {api_key} failed!")

    except Exception as e:
        eventLogger.warning(f"Push Bullet Notification to {api_key} failed: {e}")
    except:
        eventLogger.warning(f"Push Bullet Notification to {api_key} failed for unknown reason.")


def _send_onesignal_notification(settings, title_message, body_message, channel):
    """
    Send OneSignal Push Notification

    :param settings: Settings
    :param title_message: Message Title
    :param body_message: Message Body
    :param channel: Android Notifications Channel
    """
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
    )
    app_id = settings["notify_services"]["onesignal"]["app_id"]
    devices = settings["notify_services"]["onesignal"]["devices"]
    url = "https://onesignal.com/api/v1/notifications"
    player_ids = []

    for key in devices.keys():
        player_ids.append(key)

    if player_ids:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        payload = {
            "app_id": app_id,
            "include_player_ids": player_ids,
            "headings": {"en": title_message},
            "contents": {"en": body_message},
            "priority": 10,
            "existing_android_channel_id": channel,
            "ttl": 3600,
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(payload))

            if not response.status_code == 200:
                eventLogger.warning("OneSignal Notification Failed: " + title_message)

            eventLogger.debug("OneSignal Response: " + response.text)

            json_response = response.json()
            if "errors" in json_response:
                if "invalid_player_ids" in json_response["errors"]:
                    for device in json_response["errors"]["invalid_player_ids"]:
                        if device in settings["onesignal"]["devices"]:
                            eventLogger.info(
                                "OneSignal: "
                                + settings["onesignal"]["devices"][device]["device_name"]
                                + " has an invalid id and has been removed"
                            )
                            settings["onesignal"]["devices"].pop(device)
                            write_settings(settings)

        except Exception as e:
            eventLogger.warning("OneSignal Notification failed: %s" % (e))
        except:
            eventLogger.warning("OneSignal Notification failed for unknown reason.")
    else:
        eventLogger.warning("OneSignal Notification Failed No Devices Registered")


def _send_ifttt_notification(settings, notify_event, query_args):
    """
    Send IFTTT Notifications

    :param settings: Settings
    :param notify_event: String Event
    :param query_args: Query Args
    """
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    eventLogger = create_logger(
        "events", filename="./logs/events.log", messageformat="%(asctime)s [%(levelname)s] %(message)s", level=log_level
    )
    key = settings["notify_services"]["ifttt"]["APIKey"]
    url = "https://maker.ifttt.com/trigger/" + notify_event + "/with/key/" + key

    try:
        r = requests.post(url, data=query_args)
        eventLogger.info("IFTTT Notification Success: " + r.text)
    except:
        eventLogger.warning("IFTTT Notification Failed: " + url)


influx_handler = None


def _send_influxdb_notification(notify_event, control, settings, pelletdb, in_data, grill_platform):
    """
    Send influxdb Notifications

    :param notify_event: String Event
    :param control: Control
    :param settings: Settings
    :param pelletdb: Pellet DB
    :param in_data: In Data (Probe Temps)
    :param grill_platform: Grill Platform
    """
    global influx_handler
    if not influx_handler:
        from notify.influxdb_handler import InfluxNotificationHandler

        influx_handler = InfluxNotificationHandler(settings)
    influx_handler.notify(notify_event, control, settings, pelletdb, in_data, grill_platform)


def _smooth_temperatures(temperatures, window_size=3):
    """
    Apply a simple moving average smoothing to the temperature data.

    Args:
            temperatures: List of temperature readings
            window_size: Size of the moving average window

    Returns:
            List of smoothed temperature values
    """
    if len(temperatures) < window_size:
        return temperatures[:]

    smoothed = []

    # Keep first few points unchanged
    for i in range(window_size // 2):
        smoothed.append(temperatures[i])

    # Apply moving average
    for i in range(window_size // 2, len(temperatures) - window_size // 2):
        window = temperatures[i - window_size // 2 : i + window_size // 2 + 1]
        smoothed.append(sum(window) / len(window))

    # Keep last few points unchanged
    for i in range(len(temperatures) - window_size // 2, len(temperatures)):
        smoothed.append(temperatures[i])

    return smoothed


def _estimate_eta(temperatures, target_temperature, interval_seconds=3, max_history_minutes=5, min_history_minutes=1):
    """
    Estimates the ETA (Estimated Time of Arrival) for the food probe to reach a specific target temperature using
    a simple linear regression implementation with smoothing and weighted regression.

    Args:
            temperatures: A list of temperatures measured by the food probe over time.
            target_temperature: The desired target temperature.  Value should be larger than the temperatures in the list.
            interval_seconds: Time between temperature readings.  Value between 1 and 60.
            max_history_minutes:  Maximum minutes of history to use for calculating ETA
            min_history_minutes:  Minimum minutes of history to use for calculating ETA

    Returns:
            The estimated time (in seconds) it will take for the food probe to reach the target temperature.
            None if the target temperature is already reached or the probe data is insufficient.
    """
    eventLogger = create_logger("events", filename="./logs/events.log")

    # Ensure target temperature is not already reached
    if target_temperature <= max(temperatures):
        eventLogger.debug(f"ETA: Target temperature already achieved.")
        return None

    # Ensure that interval is between 1 and 60 seconds
    if interval_seconds > 60 or interval_seconds < 1:
        eventLogger.debug(f"ETA: History data interval not between 1 and 60 seconds.")
        return None

    # Convert minutes to seconds
    max_data_points = int(max_history_minutes * 60 / interval_seconds)
    min_data_points = int(min_history_minutes * 60 / interval_seconds)

    # Check if enough data points provided
    if len(temperatures) < min_data_points:
        eventLogger.debug(f"ETA: Not enough history data to make estimate.")
        return None

    # Truncate data to fit within limits
    temperatures = temperatures[-max_data_points:]

    try:
        # Apply smoothing to reduce impact of fluctuations
        smoothed_temps = _smooth_temperatures(temperatures, window_size=5)

        # Prepare for weighted linear regression
        n = len(smoothed_temps)
        x_values = list(range(n))

        # Create weights that emphasize more recent readings
        # Exponential weighting - newer readings get more weight
        weights = [math.exp(i / 10) for i in range(n)]
        weight_sum = sum(weights)
        weights = [w / weight_sum for w in weights]  # Normalize weights

        # Calculate weighted means
        mean_x = sum(x * w for x, w in zip(x_values, weights))
        mean_y = sum(y * w for y, w in zip(smoothed_temps, weights))

        # Calculate weighted slope (m) and intercept (b) for y = mx + b
        numerator = sum(w * (x - mean_x) * (y - mean_y) for x, y, w in zip(x_values, smoothed_temps, weights))
        denominator = sum(w * (x - mean_x) ** 2 for x, w in zip(x_values, weights))

        # Avoid division by zero
        if denominator == 0:
            eventLogger.debug(f"ETA: Cannot calculate slope - no temperature change detected.")
            return None

        slope = numerator / denominator
        intercept = mean_y - slope * mean_x

        # If temperature isn't rising (or is falling), we can't predict ETA
        if slope <= 0:
            eventLogger.debug(f"ETA: Temperature not increasing, cannot estimate ETA.")
            return None

        # Predict time to reach target temperature (assuming linear trend)
        # Formula: (target - current) / rate_of_change
        current_temp = smoothed_temps[-1]  # Use the smoothed temperature
        predicted_time = (target_temperature - current_temp) / slope * interval_seconds

        # Log some debug information about the calculation
        eventLogger.debug(
            f"ETA: Using smoothed temp: current={current_temp}, slope={slope:.4f}, predicted={predicted_time:.1f}s"
        )

        # Ensure positive prediction
        if predicted_time < 0:
            eventLogger.debug(f"ETA: Estimated time is negative. [{predicted_time}]")
            return None
        if math.isinf(predicted_time) or math.isnan(predicted_time):
            eventLogger.debug(f"ETA: Estimated time is infinite or NaN. [{predicted_time}]")
            return None

        return int(predicted_time)
    except Exception as e:
        eventLogger.debug(f"ETA: Error calculating ETA: {str(e)}")
        return None


mqtt = None


def _send_mqtt_notification(
    control, settings, pelletdb=None, in_data=None, grill_platform=None, pid_data=None, notify_event=None
):
    """
    Send mqtt Notifications

    :param notify_event: String Event
    :param control: mode info
    :param settings: Settings
    :param pelletdb: Pellet level
    :param in_data: In Data (Probe Temps)
    :param grill_platform: Device status
    "param pid_data: pid configuration, and actual values
    """
    global mqtt

    if not mqtt:
        from notify.mqtt_handler import MqttNotificationHandler

        mqtt = MqttNotificationHandler(settings)

    # Send a notify_event immidiately
    if notify_event != None:
        payload = {"msg": notify_event}
        mqtt.notify("notify_event", payload)

    # Write other data if we changed modes or we've exceeded the configured
    # update rate

    mode_changed = control["mode"] != mqtt.last_mode
    current_time = time.time()

    if pid_data and (mode_changed or current_time > mqtt.pub_times["pid"] + mqtt.pub_rate):
        mqtt.pub_times["pid"] = current_time
        mqtt.notify("pid", pid_data)

    if pelletdb and (mode_changed or current_time > mqtt.pub_times["pellet"] + mqtt.pub_rate):
        mqtt.pub_times["pellet"] = current_time
        mqtt.notify("pellet", pelletdb["current"])

    if mode_changed or current_time > mqtt.pub_times["base"] + mqtt.pub_rate:
        mqtt.pub_times["base"] = current_time
        mqtt.notify("control", control)
        mqtt.notify("system", control)
        if grill_platform:
            mqtt.notify("devices", grill_platform.current)
        if in_data:
            mqtt.notify("probe_data", in_data["probe_history"])


def _check_condition(condition, current, target):
    """
     Check if condition is met based on current and target values

    :param condition: Condition
    :param current: Current
    :param target: Target
    """
    if condition == "equal":
        return current == target
    elif condition == "above":
        return current > target
    elif condition == "below":
        return current < target
    elif condition == "equal_above":
        return current >= target
    elif condition == "equal_below":
        return current <= target
    else:
        return False


wled_handler = None


def _send_wled_notification(notify_event, control, settings):
    """
    Send WLED Notifications

    :param notify_event: String Event
    :param control: Control
    :param settings: Settings
    :param pelletdb: Pellet DB
    :param in_data: In Data (Probe Temps)
    :param grill_platform: Grill Platform
    """
    global wled_handler
    if not wled_handler:
        from notify.wled_handler import WLEDNotificationHandler

        wled_handler = WLEDNotificationHandler(settings)
    wled_handler.notify(notify_event, control, settings)
