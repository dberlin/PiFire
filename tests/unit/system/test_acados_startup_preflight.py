from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
START_CONTROL = REPOSITORY / "auto-install" / "start-control.sh"
CONTROL_CONF = REPOSITORY / "auto-install" / "supervisor" / "control.conf"
WEBAPP_CONF = REPOSITORY / "auto-install" / "supervisor" / "webapp.conf"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + body)
    path.chmod(0o755)


def _startup_tree(tmp_path: Path, rebuild_body: str) -> tuple[Path, Path]:
    repo = tmp_path / "pifire"
    (repo / "auto-install").mkdir(parents=True)
    shutil.copy2(START_CONTROL, repo / "auto-install" / "start-control.sh")
    (repo / ".venv" / "bin").mkdir(parents=True)
    marker = tmp_path / "control-exec.json"
    _write_executable(repo / "rebuild-acados.sh", 'cd "$(dirname "$0")"\n' + rebuild_body)
    _write_executable(
        repo / ".venv" / "bin" / "python",
        'printf "%s\\n%s\\n%s\\n%s\\n" "$PWD" "${HOME-unset}" "${PATH-unset}" "$*" >"$CONTROL_MARKER"\n',
    )
    return repo, marker


def test_control_startup_handles_empty_service_home_and_path_then_execs_control(tmp_path: Path) -> None:
    repo, marker = _startup_tree(tmp_path, 'printf "native-ok\\n"\n')
    completed = subprocess.run(
        ["/bin/bash", str(repo / "auto-install" / "start-control.sh")],
        env={"HOME": "", "PATH": "", "CONTROL_MARKER": str(marker)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    cwd, home, path, args = marker.read_text().splitlines()
    assert Path(cwd) == repo
    assert home
    assert "/usr/bin" in path and "/bin" in path
    assert args == "control.py"


def test_control_is_never_executed_after_failed_native_preflight(tmp_path: Path) -> None:
    repo, marker = _startup_tree(tmp_path, 'printf "native-failed\\n"\nexit 17\n')
    completed = subprocess.run(
        ["/bin/bash", str(repo / "auto-install" / "start-control.sh")],
        env={"HOME": "", "PATH": "", "CONTROL_MARKER": str(marker)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 17
    assert not marker.exists()
    assert "native-failed" in completed.stdout


def test_supervisor_preflights_only_control_and_leaves_web_available() -> None:
    control = CONTROL_CONF.read_text()
    web = WEBAPP_CONF.read_text()

    assert "command=/usr/local/bin/pifire/auto-install/start-control.sh" in control
    assert "control.py" not in control
    assert "start-control.sh" not in web
    assert "gunicorn" in web


def test_updater_and_startup_share_the_native_rebuild_lock_boundary(tmp_path: Path) -> None:
    repo, marker = _startup_tree(
        tmp_path,
        'lock="$PWD/native-test.lock"\n'
        'while ! mkdir "$lock" 2>/dev/null; do sleep 0.02; done\n'
        'trap \'rmdir "$lock"\' EXIT\n'
        'printf "enter:%s\\n" "$PPID" >>"$TRACE"\n'
        'sleep 0.15\n'
        'printf "exit:%s\\n" "$PPID" >>"$TRACE"\n',
    )
    trace = tmp_path / "trace.log"
    env = {**os.environ, "TRACE": str(trace), "CONTROL_MARKER": str(marker)}
    updater_call = (
        "from common.acados_build import run_acados_build; "
        f"raise SystemExit(run_acados_build({str(repo)!r}, lambda line: None, if_needed=True))"
    )

    first = subprocess.Popen([sys.executable, "-c", updater_call], cwd=REPOSITORY, env=env)
    time.sleep(0.03)
    second = subprocess.Popen(["/bin/bash", str(repo / "auto-install" / "start-control.sh")], env=env)
    assert first.wait(timeout=5) == 0
    assert second.wait(timeout=5) == 0

    phases = [line.split(":", 1)[0] for line in trace.read_text().splitlines()]
    assert phases == ["enter", "exit", "enter", "exit"]



@pytest.mark.parametrize("flow_name", ["update", "branch"])
def test_startup_waits_for_real_update_transaction_through_dependency_cursor(
    tmp_path: Path, monkeypatch, flow_name: str
) -> None:
    import threading
    import updater

    repo, marker = _startup_tree(
        tmp_path,
        'printf "native:%s\\n" "$PPID" >>"$TRACE"\n',
    )
    trace = tmp_path / "transaction-trace.log"
    dependency_started = threading.Event()
    release_dependency = threading.Event()
    monkeypatch.setattr(updater, "REPO_ROOT", str(repo))
    monkeypatch.setattr(updater, "logger", __import__("logging").getLogger("transaction-lock-test"), raising=False)
    monkeypatch.setattr(updater, "read_settings", lambda: {"versions": {"server": "1.12.0", "build": 92}})
    monkeypatch.setattr(updater, "set_updater_install_status", lambda *args: None)
    monkeypatch.setattr(updater, "time", type("_Time", (), {"sleep": staticmethod(lambda _: None)}))
    monkeypatch.setattr(
        updater,
        "install_update",
        lambda: (True, "Update Completed Successfully", " - native ready"),
    )
    monkeypatch.setattr(
        updater,
        "change_branch",
        lambda branch: (True, "Branch Changed Successfully", f" - {branch} native ready"),
    )

    def dependencies(*args):
        dependency_started.set()
        assert release_dependency.wait(timeout=5)
        with trace.open("a") as handle:
            handle.write("dependency-cursor\n")
        return 0, False

    monkeypatch.setattr(updater, "install_dependencies", dependencies)
    monkeypatch.setattr(updater, "rebuild_web_ui_if_stale", lambda: True)
    monkeypatch.setattr(updater, "publish_finished", lambda reboot: None)
    target = updater.run_update if flow_name == "update" else updater.run_branch_change
    thread = threading.Thread(target=target, args=("development",), daemon=True)
    thread.start()
    assert dependency_started.wait(timeout=5)

    env = {**os.environ, "TRACE": str(trace), "CONTROL_MARKER": str(marker)}
    startup = subprocess.Popen(["/bin/bash", str(repo / "auto-install" / "start-control.sh")], env=env)
    time.sleep(0.15)
    assert not marker.exists(), "control exec raced ahead of dependency/settings/cursor completion"
    release_dependency.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert startup.wait(timeout=5) == 0

    lines = trace.read_text().splitlines()
    assert lines[0] == "dependency-cursor"
    assert lines[1].startswith("native:")
    assert marker.exists()