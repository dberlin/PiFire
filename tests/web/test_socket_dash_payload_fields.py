"""Dash-payload fields the React dashboard cannot work without.

``settings["safety"]["maxtemp"]`` is what ``controller/runtime/logic/safety.py``
shuts the grill down above, and it is user-editable. The React dashboard bounds
every setpoint it offers by it, so it has to be on the dash payload -- the only
channel that route has, since it runs without a loader.

Pinned at the producing end because the TypeScript side can only pin its own
fixture: a field renamed or dropped here would leave that fixture green and the
real modal falling back to a fixed ceiling with nothing failing.
"""

import pytest


from common.datastore_accessors import (
    flush_current,
    init_status,
    read_pellet_db,
    read_settings,
    write_generic_key,
    write_settings,
)


def _dash_data(**status_over):
    from blueprints.mobile import socket_io
    from common.datastore_accessors import read_status, write_status

    # Same seeding _get_dash_data needs elsewhere: status/current/device-info do
    # not self-heal the way the settings and pellet blobs do. Any status
    # override has to be applied AFTER init_status(), which resets the blob.
    init_status()
    flush_current()
    write_generic_key("probe_device_info", {})
    if status_over:
        write_status({**read_status(), **status_over})

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_payload_carries_the_safety_limit(ds):
    assert _dash_data()["safetyMaxTemp"] == read_settings()["safety"]["maxtemp"]


def test_the_limit_tracks_the_setting_rather_than_a_constant(ds):
    # Not the 550 default, and not the 500 the UI used to hardcode, so neither
    # can make this pass by coincidence.
    settings = read_settings()
    settings["safety"]["maxtemp"] = 412
    write_settings(settings)

    assert _dash_data()["safetyMaxTemp"] == 412


def test_payload_carries_the_actuator_duties(ds):
    # The dashboard shows these in place of P-mode and Smoke+ while holding,
    # which is what the physical display has always done -- it reads the same
    # two keys straight off control:status (display/qtbackend.py:156-157).
    data = _dash_data(cycle_ratio=0.42, fan_duty=65)

    assert data["cycleRatio"] == 0.42
    assert data["fanDuty"] == 65


def test_the_limit_is_not_the_gauge_ceiling(ds):
    # primaryProbe.maxTemp comes from the dashboard's display config and is a
    # different number; conflating them is exactly the mistake this guards.
    settings = read_settings()
    settings["safety"]["maxtemp"] = 412
    write_settings(settings)

    data = _dash_data()
    assert data["safetyMaxTemp"] == 412
    assert data["primaryProbe"]["maxTemp"] != 412


@pytest.mark.parametrize(
    ("controller_name", "revision"),
    [("mpc", "mpc-revision-17"), ("pid_sp", "pid-sp-revision-23")],
)
def test_payload_dispatches_the_learning_revision_from_the_selected_controller(
    ds, monkeypatch, controller_name, revision
):
    from blueprints.mobile import socket_io

    dispatched: list[str] = []

    def controller_revision(selected):
        dispatched.append(selected)
        return revision

    monkeypatch.setattr(socket_io, "controller_learning_report_revision", controller_revision)
    settings = read_settings()
    settings["controller"]["selected"] = controller_name
    write_settings(settings)

    data = _dash_data()

    assert dispatched == [controller_name]
    assert data["modelLearningRevision"] == revision
    assert "modelLearningReport" not in data
