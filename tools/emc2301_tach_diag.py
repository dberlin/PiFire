#!/usr/bin/env python3

# *****************************************
# EMC2301 Tachometer Diagnostic (read-only)
# *****************************************
#
# Dumps the EMC2301 tachometer-related registers so we can see exactly what a
# stopped fan vs. a running fan reports. It was written to diagnose a
# stopped-fan-reads-960-RPM bug (a stopped fan saturates the tach count near,
# but not at, its max, so the fix keys off the Fan Stall Status bit at 0x25);
# it is kept as a reusable tach/stall diagnostic. This is READ-ONLY: it reads
# only -- it does NOT run the driver's __init__ (which would write FAN_SETTING
# and stop the fan), so it is safe to run while PiFire is driving the fan.
#
# Usage (on the fan-controller hardware, in the PiFire venv):
#   .venv/bin/python3 tools/emc2301_tach_diag.py
#
# Run it once with the fan STOPPED and once at FULL speed, and share both
# dumps.
#
# *****************************************

import sys

import board
import busio
from adafruit_bus_device.i2c_device import I2CDevice
from adafruit_extended_bus import ExtendedI2C

from common.datastore_accessors import read_settings
from probes.base import resolve_i2c_bus

# EMC2301/2/3/5 registers (DS20006532A).
_REGS = {
    0x24: "Fan Status",
    0x25: "Fan Stall Status",
    0x26: "Fan Spin Status",
    0x27: "Drive Fail Status",
    0x30: "Fan Setting (duty)",
    0x32: "Fan Config 1 (RANGE[6:5], EDGES[4:3])",
    0x3C: "TACH Target Low",
    0x3D: "TACH Target High",
    0x3E: "TACH Reading High",
    0x3F: "TACH Reading Low",
}

_RANGE_TO_MULTIPLIER = {0: 1, 1: 2, 2: 4, 3: 8}
_RPM_CONSTANT = 3932160


def _read_register(i2c_device, register):
    result = bytearray(1)
    with i2c_device as i2c:
        i2c.write_then_readinto(bytes([register]), result)
    return result[0]


def main():
    settings = read_settings()
    fan_cfg = settings["platform"].get("fan_controller", {})
    if str(fan_cfg.get("chip", "emc2101")).lower() != "emc2301":
        print(f"WARNING: configured fan chip is {fan_cfg.get('chip')!r}, not emc2301.")

    bus_kind = fan_cfg.get("i2c_bus_kind", "basic")
    bus_num = fan_cfg.get("i2c_bus_num", "1")
    address = fan_cfg.get("address", "0x2f")
    address = int(address, 16) if isinstance(address, str) else address

    if bus_kind == "extended":
        i2c = ExtendedI2C(resolve_i2c_bus(bus_num))
    else:
        i2c = busio.I2C(board.SCL, board.SDA)
    i2c_device = I2CDevice(i2c, address)

    print(f"EMC2301 @ {hex(address)} on {bus_kind} bus ({bus_num})")
    print("-" * 60)
    values = {}
    for reg, name in _REGS.items():
        val = _read_register(i2c_device, reg)
        values[reg] = val
        print(f"  0x{reg:02X} {name:<40} = 0x{val:02X}  (0b{val:08b})")

    print("-" * 60)
    # Decode the tach reading and RPM the way the driver does.
    count = ((values[0x3E] << 8) | values[0x3F]) >> 3
    config1 = values[0x32]
    m = _RANGE_TO_MULTIPLIER[(config1 >> 5) & 0x03]
    edges = (config1 >> 3) & 0x03
    rpm = round((m * _RPM_CONSTANT) / count, 2) if count else 0.0
    print(f"  tach count            = {count} (0x{count:04X})")
    print(f"  RANGE multiplier m    = {m}")
    print(f"  EDGES field           = {edges} (implies {edges + 1} poles)")
    print(f"  driver would report   = {rpm} RPM")
    print(f"  stall bit (0x25 b0)   = {values[0x25] & 0x01}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
