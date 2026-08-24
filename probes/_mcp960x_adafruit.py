from collections.abc import Mapping

import time

from adafruit_mcp9600 import MCP9600

from common.i2c_bus import open_i2c_bus
from common.i2c_bus_config import BasicBus, parse_i2c_bus
from probes.base import ProbeInterface
from probes.thermocouple_health import (
    HardwareFaultLatch,
    ThermocoupleFault,
    ThermocoupleHealthReport,
)
from probes.thermocouple_inference import ThermocoupleJunctionSample


class MCP960xDevice:
    sensor_class = MCP9600

    def __init__(self, i2c_bus_addr=0x67, bus=None, tc_type="K"):
        self.status = {}
        self.i2c = open_i2c_bus(bus or BasicBus())
        self.sensor = self.sensor_class(self.i2c, address=i2c_bus_addr, tctype=tc_type)

    @property
    def temperature(self):
        return self.sensor.temperature

    @property
    def ambient_temperature(self):
        return self.sensor.ambient_temperature

    def get_status(self):
        return self.status


class MCP960xProbe(ProbeInterface):
    device_class = MCP960xDevice
    default_i2c_address = 0x67
    port_name = "KTT0"
    supports_hardware_fault_detection = False

    def _init_device(self):
        self.time_delay = 0
        self.device_info["ports"] = [self.port_name]
        config = self.device_info["config"]
        i2c_bus_addr = int(
            config.get("i2c_bus_addr", f"0x{self.default_i2c_address:02x}"),
            16,
        )
        bus = parse_i2c_bus(config.get("i2c_bus") or {"kind": "basic"})
        tc_type = config.get("tc_type", "K")
        self.hardware_fault_detection = self.supports_hardware_fault_detection and (
            config.get("hardware_fault_detection", "False") == "True"
        )
        try:
            self.device = self.device_class(
                i2c_bus_addr=i2c_bus_addr,
                bus=bus,
                tc_type=tc_type,
            )
        except Exception:
            self.logger.error(
                "Something went wrong when trying to initialize the MCP9600 device "
                f"(i2c bus {bus.describe()}, address=0x{i2c_bus_addr:02X})."
            )
            raise
        self._hardware_fault_latch = HardwareFaultLatch(recovery_seconds=60.0)
        self._thermocouple_health_report = ThermocoupleHealthReport.unmonitored(0.0)
        self._last_hardware_status = None
        self._thermocouple_samples: dict[str, ThermocoupleJunctionSample] = {}

    def _read_hardware_faults(self) -> tuple[ThermocoupleFault, ...] | None:
        return None

    def read_all_ports(self, output_data):
        """Read the thermocouple health and temperature from the device."""
        port = self.device_info["ports"][0]
        label = self.port_map[port]
        self._thermocouple_samples = {}
        faults = self._read_hardware_faults()
        now = time.monotonic()
        temperature = None
        junction_sample = None
        if faults or (faults is not None and port == self.primary_port and self._thermocouple_health_report.confirmed):
            report = self._hardware_fault_latch.update(
                faults,
                now=now,
                primary=port == self.primary_port,
                status=self._last_hardware_status,
            )
        else:
            try:
                hot_c = self.device.temperature
                cold_c = self.device.ambient_temperature
                junction_sample = ThermocoupleJunctionSample(
                    hot_c=hot_c,
                    cold_c=cold_c,
                )
                temp_c = round(hot_c, 1)
            except Exception:
                self._hardware_fault_latch.cancel_clean_recovery()
                raise
            temp_f = round(temp_c * (9 / 5) + 32, 1)
            temperature = temp_f if self.units == "F" else temp_c
            if faults is None:
                report = ThermocoupleHealthReport.unmonitored(now)
            else:
                report = self._hardware_fault_latch.update(
                    faults,
                    now=now,
                    primary=port == self.primary_port,
                    status=self._last_hardware_status,
                )
                if not report.temperature_valid:
                    temperature = None
        self._thermocouple_health_report = report
        self.output_data["tr"][label] = 0
        if port == self.primary_port:
            self.output_data["primary"][label] = temperature
        elif port in self.food_ports:
            self.output_data["food"][label] = temperature
        elif port in self.aux_ports:
            self.output_data["aux"][label] = temperature
        if junction_sample is not None:
            self._thermocouple_samples = {port: junction_sample}

        return self.output_data

    def get_thermocouple_health(self) -> dict[str, ThermocoupleHealthReport]:
        port = self.device_info["ports"][0]
        label = self.port_map.get(port)
        if label is None:
            return {}
        return {label: self._thermocouple_health_report}

    def get_thermocouple_samples(self) -> Mapping[str, ThermocoupleJunctionSample]:
        return self._thermocouple_samples
