import io
import shlex
import sys

import pytest

import display_launch


def test_bare_for_spi_display():
    settings = {"modules": {"display": "st7789_240x320"}}
    argv, env_updates = display_launch.build_launch_argv(settings, {})
    assert argv == [sys.executable, "display_process.py"]
    assert env_updates == {}


def test_bare_for_none_display():
    settings = {"modules": {"display": "none"}}
    argv, env_updates = display_launch.build_launch_argv(settings, {})
    assert argv == [sys.executable, "display_process.py"]
    assert env_updates == {}


def test_bare_for_pygame_display():
    settings = {"modules": {"display": "dsi_800x480t"}}
    argv, env_updates = display_launch.build_launch_argv(settings, {})
    assert argv == [sys.executable, "display_process.py"]
    assert env_updates == {}


def test_sway_for_qtquick_flex():
    settings = {"modules": {"display": "qtquick_flex"}}
    argv, env_updates = display_launch.build_launch_argv(settings, {"XDG_RUNTIME_DIR": "/run/user/1000"})
    assert argv == ["sway", "-c", display_launch.SWAY_KIOSK_CONFIG]
    assert env_updates["QT_QPA_PLATFORM"] == "wayland"
    assert env_updates["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert env_updates["PIFIRE_DISPLAY_CMD"] == shlex.join([sys.executable, "display_process.py"])


def test_sway_for_qtquick_dsi():
    settings = {"modules": {"display": "qtquick_dsi_1280x720t"}}
    argv, _env = display_launch.build_launch_argv(settings, {})
    assert argv == ["sway", "-c", display_launch.SWAY_KIOSK_CONFIG]


def test_xdg_runtime_dir_preserved_when_set():
    settings = {"modules": {"display": "qtquick_flex"}}
    _argv, env_updates = display_launch.build_launch_argv(settings, {"XDG_RUNTIME_DIR": "/custom/run"})
    assert env_updates["XDG_RUNTIME_DIR"] == "/custom/run"


def test_xdg_runtime_dir_defaults_to_run_user_uid(monkeypatch):
    monkeypatch.setattr(display_launch.os, "getuid", lambda: 0)
    settings = {"modules": {"display": "qtquick_flex"}}
    _argv, env_updates = display_launch.build_launch_argv(settings, {})
    assert env_updates["XDG_RUNTIME_DIR"] == "/run/user/0"


def test_build_launch_argv_does_not_mutate_env():
    settings = {"modules": {"display": "qtquick_flex"}}
    env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
    display_launch.build_launch_argv(settings, env)
    assert env == {"XDG_RUNTIME_DIR": "/run/user/1000"}


@pytest.mark.parametrize(
    "line",
    [
        "00:00:55.148 [ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: "
        "Atomic commit failed: Device or resource busy\n",
        "00:00:55.148 [ERROR] [wlr] [backend/drm/atomic.c:912] connector HDMI-A-1: "
        "Atomic commit failed: Device or resource busy\n",
        "00:00:55.148 [ERROR] [sway/desktop/output.c:300] Page-flip failed on output DP-1\n",
        "00:00:55.148 [ERROR] [sway/desktop/output.c:411] Page-flip failed on output HDMI-A-1\n",
    ],
)
def test_known_powered_down_output_errors_are_ignored(line):
    assert display_launch._is_ignored_sway_stderr(line)


@pytest.mark.parametrize(
    "line",
    [
        "[ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: "
        "Atomic commit failed: Invalid argument\n",
        "[ERROR] [wlr] connector DP-1: Atomic commit failed: Device or resource busy\n",
        "[ERROR] [sway/desktop/output.c:300] Page-flip failed while enabling output DP-1\n",
        "Qt warning: Page-flip failed on output DP-1\n",
    ],
)
def test_other_display_errors_are_preserved(line):
    assert not display_launch._is_ignored_sway_stderr(line)


def test_relay_removes_only_known_noise():
    source = io.StringIO(
        "[ERROR] [wlr] [backend/drm/atomic.c:81] connector DP-1: "
        "Atomic commit failed: Device or resource busy\n"
        "actionable display error\n"
        "[ERROR] [sway/desktop/output.c:300] Page-flip failed on output DP-1\n"
    )
    destination = io.StringIO()

    display_launch._relay_sway_stderr(source, destination)

    assert destination.getvalue() == "actionable display error\n"
