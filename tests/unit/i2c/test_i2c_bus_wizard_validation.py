import pytest

from common.i2c_bus import I2CBusConfigError, configured_bus_kinds, validate_bus_kinds


def _settings(distance=None, fan=None, fan_chip="emc2101", distance_module="vl53l0x"):
    return {
        "modules": {"dist": distance_module},
        "platform": {
            "devices": {"distance": {"i2c_bus": distance} if distance else {}},
            "fan_controller": {
                "chip": fan_chip,
                **({"i2c_bus": fan} if fan else {}),
            },
        },
    }


def _probe_map(*buses):
    return {"probe_devices": [{"config": {"i2c_bus": bus}} for bus in buses]}


def test_configured_bus_kinds_collects_all_surfaces():
    kinds = configured_bus_kinds(
        _settings(distance={"kind": "ft232h", "url": ""}, fan={"kind": "mcp2221", "serial": ""}),
        _probe_map({"kind": "ft232h", "url": ""}, {"kind": "kernel", "bus_num": 1}),
    )
    assert kinds == {"ft232h", "mcp2221", "kernel"}


def test_configured_bus_kinds_conflict_raises_when_validated():
    kinds = configured_bus_kinds(_settings(fan={"kind": "basic"}), _probe_map({"kind": "ft232h", "url": ""}))
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(kinds)


def test_add_conflicting_probe_is_rejected():
    # basic fan + ft232h probe is the one unworkable combination.
    kinds = configured_bus_kinds(_settings(fan={"kind": "basic"}), _probe_map({"kind": "ft232h", "url": ""}))
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds(kinds)
    # a workable combination validates cleanly
    validate_bus_kinds(
        configured_bus_kinds(
            _settings(fan={"kind": "mcp2221", "serial": ""}), _probe_map({"kind": "ft232h", "url": ""})
        )
    )


def test_configured_bus_kinds_ignores_unused_buses_in_relay_only_mode():
    kinds = configured_bus_kinds(
        _settings(
            distance={"kind": "basic"},
            fan={"kind": "mcp2221", "serial": ""},
            fan_chip="none",
            distance_module="none",
        ),
        _probe_map(),
    )
    assert kinds == set()
    validate_bus_kinds(kinds)


def test_configured_bus_kinds_counts_uppercase_pwm_chip_but_not_disabled_distance():
    kinds = configured_bus_kinds(
        _settings(
            distance={"kind": "basic"},
            fan={"kind": "mcp2221", "serial": ""},
            fan_chip="EMC2101",
            distance_module="none",
        ),
        _probe_map(),
    )
    assert kinds == {"mcp2221"}
    validate_bus_kinds(kinds)
