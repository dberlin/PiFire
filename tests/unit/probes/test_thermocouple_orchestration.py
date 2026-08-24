from dataclasses import replace
from types import SimpleNamespace

import pytest

import probes.main as probes_main_module
from probes.main import ProbesMain
from probes.thermocouple_health import (
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)
from probes.thermocouple_inference import (
    ThermocoupleExcitationContext,
    ThermocoupleInferencePolicy,
    ThermocoupleJunctionSample,
)


def _empty_probe_map():
    return {"probe_devices": [], "probe_info": []}


def _probe(device, port, label, probe_type):
    return {
        "device": device,
        "port": port,
        "label": label,
        "type": probe_type,
        "profile": {},
    }


class _Device:
    applies_kalman = False

    def __init__(self, name, probes, samples, health=None, values=None):
        self.device_info = {"device": name, "ports": [probe["port"] for probe in probes]}
        self.port_map = {probe["port"]: probe["label"] for probe in probes}
        self.primary_port = next(
            (probe["port"] for probe in probes if probe["type"] == "Primary"), None
        )
        self.food_ports = [probe["port"] for probe in probes if probe["type"] == "Food"]
        self.aux_ports = [probe["port"] for probe in probes if probe["type"] == "Aux"]
        self.samples = dict(samples)
        self.health = dict(health or {})
        self.values = dict(values or {})
        self.closed = False

    def read_all_ports(self, _output_data):
        output = {"primary": {}, "food": {}, "aux": {}, "tr": {}}
        for port, label in self.port_map.items():
            if port == self.primary_port:
                group = "primary"
            elif port in self.food_ports:
                group = "food"
            elif port in self.aux_ports:
                group = "aux"
            else:
                continue
            output[group][label] = self.values.get(port, 225.0)
            output["tr"][label] = 1000.0
        return output

    def apply_filters(self, output):
        return output

    def get_thermocouple_samples(self):
        return dict(self.samples)

    def get_thermocouple_health(self):
        return dict(self.health)

    def get_device_info(self):
        status = {"driver": "ready"}
        if self.health:
            status["thermocouple_health"] = {
                label: report.as_dict() for label, report in self.health.items()
            }
        return {**self.device_info, "status": status}

    def close(self):
        self.closed = True


class _RecordingEngine:
    created = 0

    def __init__(self):
        type(self).created += 1
        self.report = ThermocoupleHealthReport.unmonitored(0.0)
        self.next_report = None
        self.observations = []
        self.reset_count = 0

    def current_report(self):
        return self.report

    def reset(self):
        self.reset_count += 1
        self.report = ThermocoupleHealthReport.unmonitored(0.0)
        self.next_report = None
        self.observations.clear()

    def observe(self, sample, excitation, is_primary, now):
        self.observations.append((sample, excitation, is_primary, now))
        if self.next_report is not None:
            self.report = self.next_report
            self.next_report = None
        elif self.report.state is ThermocoupleHealthState.UNMONITORED:
            self.report = ThermocoupleHealthReport.healthy(now)
        return self.report


@pytest.fixture
def recording_engines(monkeypatch):
    _RecordingEngine.created = 0
    monkeypatch.setattr(probes_main_module, "ThermocoupleInferenceEngine", _RecordingEngine)


def _main(probes, devices, policy=ThermocoupleInferencePolicy.OBSERVE):
    main = ProbesMain(_empty_probe_map(), "F", inference_policy=policy)
    main.probe_info = list(probes)
    main.probe_device_list = list(devices)
    return main


def _raw_inferred(state, now=2.0):
    return ThermocoupleHealthReport(
        state=state,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(ThermocoupleEvidence.STUCK_RESPONSE,),
        temperature_valid=state is not ThermocoupleHealthState.CONFIRMED,
        observed_at=now,
        detail={"sample_count": 20},
    )


def test_constructor_validates_policy_before_building_devices():
    with pytest.raises(ValueError):
        ProbesMain(_empty_probe_map(), "F", inference_policy="sometimes")


def test_constructor_builds_each_configured_device_with_fresh_inference_ownership(
    monkeypatch,
):
    constructed = []

    def read_probes(probe_info, device_info, units):
        instance = SimpleNamespace(
            probe_info=probe_info,
            device_info=device_info,
            units=units,
        )
        constructed.append(instance)
        return instance

    module = SimpleNamespace(ReadProbes=read_probes)
    imported = []

    def import_module(name):
        imported.append(name)
        return module

    monkeypatch.setattr(probes_main_module.importlib, "import_module", import_module)
    configured = {
        "device": "junctions",
        "module": "sample_driver",
        "ports": [],
        "config": {},
    }
    probe_map = {"probe_devices": [configured], "probe_info": []}

    first = ProbesMain(probe_map, "C")
    first._thermocouple_inference_engines[("junctions", "port")] = _RecordingEngine()
    second = ProbesMain(probe_map, "F", disable=True)

    assert imported == ["probes.sample_driver", "probes.disabled"]
    assert [(item.device_info, item.units) for item in constructed] == [
        (configured, "C"),
        (configured, "F"),
    ]
    assert second._thermocouple_inference_engines == {}


