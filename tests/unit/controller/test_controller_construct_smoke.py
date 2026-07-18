"""Smoke: every ControllerBase subclass still imports/constructs after the dead
dispatch surface (set_config/get_config/set_cycle_data/set_units/set_gains/get_k/
function_list/supported_functions) was removed. Confirms nothing referenced them."""

import importlib
import pytest

REMOVED = [
    "set_config",
    "get_config",
    "set_cycle_data",
    "set_units",
    "set_gains",
    "get_k",
    "supported_functions",
    "function_list",
]

CONFIGS = {
    "pid": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "center": 0.5},
    "pid_clamping": {"PB": 100.0, "Ti": 180.0, "Td": 45.0},
    "pid_clamping_percent_pb": {"PB": 42.0, "Ti": 180.0, "Td": 45.0},
    "pid_ac": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "stable_window": 12, "center_factor": 0.0010},
    "pid_parallel": {"Kp": 0.01, "Ki": 0.000055, "Kd": 0.45, "Clamping": True},
    "pid_sp": {
        "PB": 60.0,
        "Ti": 180.0,
        "Td": 45.0,
        "stable_window": 12,
        "center_factor": 0.0010,
        "tau": 115,
        "theta": 65,
    },
}
CYCLE_DATA = {"HoldCycleTime": 20}


@pytest.mark.parametrize("module_name", list(CONFIGS))
def test_pid_variant_constructs_without_dead_surface(module_name):
    c = importlib.import_module(f"controller.{module_name}").Controller(
        dict(CONFIGS[module_name]), "F", dict(CYCLE_DATA)
    )
    for name in REMOVED:
        assert not hasattr(c, name), f"{module_name} still exposes removed {name}"


@pytest.mark.parametrize("module_name", ["mpc", "fuzzy", "ml"])
def test_non_pid_controller_imports_clean(module_name):
    mod = importlib.import_module(f"controller.{module_name}")
    assert hasattr(mod, "Controller")
    for name in ("set_config", "supported_functions", "get_config", "set_units"):
        assert not hasattr(mod.Controller, name), f"{module_name}.Controller still has {name}"
