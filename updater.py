"""
==============================================================================
 PiFire Updater
==============================================================================

 Description: Update support functions to utilize Git/GitHub for live system updates

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""

import json
import time

from common.common import (
    write_log,
    read_updater_manifest,
    semantic_ver_is_lower,
    create_logger,
    log_path,
    write_generic_json,
)
from common.install_log import INSTALL_FAILED_PERCENT
from common.datastore_accessors import (
    set_updater_install_status,
    read_settings,
    set_wizard_install_status,
    write_settings,
)
from common.web_ui_build import (
    BUILD_FAIL_MARKER,
    BUILD_LOG_NAME,
    BUILD_OK_MARKER,
    BUILD_RUN_MARKER,
    rebuild_web_ui,
    web_ui_needs_rebuild,
)
from importlib.metadata import version, PackageNotFoundError

import os
import subprocess
import argparse
import logging

#: This file sits at the repo root, beside web-react/ and updater/.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

#: The same logger __main__ attaches a file handler to -- logging caches by
#: name, so both names are one object. Bound here as well so the functions below
#: can log when they are called from an import rather than from the CLI.
logger = logging.getLogger("updater")

"""
==============================================================================
 Supporting Functions
==============================================================================
"""


def get_available_branches():
    command = ["git", "branch", "-a"]
    branches = subprocess.run(command, capture_output=True, text=True)
    branch_list = []
    error_msg = ""
    if branches.returncode == 0:
        input_list = branches.stdout.split("\n")
        for line in input_list:
            line = line.strip(" *")
            if "->" in line:
                # Skip symbolic-ref lines like "remotes/origin/HEAD -> origin/<branch>"
                continue
            if "origin/main" in line:
                # Skip this line
                pass
            elif "remotes/origin/" in line:
                line = line.replace("remotes/origin/", "")
                if line not in branch_list and line != "":
                    branch_list.append(line)
            elif line != "":
                branch_list.append(line)
    else:
        error_msg = branches.stderr
    return (branch_list, error_msg)


def update_remote_branches():
    # git remote set-branches origin '*'
    command = ["git", "remote", "set-branches", "origin", "*"]
    remote_branches = subprocess.run(command, capture_output=True, text=True)
    error_msg = ""
    if remote_branches.returncode != 0:
        error_msg = remote_branches.stderr
    # Fetch Branch Information Locally
    command = ["git", "fetch"]
    fetch = subprocess.run(command, capture_output=True, text=True)
    if fetch.returncode != 0:
        error_msg += " | " + remote_branches.stderr
    return error_msg


def get_branch():
    # --show-current is only in later versions of git, and unfortunately buster does not have this
    command = ["git", "branch", "-a"]
    branches = subprocess.run(command, capture_output=True, text=True)
    error_msg = ""
    result = ""
    if branches.returncode == 0:
        input_list = branches.stdout.split("\n")
        for line in input_list:
            if "*" in line:
                result = line.strip(" *")
                break
    else:
        result = "ERROR Getting Current Branch"
        error_msg = branches.stderr
    return (result, error_msg)


def set_branch(branch_target):
    command = ["git", "checkout", "-f", branch_target]
    target = subprocess.run(command, capture_output=True, text=True)
    error_msg = ""
    result = ""
    if target.returncode == 0:
        result = target.stdout.replace("\n", "<br>") + target.stderr.replace("\n", "<br>")
    else:
        result = "ERROR Setting Branch"
        error_msg = target.stderr.replace("\n", "<br>")
    return (result, error_msg)


def get_remote_url():
    command = ["git", "config", "--get", "remote.origin.url"]
    remote = subprocess.run(command, capture_output=True, text=True)
    error_msg = ""
    if remote.returncode == 0:
        result = remote.stdout.strip(" \n")
    else:
        result = "ERROR Retrieving URL"
        error_msg = remote.stderr.replace(" \n", " ")
    return (result, error_msg)


def get_available_updates(branch=""):
    result = {}
    remote, error_msg1 = get_remote_url()
    if branch == "":
        branch, error_msg2 = get_branch()

    if "ERROR" not in remote and "ERROR" not in branch:
        command = ["git", "fetch"]
        fetch = subprocess.run(command, capture_output=True, text=True)
        command = ["git", "rev-list", "--left-only", "--count", f"origin/{branch}...@"]
        rev_list = subprocess.run(command, capture_output=True, text=True)
        # print(f'rev_list.returncode = {rev_list.returncode}')
        # print(f'fetch.returncode = {fetch.returncode}')

        if rev_list.returncode == 0 and fetch.returncode == 0:
            rev_list = rev_list.stdout.strip(" \n")
            if rev_list.isnumeric():
                result["success"] = True
                result["commits_behind"] = int(rev_list)
            else:
                result["success"] = False
                result["message"] = rev_list
        else:
            result["success"] = False
            result["message"] = (
                "ERROR Getting Revision List: "
                + rev_list.stderr.replace("\n", " ")
                + rev_list.stdout.replace("\n", " ")
            )
    else:
        result["success"] = False
        result["message"] = (
            "ERROR Getting Remote or Branch: " + error_msg1.replace("\n", " ") + " " + error_msg2.replace("\n", " ")
        )
    return result


def do_update():
    branch, error_msg1 = get_branch()
    remote, error_msg2 = get_remote_url()
    if error_msg1 == "" and error_msg2 == "":
        command = ["git", "fetch", "--all"]
        fetch = subprocess.run(command, capture_output=True, text=True)
        command = ["git", "reset", "--hard", f"origin/{branch}"]
        reset = subprocess.run(command, capture_output=True, text=True)

        """
		command = ['git', 'reset', '--hard', 'HEAD']
		reset = subprocess.run(command, capture_output=True, text=True)
		command = ['git', 'merge', f'origin/{branch}']
		merge = subprocess.run(command, capture_output=True, text=True)
		"""
        error_msg = ""
        if fetch.returncode == 0 and reset.returncode == 0:
            result = (
                fetch.stdout.replace("\n", "<br>") + "<br>" + reset.stdout.replace("\n", "<br>")
            )  # + '<br>' + merge.stdout.replace('\n', '<br>')
        else:
            result = "ERROR Performing Update."
            error_msg = (
                fetch.stderr.replace("\n", "<br>") + "<br>" + reset.stderr.replace("\n", "<br>")
            )  # + '<br>' + merge.stderr.replace('\n', '<br>')
    else:
        result = "ERROR Getting Remote URL."
    return (result, error_msg)


def get_log(num_commits=10):
    branch, error_msg = get_branch()
    if error_msg == "":
        command = ["git", "log", f"origin/{branch}", f"-{num_commits}", '--pretty="%h - %cr : %s"']
        log = subprocess.run(command, capture_output=True, text=True)
        if log.returncode == 0:
            result = log.stdout.replace("\n", "<br>").replace('"', "")
        else:
            result = "ERROR Getting Log."
            error_msg = log.stderr.replace("\n", "<br>")
    else:
        result = "ERROR Getting Branch Name."
    return (result, error_msg)


def get_remote_version():
    remote_url, error_msg = get_remote_url()
    current_branch, branch_error = get_branch()

    if error_msg == "" and branch_error == "":
        # Instead of getting all tags, we'll get tags that are on the current branch
        # First fetch the latest tags
        fetch_command = ["git", "fetch", "--tags"]
        fetch = subprocess.run(fetch_command, capture_output=True, text=True)

        if fetch.returncode != 0:
            return "ERROR Fetching Tags.", fetch.stderr.replace("\n", " | ")

        # Now get tags that contain commits from the current branch
        # This command finds tags that are reachable from the branch
        command = ["git", "tag", "--sort=v:refname", "--merged", f"origin/{current_branch}"]
        versions = subprocess.run(command, capture_output=True, text=True)

        if versions.returncode == 0:
            version_list = versions.stdout.split("\n")  # Make a list of versions from the output
            # Remove empty entries
            version_list = [v for v in version_list if v]

            if version_list:
                # Get the latest tag from the list
                result = version_list[-1]
            else:
                result = "No release tag on this branch"
        else:
            result = "ERROR Getting Remote Version."
            error_msg = versions.stderr.replace("\n", " | ")
    else:
        result = "ERROR Getting Remote URL or Branch."
        if error_msg:
            error_msg += " | "
        error_msg += branch_error
    return (result, error_msg)


def get_current_tag():
    error_msg = ""
    # --always falls back to an abbreviated commit hash when no tag is reachable
    # (e.g. development branches with no version tags in their history), so this
    # succeeds instead of erroring out on the version display.
    command = ["git", "describe", "--tags", "--always"]
    tag = subprocess.run(command, capture_output=True, text=True)
    if tag.returncode == 0:
        result = tag.stdout.replace("\n", "")
    else:
        result = "ERROR Getting Current Tag."
        error_msg = tag.stderr.replace("\n", "<br>")
    return (result, error_msg)


def get_update_data(settings):
    # Populate Update Data Structure
    update_data = {}
    tag, error_msg = get_current_tag()
    if error_msg != "":
        write_log(error_msg)
    update_data["version"] = f"v{settings['versions']['server']} ({tag})"
    update_data["branch_target"], error_msg = get_branch()
    if error_msg != "":
        write_log(error_msg)
    update_data["branches"], error_msg = get_available_branches()
    if error_msg != "":
        write_log(error_msg)
    update_data["remote_url"], error_msg = get_remote_url()
    if error_msg != "":
        write_log(error_msg)
    update_data["remote_version"], error_msg = get_remote_version()
    if error_msg != "":
        write_log(error_msg)

    return update_data


def change_branch(branch_target):
    command = ["git", "checkout", "-f", branch_target]
    target = subprocess.run(command, capture_output=True, text=True)
    if target.returncode == 0:
        status = "Branch Changed Successfully"
        output = " - " + target.stdout + target.stderr
        success = True
    else:
        status = "ERROR Changing Branch"
        output = " - " + target.stderr
        success = False
    return (success, status, output)


def install_update():
    branch, error_msg1 = get_branch()
    remote, error_msg2 = get_remote_url()
    if error_msg1 == "" and error_msg2 == "":
        command = ["git", "fetch"]
        fetch = subprocess.run(command, capture_output=True, text=True)
        command = ["git", "reset", "--hard", "HEAD"]
        reset = subprocess.run(command, capture_output=True, text=True)
        command = ["git", "merge", f"origin/{branch}"]
        merge = subprocess.run(command, capture_output=True, text=True)
        if fetch.returncode == 0 and reset.returncode == 0 and merge.returncode == 0:
            status = "Update Completed Successfully"
            output = " - " + fetch.stdout + reset.stdout + merge.stdout
            success = True
        else:
            status = "ERROR Performing Update."
            output = " - " + fetch.stdout + reset.stdout + merge.stdout
            success = False
    else:
        status = "ERROR Getting Remote URL."
        output = " - ERROR Getting Remote URL. Please check your git install"
        success = False
    return (success, status, output)


def read_output(command):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, encoding="utf-8")
    while True:
        output = process.stdout.readline()
        if process.poll() is not None:
            break
        if output:
            set_updater_install_status(percent, status, output.strip())
            print(output.strip())

    return_code = process.poll()
    print(f"Return Code: {return_code}")


def rebuild_web_ui_if_stale(repo_root=None, runner=None, force=False):
    """Rebuild the React bundle when the checked-out sources are newer than it.

    Runs after every update and branch change: web-react/dist is git-ignored,
    so pulling new React sources leaves the built bundle untouched and the SPA
    blueprint keeps serving it.

    Skipped when the bundle is already newer than every source, so a
    backend-only update does not pay for a build that takes minutes on a Pi and
    an update whose migration step already built it does not build twice.
    Returns True when the bundle is up to date afterwards.

    `force` builds regardless, for the updater page's Rebuild button: a bundle
    can be newer than its sources and still be wrong -- a build that failed
    half-way, or one whose output was disturbed -- and the mtime check cannot
    see that. An explicit request is taken at face value.
    """
    repo_root = repo_root or REPO_ROOT
    if not force and not web_ui_needs_rebuild(repo_root):
        output = " - Web UI is already up to date with its sources"
        logger.info(output)
        set_updater_install_status(95, "Checking Web UI...", output)
        return True

    status = "Rebuilding Web UI..."
    logger.info(status)
    set_updater_install_status(95, status, " - Rebuilding the web UI from the updated sources")

    # Bounds this build's lines within logs/update.log, which carries the whole
    # update. The updater page reads back what lies between these markers, so a
    # failed build can be shown on its own rather than as a needle in the
    # update's git and apt output.
    logger.info(BUILD_RUN_MARKER)
    code = rebuild_web_ui(repo_root, lambda line: _publish(95, status, line), runner=runner)
    if code == 0:
        logger.info("Web UI rebuilt")
        logger.info(BUILD_OK_MARKER)
        return True

    # Not fatal to the update -- the backend is already on the new code and a
    # failed build must not look like a failed update -- but it cannot pass
    # silently either: what is being served is now a bundle built from
    # different sources.
    output = f" - Web UI rebuild FAILED (exit {code}). The previous bundle is still being served."
    logger.error(output)
    logger.error(BUILD_FAIL_MARKER)
    set_updater_install_status(95, "Web UI rebuild failed", output)
    return False


def _publish(percent, status, line):
    set_updater_install_status(percent, status, line)
    logger.info(line)


def install_dependencies(current_version_string="0.0.0", current_build=None):
    result = 0
    percent = 30
    status = "Calculating Python/Package Dependencies..."
    output = " - Calculating Python & Package Dependencies"
    if DEBUG:
        print(f"Percent: {percent}")
        print(f"Status:  {status}")
        print(f"Output:  {output}")
    logger.debug(f"Percent: {percent}")
    logger.info(f"Status:  {status}")
    logger.debug(f"Output:  {output}")
    # Update the status bar with the current status
    set_updater_install_status(percent, status, output)
    time.sleep(2)

    updaterInfo = read_updater_manifest()

    # Get ALL PyPi & Apt dependencies and commands to install / update
    py_dependencies = []
    apt_dependencies = []
    command_list = []
    reboot = False

    for version_info in updaterInfo["versions"]:
        """ Walk list of versions in updater_manifest, check for dependencies """
        if (semantic_ver_is_lower(current_version_string, version_info["version"])) or (
            (current_version_string == version_info["version"]) and (current_build < version_info["build"])
        ):
            # If the current version (pre-update) is less than this version information, install dependencies, etc.
            for section in version_info["dependencies"]:
                for module in version_info["dependencies"][section]["py_dependencies"]:
                    try:
                        pkg_version = version(module)
                        logger.info(f"Found {module} version {pkg_version}")
                    except PackageNotFoundError:
                        logger.info(f"Package {module} not found, adding to dependencies")
                        py_dependencies.append(module)

                for package in version_info["dependencies"][section]["apt_dependencies"]:
                    if subprocess.call(["which", package]) != 0:
                        apt_dependencies.append(package)

                for command in version_info["dependencies"][section]["command_list"]:
                    command_list.append(command)

                if version_info["reboot_required"]:
                    reboot = True

    if DEBUG:
        print(f"py_dep: {py_dependencies}")
        print(f"apt_dep:  {apt_dependencies}")
        print(f"command:  {command_list}")
    logger.debug(f"py_dep: {py_dependencies}")
    logger.debug(f"apt_dep:  {apt_dependencies}")
    logger.debug(f"command:  {command_list}")

    # Calculate the percent done from remaining items to install
    items_remaining = len(py_dependencies) + len(apt_dependencies) + len(command_list)
    if items_remaining == 0:
        increment = 70
    else:
        increment = 70 / items_remaining

    # Install Py dependencies
    settings = read_settings()
    python_exec = settings["globals"].get("python_exec", "python")

    if settings["globals"].get("uv", False):
        launch_pip = ["uv", "pip", "install"]
    else:
        launch_pip = [python_exec, "-m", "pip", "install"]

    status = "Installing Python Dependencies..."
    output = " - Installing Python Dependencies"
    set_updater_install_status(percent, status, output)
    if DEBUG:
        print(f"Percent: {percent}")
        print(f"Status:  {status}")
        print(f"Output:  {output}")
    logger.debug(f"Percent: {percent}")
    logger.info(f"Status:  {status}")
    logger.debug(f"Output:  {output}")

    for py_item in py_dependencies:
        command = []
        command.extend(launch_pip)
        command.append(py_item)
        if not DEBUG:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, encoding="utf-8")
            while True:
                output = process.stdout.readline()
                if process.poll() is not None:
                    break
                if output:
                    set_wizard_install_status(percent, status, output.strip())
                    print(output.strip())
                    logger.info(output.strip())
            return_code = process.poll()
            result += return_code
            print(f"Return Code: {return_code}")

        percent += increment
        output = f" - Completed Install of {py_item}"
        set_updater_install_status(percent, status, output)
        if DEBUG:
            print(f"Percent: {percent}")
            print(f"Status:  {status}")
            print(f"Output:  {output}")
        logger.debug(f"Percent: {percent}")
        logger.debug(f"Status:  {status}")
        logger.info(f"Output:  {output}")

    time.sleep(4)

    # Install Apt dependencies
    launch_apt = ["sudo", "apt", "install"]
    status = "Installing Package Dependencies..."
    output = " - Installing APT Package Dependencies"
    set_updater_install_status(percent, status, output)

    for apt_item in apt_dependencies:
        command = []
        command.extend(launch_apt)
        command.append(apt_item)
        command.append("-y")
        if not DEBUG:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, encoding="utf-8")
            while True:
                output = process.stdout.readline()
                if process.poll() is not None:
                    break
                if output:
                    set_updater_install_status(percent, status, output.strip())
                    print(output.strip())
                    logger.info(output.strip())
            return_code = process.poll()
            result += return_code
            print(f"Return Code: {return_code}")

        percent += increment
        output = f" - Completed Install of {apt_item}"
        set_updater_install_status(percent, status, output)
        if DEBUG:
            print(f"Percent: {percent}")
            print(f"Status:  {status}")
            print(f"Output:  {output}")
        logger.debug(f"Percent: {percent}")
        logger.debug(f"Status:  {status}")
        logger.info(f"Output:  {output}")

    time.sleep(4)

    # Run system commands dependencies
    status = "Installing General Dependencies..."
    output = " - Installing General Dependencies"
    set_updater_install_status(percent, status, output)
    if DEBUG:
        print(f"Percent: {percent}")
        print(f"Status:  {status}")
        print(f"Output:  {output}")
    logger.debug(f"Percent: {percent}")
    logger.info(f"Status:  {status}")
    logger.debug(f"Output:  {output}")

    for command in command_list:
        if "sudo" in command and "python" in command:
            # replace "python" with python_exec in command list object
            command = [python_exec if item == "python" else item for item in command]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, encoding="utf-8")
        while True:
            output = process.stdout.readline()
            if process.poll() is not None:
                break
            if output:
                set_updater_install_status(percent, status, output.strip())
                print(f"{output.strip()}")
                logger.info(output.strip())
        return_code = process.poll()
        result += return_code
        print(f"Return Code: {return_code}")

        percent += increment
        output = f" - Completed General Dependency Item"
        set_updater_install_status(percent, status, output)
        if DEBUG:
            print(f"Percent: {percent}")
            print(f"Status:  {status}")
            print(f"Output:  {output}")
        logger.debug(f"Percent: {percent}")
        logger.debug(f"Status:  {status}")
        logger.debug(f"Output:  {output}")

    time.sleep(4)

    percent = 100
    status = "Finished!"
    output = " - Finished!  Restarting Server..."
    set_updater_install_status(percent, status, output)
    if DEBUG:
        print(f"Percent: {percent}")
        print(f"Status:  {status}")
        print(f"Output:  {output}")

    time.sleep(4)

    percent = 142 if reboot else 101
    set_updater_install_status(percent, status, output)

    return result


"""
==============================================================================
 Main