def test_module_load_failure_falls_back_to_disabled_and_reports_error(monkeypatch):
    fallback = SimpleNamespace(
        ReadProbes=lambda probe_info, device_info, units: SimpleNamespace(
            probe_info=probe_info,
            device_info=device_info,
            units=units,
        )
    )

    def import_module(name):
        if name == "probes.broken":
            raise ImportError("driver missing")
        assert name == "probes.disabled"
        return fallback

    monkeypatch.setattr(probes_main_module.importlib, "import_module", import_module)
    configured = {
        "device": "junctions",
        "module": "broken",
        "ports": [],
        "config": {},
    }

    main = ProbesMain({"probe_devices": [configured], "probe_info": []}, "C")

    assert configured["module"] == "disabled"
    assert len(main.get_errors()) == 1
    assert "junctions" in main.get_errors()[0]


def test_rebuild_clears_inference_even_when_previous_device_close_fails(caplog):
    class Uncloseable:
        device_info = {"device": "stuck"}

        def close(self):
            raise OSError("busy")

    main = ProbesMain(_empty_probe_map(), "F")
    main.probe_device_list = [Uncloseable()]
    main._thermocouple_inference_engines[("stuck", "port")] = _RecordingEngine()

    main.update_probe_map(_empty_probe_map())

    assert main._thermocouple_inference_engines == {}
    assert "busy" in caplog.text


def test_engines_are_owned_by_device_and_physical_port_and_rebuild_resets(recording_engines):
    a_probe = _probe("device-a", "port-0", "Shared", "Primary")
    b_probe = _probe("device-b", "port-0", "Shared", "Aux")
    incompatible = _probe("device-c", "port-0", "Other", "Aux")
    devices = [
        _Device("device-a", [a_probe], {"port-0": ThermocoupleJunctionSample(100.0, 20.0)}),
        _Device("device-b", [b_probe], {"port-0": ThermocoupleJunctionSample(90.0, 20.0)}),
        _Device("device-c", [incompatible], {"port-0": object()}),
        _Device(
            "not-configured",
            [],
            {"ghost": ThermocoupleJunctionSample(70.0, 20.0)},
        ),
    ]
    main = _main([a_probe, b_probe, incompatible], devices)

    main.read_probes(now=1.0)

    assert set(main._thermocouple_inference_engines) == {
        ("device-a", "port-0"),
        ("device-b", "port-0"),
    }
    original = dict(main._thermocouple_inference_engines)
    a_probe["label"] = "Renamed"
    devices[0].port_map["port-0"] = "Renamed"
    main.read_probes(now=2.0)
    assert main._thermocouple_inference_engines == original
    assert _RecordingEngine.created == 2

    main.update_probe_map(_empty_probe_map())
    assert main._thermocouple_inference_engines == {}
    assert main.get_thermocouple_health() == {}


def test_policy_lifecycle_drops_only_when_off_and_invalid_change_is_atomic(recording_engines):
    probe = _probe("device", "port", "Pit", "Primary")
    device = _Device("device", [probe], {"port": ThermocoupleJunctionSample(100.0, 20.0)})
    main = _main([probe], [device])
    main.read_probes(now=1.0)
    engine = main._thermocouple_inference_engines[("device", "port")]

    main.set_thermocouple_inference_policy("enforce")
    assert main._thermocouple_inference_engines[("device", "port")] is engine
    main.set_thermocouple_inference_policy(ThermocoupleInferencePolicy.OBSERVE)
    assert main._thermocouple_inference_engines[("device", "port")] is engine

    with pytest.raises(ValueError):
        main.set_thermocouple_inference_policy("invalid")
    assert main.thermocouple_inference_policy is ThermocoupleInferencePolicy.OBSERVE
    assert main._thermocouple_inference_engines[("device", "port")] is engine

    main.set_thermocouple_inference_policy("off")
    assert main._thermocouple_inference_engines == {}
    main.read_probes(now=2.0)
    assert main._thermocouple_inference_engines == {}
    main.set_thermocouple_inference_policy("observe")
    assert main._thermocouple_inference_engines == {}
    main.read_probes(now=3.0)
    assert main._thermocouple_inference_engines[("device", "port")] is not engine


