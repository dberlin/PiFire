"""Golden-master characterization for the six PID variants' update() output.

Pins each variant's update() series for a fixed input under a controlled clock,
so the PIDControllerBase refactor + dead-API removal (Phase I) are provably
behavior-preserving. METHOD: run-then-freeze -- the GOLDEN dict below was captured
from the CURRENT (pre-refactor) code and must not change when methods move into
PIDControllerBase or when the dead dispatch surface is deleted.
"""

import time
import importlib
import pytest

PID_CONFIGS = {
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
SERIES = [150, 160, 180, 200, 205, 210, 215, 218, 220, 221]
SETPOINT = 220.0
STEP = 20.0
T0 = 1000.0


class _Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


def _run_variant(module_name, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    mod = importlib.import_module(f"controller.{module_name}")
    c = mod.Controller(dict(PID_CONFIGS[module_name]), "F", dict(CYCLE_DATA))
    c.set_target(SETPOINT)
    out = []
    for i, current in enumerate(SERIES, 1):
        clock.t = T0 + i * STEP
        out.append(round(float(c.update(current)), 6))
    return out


# GOLDEN: captured from pre-refactor code on THIS machine (see step 2 -- the plan's
# pasted values differed beyond the 6th decimal for 5/6 variants, so per the plan's
# own instruction ("use the machine's value"), these were regenerated here and verified
# by hand against pid_clamping's first-update arithmetic). Do NOT hand-edit after capture.
GOLDEN = {
    "pid": [1.796296, 1.365741, 0.731481, 0.435185, 0.94213, 0.877315, 0.803241, 0.831944, 0.836111, 0.855093],
    "pid_clamping": [2.352778, 0.441667, 0.061111, -0.116667, 0.1875, 0.148611, 0.104167, 0.121389, 0.123889, 0.135278],
    "pid_clamping_percent_pb": [
        2.546296,
        0.477994,
        0.066138,
        -0.126263,
        0.202922,
        0.160835,
        0.112734,
        0.131373,
        0.134079,
        0.146405,
    ],
    "pid_ac": [1.0, 0.956111, 0.210741, -0.15963, 0.310278, 0.217685, 0.125093, 0.153796, 0.157963, 0.176944],
    "pid_parallel": [2.352, 0.441, 0.06, -0.118, 0.186, 0.147, 0.1025, 0.1197, 0.1222, 0.1336],
    "pid_sp": [1.0, 0.665488, -0.370505, -0.740875, 0.164966, 0.072374, -0.020219, 0.061806, 0.092153, 0.138275],
}


@pytest.mark.parametrize("module_name", list(PID_CONFIGS))
def test_pid_variant_update_series_is_stable(module_name, monkeypatch):
    assert _run_variant(module_name, monkeypatch) == GOLDEN[module_name]
