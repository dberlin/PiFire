from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


REPOSITORY = Path(__file__).resolve().parents[3]
MIGRATION = REPOSITORY / "updater" / "install-acados-prerequisites.sh"
MANIFEST = REPOSITORY / "updater" / "updater_manifest.json"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def _migration_tree(tmp_path: Path, distro: str) -> tuple[Path, dict[str, str], Path, Path]:
    repo = tmp_path / "pifire"
    (repo / "updater").mkdir(parents=True)
    shutil.copy2(MIGRATION, repo / "updater" / MIGRATION.name)
    (repo / "auto-install" / "supervisor").mkdir(parents=True)
    (repo / "auto-install" / "supervisor" / "control.conf").write_text(
        "[program:control]\ncommand=/usr/local/bin/pifire/auto-install/start-control.sh\n"
    )
    native = repo / "controller" / "_native"
    (native / "releases" / "old").mkdir(parents=True)
    (native / "current").symlink_to("releases/old")

    supervisor = tmp_path / "etc"
    if distro == "debian":
        target = supervisor / "supervisor" / "conf.d" / "control.conf"
        os_release = "ID=debian\nID_LIKE=debian\n"
    else:
        target = supervisor / "supervisord.d" / "control.ini"
        os_release = "ID=fedora\nID_LIKE=fedora\n"
    target.parent.mkdir(parents=True)
    target.write_text("[program:control]\ncommand=old-control\nuser=pitmaster\n")

    db = repo / "pifire.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    _write_executable(fake_bin / "sudo", 'printf "sudo %s\\n" "$*" >>"$COMMAND_LOG"\nexec "$@"\n')
    _write_executable(fake_bin / "apt-get", 'printf "apt-get %s\\n" "$*" >>"$COMMAND_LOG"\n')
    _write_executable(fake_bin / "dnf", 'printf "dnf %s\\n" "$*" >>"$COMMAND_LOG"\n')
    _write_executable(fake_bin / "cmake", "exit 1\n")
    _write_executable(fake_bin / "cc", "exit 1\n")
    _write_executable(fake_bin / "c++", "exit 1\n")
    _write_executable(
        fake_bin / "git",
        'printf "git %s\\n" "$*" >>"$COMMAND_LOG"\n'
        'case "$*" in\n'
        '  "rev-parse HEAD@{1}") echo old-revision ;;\n'
        '  "rev-parse --verify old-revision^{commit}") echo old-revision ;;\n'
        '  "symbolic-ref --quiet --short HEAD") echo main ;;\n'
        '  "reflog -1 --format=%gs") echo "merge origin/main: Fast-forward" ;;\n'
        'esac\n',
    )
    _write_executable(repo / "rebuild-acados.sh", 'printf "rebuild %s\\n" "$*" >>"$COMMAND_LOG"\n')

    os_release_path = tmp_path / "os-release"
    os_release_path.write_text(os_release)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "COMMAND_LOG": str(log),
        "PIFIRE_REPO_ROOT": str(repo),
        "PIFIRE_OS_RELEASE": str(os_release_path),
        "PIFIRE_SUPERVISOR_ROOT": str(supervisor),
        "PIFIRE_DB_PATH": str(db),
        "PIFIRE_FORCE_PREREQUISITES": "1",
    }
    return repo, env, target, log