def test_no_argument_read_uses_safe_inactive_excitation_and_monotonic_time(
    recording_engines, monkeypatch
):
    probe = _probe("device", "port", "Pit", "Primary")
    device = _Device("device", [probe], {"port": ThermocoupleJunctionSample(100.0, 20.0)})
    main = _main([probe], [device])
    monkeypatch.setattr(probes_main_module.time, "monotonic", lambda: 42.5)

    main.read_probes()

    engine = main._thermocouple_inference_engines[("device", "port")]
    _, excitation, is_primary, observed_at = engine.observations[-1]
    assert observed_at == 42.5
    assert is_primary is True
    assert excitation == ThermocoupleExcitationContext(
        active_cook=False,
        primary_setpoint_c=0.0,
        delivered_heat_on_s=0.0,
        witnesses=(),
    )


def test_witnesses_use_prior_fused_health_and_exclude_self_food_and_nonhealthy(
    recording_engines,
):
    definitions = [
        _probe("target", "p0", "Pit", "Primary"),
        _probe("primary", "p0", "Peer Primary", "Primary"),
        _probe("aux", "p0", "Peer Aux", "Aux"),
        _probe("food", "p0", "Food", "Food"),
        _probe("unhealthy", "p0", "Unhealthy", "Aux"),
        _probe("invalid", "p0", "Invalid", "Aux"),
    ]
    devices = [
        _Device(
            probe["device"],
            [probe],
            {"p0": ThermocoupleJunctionSample(80.0 + index, 20.0)},
        )
        for index, probe in enumerate(definitions)
    ]
    main = _main(definitions, devices)
    main.read_probes(now=1.0)
    main.consume_thermocouple_health_transitions()
    main._thermocouple_inference_engines[("unhealthy", "p0")].report = ThermocoupleHealthReport(
        state=ThermocoupleHealthState.SUSPECTED,
        faults=(ThermocoupleFault.MALFUNCTION,),
        evidence=(ThermocoupleEvidence.STUCK_RESPONSE,),
        observed_at=1.0,
    )
    main._thermocouple_inference_engines[("invalid", "p0")].report = _raw_inferred(
        ThermocoupleHealthState.CONFIRMED, now=1.0
    )

    main.read_probes(now=2.0)

    target_engine = main._thermocouple_inference_engines[("target", "p0")]
    witnesses = target_engine.observations[-1][1].witnesses
    assert [(w.source, w.temperature_c) for w in witnesses] == [
        (("aux", "p0"), 82.0),
        (("primary", "p0"), 81.0),
    ]


def test_same_pass_confirmation_does_not_change_another_targets_witnesses(recording_engines):
    a_probe = _probe("a", "p0", "A", "Primary")
    b_probe = _probe("b", "p0", "B", "Aux")
    devices = [
        _Device("a", [a_probe], {"p0": ThermocoupleJunctionSample(80.0, 20.0)}),
        _Device("b", [b_probe], {"p0": ThermocoupleJunctionSample(90.0, 20.0)}),
    ]
    main = _main([a_probe, b_probe], devices)
    main.read_probes(now=1.0)
    for engine in main._thermocouple_inference_engines.values():
        engine.next_report = _raw_inferred(ThermocoupleHealthState.CONFIRMED)

    main.read_probes(now=2.0)

    a_witnesses = main._thermocouple_inference_engines[("a", "p0")].observations[-1][1].witnesses
    b_witnesses = main._thermocouple_inference_engines[("b", "p0")].observations[-1][1].witnesses
    assert [w.source for w in a_witnesses] == [("b", "p0")]
    assert [w.source for w in b_witnesses] == [("a", "p0")]


def test_real_engine_selects_greatest_peer_rise_with_identity_tie_before_cold_fallback():
    target_probe = _probe("target", "p0", "Pit", "Primary")
    b_probe = _probe("b", "p0", "B", "Aux")
    c_probe = _probe("c", "p0", "C", "Primary")
    d_probe = _probe("d", "p0", "D", "Aux")
    probes = [target_probe, b_probe, c_probe, d_probe]
    devices = [
        _Device("target", [target_probe], {"p0": ThermocoupleJunctionSample(30.0, 20.0)}),
        _Device("b", [b_probe], {"p0": ThermocoupleJunctionSample(20.0, 20.0)}),
        _Device("c", [c_probe], {"p0": ThermocoupleJunctionSample(20.0, 20.0)}),
        _Device("d", [d_probe], {"p0": ThermocoupleJunctionSample(20.0, 20.0)}),
    ]
    main = _main(probes, devices)
    main.read_probes(now=-1.0)
    main._thermocouple_inference_engines[("target", "p0")].reset()

    for index in range(20):
        fraction = index / 19
        devices[0].samples["p0"] = ThermocoupleJunctionSample(30.0, 20.0 + 20.0 * fraction)
        devices[1].samples["p0"] = ThermocoupleJunctionSample(20.0 + 12.0 * fraction, 20.0)
        devices[2].samples["p0"] = ThermocoupleJunctionSample(20.0 + 15.0 * fraction, 20.0)
        devices[3].samples["p0"] = ThermocoupleJunctionSample(20.0 + 15.0 * fraction, 20.0)
        main.read_probes(
            now=240.0 * fraction,
            excitation=ThermocoupleExcitationContext(
                active_cook=True,
                primary_setpoint_c=100.0,
                delivered_heat_on_s=30.0 if index == 19 else 0.0,
            ),
        )

    report = main.get_thermocouple_health()["Pit"]
    assert report.detail["witness_source"] == ("c", "p0")
    assert report.detail["witness_rise_c"] == pytest.approx(15.0)


