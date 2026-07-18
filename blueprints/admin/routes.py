import os
import datetime
import pathlib
import zipfile
from flask import render_template, current_app, request, send_file
from werkzeug.utils import secure_filename
from common.common import WriteKind, write_log, read_generic_json, write_generic_json, get_display_info
from common.datastore_accessors import (
    read_settings,
    write_settings,
    read_control,
    write_control,
    read_pellet_db,
    write_pellet_db,
    read_history,
)
from common.settings_migration import read_settings_file
from common.backups import read_pellet_db_file, backup_settings, backup_pellet_db
from common.system import reboot_system, shutdown_system, restart_scripts, gather_system_info
from common.defaults import default_settings, default_control
from common.app import allowed_file
from common.server_status import set_server_status, get_server_status
from . import admin_bp


class _AdminActionContext:
    """Mutable state shared across the admin_page action handlers.

    Handlers mutate settings/control/pelletdb/errors/success in place; the
    route's tail render sees those mutations because it holds the same objects.
    """

    def __init__(self, settings, control, pelletdb, errors, warnings, success, backup_path):
        self.settings = settings
        self.control = control
        self.pelletdb = pelletdb
        self.errors = errors
        self.warnings = warnings
        self.success = success
        self.backup_path = backup_path


def _admin_reboot(ctx):
    event = "Admin: Reboot"
    write_log(event)
    set_server_status("rebooting")
    reboot_system()
    return render_template(
        "shutdown.html",
        action="reboot",
    )


def _admin_shutdown(ctx):
    event = "Admin: Shutdown"
    write_log(event)
    set_server_status("shutdown")
    shutdown_system()
    return render_template(
        "shutdown.html",
        action="shutdown",
    )


def _admin_restart(ctx):
    event = "Admin: Restart Server"
    write_log(event)
    set_server_status("restarting")
    restart_scripts()
    return render_template(
        "shutdown.html",
        action="restart",
    )


def _admin_setting_debugenabled(ctx):
    settings = ctx.settings
    control = ctx.control
    response = request.form

    control["settings_update"] = True
    if response["debugenabled"] == "disabled":
        write_log("Debug Mode Disabled.")
        settings["globals"]["debug_mode"] = False
        write_settings(settings)
        write_control(control, WriteKind.MERGE, origin="app")
    else:
        settings["globals"]["debug_mode"] = True
        write_settings(settings)
        write_control(control, WriteKind.MERGE, origin="app")
        write_log("Debug Mode Enabled.")


def _admin_setting_clearhistory(ctx):
    response = request.form
    if response["clearhistory"] == "true":
        write_log("Clearing History Log.")
        read_history(0, flushhistory=True)


def _admin_setting_clearevents(ctx):
    response = request.form
    if response["clearevents"] == "true":
        write_log("Clearing Events Log.")
        os.system("rm ./logs/events.log")


def _admin_setting_clearpelletdb(ctx):
    response = request.form
    if response["clearpelletdb"] == "true":
        write_log("Clearing Pellet Database.")
        os.system("rm pelletdb.json")


def _admin_setting_clearpelletdblog(ctx):
    pelletdb = ctx.pelletdb
    response = request.form
    if response["clearpelletdblog"] == "true":
        write_log("Clearing Pellet Database Log.")
        pelletdb["log"].clear()
        write_pellet_db(pelletdb)


def _admin_setting_factorydefaults(ctx):
    response = request.form
    if response["factorydefaults"] == "true":
        write_log("Resetting Settings, Control and History to factory defaults.")
        read_history(0, flushhistory=True)
        read_control(flush=True)
        os.system("rm settings.json")
        os.system("rm pelletdb.json")
        settings = default_settings()
        control = default_control()
        write_settings(settings)
        write_control(control, WriteKind.MERGE, origin="app")
        set_server_status("restarting")
        restart_scripts()
        return render_template(
            "shutdown.html",
            action="restart",
        )


def _admin_setting_download_logs(ctx):
    zip_file = _zip_files_logs("logs")
    return send_file(zip_file, as_attachment=True, max_age=0)


def _admin_setting_delete_logs(ctx):
    # Delete *.log files in logs/
    try:
        os.system("rm logs/*.log")
        ctx.success.append("Log files deleted.")
    except:
        ctx.errors.append("There was an error deleting the log files.")


def _admin_setting_download_settings(ctx):
    # settings.json is not kept in sync -- SQLite is the source of
    # truth at runtime. Write the current settings out to a temp
    # file to download, same pattern as 'download_control' below.
    filename = "/tmp/settings.json"
    write_generic_json(ctx.settings, filename)
    return send_file(filename, as_attachment=True, max_age=0)


def _admin_setting_download_control(ctx):
    filename = "/tmp/control_general.json"
    write_generic_json(ctx.control, filename)
    return send_file(filename, as_attachment=True, max_age=0)


def _admin_setting_download_pip_list(ctx):
    filename = "pip_list.json"
    return send_file(filename, as_attachment=True, max_age=0)


def _admin_setting_backupsettings(ctx):
    backup_file = backup_settings()
    return send_file(backup_file, as_attachment=True, max_age=0)


def _admin_setting_restoresettings(ctx):
    # Assume we have request.files and local file in response
    remote_file = request.files["uploadfile"]
    local_file = request.form["localfile"]

    if local_file != "none":
        new_settings = read_settings_file(filename=ctx.backup_path + local_file)
        write_settings(new_settings)
        set_server_status("restarting")
        restart_scripts()
        return render_template(
            "shutdown.html",
            action="restart",
        )
    elif remote_file.filename != "":
        # If the user does not select a file, the browser submits an
        # empty file without a filename.
        if remote_file and allowed_file(remote_file.filename):
            filename = secure_filename(remote_file.filename)
            remote_file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
            ctx.success.append("Successfully restored settings.")
            new_settings = read_settings_file(filename=ctx.backup_path + filename)
            write_settings(new_settings)
            set_server_status("restarting")
            restart_scripts()
            return render_template(
                "shutdown.html",
                action="restart",
            )
        else:
            ctx.errors.append(
                "There was an error restoring settings.  File either is a disallowed type or was not found."
            )
    else:
        ctx.errors.append("There was an error restoring settings.  Restore file wasn't specified or found")


