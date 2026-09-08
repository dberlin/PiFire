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

from common.clock_domain import RuntimeClockDomain
from common.persistence.runtime import (
    CONTROL_HEARTBEAT_KEY,
    flush_current,
    init_status,
    read_pellet_db,
    read_settings,
    write_generic_key,
    write_settings,
)
from common.web_contracts.core import DashSocketPayload
from probes.thermocouple_health import THERMOCOUPLE_HEALTH_REPORT_SCHEMA
from tests.fakes.clock import BOOT_ID, clock_stamp


@pytest.fixture(autouse=True)
def reader_clock(ds, monkeypatch):
    from blueprints.mobile import socket_io

    monkeypatch.setattr(socket_io, "local_clock_stamp", clock_stamp)
    write_generic_key(CONTROL_HEARTBEAT_KEY, clock_stamp().as_dict())


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
    observed_monotonic_s=92.5,
    detail=None,
):
    if detail is None:
        report_detail = {"policy": "observe"}
    elif isinstance(detail, dict):
        report_detail = {"policy": "observe", **detail}
    else:
        report_detail = detail
    return {
        "state": state,
        "faults": list(faults),
        "evidence": list(evidence),
        "temperature_valid": temperature_valid,
        "report_schema_version": THERMOCOUPLE_HEALTH_REPORT_SCHEMA,
        "observed_monotonic_s": observed_monotonic_s,
        "clock_stamp": clock_stamp(monotonic_s=observed_monotonic_s).as_dict(),
        "detail": report_detail,
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

    assert _project_thermocouple_health(settings, None, current=clock_stamp()) == []
    assert _project_thermocouple_health(settings, [], current=clock_stamp()) == []
    assert _project_thermocouple_health(settings, _device_info("tc0", {}), current=clock_stamp()) == []


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
def test_health_projection_preserves_every_state_and_identifies_source(ds, state, evidence, source):
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
        current=clock_stamp(),
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
                "reason": "current",
            },
        }
    ]