@pytest.mark.parametrize(
    ("policy", "probe_type", "expected"),
    [
        (ThermocoupleInferencePolicy.OBSERVE, "Primary", 225.0),
        (ThermocoupleInferencePolicy.ENFORCE, "Primary", None),
        (ThermocoupleInferencePolicy.OBSERVE, "Aux", None),
        (ThermocoupleInferencePolicy.ENFORCE, "Aux", None),
    ],
)
def test_confirmed_inference_invalidation_depends_on_policy_and_primary(
    recording_engines, policy, probe_type, expected
):
    probe = _probe("device", "port", "Probe", probe_type)
    device = _Device("device", [probe], {"port": ThermocoupleJunctionSample(100.0, 20.0)})
    main = _main([probe], [device], policy)
    main.read_probes(now=1.0)
    main._thermocouple_inference_engines[("device", "port")].report = _raw_inferred(
        ThermocoupleHealthState.CONFIRMED
    )

    output = main.read_probes(now=2.0)

    group = "primary" if probe_type == "Primary" else "aux"
    if expected is None:
        assert output[group]["Probe"] is None
    else:
        assert output[group]["Probe"] == expected
    assert main.get_thermocouple_health()["Probe"].temperature_valid is (
        expected is not None
    )


def test_suspected_inference_keeps_numeric_output(recording_engines):
    probe = _probe("device", "port", "Probe", "Aux")
    device = _Device("device", [probe], {"port": ThermocoupleJunctionSample(100.0, 20.0)})
    main = _main([probe], [device], ThermocoupleInferencePolicy.ENFORCE)
    main.read_probes(now=1.0)
    main._thermocouple_inference_engines[("device", "port")].report = _raw_inferred(
        ThermocoupleHealthState.SUSPECTED
    )

    output = main.read_probes(now=2.0)

    assert output["aux"]["Probe"] == 225.0
    assert main.get_thermocouple_health()["Probe"].temperature_valid is True


@pytest.mark.parametrize("policy", list(ThermocoupleInferencePolicy))
def test_hardware_confirmation_is_authoritative_and_invalid_in_every_policy(
    recording_engines, policy
):
    probe = _probe("device", "port", "Probe", "Primary")
    hardware = ThermocoupleHealthReport.confirmed_hardware(
        (ThermocoupleFault.OPEN,), now=2.0, status=0x10
    )
    device = _Device(
        "device",
        [probe],
        {"port": ThermocoupleJunctionSample(100.0, 20.0)},
        health={"Probe": hardware},
    )
    main = _main([probe], [device], policy)

    output = main.read_probes(now=2.0)

    assert output["primary"]["Probe"] is None
    assert main.get_thermocouple_health()["Probe"] == hardware
    if policy is ThermocoupleInferencePolicy.OFF:
        assert main._thermocouple_inference_engines == {}


def test_device_info_projects_exact_fused_report_and_metadata_changes_do_not_transition(
    recording_engines,
):
    probe = _probe("device", "port", "Probe", "Primary")
    device = _Device("device", [probe], {"port": ThermocoupleJunctionSample(100.0, 20.0)})
    main = _main([probe], [device])
    main.read_probes(now=1.0)
    main.consume_thermocouple_health_transitions()
    engine = main._thermocouple_inference_engines[("device", "port")]
    engine.report = _raw_inferred(ThermocoupleHealthState.CONFIRMED, now=2.0)
    main.read_probes(now=2.0)
    main.consume_thermocouple_health_transitions()
    fused = main.get_thermocouple_health()["Probe"]

    info = main.get_device_info()[0]
    assert info["status"]["thermocouple_health"]["Probe"] == fused.as_dict()

    engine.report = replace(engine.report, observed_at=3.0, detail={"sample_count": 21})
    main.read_probes(now=3.0)
    assert main.consume_thermocouple_health_transitions() == ()
