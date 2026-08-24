from probes.base import ProbeInterface
from probes.main import ProbesMain
from probes.thermocouple_health import (
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)


class _HealthDevice:
    applies_kalman = False

    def __init__(self, health):
        self.health = health
        self.closed = False

    def read_all_ports(self, _output_data):
        return {
            "primary": {"Grill": 225.0},
            "food": {},
            "aux": {},
            "tr": {"Grill": 225.0},
        }

    def apply_filters(self, _device_data):
        pass

    def get_thermocouple_health(self):
        return self.health

    def close(self):
        self.closed = True


class _StatusDevice:
    def get_status(self):
        return {"driver": "ready"}


class _ProbeWithHealth(ProbeInterface):
    def get_thermocouple_health(self):
        return {"Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=5.0, status=0x10)}


def _empty_probe_map():
    return {"probe_devices": [], "probe_info": []}


def _main_with_device_health(health):
    probes = ProbesMain(_empty_probe_map(), "F")
    probes.probe_device_list = [_HealthDevice(health)]
    return probes


def _bare_probe(interface=ProbeInterface):
    probe = interface.__new__(interface)
    probe.device_info = {"device": "test"}
    probe.device = _StatusDevice()
    return probe


def test_read_collects_health_by_logical_label_and_emits_state_transition():
    probes = _main_with_device_health(
        {"Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=5.0, status=0x10)}
    )

    probes.read_probes()

    returned = probes.get_thermocouple_health()
    assert returned["Grill"].confirmed
    returned.clear()
    assert probes.get_thermocouple_health()["Grill"].confirmed
    changes = probes.consume_thermocouple_health_transitions()
    assert [(c.label, c.previous.state, c.current.state) for c in changes] == [
        (
            "Grill",
            ThermocoupleHealthState.UNMONITORED,
            ThermocoupleHealthState.CONFIRMED,
        )
    ]
    assert probes.consume_thermocouple_health_transitions() == ()


def test_observation_metadata_change_does_not_emit_transition():
    probes = _main_with_device_health(
        {"Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=5.0, status=0x10)}
    )
    probes.read_probes()
    probes.consume_thermocouple_health_transitions()
    probes.probe_device_list[0].health = {
        "Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=6.0, status=0x11)
    }

    probes.read_probes()

    assert probes.consume_thermocouple_health_transitions() == ()
    assert probes.get_thermocouple_health()["Grill"].observed_at == 6.0


def test_fault_kind_change_emits_transition():
    probes = _main_with_device_health(
        {"Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=5.0, status=0x10)}
    )
    probes.read_probes()
    probes.consume_thermocouple_health_transitions()
    probes.probe_device_list[0].health = {
        "Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.SHORT,), now=6.0, status=0x20)
    }

    probes.read_probes()

    changes = probes.consume_thermocouple_health_transitions()
    assert len(changes) == 1
    assert changes[0].previous.faults == (ThermocoupleFault.OPEN,)
    assert changes[0].current.faults == (ThermocoupleFault.SHORT,)


def test_device_map_rebuild_clears_stale_health_and_events():
    probes = _main_with_device_health(
        {"Grill": ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=5.0, status=0x10)}
    )
    probes.read_probes()

    probes.update_probe_map(_empty_probe_map())

    assert probes.get_thermocouple_health() == {}
    assert probes.consume_thermocouple_health_transitions() == ()


def test_probe_interface_default_health_is_empty_and_omitted_from_device_info():
    probe = _bare_probe()

    assert probe.get_thermocouple_health() == {}
    assert probe.get_device_info()["status"] == {"driver": "ready"}
    assert "thermocouple_health" not in probe.get_device_info()["status"]


def test_probe_device_info_serializes_health_when_reports_exist():
    probe = _bare_probe(_ProbeWithHealth)

    info = probe.get_device_info()

    assert info["status"] == {
        "driver": "ready",
        "thermocouple_health": {
            "Grill": {
                "state": "confirmed",
                "faults": ["open"],
                "evidence": ["hardware"],
                "temperature_valid": False,
                "observed_at": 5.0,
                "detail": {"status": 0x10},
            }
        },
    }
