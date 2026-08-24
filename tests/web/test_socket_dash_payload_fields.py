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
from pydantic import ValidationError

from common.web_contracts.core import DashSocketPayload


from common.persistence.runtime import (
    flush_current,
    init_status,
    read_pellet_db,
    read_settings,
    write_generic_key,
    write_settings,
)


def _dash_data(*, probe_device_info=None, **status_over):
    from blueprints.mobile import socket_io
    from common.persistence.runtime import read_status, write_status

    # Same seeding _get_dash_data needs elsewhere: status/current/device-info do
    # not self-heal the way the settings and pellet blobs do. Any status
    # override has to be applied AFTER init_status(), which resets the blob.
    init_status()
    flush_current()
    write_generic_key(
        "probe_device_info",
        {} if probe_device_info is None else probe_device_info,
    )
    if status_over:
        write_status({**read_status(), **status_over})

    return socket_io._get_dash_data(read_settings(), read_pellet_db())


def test_dash_payload_matches_the_strict_wire_contract(ds):
    payload = _dash_data()

    validated = DashSocketPayload.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", by_alias=True, exclude_none=False) == payload


def test_dash_payload_rejects_non_finite_numbers(ds):
    payload = _dash_data()
    payload["safetyMaxTemp"] = float("inf")

    with pytest.raises(ValidationError):
        DashSocketPayload.model_validate(payload, strict=True)


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


def _health_settings(*probes, policy="observe"):
    return {
        "thermocouple_health": {"inference_policy": policy},
        "probe_settings": {"probe_map": {"probe_info": list(probes)}},
    }


def _probe(
    label,
    *,
    role="Food",
    device="tc0",
    port="CH0",
    name=None,
):
    return {
        "type": role,
        "device": device,
        "port": port,
        "label": label,
        "name": name or label,
    }


def _report(
    state,
    *,
    faults=(),
    evidence=(),
    temperature_valid=True,
    observed_at=92.5,
    detail=None,
):
    return {
        "state": state,
        "faults": list(faults),
        "evidence": list(evidence),
        "temperature_valid": temperature_valid,
        "observed_at": observed_at,
        "detail": {} if detail is None else detail,
    }


def _device_info(device, reports):
    return [
        {
            "device": device,
            "status": {"thermocouple_health": reports},
        }
    ]