==============================================================================
"""
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Updater Script")
    parser.add_argument("-b", "--branch", metavar="BRANCH", type=str, required=False, help="Change Branches")
    parser.add_argument("-u", "--update", metavar="BRANCH", type=str, required=False, help="Update Current Branch")
    parser.add_argument("-r", "--remote", action="store_true", required=False, help="Update Remote Branches")
    parser.add_argument(
        "-p", "--piplist", action="store_true", required=False, help="Output PIP List packages to JSON file."
    )
    parser.add_argument(
        "-v", "--uv", action="store_true", required=False, help="Set uv flag and clear venv flag in settings"
    )
    parser.add_argument(
        "-w",
        "--rebuildwebui",
        action="store_true",
        required=False,
        help="Rebuild the React web UI from the current sources",
    )
    parser.add_argument("-l", "--legacyvenv", action="store_true", required=False, help="Set venv flag in settings")
    parser.add_argument("-d", "--debug", action="store_true", required=False, help="Enable Debug Mode")
    parser.add_argument(
        "-i",
        "--installdependencies",
        action="store_true",
        required=False,
        help="Install Dependencies for current version",
    )

    args = parser.parse_args()

    """ Setup Logger """
    if args.debug:
        log_level = logging.DEBUG
        DEBUG = True
    else:
        log_level = logging.INFO
        DEBUG = False

    logger = create_logger(
        "updater",
        filename=log_path(BUILD_LOG_NAME),
        messageformat="%(asctime)s | %(levelname)s | %(message)s",
        level=log_level,
    )

    # num_args = number of arguments passed to the script
    num_args = 0

    if args.update:
        num_args += 1
        settings = read_settings()
        current_version = settings["versions"]["server"]
        current_build = settings["versions"].get("build", 0)

        percent = 10
        status = f"Attempting Update on {args.update}..."
        output = f" - Attempting an update on branch {args.update}"
        set_updater_install_status(percent, status, output)
        time.sleep(2)

        success, status, output = install_update()

        percent = 20
        set_updater_install_status(percent, status, output)
        time.sleep(4)

        install_dependencies(current_version, current_build)
        # After the dependencies, so a rebuild sees any new node/bun the
        # migration step installed -- and so an upgrade.sh that already built
        # the bundle leaves nothing for this to do.
        rebuild_web_ui_if_stale()

    elif args.branch:
        num_args += 1
        settings = read_settings()
        current_version = settings["versions"]["server"]
        current_build = settings["versions"].get("build", 0)

        percent = 10
        status = f"Changing Branch to {args.branch}..."
        output = f" - Changing to selected branch {args.branch}"
        set_updater_install_status(percent, status, output)
        time.sleep(2)

        success, status, output = change_branch(args.branch)

        percent = 20
        set_updater_install_status(percent, status, output)
        time.sleep(4)

        install_dependencies(current_version, current_build)
        # A branch change swaps the React sources wholesale; the bundle from
        # the branch just left is exactly what must not keep being served.
        rebuild_web_ui_if_stale()

    elif args.rebuildwebui:
        num_args += 1
        # Forced: the caller asked for this build explicitly, so it runs even
        # when the mtime check would call the bundle current.
        if rebuild_web_ui_if_stale(force=True):
            set_updater_install_status(101, "Finished!", " - Web UI rebuild finished")
        else:
            # The negative sentinel is what the browser reads as "this run
            # ended, and it ended badly" -- percent above 100 means finished, so
            # reporting 101 here would have called a failed build a success.
            set_updater_install_status(
                INSTALL_FAILED_PERCENT,
                "Web UI rebuild failed",
                " - The build did not complete. The previously built interface is still being served.",
            )

    elif args.remote:
        num_args += 1
        error_msg = update_remote_branches()
        if error_msg != "":
            print(f"Error updating remote branches: {error_msg}")

    elif args.installdependencies:
        num_args += 1
        settings = read_settings()
        current_version = settings["versions"]["server"]
        current_build = settings["versions"].get("build", 0)

        percent = 10
        status = f"Installing Dependencies for Current Version..."
        output = f" - APT, Python and Command Dependencies for version {current_version} ({current_build})"
        set_updater_install_status(percent, status, output)

        install_dependencies(current_version, current_build)

    if args.piplist:
        num_args += 1
        settings = read_settings()

        # Get python executable
        python_exec = settings["globals"].get("python_exec", "python")

        if settings["globals"].get("uv", False):
            command = ["uv", "pip", "list", "--format=json"]
        else:
            command = [python_exec, "-m", "pip", "list", "--format=json"]

        pip_list = subprocess.run(command, capture_output=True, text=True)
        if pip_list.returncode == 0:
            write_generic_json(json.loads(pip_list.stdout), "pip_list.json")
            # print(f'PIP List: {pip_list.stdout}')
        else:
            print(f"Error creating PIP List: {pip_list.stderr}")
            pip_list = []
            write_generic_json(pip_list, "pip_list.json")

    if args.uv:
        num_args += 1
        settings = read_settings()
        settings["globals"]["uv"] = True
        settings["globals"]["venv"] = True
        settings["globals"]["python_exec"] = ".venv/bin/python"
        write_settings(settings)
        print("Updated settings to set uv flag and set venv flag")

    if args.legacyvenv:
        num_args += 1
        settings = read_settings()
        settings["globals"]["uv"] = False
        settings["globals"]["venv"] = True
        settings["globals"]["python_exec"] = "bin/python"
        write_settings(settings)
        print("Updated settings to set venv flag and clear uv flag")

    """ If no valid arguments are passed, print help message """
    if num_args == 0:
        print("No valid arguments provided. Use -h for help.")
