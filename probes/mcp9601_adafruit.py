from adafruit_mcp9600 import MCP9600
from adafruit_register.i2c_struct import ROUnaryStruct

from probes._mcp960x_adafruit import MCP960xDevice, MCP960xProbe
from probes.thermocouple_health import ThermocoupleFault


class MCP9601Sensor(MCP9600):
    status_register = ROUnaryStruct(0x04, ">B")


class KTTDevice(MCP960xDevice):
    sensor_class = MCP9601Sensor
    sensor: MCP9601Sensor

    @property
    def fault_status(self) -> int:
        return int(self.sensor.status_register)


class ReadProbes(MCP960xProbe):
    device_class = KTTDevice
    device: KTTDevice
    default_i2c_address = 0x61
    supports_hardware_fault_detection = True

    def _read_hardware_faults(self):
        if not self.hardware_fault_detection:
            return None
        status = self.device.fault_status
        faults = []
        if status & 0x10:
            faults.append(ThermocoupleFault.OPEN)
        if status & 0x20:
            faults.append(ThermocoupleFault.SHORT)
        self._last_hardware_status = status
        return tuple(faults)