def test_health_projection_defaults_missing_and_empty_reports_to_empty(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    settings = _health_settings(_probe("Grill", role="Primary"))

    assert _project_thermocouple_health(settings, None, "Hold", now=100.0) == []
    assert _project_thermocouple_health(settings, [], "Hold", now=100.0) == []
    assert (
        _project_thermocouple_health(
            settings,
            _device_info("tc0", {}),
            "Hold",
            now=100.0,
        )
        == []
    )


@pytest.mark.parametrize(
    ("state", "evidence", "source"),
    [
        ("unmonitored", [], "software"),
        ("healthy", [], "software"),
        ("suspected", ["junction-collapse"], "software"),
        ("confirmed", ["hardware"], "hardware"),
        ("confirmed", ["hardware", "stuck-response"], "mixed"),
    ],
)
def test_health_projection_preserves_every_state_and_identifies_source(
    ds, state, evidence, source
):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    report = _report(
        state,
        faults=["malfunction"] if state == "confirmed" else [],
        evidence=evidence,
        temperature_valid=state != "confirmed",
        detail={"window": {"accepted": 12}, "reasons": ["stable"]},
    )
    result = _project_thermocouple_health(
        _health_settings(_probe("Food1", name="Brisket")),
        _device_info("tc0", {"Food1": report}),
        "Hold",
        now=100.0,
    )

    assert result == [
        {
            "device": "tc0",
            "port": "CH0",
            "label": "Food1",
            "displayName": "Brisket",
            "role": "Food",
            "report": {
                "state": state,
                "faults": report["faults"],
                "evidence": evidence,
                "temperatureValid": report["temperature_valid"],
                "detail": report["detail"],
            },
            "detector": {"source": source, "policy": "observe"},
            "outcome": "unavailable" if state == "confirmed" else "none",
            "freshness": {
                "current": True,
                "lastReportedAgeS": 7.5,
            },
        }
    ]


@pytest.mark.parametrize(
    ("role", "policy", "temperature_valid", "mode", "outcome"),
    [
        ("Primary", "observe", True, "Hold", "notify_only"),
        ("Primary", "enforce", False, "Error", "stopped"),
        ("Primary", "enforce", False, "Hold", "unavailable"),
        ("Food", "observe", False, "Hold", "unavailable"),
        ("Aux", "enforce", False, "Error", "unavailable"),
    ],
)
def test_health_projection_uses_policy_and_actual_controller_outcome(
    ds, role, policy, temperature_valid, mode, outcome
):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    label = f"{role}Probe"
    detail = {
        "policy": policy,
        "authority": "notify_only" if temperature_valid else "stop",
        "is_primary": role == "Primary",
    }
    result = _project_thermocouple_health(
        _health_settings(
            _probe(
                label,
                role=role,
                device="aux0" if role == "Aux" else "tc0",
                port="A2" if role == "Aux" else "CH1",
                name="Ambient" if role == "Aux" else "Control",
            ),
            policy=policy,
        ),
        _device_info(
            "aux0" if role == "Aux" else "tc0",
            {
                label: _report(
                    "confirmed",
                    faults=["malfunction"],
                    evidence=["stuck-response"],
                    temperature_valid=temperature_valid,
                    detail=detail,
                )
            },
        ),
        mode,
        now=100.0,
    )

    assert result[0]["role"] == role
    assert result[0]["port"] == ("A2" if role == "Aux" else "CH1")
    assert result[0]["displayName"] == ("Ambient" if role == "Aux" else "Control")
    assert result[0]["detector"]["policy"] == policy
    assert result[0]["outcome"] == outcome


def test_health_projection_preserves_open_and_short_without_error_strings(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    result = _project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary")),
        _device_info(
            "tc0",
            {
                "Grill": _report(
                    "confirmed",
                    faults=["open", "short"],
                    evidence=["hardware"],
                    temperature_valid=False,
                    detail={"status": {"open": True, "short": True}},
                )
            },
        ),
        "Error",
        now=100.0,
    )

    assert result[0]["report"]["faults"] == ["open", "short"]
    assert result[0]["report"]["detail"] == {
        "status": {"open": True, "short": True}
    }
    assert result[0]["detector"]["source"] == "hardware"
    assert result[0]["outcome"] == "stopped"


def test_health_projection_rejects_malformed_detail_and_computes_finite_age(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    settings = _health_settings(
        _probe("Bad", port="CH0"),
        _probe("Fresh", port="CH1"),
        _probe("Old", port="CH2"),
        _probe("Future", port="CH3"),
    )
    reports = {
        "Bad": _report("healthy", detail=["not", "an", "object"]),
        "Fresh": _report("healthy", observed_at=98.75),
        "Old": _report("suspected", evidence=["implausible-step"], observed_at=50.0),
        "Future": _report("healthy", observed_at=101.0),
    }

    result = _project_thermocouple_health(
        settings,
        _device_info("tc0", reports),
        "Hold",
        now=100.0,
    )

    assert [item["label"] for item in result] == ["Fresh", "Old", "Future"]
    assert [item["freshness"] for item in result] == [
        {"current": True, "lastReportedAgeS": 1.25},
        {"current": False, "lastReportedAgeS": 50.0},
        {"current": True, "lastReportedAgeS": 0.0},
    ]
    assert all(
        item["freshness"]["lastReportedAgeS"] >= 0.0
        for item in result
    )


def test_dash_payload_projects_persisted_health_and_accepts_old_omission(ds):
    probe_info = _device_info(
        "proto_adc",
        {
            "Grill": _report(
                "healthy",
                observed_at=0.0,
            )
        },
    )
    payload = _dash_data(probe_device_info=probe_info)

    assert payload["thermocoupleHealth"][0]["label"] == "Grill"

    old_payload = dict(payload)
    old_payload.pop("thermocoupleHealth")
    validated = DashSocketPayload.model_validate(old_payload, strict=True)
    assert validated.thermocouple_health == []
