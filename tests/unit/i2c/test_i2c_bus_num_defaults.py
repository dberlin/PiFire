"""The extended-I2C-bus field starts blank.

"CP2112" as a default is a guess at one particular USB-to-I2C bridge, offered on
every board and every I2C probe including the ones with no bridge at all. It
reads as a configured choice rather than a prompt, and it is wrong for the two
bus kinds whose selector is a device URL/serial (ft232h, mcp2221) -- a leftover
"CP2112" there names a bus that kind can never open.
"""

import json
from pathlib import Path

from common.defaults import default_settings
from common.settings_schema import _DistanceDeviceConfig

MANIFEST = Path(__file__).resolve().parents[3] / "wizard" / "wizard_manifest.json"


def _manifest():
    with open(MANIFEST) as handle:
        return json.load(handle)


def _bus_num_defaults():
    """Every declared default for an i2c_bus_num field in the manifest, as
    (where, value) pairs -- the grillplatform/distance settings-dependency form
    and the probes' device_specific config form."""
    found = []
    manifest = _manifest()
    for section, modules in manifest["modules"].items():
        for name, module in modules.items():
            for dep, spec in (module.get("settings_dependencies") or {}).items():
                if (spec.get("settings") or [""])[-1] == "i2c_bus_num":
                    found.append((f"{section}/{name}/{dep}", spec.get("default")))
            for option in (module.get("device_specific") or {}).get("config") or []:
                if option.get("label") == "i2c_bus_num":
                    found.append((f"{section}/{name}/config/i2c_bus_num", option.get("default")))
    return found


def test_manifest_declares_every_i2c_bus_num_blank():
    defaults = _bus_num_defaults()
    # Guard the guard: if the shapes above stop matching, this test would pass
    # by finding nothing at all.
    assert len(defaults) >= 13
    assert [(where, value) for where, value in defaults if value != ""] == []


def test_distance_sensor_bus_default_is_blank():
    assert default_settings()["platform"]["devices"]["distance"]["i2c_bus_num"] == ""
    assert _DistanceDeviceConfig().i2c_bus_num == ""
