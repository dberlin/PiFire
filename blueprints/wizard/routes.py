import os
import asyncio
from flask import render_template, request, jsonify, redirect, render_template_string
from probes.thermoworks_cloud import discover
from thermoworks_cloud import AuthenticationError
from common.common import (
    read_settings,
    read_control,
    read_wizard,
    get_wizard_install_status,
    set_wizard_install_status,
    store_wizard_install_info,
    write_settings,
    is_real_hardware,
)
from common.app import get_supported_cmds, process_command, get_system_command_output
from common.i2c_bus import (
    I2CBusConfigError,
    discover_extended_i2c_buses,
    discover_ft232h_devices,
    discover_mcp2221_devices,
    validate_bus_kinds,
)
from common.usb_serial import discover_usb_serial_devices

from . import wizard_bp
from .wizard import *


# Full-page error shown when the finish step's assembled config has an
# unworkable I2C bus combination; mirrors wizard-finish.html's block overrides
# so it renders with just page_theme/grill_name and does not start an install.
_WIZARD_BUS_CONFLICT_PAGE = """{% extends 'base.html' %}
{% block title %}Wizard Configuration Error{% endblock %}
{% block timer_bar %}{% endblock %}
{% block content %}
<div class="container">
	<div class="card shadow">
		<div class="card-body text-center">
			<br>
			<h2 class="text-danger"><i class="fas fa-exclamation-triangle"></i>&nbsp;I2C Bus Configuration Error</h2>
			<br>
			<p>{{ message }}</p>
			<p>Your configuration was <strong>not</strong> saved and no install was started. Please go back and
			change the conflicting I2C bus selection.</p>
			<br>
			<a class="btn btn-outline-primary" href="/wizard">&larr; Back to the Configuration Wizard</a>
			<br><br>
		</div>
	</div>
</div>
{% endblock %}
{% block controlpanel %}{% endblock %}
{% block controlpanel_scripts %}{% endblock %}
{% block scripts %}{% endblock %}"""