@pytest.mark.parametrize(
    ("role", "policy", "temperature_valid", "authority", "outcome"),
    [
        ("Primary", "observe", True, "notify_only", "notify_only"),
        ("Primary", "enforce", False, "stop", "stopped"),
        ("Primary", "off", False, None, "unavailable"),
        ("Food", "observe", False, "stop", "unavailable"),
        ("Aux", "enforce", False, "stop", "unavailable"),
    ],
)
def test_health_projection_uses_report_authority_without_global_mode(
    ds, role, policy, temperature_valid, authority, outcome
):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    label = f"{role}Probe"
    detail = {
        "policy": policy,
        "is_primary": role == "Primary",
    }
    if authority is not None:
        detail["authority"] = authority
    result = _project_thermocouple_health(
        _health_settings(
            _probe(
                label,
                role=role,
                device="aux0" if role == "Aux" else "tc0",
                port="A2" if role == "Aux" else "CH1",
                name="Ambient" if role == "Aux" else "Control",
            ),
            policy="enforce" if policy != "enforce" else "observe",
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
        current=clock_stamp(),
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
        current=clock_stamp(),
    )

    assert result[0]["report"]["faults"] == ["open", "short"]
    assert result[0]["report"]["detail"] == {
        "policy": "observe",
        "status": {"open": True, "short": True},
    }
    assert result[0]["detector"]["source"] == "hardware"
    assert result[0]["outcome"] == "stopped"


def test_health_projection_rejects_malformed_detail_without_inventing_future_age(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    settings = _health_settings(
        _probe("Bad", port="CH0"),
        _probe("Fresh", port="CH1"),
        _probe("Old", port="CH2"),
        _probe("Future", port="CH3"),
    )
    reports = {
        "Bad": _report("healthy", detail=["not", "an", "object"]),
        "Fresh": _report("healthy", observed_monotonic_s=98.75),
        "Old": _report("suspected", evidence=["implausible-step"], observed_monotonic_s=50.0),
        "Future": _report("healthy", observed_monotonic_s=101.0),
    }

    result = _project_thermocouple_health(settings, _device_info("tc0", reports), current=clock_stamp())

    assert [item["label"] for item in result] == ["Fresh", "Old", "Future"]
    assert [item["freshness"] for item in result] == [
        {"current": True, "lastReportedAgeS": 1.25, "reason": "current"},
        {"current": False, "lastReportedAgeS": 50.0, "reason": "stale"},
        {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"},
    ]


@pytest.mark.parametrize("wall_jump", [-3600.0, 3600.0])
def test_fresh_heartbeat_cannot_freshen_old_report_across_wall_steps(ds, monkeypatch, wall_jump):
    from blueprints.mobile import socket_io

    settings = _health_settings(_probe("Grill", role="Primary"))
    reports = _device_info("tc0", {"Grill": _report("healthy", observed_monotonic_s=100.0)})
    current = clock_stamp(monotonic_s=100.5)
    monkeypatch.setattr(socket_io, "local_clock_stamp", lambda: current)
    write_generic_key(CONTROL_HEARTBEAT_KEY, current.as_dict())
    fresh = socket_io._project_thermocouple_health(settings, reports)

    current = clock_stamp(monotonic_s=116.0, wall_s=1_800_000_016.0 + wall_jump)
    write_generic_key(CONTROL_HEARTBEAT_KEY, current.as_dict())
    stale = socket_io._project_thermocouple_health(settings, reports)

    assert fresh[0]["freshness"] == {"current": True, "lastReportedAgeS": 0.5, "reason": "current"}
    assert stale[0]["freshness"] == {"current": False, "lastReportedAgeS": 16.0, "reason": "stale"}


@pytest.mark.parametrize(
    "stamp",
    [
        clock_stamp(boot_id="5014e60d-4e18-41dd-9fc3-7c87c39a83f0"),
        clock_stamp(runtime_id="c82032ec-e223-41ca-a94c-7166010b7570"),
        clock_stamp(boot_id=None),
        clock_stamp(suspend_offset_s=None),
    ],
    ids=["previous-boot", "previous-runtime", "unknown-boot", "unknown-offset"],
)
def test_foreign_health_retains_fault_without_clock_authority(ds, stamp):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    report = _report(
        "confirmed",
        faults=["open"],
        evidence=["hardware"],
        temperature_valid=False,
        observed_monotonic_s=100.0,
        detail={"status": {"open": True}},
    )
    report["clock_stamp"] = stamp.as_dict()
    result = _project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary")),
        _device_info("tc0", {"Grill": report}),
    )

    assert result[0]["report"]["faults"] == ["open"]
    assert result[0]["report"]["detail"]["status"] == {"open": True}
    assert result[0]["outcome"] == "stopped"
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


def test_legacy_wall_report_is_retained_unknown_with_fresh_heartbeat(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    report = {
        "state": "confirmed",
        "faults": ["open"],
        "evidence": ["hardware"],
        "temperature_valid": False,
        "observed_at": 1_800_000_000.0,
        "detail": {"policy": "enforce", "status": {"open": True}},
    }
    result = _project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary")),
        _device_info("tc0", {"Grill": report}),
    )

    assert result[0]["report"]["state"] == "confirmed"
    assert result[0]["report"]["faults"] == ["open"]
    assert result[0]["report"]["detail"] == report["detail"]
    assert result[0]["outcome"] == "stopped"
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


@pytest.mark.parametrize("heartbeat", [1_800_000_000.0, None])
def test_legacy_or_missing_heartbeat_cannot_lend_report_identity(ds, heartbeat):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    write_generic_key(CONTROL_HEARTBEAT_KEY, heartbeat)
    result = _project_thermocouple_health(
        _health_settings(_probe("Food1")),
        _device_info("tc0", {"Food1": _report("suspected", evidence=["junction-collapse"])}),
    )

    assert result[0]["report"]["state"] == "suspected"
    assert result[0]["report"]["evidence"] == ["junction-collapse"]
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


def test_stale_heartbeat_cannot_authorize_even_newer_report(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    write_generic_key(CONTROL_HEARTBEAT_KEY, clock_stamp(monotonic_s=84.0).as_dict())
    result = _project_thermocouple_health(
        _health_settings(_probe("Food1")),
        _device_info("tc0", {"Food1": _report("healthy", observed_monotonic_s=100.0)}),
    )

    assert result[0]["report"]["state"] == "healthy"
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


@pytest.mark.parametrize("corruption", ["stamp", "coordinate", "huge-coordinate", "schema"])
def test_malformed_report_timing_keeps_alarm_but_cannot_authorize_freshness(ds, corruption):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    report = _report("confirmed", faults=["short"], evidence=["hardware"], temperature_valid=False)
    if corruption == "stamp":
        report["clock_stamp"] = {"observed_monotonic_s": 92.5}
    elif corruption == "coordinate":
        report["observed_monotonic_s"] = 99.0
    elif corruption == "huge-coordinate":
        report["observed_monotonic_s"] = 10**10_000
    else:
        report["report_schema_version"] = True
    result = _project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary")),
        _device_info("tc0", {"Grill": report}),
    )

    assert result[0]["report"]["faults"] == ["short"]
    assert result[0]["report"]["temperatureValid"] is False
    assert result[0]["outcome"] == "stopped"
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


def test_resume_offset_invalidates_report_before_next_writer_tick(ds):
    from blueprints.mobile.socket_io import _project_thermocouple_health

    result = _project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary")),
        _device_info(
            "tc0", {"Grill": _report("confirmed", faults=["open"], evidence=["hardware"], temperature_valid=False)}
        ),
        current=clock_stamp(monotonic_s=101.0, suspend_offset_s=62.001),
    )

    assert result[0]["report"]["faults"] == ["open"]
    assert result[0]["outcome"] == "stopped"
    assert result[0]["freshness"] == {"current": False, "lastReportedAgeS": None, "reason": "unknown-clock"}


def test_inactive_policy_reprojection_does_not_renew_socket_report_age(ds):
    from blueprints.mobile import socket_io
    from probes.main import ProbesMain
    from probes.thermocouple_health import ThermocoupleFault, ThermocoupleHealthReport

    monotonic_s = 100.0
    wall_s = 1_800_000_000.0
    domain = RuntimeClockDomain(
        monotonic=lambda: monotonic_s,
        wall_time=lambda: wall_s,
        boottime=lambda: monotonic_s + 2.0,
        boot_id=BOOT_ID,
    )
    hardware = ThermocoupleHealthReport.confirmed_hardware(
        (ThermocoupleFault.OPEN,),
        now=32.0,
        status=0x10,
    )

    class CachedHardwareDevice:
        device_info = {"device": "tc0"}

        def read_all_ports(self, output_data):
            return {"primary": {"Grill": None}}

        def apply_filters(self, device_data):
            pass

        def get_thermocouple_samples(self):
            return {}

        def get_thermocouple_health(self):
            return {"Grill": hardware}

        def get_device_info(self):
            return {"device": "tc0", "status": {"driver": "ready"}}

    probes = ProbesMain(
        {"probe_devices": [], "probe_info": []},
        "F",
        clock_domain=domain,
    )
    probes.probe_info = [{"device": "tc0", "port": "CH0", "label": "Grill", "type": "Primary", "profile": {}}]
    probes.probe_device_list = [CachedHardwareDevice()]
    probes.read_probes(monotonic_s=monotonic_s, wall_s=wall_s)

    monotonic_s = 120.0
    wall_s += 20.0
    probes.set_thermocouple_inference_policy("off")
    projected = probes.get_device_info()
    heartbeat = domain.capture()
    first = socket_io._project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary"), policy="off"),
        projected,
        current=heartbeat,
        heartbeat=heartbeat,
    )
    monotonic_s = 123.0
    heartbeat = domain.capture()
    later = socket_io._project_thermocouple_health(
        _health_settings(_probe("Grill", role="Primary"), policy="off"),
        probes.get_device_info(),
        current=heartbeat,
        heartbeat=heartbeat,
    )

    assert first[0]["detector"]["policy"] == "off"
    assert first[0]["report"]["faults"] == ["open"]
    assert first[0]["report"]["temperatureValid"] is False
    assert first[0]["outcome"] == "stopped"
    assert first[0]["freshness"] == {"current": False, "lastReportedAgeS": 20.0, "reason": "stale"}
    assert later[0]["freshness"] == {"current": False, "lastReportedAgeS": 23.0, "reason": "stale"}


def test_dash_payload_projects_persisted_health_and_accepts_old_omission(ds):
    probe_info = _device_info(
        "proto_adc",
        {
            "Grill": _report(
                "healthy",
                observed_monotonic_s=0.0,
            )
        },
    )
    payload = _dash_data(probe_device_info=probe_info)

    assert payload["thermocoupleHealth"][0]["label"] == "Grill"

    old_payload = dict(payload)
    old_payload.pop("thermocoupleHealth")
    validated = DashSocketPayload.model_validate(old_payload, strict=True)
    assert validated.thermocouple_health == []
