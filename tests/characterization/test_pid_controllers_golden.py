"""Golden-master characterization for the two supported PID-family controllers'
update() output.

Pins each controller's update() series for a fixed input under a controlled
clock, so the PIDControllerBase refactor + dead-API removal are provably
behavior-preserving. METHOD: run-then-freeze -- the GOLDEN dict below was
captured from the CURRENT (pre-refactor) code and must not change when methods
move into PIDControllerBase or when the dead dispatch surface is deleted.
"""

import importlib
import time

import pytest

PID_CONFIGS = {
    "pid": {"PB": 60.0, "Ti": 180.0, "Td": 45.0, "center": 0.5},
    "pid_sp": {
        "PB": 60.0,
        "Ti": 180.0,
        "Td": 45.0,
        "stable_window": 12,
        "center_factor": 0.0010,
    },
}

CYCLE_DATA = {}
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


# GOLDEN: captured from pre-refactor code, run on THIS machine (floating-point
# results differ beyond the 6th decimal across machines/library versions, so
# these values are machine-specific). Do NOT hand-edit after capture.
GOLDEN = {
    "pid": [1.796296, 1.365741, 0.731481, 0.435185, 0.94213, 0.877315, 0.803241, 0.831944, 0.836111, 0.855093],
    # Recaptured after PID-SP moved onto the Smith predictor: it regulates on
    # a dead-time-corrected temperature instead of extrapolating from a
    # configured tau/theta, so the series it produces is a different one by
    # design, not a drift in the refactor this file guards.
    "pid_sp": [1.0, 0.621472, 0.210741, -0.15963, 0.310278, 0.217685, 0.125093, 0.153796, 0.157963, 0.176944],
}


@pytest.mark.parametrize("module_name", list(PID_CONFIGS))
def test_pid_variant_update_series_is_stable(module_name, monkeypatch):
    assert _run_variant(module_name, monkeypatch) == GOLDEN[module_name]