@wizard_bp.route("/<action>", methods=["POST", "GET"])
@wizard_bp.route("/", methods=["POST", "GET"])
def wizard_page(action=None):
    settings = read_settings()
    control = read_control()
    wizardData = read_wizard()
    errors = []

    if is_real_hardware():
        python_exec = settings["globals"].get("python_exec", "python")
    else:
        python_exec = "python"  # Bug fix for development environment where python_exec isn't relevant

    if request.method == "GET":
        if action == "installstatus":
            percent, status, output = get_wizard_install_status()
            return jsonify({"percent": percent, "status": status, "output": output})
    elif request.method == "POST":
        r = request.form
        if action == "cancel":
            settings["globals"]["first_time_setup"] = False
            write_settings(settings)
            return redirect("/")

        if action == "finish":
            if control["mode"] == "Stop":
                wizardInstallInfo = prepare_wizard_data(r)
                # Whole-config check on the user's in-progress selections (probes +
                # distance + fan controller). Unlike the per-probe step, this sees the
                # platform bus the user just chose, so it catches a real basic+USB-HID
                # conflict before the install starts -- without the stale-settings
                # false positives that plagued the per-device check.
                try:
                    validate_bus_kinds(wizard_bus_kinds(wizardInstallInfo, wizardData))
                except I2CBusConfigError as exc:
                    return render_template_string(
                        _WIZARD_BUS_CONFLICT_PAGE,
                        message=str(exc),
                        page_theme=settings["globals"].get("page_theme", "light"),
                        grill_name=settings["globals"].get("grill_name", ""),
                    )
                store_wizard_install_info(wizardInstallInfo)
                set_wizard_install_status(0, "Starting Install...", "")
                os.system(f"{python_exec} wizard.py &")  # Kickoff Installation
                return render_template(
                    "wizard/wizard-finish.html",
                    page_theme=settings["globals"].get("page_theme", "light"),
                    grill_name=settings["globals"].get("grill_name", ""),
                    wizardData=wizardData,
                )

        if action == "modulecard":
            module = r["module"]
            section = r["section"]
            if section in ["grillplatform", "display", "distance"]:
                moduleData = wizardData["modules"][section][module]
                moduleSettings = {}
                moduleSettings["settings"] = get_settings_dependencies_values(settings, moduleData)
                moduleSettings["config"] = {} if section != "display" else settings["display"]["config"][module]
                render_string = "{% from 'wizard/_macro_wizard_card.html' import render_wizard_card %}{{ render_wizard_card(moduleData, moduleSection, moduleSettings) }}"
                return render_template_string(
                    render_string, moduleData=moduleData, moduleSection=section, moduleSettings=moduleSettings
                )
            else:
                return '<strong color="red">No Data</strong>'

        if action == "bt_scan":
            itemID = r["itemID"]
            bt_data = []
            error = None

            try:
                supported_cmds = get_supported_cmds()

                if "scan_bluetooth" in supported_cmds:
                    process_command(
                        action="sys", arglist=["scan_bluetooth"], origin="admin"
                    )  # Request supported commands
                    data = get_system_command_output(requested="scan_bluetooth", timeout=6)
                    # print('[DEBUG] BT Scan Data:', data)
                    if data["result"] != "OK":
                        error = data["message"]
                    else:
                        bt_data = parse_bt_device_info(data["data"]["bt_devices"])
                        if bt_data == []:
                            error = "No bluetooth devices found."
                else:
                    error = "No support for bluetooth scan command."

            except Exception as e:
                error = f"Something bad happened: {e}"
                # print(f'[DEBUG] {error}')

            render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_bt_scan_table %}{{ render_bt_scan_table(itemID, bt_data, error) }}"
            return render_template_string(render_string, itemID=itemID, bt_data=bt_data, error=error)

        if action == "thermoworks_discover":
            email = r.get("email", "")
            password = r.get("password", "")
            serialID = r.get("serialID", "")
            numProbesID = r.get("numProbesID", "")
            tw_data = []
            error = None

            try:
                tw_data = asyncio.run(discover(email, password))
                if tw_data == []:
                    error = "No ThermoWorks Cloud devices found for this account."
            except AuthenticationError as e:
                error = f"Could not log in to ThermoWorks Cloud: {e}"
            except Exception as e:
                error = f"Something bad happened: {e}"

            render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_thermoworks_scan_table %}{{ render_thermoworks_scan_table(serialID, numProbesID, tw_data, error) }}"
            return render_template_string(
                render_string, serialID=serialID, numProbesID=numProbesID, tw_data=tw_data, error=error
            )

        if action == "i2c_bus_scan":
            itemID = r["itemID"]
            kind = r.get("kind", "")
            groups = []
            error = None

            try:
                if kind == "extended":
                    adapters = discover_extended_i2c_buses()
                    by_bus = [
                        {"value": str(a["bus_num"]), "label": f"i2c-{a['bus_num']} ({a['name']})"} for a in adapters
                    ]
                    by_serial = sorted(
                        (
                            {"value": f"serial:{a['serial']}", "label": f"{a['name']} — serial {a['serial']}"}
                            for a in adapters
                            if a["serial"]
                        ),
                        key=lambda c: c["value"].lower(),
                    )
                    if by_bus:
                        groups.append({"title": "By Bus Number", "items": by_bus})
                    if by_serial:
                        groups.append({"title": "By Serial", "items": by_serial})
                elif kind == "mcp2221":
                    items = [
                        {"value": d["serial"], "label": f"MCP2221 serial {d['serial']}"}
                        for d in discover_mcp2221_devices()
                    ]
                    if items:
                        groups.append({"title": "MCP2221 Devices", "items": items})
                elif kind == "ft232h":
                    items = [
                        {"value": d["url"], "label": f"{d['description'] or 'FT232H'} ({d['url']})"}
                        for d in discover_ft232h_devices()
                    ]
                    if items:
                        groups.append({"title": "FT232H Devices", "items": items})
                else:
                    error = f"Unknown I2C bus kind {kind!r}. Select Extended, FT232H, or MCP2221 first."

                if not groups and error is None:
                    error = f"No {kind} I2C buses discovered."
            except Exception as e:
                error = f"Something bad happened: {e}"

            render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_i2c_scan_table %}{{ render_i2c_scan_table(itemID, groups, error) }}"
            return render_template_string(render_string, itemID=itemID, groups=groups, error=error)

        if action == "usb_serial_scan":
            itemID = r["itemID"]
            vid_raw = r.get("vid", "")
            pid_raw = r.get("pid", "")
            groups = []
            error = None

            try:
                vid = int(vid_raw, 16) if vid_raw else None
                pid = int(pid_raw, 16) if pid_raw else None
                devices = discover_usb_serial_devices(vid=vid, pid=pid)
                items = [
                    {
                        "value": d["device"],
                        "label": f"{d['device']} — {d['description'] or 'Unknown device'}"
                        + (f" (serial {d['serial_number']})" if d["serial_number"] else ""),
                    }
                    for d in devices
                ]
                if items:
                    title = "Matched Devices" if (vid is not None or pid is not None) else "All Serial Devices"
                    groups.append({"title": title, "items": items})
                if not groups:
                    error = (
                        "No matching USB serial devices found."
                        if (vid is not None or pid is not None)
                        else "No serial devices found."
                    )
            except Exception as e:
                error = f"Something bad happened: {e}"

            render_string = "{% from 'probeconfig/_macro_probes_config.html' import render_i2c_scan_table %}{{ render_i2c_scan_table(itemID, groups, error) }}"
            return render_template_string(render_string, itemID=itemID, groups=groups, error=error)

    """ Create Temporary Probe Device/Port Structure for Setup, Use Existing unless First Time Setup """
    if settings["globals"]["first_time_setup"]:
        wizardInstallInfo = wizardInstallInfoDefaults(wizardData, settings)
    else:
        wizardInstallInfo = wizardInstallInfoExisting(wizardData, settings)

    store_wizard_install_info(wizardInstallInfo)

    if control["mode"] != "Stop":
        errors.append(
            "PiFire configuration wizard cannot be run while the system is active.  Please stop the current cook before continuing."
        )

    return render_template(
        "wizard/wizard.html",
        settings=settings,
        control=control,
        errors=errors,
        wizardData=wizardData,
        wizardInstallInfo=wizardInstallInfo,
        page_theme=settings["globals"].get("page_theme", "light"),
        grill_name=settings["globals"].get("grill_name", ""),
    )
