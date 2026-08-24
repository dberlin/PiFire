"""SQLite-backed thermocouple fault scenarios through the production work cycle."""

from dataclasses import dataclass
import json
import logging

import pytest

from common.persistence import runtime as runtime_persistence
from controller.runtime.clock import ManualClock
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.store import SqliteStore
import controller.runtime.controller as controller_mod
from probes.main import ProbesMain
from probes.thermocouple_inference import (
    ThermocoupleInferencePolicy,
    ThermocoupleJunctionSample,
)
from tests.characterization.fixtures import base_control, base_pellet_db, base_settings
from tests.fakes.distance import FakeDistance
from tests.fakes.grill import FakeGrillPlatform
from tests.fakes.notifier import FakeNotifier


_DEVICE = "simulated_tc"
_PORT = "TC0"
_LABEL = "Grill"
_TRANSITION_LOG_PREFIX = "Thermocouple health transition "
_POSITIVE_ACTUATION = {"auger_on", "igniter_on", "fan_on", "power_on"}


def _c_to_f(value):
    return value * 9.0 / 5.0 + 32.0


def _primary_probe():
    return {
        "type": "Primary",
        "label": _LABEL,
        "name": "Grill",
        "profile": {},
        "device": _DEVICE,
        "port": _PORT,
        "enabled": True,
    }

class _ExactManualClock(ManualClock):
    """Keep 50 ms production-loop ticks exact at one-second sample boundaries."""

    def sleep(self, seconds):
        self._t = round(self._t + seconds, 10)

    def advance(self, seconds):
        self._t = round(self._t + seconds, 10)




class _SimulatedSinglePortThermocouple:
    """Physical-boundary fake: raw hot/cold samples are clock-scripted."""

    applies_kalman = False

    def __init__(self, clock, sample_at):
        self.clock = clock
        self.sample_at = sample_at
        self.device_info = {
            "device": _DEVICE,
            "ports": [_PORT],
            "status": {"driver": "ready"},
        }
        self.raw_reads = []
        self._sample = ThermocoupleJunctionSample(hot_c=0.0, cold_c=0.0)

    def read_all_ports(self, _output_data):
        now = self.clock.now()
        self._sample = self.sample_at(now)
        self.raw_reads.append((now, self._sample))
        return {
            "primary": {_LABEL: _c_to_f(self._sample.hot_c)},
            "food": {},
            "aux": {},
            "tr": {_LABEL: 0.0},
        }

    def apply_filters(self, output_data):
        return output_data

    def get_thermocouple_samples(self):
        return {_PORT: self._sample}

    def get_thermocouple_health(self):
        return {}

    def get_device_info(self):
        return {
            **self.device_info,
            "ports": list(self.device_info["ports"]),
            "status": dict(self.device_info["status"]),
        }

    def close(self):
        return None


class _TimedGrill(FakeGrillPlatform):
    def __init__(self, clock, settings):
        self.clock = clock
        self.timed_calls = []
        super().__init__(
            dc_fan=settings["platform"]["dc_fan"],
            standalone=settings["platform"]["standalone"],
            outputs=tuple(settings["platform"]["outputs"]),
        )

    def _rec(self, name, *args):
        self.timed_calls.append((self.clock.now(), name, args))
        super()._rec(name, *args)


class _TimedNotifier(FakeNotifier):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.timed_sent = []

    def send(self, name):
        self.timed_sent.append((self.clock.now(), name))
        super().send(name)


@dataclass
class _ScenarioResult:
    clock: ManualClock
    device: _SimulatedSinglePortThermocouple
    grill: _TimedGrill
    notifier: _TimedNotifier
    store: SqliteStore
    transition_logs: list[dict]