@pytest.mark.parametrize(
    ("distro", "package_line", "target_suffix"),
    [
        ("debian", "apt-get install -y build-essential cmake", "supervisor/conf.d/control.conf"),
        ("fedora", "dnf -y install gcc gcc-c++ make cmake", "supervisord.d/control.ini"),
    ],
)
def test_historical_migration_uses_native_packages_and_supervisor_path_without_restart(
    tmp_path: Path, distro: str, package_line: str, target_suffix: str
) -> None:
    repo, env, target, log = _migration_tree(tmp_path, distro)

    completed = subprocess.run(
        ["/bin/bash", str(repo / "updater" / MIGRATION.name)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    commands = log.read_text()
    assert package_line in commands
    assert "rebuild --if-needed" in commands
    assert str(target).endswith(target_suffix)
    installed = target.read_text()
    assert "command=/usr/local/bin/pifire/auto-install/start-control.sh" in installed
    assert installed.rstrip().endswith("user=pitmaster")
    assert "restart" not in commands
    assert "supervisorctl" not in commands
    assert "systemctl" not in commands


def _run_failed_pre_migration_flow(tmp_path: Path, *, branch_change: bool) -> tuple[Path, Path, str, subprocess.CompletedProcess[str]]:
    repo, env, target, log = _migration_tree(tmp_path, "debian")
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    prior_branch = "stable" if branch_change else "main"
    reflog = "checkout: moving from stable to development" if branch_change else "merge origin/main: Fast-forward"
    _write_executable(
        fake_bin / "git",
        'printf "git %s\\n" "$*" >>"$COMMAND_LOG"\n'
        'case "$*" in\n'
        '  "rev-parse HEAD@{1}") echo old-revision ;;\n'
        '  "rev-parse --verify old-revision^{commit}") echo old-revision ;;\n'
        f'  "symbolic-ref --quiet --short HEAD") echo {"development" if branch_change else "main"} ;;\n'
        f'  "reflog -1 --format=%gs") echo "{reflog}" ;;\n'
        'esac\n',
    )
    _write_executable(
        repo / "rebuild-acados.sh",
        'rm -f controller/_native/current\n'
        'ln -s releases/broken controller/_native/current\n'
        'printf "rebuild %s\\n" "$*" >>"$COMMAND_LOG"\n'
        'exit 23\n',
    )
    marker = tmp_path / "old-flow-continued"
    restart = tmp_path / "restart-called"
    wrapper = (
        "import pathlib, subprocess, sys; "
        f"code=subprocess.call(['/bin/bash', {str(repo / 'updater' / MIGRATION.name)!r}]); "
        f"pathlib.Path({str(marker)!r}).write_text('Finished!'); "
        f"pathlib.Path({str(restart)!r}).write_text('restart'); "
        "sys.exit(code)"
    )
    completed = subprocess.run([sys.executable, "-c", wrapper], cwd=repo, env=env, capture_output=True, text=True)

    assert not marker.exists(), "the pre-migration updater continued and overwrote terminal failure with Finished"
    assert not restart.exists(), "the pre-migration updater reached its restart path after native failure"
    commands = log.read_text()
    assert f"git checkout -f {prior_branch}" in commands
    assert "git reset --hard old-revision" in commands
    assert os.readlink(repo / "controller" / "_native" / "current") == "releases/old"
    assert target.read_text() == "[program:control]\ncommand=old-control\nuser=pitmaster\n"
    return repo, Path(env["PIFIRE_DB_PATH"]), commands, completed


@pytest.mark.parametrize("branch_change", [False, True], ids=["ordinary-update", "branch-change"])
def test_pre_migration_updater_rolls_back_and_terminates_on_native_failure(
    tmp_path: Path, branch_change: bool
) -> None:
    _, db, commands, completed = _run_failed_pre_migration_flow(tmp_path, branch_change=branch_change)

    assert completed.returncode != 0
    with sqlite3.connect(db) as conn:
        status = dict(conn.execute("SELECT key, value FROM kv WHERE key LIKE 'updater:%'"))
    assert json.loads(status["updater:percent"]) < 0
    assert "failed" in json.loads(status["updater:status"]).lower()
    assert "apt-get install" in commands
    assert "rebuild --if-needed" in commands


def test_manifest_bootstrap_is_the_first_acados_migration_and_uses_no_python_environment() -> None:
    manifest = json.loads(MANIFEST.read_text())
    acados_entries = [item for item in manifest["versions"] if item.get("acados_bootstrap")]

    assert len(acados_entries) == 1
    entry = acados_entries[0]
    commands = [command for section in entry["dependencies"].values() for command in section["command_list"]]
    assert commands == [["bash", "/usr/local/bin/pifire/updater/install-acados-prerequisites.sh"]]
    assert all(not section["py_dependencies"] and not section["apt_dependencies"] for section in entry["dependencies"].values())


def test_fresh_installers_select_platform_toolchains_and_gate_after_python_sync() -> None:
    expected = {
        "auto-install/install.sh": ("build-essential", "cmake"),
        "auto-install/pifire-dietpi.sh": ("build-essential", "cmake"),
        "auto-install/install-debian.sh": ("build-essential", "cmake"),
        "auto-install/install-fedora.sh": ("gcc", "gcc-c++", "cmake"),
    }
    for relative, packages in expected.items():
        text = (REPOSITORY / relative).read_text()
        for package in packages:
            assert package in text, f"{relative} does not install {package}"
        sync = text.index("uv sync --no-dev --inexact")
        native = text.index("pifire_rebuild_acados", sync)
        supervisor = text.lower().index("configuring supervisor", native)
        assert sync < native < supervisor

    fedora = (REPOSITORY / "auto-install" / "install-fedora.sh").read_text()
    assert "build-essential" not in fedora