def _admin_setting_backuppelletdb(ctx):
    backup_file = backup_pellet_db(action="backup")
    return send_file(backup_file, as_attachment=True, max_age=0)


def _admin_setting_restorepelletdb(ctx):
    # Assume we have request.files and local file in response
    remote_file = request.files["uploadfile"]
    local_file = request.form["localfile"]

    if local_file != "none":
        pelletdb = read_pellet_db_file(filename=ctx.backup_path + local_file)
        write_pellet_db(pelletdb)
        ctx.success.append("Successfully restored pellet database.")
    elif remote_file.filename != "":
        # If the user does not select a file, the browser submits an
        # empty file without a filename.
        if remote_file and allowed_file(remote_file.filename):
            filename = secure_filename(remote_file.filename)
            remote_file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
            ctx.success.append("Successfully restored pellet database.")
            pelletdb = read_pellet_db_file(filename=ctx.backup_path + filename)
            write_pellet_db(pelletdb)
        else:
            ctx.errors.append(
                "There was an error restoring the pellet database.  File either is a disallowed type or was not found."
            )
    else:
        ctx.errors.append("There was an error restoring pellet database.  Restore file wasn't specified or found")


# Sub-actions of the `setting` POST action are gated on RESPONSE KEYS (not an
# `action` value). Ordering matters: several branches early-return, and the
# original evaluated these in this exact sequence with independent (non-elif)
# `if key in response` checks -- preserved here by iterating in insertion order.
_ADMIN_SETTING_DISPATCH = {
    "debugenabled": _admin_setting_debugenabled,
    "clearhistory": _admin_setting_clearhistory,
    "clearevents": _admin_setting_clearevents,
    "clearpelletdb": _admin_setting_clearpelletdb,
    "clearpelletdblog": _admin_setting_clearpelletdblog,
    "factorydefaults": _admin_setting_factorydefaults,
    "download_logs": _admin_setting_download_logs,
    "delete_logs": _admin_setting_delete_logs,
    "download_settings": _admin_setting_download_settings,
    "download_control": _admin_setting_download_control,
    "download_pip_list": _admin_setting_download_pip_list,
    "backupsettings": _admin_setting_backupsettings,
    "restoresettings": _admin_setting_restoresettings,
    "backuppelletdb": _admin_setting_backuppelletdb,
    "restorepelletdb": _admin_setting_restorepelletdb,
}


def _admin_setting(ctx):
    if request.method != "POST":
        return None
    response = request.form
    for key, handler in _ADMIN_SETTING_DISPATCH.items():
        if key in response:
            result = handler(ctx)
            if result is not None:
                return result
    return None


def _admin_boot(ctx):
    if request.method != "POST":
        return None
    settings = ctx.settings
    response = request.form

    if "boot_to_monitor" in response:
        settings["globals"]["boot_to_monitor"] = True
    else:
        settings["globals"]["boot_to_monitor"] = False

    write_settings(settings)
    return None


_ADMIN_DISPATCH = {
    "reboot": _admin_reboot,
    "shutdown": _admin_shutdown,
    "restart": _admin_restart,
    "setting": _admin_setting,
    "boot": _admin_boot,
}


@admin_bp.route("/<action>", methods=["POST", "GET"])
@admin_bp.route("/", methods=["POST", "GET"])
def admin_page(action=None):
    server_status = get_server_status()
    settings = read_settings()
    control = read_control()
    pelletdb = read_pellet_db()

    errors = []
    warnings = []
    success = []

    BACKUP_PATH = current_app.config["BACKUP_PATH"]

    if not os.path.exists(BACKUP_PATH):
        os.mkdir(BACKUP_PATH)
    files = os.listdir(BACKUP_PATH)
    for file in files:
        if not allowed_file(file):
            files.remove(file)

    ctx = _AdminActionContext(
        settings=settings,
        control=control,
        pelletdb=pelletdb,
        errors=errors,
        warnings=warnings,
        success=success,
        backup_path=BACKUP_PATH,
    )

    handler = _ADMIN_DISPATCH.get(action)
    if handler is not None:
        result = handler(ctx)
        if result is not None:
            return result

    """
        Get System Information
    """

    system_info, system_info_failures = gather_system_info(control)
    errors.extend(system_info_failures)

    url = request.url_root

    pip_list = read_generic_json("pip_list.json")
    if pip_list == {}:
        event = "Pip list is empty. Run 'updater.py -p' to generate pip list."
        errors.append(event)
        pip_list = []

    return render_template(
        "admin/index.html",
        settings=settings,
        control=control,
        system_info=system_info,
        qr_content=url,
        display_info=get_display_info(settings),
        pip_list=pip_list,
        files=files,
        errors=errors,
        warnings=warnings,
        success=success,
    )


def _zip_files_logs(dir_name):
    time_now = datetime.datetime.now()
    time_str = time_now.strftime("%m-%d-%y_%H%M%S")  # Truncate the microseconds
    file_name = f"/tmp/PiFire_Logs_{time_str}.zip"
    directory = pathlib.Path(f"{dir_name}")
    with zipfile.ZipFile(file_name, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in directory.rglob("*.log"):
            archive.write(file_path, arcname=file_path.relative_to(directory))
    return file_name