def _run_scenario(ds, caplog, *, sample_at, duration, setpoint_f=425):
    settings = base_settings()
    settings["thermocouple_health"]["inference_policy"] = "enforce"
    settings["startup"]["duration"] = int(duration)
    settings["shutdown"]["shutdown_duration"] = 1
    settings["startup"]["startup_exit_temp"] = 0
    settings["probe_settings"]["probe_map"] = {
        "probe_devices": [
            {
                "device": _DEVICE,
                "module": "simulated",
                "module_filename": "simulated",
                "ports": [_PORT],
                "config": {},
            }
        ],
        "probe_info": [_primary_probe()],
    }
    control = base_control(mode="Startup")
    control["primary_setpoint"] = setpoint_f
    pellet_db = base_pellet_db()

    store = SqliteStore()
    runtime_persistence.write_settings_store(settings)
    store.system_commands().flush()
    store.system_output().flush()
    store.display_commands().flush()
    store.flush_metrics()
    store.flush_current()
    store.write_control_snapshot(control, origin="test-e2e")
    store.write_pellet_db(pellet_db)

    clock = _ExactManualClock()
    device = _SimulatedSinglePortThermocouple(clock, sample_at)
    probes = ProbesMain(
        {"probe_devices": [], "probe_info": []},
        "F",
        inference_policy=ThermocoupleInferencePolicy.ENFORCE,
    )
    probes.probe_info = [_primary_probe()]
    probes.probe_devices = settings["probe_settings"]["probe_map"]["probe_devices"]
    probes.probe_device_list = [device]
    grill = _TimedGrill(clock, settings)
    notifier = _TimedNotifier(clock)
    logger = logging.getLogger("thermocouple-e2e")
    caplog.set_level(logging.INFO, logger=logger.name)
    ctx = ControllerContext(
        devices=Devices(
            grill_platform=grill,
            probe_complex=probes,
            dist_device=FakeDistance(),
        ),
        store=store,
        notifications=notifier,
        clock=clock,
        event_log=logger,
        control_log=logger,
    )

    controller_mod.run_work_cycle("Startup", ctx)

    transition_logs = [
        json.loads(record.message.removeprefix(_TRANSITION_LOG_PREFIX))
        for record in caplog.records
        if record.name == logger.name
        and record.message.startswith(_TRANSITION_LOG_PREFIX)
    ]
    return _ScenarioResult(clock, device, grill, notifier, store, transition_logs)


def _persisted_report(result):
    device_info = result.store.read_generic_key("probe_device_info")
    return device_info, device_info[0]["status"]["thermocouple_health"][_LABEL]


def _assert_authoritative_stop(result, report):
    assert result.store.read_control()["mode"] == "Error"
    assert result.store.display_commands().list().count(["text", "ERROR"]) == 1
    assert result.notifier.sent == ["Thermocouple_Fault_Primary"]
    assert result.notifier.timed_sent == [
        (pytest.approx(report["observed_at"]), "Thermocouple_Fault_Primary")
    ]
    assert result.transition_logs == [
        {
            "authority": "stop",
            "cold_span_c": pytest.approx(report["detail"]["cold_span_c"]),
            "collapse_fraction": pytest.approx(report["detail"]["collapse_fraction"]),
            "coverage_seconds": pytest.approx(report["detail"]["coverage_seconds"]),
            "delta_span_c": pytest.approx(report["detail"]["delta_span_c"]),
            "evidence": report["evidence"],
            "faults": ["malfunction"],
            "heat_on_seconds": pytest.approx(report["detail"]["heat_on_seconds"]),
            "hot_span_c": pytest.approx(report["detail"]["hot_span_c"]),
            "label": _LABEL,
            "max_gap_seconds": pytest.approx(report["detail"]["max_gap_seconds"]),
            "policy": "enforce",
            "policy_version": 1,
            "role": "primary",
            "sample_count": report["detail"]["sample_count"],
            "state": "confirmed",
            "witness_rise_c": report["detail"]["witness_rise_c"],
            "witness_source": report["detail"]["witness_source"],
        }
    ]
    confirmed_at = report["observed_at"]
    assert not [
        call
        for call in result.grill.timed_calls
        if call[0] >= confirmed_at and call[1] in _POSITIVE_ACTUATION
    ]
    assert result.store.read_current()["P"][_LABEL] is None


def _dash_health(result, monkeypatch):
    from blueprints.mobile import socket_io

    monkeypatch.setattr(socket_io.time, "monotonic", result.clock.now)
    payload = socket_io._get_dash_data(
        result.store.read_settings(),
        result.store.read_pellet_db(),
    )
    return payload["thermocoupleHealth"]


