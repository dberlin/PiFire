#!/usr/bin/env python3
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

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from common import datastore
from common.acados_build import run_acados_build
from common.common import (
    create_logger,
    log_path,
    read_updater_manifest,
    semantic_ver_is_lower,
    write_generic_json,
    write_log,
)
from common.install_log import INSTALL_FAILED_PERCENT
from common.persistence.install_state import (
    set_updater_install_status,
    set_wizard_install_status,
)
from common.persistence.runtime import (
    read_settings,
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

#: This file sits at the repo root, beside web-react/ and updater/.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

#: The same logger __main__ attaches a file handler to -- logging caches by
#: name, so both names are one object. Bound here as well so the functions below
#: can log when they are called from an import rather than from the CLI.
logger = logging.getLogger("updater")


@contextmanager
def update_startup_transaction(repo_root=None):
    """Serialize source/runtime mutation with control's startup preflight."""
    repository = Path(repo_root or REPO_ROOT).resolve()
    lock_path = repository / "controller" / "_native" / "update-startup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


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
            if line.startswith("("):
                # `(HEAD detached at abc1234)`, and the same shape for a detached
                # tag or an interrupted rebase. Prose describing where HEAD is,
                # not a branch anyone can check out.
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


def detached_head():
    """The commit HEAD is parked on when the checkout is not on a branch, else None.

    `symbolic-ref` is the question being asked -- does HEAD name a branch --
    and it answers non-zero when it does not, which is the only reliable
    signal. Reading it out of `git branch` cannot work: git prints the
    placeholder `* (HEAD detached at abc1234)` on the starred line, which is
    prose, not a ref, and nothing downstream can tell the two apart.
    """
    on_branch = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
    if on_branch.returncode == 0:
        return None
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return sha.stdout.strip() if sha.returncode == 0 else "an unknown commit"


DETACHED_HEAD_ERROR = "ERROR Not On A Branch"


def get_branch():
    """(branch name, error). A detached HEAD is an ERROR, not a branch.

    Every caller builds `origin/{branch}` out of this. Handing back git's
    `(HEAD detached at abc1234)` placeholder made all of them ask git for a ref
    named `origin/(HEAD detached at abc1234)` -- and made the updater page fire
    that string, unquoted and full of parentheses and spaces, into a shell.
    """
    detached = detached_head()
    if detached is not None:
        return (
            DETACHED_HEAD_ERROR,
            f"This checkout is not on a branch -- HEAD is detached at {detached}. Check out a branch before updating.",
        )
    # --show-current is only in later versions of git, and unfortunately buster does not have this
    on_branch = subprocess.run(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], capture_output=True, text=True)
    if on_branch.returncode != 0:
        return ("ERROR Getting Current Branch", on_branch.stderr)
    return (on_branch.stdout.strip(), "")


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
    # Bound before the branch is looked up, not inside it: the error path below
    # reads this whether or not a branch was passed in.
    error_msg2 = ""
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
    _remote, error_msg2 = get_remote_url()
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
        result = "ERROR Getting Remote URL or Branch."
        error_msg = " | ".join(m for m in (error_msg1, error_msg2) if m)
    return (result, error_msg)


def get_log(num_commits=10):
    """The last `num_commits` on origin/<branch>, one per line.

    PLAIN TEXT. Its only consumer is /api/update/log, which is JSON served into
    a <pre>: the `<br>`s this used to substitute for newlines were written for
    a Flask template that no longer calls it, and they reached the browser as
    four literal characters between every commit instead of a line break.
    """
    branch, error_msg = get_branch()
    if error_msg == "":
        command = ["git", "log", f"origin/{branch}", f"-{num_commits}", '--pretty="%h - %cr : %s"']
        log = subprocess.run(command, capture_output=True, text=True)
        if log.returncode == 0:
            result = log.stdout.replace('"', "")
        else:
            result = "ERROR Getting Log."
            error_msg = log.stderr
    else:
        result = "ERROR Getting Branch Name."
    return (result, error_msg)


