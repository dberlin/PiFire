"""The parts of the GrillPlatform system API that every platform must satisfy
identically. These assertions were copy-pasted per platform; a shared contract
test also catches a NEW platform that quietly omits a command."""

import logging
import sys
import types

import pytest

# raspberry_pi_all imports `from rpi_hardware_pwm import HardwarePWM` at module
# load; that package is Pi-only and absent in the test venv. Stub it so the
# module imports on a generic host. (gpiozero IS installed.)
if "rpi_hardware_pwm" not in sys.modules:
    _stub = types.ModuleType("rpi_hardware_pwm")
    _stub.HardwarePWM = type("HardwarePWM", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["rpi_hardware_pwm"] = _stub

import grillplat.prototype as proto  # noqa: E402
import grillplat.raspberry_pi_all as rpi  # noqa: E402

REQUIRED_COMMANDS = (
    "check_throttled",
    "check_wifi_quality",
    "check_cpu_temp",
    "supported_commands",
    "check_alive",
    "scan_bluetooth",
    "os_info",
    "network_info",
    "hardware_info",
)


def _bare(module, logger_name):
    # System methods only need self.logger; skip __init__ (no GPIO on host).
    obj = object.__new__(module.GrillPlatform)
    obj.logger = logging.getLogger(logger_name)
    return obj


@pytest.fixture(params=[(proto, "test.prototype"), (rpi, "test.rpi")], ids=["prototype", "raspberry_pi"])
def platform(request):
    module, logger_name = request.param
    return _bare(module, logger_name)


def test_supported_commands_lists_all_nine(platform):
    cmds = platform.supported_commands([])["data"]["supported_cmds"]
    for name in REQUIRED_COMMANDS:
        assert name in cmds


def test_check_alive_ok(platform):
    assert platform.check_alive([]) == {
        "result": "OK",
        "message": "The control script is running.",
        "data": {},
    }
