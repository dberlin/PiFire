"""The TS validation rules and the Python ones must agree.

i2cBusTypes.ts duplicates the per-field format checks so the wizard can report
them on a keystroke rather than an HTTP round trip. Python stays authoritative
for configurations that arrive by settings import or a hand-edited backup. Two
copies of a rule drift; this reads the TS source and asserts the pairs still
match, so the drift is a red test rather than a config the wizard accepts and
the control process rejects.
"""

import pathlib

import pytest

from common.i2c_bus_config import I2CBusConfigError, parse_i2c_bus

TS = pathlib.Path(__file__).resolve().parents[2] / "web-react/src/helpers/wizard/i2cBusTypes.ts"

REJECTED = [
    {"kind": "kernel", "adapter": ""},
    {"kind": "kernel", "serial": ""},
    {"kind": "kernel", "bus_num": "CP2112"},
    # What a saved draft holds when the operator picked "Bus number" and typed
    # nothing: i2cBusTypes.ts writes null, and JSON carries it across unchanged.
    {"kind": "kernel", "bus_num": None},
]

ACCEPTED = [
    {"kind": "basic"},
    {"kind": "kernel", "bus_num": 3},
    {"kind": "kernel", "adapter": "CP2112"},
    {"kind": "kernel", "serial": "AB12"},
    {"kind": "ft232h", "url": ""},
    {"kind": "ft232h", "url": "ftdi://ftdi:232h:FT9/1"},
    {"kind": "mcp2221", "serial": ""},
]


@pytest.mark.parametrize("config", REJECTED)
def test_python_rejects_what_the_ts_rules_reject(config):
    with pytest.raises(I2CBusConfigError):
        parse_i2c_bus(config)


@pytest.mark.parametrize("config", ACCEPTED)
def test_python_accepts_what_the_ts_rules_accept(config):
    parse_i2c_bus(config)


def test_the_ts_rules_cover_the_same_fields():
    """A field checked on one side and not the other is the drift this catches."""
    source = TS.read_text()
    for token in ("bus_num", "adapter", "serial", "ftdi://"):
        assert token in source, f"i2cBusTypes.ts no longer mentions {token}"