def get_remote_version():
    _remote_url, error_msg = get_remote_url()
    current_branch, branch_error = get_branch()

    # A detached checkout has no origin/<branch> to ask about, but it does have
    # a history -- so the question becomes "the newest release tag reachable
    # from where HEAD actually is". Reporting an error instead would blank the
    # version on a checkout that is perfectly capable of naming its own.
    reachable_from = f"origin/{current_branch}" if branch_error == "" else "HEAD"
    if error_msg == "":
        # --force, because a tag that MOVED upstream otherwise fails the whole
        # fetch with "would clobber existing tag" -- permanently, for every
        # clone that already had the old one. Tags are read-only input here:
        # this only ever displays the newest one, so taking the remote's answer
        # is the whole point.
        fetch_command = ["git", "fetch", "--tags", "--force"]
        fetch = subprocess.run(fetch_command, capture_output=True, text=True)
        # A failed fetch is not fatal. The tags already on disk still answer
        # "what version is this checkout", just possibly not the very newest --
        # and a network blip should not blank the version line.
        if fetch.returncode != 0:
            logger.warning(f"Could not fetch tags: {fetch.stderr.strip()}")

        # Now get tags that contain commits from the current branch
        # This command finds tags that are reachable from the branch
        command = ["git", "tag", "--sort=v:refname", "--merged", reachable_from]
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
        result = "ERROR Getting Remote URL."
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


@dataclass(frozen=True)
class UpdateSnapshot:
    revision: str
    branch: str
    runtime_target: str | None
    metadata_path: Path


def ensure_acados_prerequisites(repo_root=REPO_ROOT):
    """Install only the native toolchain needed before the checkout moves."""
    command = ["bash", str(Path(repo_root) / "updater" / "install-acados-prerequisites.sh"), "--prerequisites-only"]
    status = "Checking acados native prerequisites..."
    process = subprocess.Popen(
        command,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for raw_line in process.stdout:
        _publish(15, status, raw_line.rstrip("\r\n"))
    code = process.wait()
    if code != 0:
        return False, f"acados prerequisite installation exited {code}"
    return True, "acados prerequisites ready"


def capture_update_snapshot(repo_root=REPO_ROOT):
    """Persist the exact source and runtime authorities before checkout."""
    repository = Path(repo_root).resolve()
    branch, branch_error = get_branch()
    if branch_error:
        raise RuntimeError(branch_error)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if revision.returncode != 0 or not revision.stdout.strip():
        raise RuntimeError(revision.stderr.strip() or "could not record the current source revision")

    current = repository / "controller" / "_native" / "current"
    if current.is_symlink():
        runtime_target = os.readlink(current)
    elif current.exists():
        raise RuntimeError(f"{current} is not a symbolic link")
    else:
        runtime_target = None
    metadata_path = current.parent / "update-transaction.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = UpdateSnapshot(revision.stdout.strip(), branch, runtime_target, metadata_path)
    temporary = metadata_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema": 1,
                "previous_revision": snapshot.revision,
                "previous_branch": snapshot.branch,
                "runtime_target": snapshot.runtime_target,
            },
            sort_keys=True,
        )
        + "\n"
    )
    os.replace(temporary, metadata_path)
    return snapshot


