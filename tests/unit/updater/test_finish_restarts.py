"""What happens at the very end of an update.

The updater once published "Finished! Restarting Server..." and exited without
restarting anything. The only path that loaded new code was a button on the
updater page, at the end of a run that page had to have watched from the start.

So an update would finish, announce a restart, and leave gunicorn serving the
Python from before the pull -- new routes 404ing against a freshly built bundle
that had them.
"""

import pytest

import updater
from common.modes import Mode
from common.persistence.install_state import get_update_manual_dependency_actions, get_update_restart_pending


@pytest.fixture
def finish(ds, monkeypatch):
    """Capture what a finished run publishes and whether it restarted."""
    published = []
    restarts = []
    monkeypatch.setattr(updater, "_publish", lambda percent, status, line: published.append(percent))
    monkeypatch.setattr(updater, "restart_scripts", lambda wait=False: restarts.append(wait))

    def run(mode, reboot=False, manual_actions=()):
        monkeypatch.setattr(updater, "read_control", lambda: {"mode": mode})
        updater.publish_finished(reboot, manual_actions)
        return published[-1], restarts

    return run


def test_a_finished_update_restarts_pifire_itself(finish):
    """`wait=True` is not incidental: updater.py exits the moment
    publish_finished returns, and the default hands supervisorctl to a daemon
    thread that would die with the process before it ever ran. That the waiting
    path really does skip the thread is pinned in
    tests/unit/system/test_system_lifecycle.py; what belongs here is that the
    updater asks for it."""
    percent, restarts = finish(Mode.STOP)
    assert restarts == [True]
    assert percent == updater.FINISHED_PERCENT
    assert get_update_restart_pending() is False


def test_a_lit_grill_is_asked_rather_than_interrupted(finish):
    """`supervisorctl restart all` stops the control process too, so restarting
    mid-cook drops the fire."""
    percent, restarts = finish(Mode.HOLD)
    assert restarts == []
    assert percent == updater.FINISHED_PERCENT
    assert get_update_restart_pending() is True


def test_the_pending_flag_outlives_the_run_that_set_it(finish):
    """It is read by /api/update/state on a plain page load, not carried in the
    progress poll: the tab that started the update is usually long gone by the
    time anyone reads the answer, which is exactly how the restart got lost."""
    finish(Mode.HOLD)
    assert get_update_restart_pending() is True
    finish(Mode.STOP)
    assert get_update_restart_pending() is False


def test_a_reboot_required_run_restarts_nothing(finish):
    """Power-cycling the machine is not ours to take, and the page offers it."""
    percent, restarts = finish(Mode.STOP, reboot=True)
    assert restarts == []
    assert percent == updater.REBOOT_REQUIRED_PERCENT


def test_an_unreadable_control_record_counts_as_running(ds, monkeypatch):
    """Being wrong one way costs a restart the user has to click. The other way
    it costs a fire."""

    def boom():
        raise RuntimeError("no control record")

    monkeypatch.setattr(updater, "read_control", boom)
    assert updater.grill_is_stopped() is False


def test_manual_dependency_actions_block_restart_and_persist_for_the_user(finish):
    actions = [
        "Install OS package: libusb",
        "Run command: board-config.py --spi",
    ]

    percent, restarts = finish(Mode.STOP, manual_actions=actions)

    assert restarts == []
    assert percent == updater.FINISHED_PERCENT
    assert get_update_restart_pending() is True
    assert get_update_manual_dependency_actions() == actions


def test_install_dependencies_no_longer_claims_to_restart_anything(ds, monkeypatch):
    """Its percent-100 line said "Finished!  Restarting Server..." from the
    middle of a run, where it neither knew nor decided whether a restart
    happened. That line is why the update log read as though one had."""
    lines = []
    #  DEBUG is assigned inside updater.py's `if __name__ == "__main__"` block,
    #  so it does not exist on an imported module at all.
    monkeypatch.setattr(updater, "DEBUG", False, raising=False)
    monkeypatch.setattr(
        updater,
        "set_updater_install_status",
        lambda percent, status, output: lines.append(output),
    )
    monkeypatch.setattr(updater, "read_updater_manifest", lambda: {"versions": []})
    monkeypatch.setattr(updater, "_run_acados_bootstrap_migrations", lambda *a: 0)
    monkeypatch.setattr(updater, "refresh_python_environment", lambda **kwargs: (0, ()))
    monkeypatch.setattr(updater, "read_settings", lambda: {"globals": {"uv": True}})
    monkeypatch.setattr(updater, "record_installed_version", lambda manifest=None: None)
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: None)

    updater.install_dependencies("1.0.0", 1)

    assert not any("Restarting" in line for line in lines)
