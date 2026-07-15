import pytest

from common.i2c_bus import I2CBusConfigError, configured_bus_kinds, validate_bus_kinds


def _settings(distance_kind=None, fan_kind=None):
    return {
        "platform": {
            "devices": {"distance": {"i2c_bus_kind": distance_kind} if distance_kind else {}},
            "fan_controller": {"i2c_bus_kind": fan_kind} if fan_kind else {},
        }
    }


def _probe_map(*kinds):
    return {"probe_devices": [{"config": {"i2c_bus_kind": k}} for k in kinds]}


def test_configured_bus_kinds_collects_all_surfaces():
    kinds = configured_bus_kinds(
        _settings(distance_kind="ft232h", fan_kind="mcp2221"), _probe_map("ft232h", "extended")
    )
    assert kinds == {"ft232h", "mcp2221", "extended"}


def test_configured_bus_kinds_conflict_raises_when_validated():
    kinds = configured_bus_kinds(_settings(fan_kind="basic"), _probe_map("ft232h"))
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(kinds)


def test_add_conflicting_probe_is_rejected():
    # basic fan + ft232h probe is the one unworkable combination.
    kinds = configured_bus_kinds(_settings(fan_kind="basic"), _probe_map("ft232h"))
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(kinds)
    # a workable combination validates cleanly
    validate_bus_kinds(configured_bus_kinds(_settings(fan_kind="mcp2221"), _probe_map("ft232h")))