def restore_update_snapshot(snapshot, repo_root=REPO_ROOT):
    """Restore source and runtime pointers after a failed source/native gate."""
    repository = Path(repo_root).resolve()
    checkout = subprocess.run(
        ["git", "checkout", "-f", snapshot.branch],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    reset = subprocess.run(
        ["git", "reset", "--hard", snapshot.revision],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    current = repository / "controller" / "_native" / "current"
    current.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.runtime_target is None:
        current.unlink(missing_ok=True)
    else:
        temporary = current.with_name(f".current.rollback-{os.getpid()}")
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(snapshot.runtime_target)
        os.replace(temporary, current)
    snapshot.metadata_path.unlink(missing_ok=True)
    if checkout.returncode != 0 or reset.returncode != 0:
        details = " | ".join(part for part in (checkout.stderr.strip(), reset.stderr.strip()) if part)
        raise RuntimeError(details or "source rollback failed")


def _change_branch_checkout(branch_target):
    command = ["git", "checkout", "-f", branch_target]
    target = subprocess.run(command, capture_output=True, text=True)
    if target.returncode == 0:
        return True, "Branch Changed Successfully", " - " + target.stdout + target.stderr
    return False, "ERROR Changing Branch", " - " + target.stderr


def _install_update_checkout():
    branch, error_msg1 = get_branch()
    _remote, error_msg2 = get_remote_url()
    if error_msg1 == "" and error_msg2 == "":
        fetch = subprocess.run(["git", "fetch"], capture_output=True, text=True)
        reset = subprocess.run(["git", "reset", "--hard", "HEAD"], capture_output=True, text=True)
        merge = subprocess.run(["git", "merge", f"origin/{branch}"], capture_output=True, text=True)
        if fetch.returncode == 0 and reset.returncode == 0 and merge.returncode == 0:
            return True, "Update Completed Successfully", " - " + fetch.stdout + reset.stdout + merge.stdout
        return False, "ERROR Performing Update.", " - " + fetch.stdout + reset.stdout + merge.stdout
    # A detached HEAD has a specific remedy in get_branch()'s message.
    return (
        False,
        "ERROR Getting Remote URL or Branch.",
        " - " + " | ".join(message for message in (error_msg1, error_msg2) if message),
    )


def _source_native_transaction(checkout):
    ready, prerequisite_output = ensure_acados_prerequisites(REPO_ROOT)
    if not ready:
        return False, "ERROR Preparing acados Native Prerequisites", f" - {prerequisite_output}"
    try:
        snapshot = capture_update_snapshot(REPO_ROOT)
    except Exception as exc:
        return False, "ERROR Recording Update Rollback State", f" - {exc}"

    success, status, output = checkout()
    if success:
        native_status = "Building acados native runtime..."
        try:
            native_code = run_acados_build(
                REPO_ROOT,
                lambda line: _publish(25, native_status, line),
                if_needed=True,
            )
        except Exception as exc:
            native_code = -1
            output = f"{output} | native runner failed: {exc}"
        if native_code == 0:
            snapshot.metadata_path.unlink(missing_ok=True)
            return True, status, output
        success = False
        status = "ERROR Building acados Native Runtime"
        output = f"{output} | acados native rebuild exited {native_code}"

    try:
        restore_update_snapshot(snapshot, REPO_ROOT)
    except Exception as exc:
        output = f"{output} | rollback failed: {exc}"
    return False, status, output


def change_branch(branch_target):
    return _source_native_transaction(lambda: _change_branch_checkout(branch_target))


def install_update():
    return _source_native_transaction(_install_update_checkout)


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


#: The percent the browser reads as "this run is over". Anything above 100
#: means finished; 142 additionally means a reboot is needed before the new
#: code can load.
FINISHED_PERCENT = 101
REBOOT_REQUIRED_PERCENT = 142


def publish_finished(reboot):
    """End a run so the page stops polling.

    Published by whatever ran LAST, not by install_dependencies: an update
    rebuilds the web UI after the dependencies, and that rebuild publishes
    progress of its own. Ending inside install_dependencies left every update
    sitting at percent 95 -- "Checking Web UI..." -- against a process that had
    already exited.
    """
    percent = REBOOT_REQUIRED_PERCENT if reboot else FINISHED_PERCENT
    set_updater_install_status(percent, "Finished!", " - Finished!  Restarting Server...")


def report_failure(status, output):
    """End a run on a step that failed, rather than carrying on to report a
    version that was never installed."""
    logger.error(output)
    set_updater_install_status(INSTALL_FAILED_PERCENT, status, output)
    sys.exit(1)


def run_acados_rebuild(repo_root: str | Path = REPO_ROOT) -> None:
    """Conditionally rebuild the native runtime and publish terminal status."""
    status = "Rebuilding acados native runtime..."
    code = run_acados_build(
        repo_root,
        lambda line: _publish(25, status, line),
        if_needed=True,
    )
    if code != 0:
        report_failure(
            "Acados rebuild failed",
            f" - The conditional acados rebuild exited {code}.",
        )
    publish_finished(False)


def run_update(branch):
    """The -u flow: pull, gate native, install dependencies, then finish."""
    settings = read_settings()
    current_version = settings["versions"]["server"]
    current_build = settings["versions"].get("build", 0)

    with update_startup_transaction():
        set_updater_install_status(
            10,
            f"Attempting Update on {branch}...",
            f" - Attempting an update on branch {branch}",
        )
        time.sleep(2)
        success, status, output = install_update()
        set_updater_install_status(20, status, output)
        if not success:
            report_failure(status, output)
        time.sleep(4)
        dependency_result, reboot = install_dependencies(current_version, current_build)
        if dependency_result != 0:
            report_failure(
                "ERROR Installing Dependencies and Migrations",
                f" - dependency steps exited {dependency_result}",
            )

    # Web remains outside the control transaction: it is diagnostic and does
    # not consume the native ABI. Control may start once the cursor is durable.
    rebuild_web_ui_if_stale()
    publish_finished(reboot)


def run_branch_change(branch):
    """The -b flow uses the same source/native/startup transaction."""
    settings = read_settings()
    current_version = settings["versions"]["server"]
    current_build = settings["versions"].get("build", 0)

    with update_startup_transaction():
        set_updater_install_status(
            10,
            f"Changing Branch to {branch}...",
            f" - Changing to selected branch {branch}",
        )
        time.sleep(2)
        success, status, output = change_branch(branch)
        set_updater_install_status(20, status, output)
        if not success:
            report_failure(status, output)
        time.sleep(4)
        dependency_result, reboot = install_dependencies(current_version, current_build)
        if dependency_result != 0:
            report_failure(
                "ERROR Installing Dependencies and Migrations",
                f" - dependency steps exited {dependency_result}",
            )

    rebuild_web_ui_if_stale()
    publish_finished(reboot)


def record_installed_version(manifest=None):
    """Bring the stored version up to the manifest's. Returns whether it moved.

    settings["versions"] is the migration CURSOR: install_dependencies runs every
    updater_manifest entry above it, and skips everything at or below. Nothing
    wrote it back on a SQLite install -- the only code that does lives in
    common/settings_migration.py, reached exclusively through
    read_settings_file(), the JSON-file reader that now runs on first boot and a
    backup restore and nowhere else. So an updated grill kept the version it was
    first installed with: the updater page reported that version forever, and
    every update re-ran every migration above it.

    Forward only. A branch change onto older code leaves the cursor where it is,
    which is what install_dependencies' own walk already assumes; unwinding a
    version is settings_migration's downgrade path, not this.
    """
    manifest = manifest if manifest is not None else read_updater_manifest()
    target = manifest.get("metadata", {}).get("versions")
    if not target or "server" not in target:
        logger.error("updater_manifest.json has no metadata.versions.server; leaving the version alone")
        return False

    settings = read_settings()
    stored = settings["versions"]["server"]
    stored_build = settings["versions"].get("build", 0)
    target_build = target.get("build", 0)
    if semantic_ver_is_lower(target["server"], stored) or (target["server"] == stored and target_build <= stored_build):
        return False

    settings["versions"] = dict(target)
    write_settings(settings)
    logger.info(
        f"Recorded installed version {target['server']} build {target_build} (was {stored} build {stored_build})"
    )
    return True


def _migration_is_pending(current_version_string, current_build, version_info):
    return semantic_ver_is_lower(current_version_string, version_info["version"]) or (
        current_version_string == version_info["version"] and (current_build or 0) < version_info["build"]
    )


def _run_acados_bootstrap_migrations(updater_info, current_version_string, current_build):
    """Run the new-tree native bootstrap before inspecting any dependency."""
    status = "Bootstrapping acados native runtime..."
    for version_info in updater_info["versions"]:
        if not version_info.get("acados_bootstrap") or not _migration_is_pending(
            current_version_string,
            current_build,
            version_info,
        ):
            continue
        for section in version_info["dependencies"].values():
            for command in section["command_list"]:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    set_updater_install_status(25, status, line)
                    logger.info(line)
                return_code = process.wait()
                if return_code != 0:
                    return return_code
    return 0


def install_dependencies(current_version_string="0.0.0", current_build=None):
    updaterInfo = read_updater_manifest()
    bootstrap_result = _run_acados_bootstrap_migrations(
        updaterInfo,
        current_version_string,
        current_build,
    )
    if bootstrap_result != 0:
        return bootstrap_result, False

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
    set_updater_install_status(percent, status, output)
    time.sleep(2)

    # Get ALL PyPi & Apt dependencies and commands to install / update
    py_dependencies = []
    apt_dependencies = []
    command_list = []
    reboot = False

    for version_info in updaterInfo["versions"]:
        """ Walk list of versions in updater_manifest, check for dependencies """
        if _migration_is_pending(current_version_string, current_build, version_info):
            if version_info.get("acados_bootstrap"):
                continue
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
        output = " - Completed General Dependency Item"
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

    # Only once every migration above the stored version has actually run. The
    # version IS the cursor those migrations are selected by, so recording it
    # after a failed step would skip them permanently on the next run.
    if result == 0:
        record_installed_version(updaterInfo)
    else:
        logger.error(f"Dependency steps returned {result}; leaving the recorded version alone so they run again")

    # The terminal sentinel is the CALLER's to publish, not this function's.
    # An update runs the web UI rebuild after this returns, and that rebuild
    # publishes progress of its own -- so ending here left every update sitting
    # at percent 95 ("Checking Web UI...") with the browser polling a run that
    # had already finished.
    return result, reboot


"""
==============================================================================
 Main
==============================================================================
"""
if __name__ == "__main__":
    # updater.py runs as its own standalone process (upgrade.sh launches it
    # directly), so it gets no benefit from app.py's or control.py's init()
    # call. Must run before the first read_settings()/write_settings() call
    # below -- see app.py:39 and control.py:70, which do the same.
    datastore.init()

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
    parser.add_argument(
        "--rebuild-acados",
        action="store_true",
        required=False,
        help="Conditionally rebuild the acados native runtime",
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

    def report_crash(exc_type, exc, tb):
        """Publish an unhandled exception as a terminal failure before dying.

        This process is detached: nothing waits on it and nothing sees its
        traceback. Its only channel to the browser is the install status, and a
        run that raises simply stops writing to it -- leaving the page polling
        "Starting Update..." forever, which is indistinguishable from a slow
        update. The negative percent is what ends that poll.
        """
        logger.error("Update failed: %s", exc, exc_info=(exc_type, exc, tb))
        set_updater_install_status(INSTALL_FAILED_PERCENT, "Update failed", f"{exc_type.__name__}: {exc}")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = report_crash

    # num_args = number of arguments passed to the script
    num_args = 0

    if args.update:
        num_args += 1
        run_update(args.update)

    elif args.branch:
        num_args += 1
        run_branch_change(args.branch)

    elif args.rebuildwebui:
        num_args += 1
        # Forced: the caller asked for this build explicitly, so it runs even
        # when the mtime check would call the bundle current.
        if rebuild_web_ui_if_stale(force=True):
            set_updater_install_status(FINISHED_PERCENT, "Finished!", " - Web UI rebuild finished")
        else:
            # The negative sentinel is what the browser reads as "this run
            # ended, and it ended badly" -- percent above 100 means finished, so
            # reporting 101 here would have called a failed build a success.
            set_updater_install_status(
                INSTALL_FAILED_PERCENT,
                "Web UI rebuild failed",
                " - The build did not complete. The previously built interface is still being served.",
            )

    elif args.rebuild_acados:
        num_args += 1
        run_acados_rebuild()

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
        status = "Installing Dependencies for Current Version..."
        output = f" - APT, Python and Command Dependencies for version {current_version} ({current_build})"
        set_updater_install_status(percent, status, output)

        dependency_result, reboot = install_dependencies(current_version, current_build)
        if dependency_result != 0:
            report_failure(
                "ERROR Installing Dependencies and Migrations",
                f" - dependency steps exited {dependency_result}",
            )
        publish_finished(reboot)

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