def test_live_pull_confirms_after_five_subsequent_collapsed_samples_and_stops(
    ds, caplog, monkeypatch
):
    def sample_at(now):
        if now < 1.0:
            return ThermocoupleJunctionSample(hot_c=100.0, cold_c=25.0)
        return ThermocoupleJunctionSample(hot_c=25.0, cold_c=25.0)

    result = _run_scenario(ds, caplog, sample_at=sample_at, duration=20.0)
    device_info, report = _persisted_report(result)

    assert report["state"] == "confirmed", report["detail"]
    assert report["faults"] == ["malfunction"]
    assert report["evidence"] == ["implausible-step", "junction-collapse"]
    assert report["temperature_valid"] is False
    assert report["detail"]["policy"] == "enforce"
    assert report["detail"]["authority"] == "stop"
    assert report["detail"]["is_primary"] is True
    assert report["detail"]["sample_count"] == 7
    assert report["detail"]["coverage_seconds"] == pytest.approx(6.0)
    assert report["detail"]["max_gap_seconds"] == pytest.approx(1.0)
    assert report["detail"]["asserted_channels"] == [
        "implausible-step",
        "junction-collapse",
    ]
    assert 6.0 <= report["observed_at"] < 6.1
    assert result.device.raw_reads[0][1] == ThermocoupleJunctionSample(
        hot_c=100.0,
        cold_c=25.0,
    )
    assert result.device.raw_reads[-1][1] == ThermocoupleJunctionSample(
        hot_c=25.0,
        cold_c=25.0,
    )
    boundary_samples = [
        next(
            sample
            for observed_at, sample in result.device.raw_reads
            if observed_at == float(second)
        )
        for second in range(7)
    ]
    assert boundary_samples[0].hot_c - boundary_samples[0].cold_c >= 15.0
    assert boundary_samples[0].hot_c - boundary_samples[1].hot_c >= 20.0
    assert all(
        abs(sample.hot_c - sample.cold_c) <= 1.0
        for sample in boundary_samples[2:]
    )
    assert len(boundary_samples[2:]) == 5

    _assert_authoritative_stop(result, report)
    assert _dash_health(result, monkeypatch) == [
        {
            "device": _DEVICE,
            "port": _PORT,
            "label": _LABEL,
            "displayName": "Grill",
            "role": "Primary",
            "report": {
                "state": "confirmed",
                "faults": ["malfunction"],
                "evidence": ["implausible-step", "junction-collapse"],
                "temperatureValid": False,
                "detail": report["detail"],
            },
            "detector": {"source": "software", "policy": "enforce"},
            "outcome": "stopped",
            "freshness": {"current": True, "lastReportedAgeS": 0.0},
        }
    ]
    assert device_info[0]["device"] == _DEVICE


def test_startup_open_at_425f_confirms_on_first_complete_slow_window(
    ds, caplog, monkeypatch
):
    def sample_at(now):
        ambient_c = 25.0 + 3.0 * min(now, 240.0) / 240.0
        return ThermocoupleJunctionSample(
            hot_c=ambient_c,
            cold_c=ambient_c,
        )

    result = _run_scenario(ds, caplog, sample_at=sample_at, duration=300)
    device_info, report = _persisted_report(result)
    detail = report["detail"]

    assert result.store.read_control()["primary_setpoint"] == 425
    assert device_info[0]["device"] == _DEVICE
    assert report["state"] == "confirmed"
    assert report["faults"] == ["malfunction"]
    assert report["evidence"] == [
        "junction-collapse",
        "excitation-response",
    ]
    assert report["temperature_valid"] is False
    assert detail["policy"] == "enforce"
    assert detail["authority"] == "stop"
    assert detail["is_primary"] is True
    assert detail["sample_count"] == 241
    assert detail["coverage_seconds"] == pytest.approx(240.0)
    assert detail["max_gap_seconds"] == pytest.approx(1.0)
    assert detail["slow_window_eligible"] is True
    assert detail["heat_on_seconds"] >= 30.0
    assert detail["cold_span_c"] == pytest.approx(3.0)
    assert detail["hot_span_c"] == pytest.approx(3.0)
    assert detail["delta_span_c"] == pytest.approx(0.0)
    assert detail["collapse_fraction"] == pytest.approx(1.0)
    assert detail["witness_source"] == ["cold_junction", "internal"]
    assert detail["witness_rise_c"] == pytest.approx(3.0)
    assert detail["asserted_channels"] == [
        "junction-collapse",
        "excitation-response",
    ]
    assert report["observed_at"] == pytest.approx(240.0)
    assert result.device.raw_reads[0][1] == ThermocoupleJunctionSample(
        hot_c=25.0,
        cold_c=25.0,
    )
    assert result.device.raw_reads[-1][1] == ThermocoupleJunctionSample(
        hot_c=28.0,
        cold_c=28.0,
    )
    assert (0.0, "igniter_on", ()) in result.grill.timed_calls
    assert (0.0, "auger_on", ()) in result.grill.timed_calls

    _assert_authoritative_stop(result, report)
    assert _dash_health(result, monkeypatch) == [
        {
            "device": _DEVICE,
            "port": _PORT,
            "label": _LABEL,
            "displayName": "Grill",
            "role": "Primary",
            "report": {
                "state": "confirmed",
                "faults": ["malfunction"],
                "evidence": [
                    "junction-collapse",
                    "excitation-response",
                ],
                "temperatureValid": False,
                "detail": detail,
            },
            "detector": {"source": "software", "policy": "enforce"},
            "outcome": "stopped",
            "freshness": {"current": True, "lastReportedAgeS": 0.0},
        }
    ]
