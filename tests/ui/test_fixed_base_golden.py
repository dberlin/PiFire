"""Frozen pixel-hash baseline for the three legacy fixed DisplayBase classes.

This is the golden contract for the base_fixed merge (Phase B, Tasks 4-5):
every case here must render to the exact same pixel hash on the merged base
as it does today on the three separate, unmodified bases (with the sole,
explicitly documented exception of the Task 5 240x240 re-baseline).

CAPTURE_GOLDEN=1 must NEVER be used again after this file's initial commit,
except for that documented re-baseline.

Determinism note on time-based branches: `_display_current` computes
countdown/lid-pause text from `time.time() - status_data["start_time"]`
(or `status_data["lid_open_endtime"] - time.time()`) with no mock available
in the harness. We pin `start_time` / `lid_open_endtime` to 0 (the Unix
epoch), which is always so far in the past that the elapsed time exceeds
any duration used here -- the code's own `> 0 else 0` clamp then always
renders "0s" / "Lid Pause 0s", regardless of when the suite runs. This
keeps the branch exercised (the text draws, the layout code runs) while
keeping the hash stable across capture-time and any later verification run.
"""

import json
import os
import pathlib

import pytest

from tests.ui.fixed_base_harness import FONT_AVAILABLE, make_base, render

pytestmark = pytest.mark.skipif(not FONT_AVAILABLE, reason="trebuc.ttf not installed")

GOLDEN = pathlib.Path(__file__).parent / "fixtures" / "fixed_base_golden.json"

# ---------------------------------------------------------------------------
# in_data variants (probe_history / primary_setpoint / notify_targets)
# ---------------------------------------------------------------------------

IN_1FOOD = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}

IN_0FOOD = {
    "probe_history": {"primary": {"Grill": 225}, "food": {}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0},
}

IN_2FOOD = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145, "Probe2": 160}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165, "Probe2": 170},
}

IN_ZERO = {
    "probe_history": {"primary": {"Grill": 0}, "food": {"Probe1": 145}},
    "primary_setpoint": 0,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}

# ---------------------------------------------------------------------------
# status_data variants, one per mode, plus Smoke-mode cross-cuts
# ---------------------------------------------------------------------------

ST_STARTUP = {
    "mode": "Startup",
    "outpins": {"fan": True, "igniter": True, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 2,
    "units": "F",
    "start_duration": 60,
    "start_time": 0,
}

ST_SMOKE = {
    "mode": "Smoke",
    "outpins": {"fan": True, "igniter": False, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 4,
    "units": "F",
}

ST_HOLD = {
    "mode": "Hold",
    "outpins": {"fan": True, "igniter": False, "auger": True},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 0,
    "units": "F",
    "lid_open_detected": True,
    "lid_open_endtime": 0,
}

ST_PRIME = {
    "mode": "Prime",
    "outpins": {"fan": False, "igniter": False, "auger": True},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 0,
    "units": "F",
    "prime_duration": 30,
    "start_time": 0,
}

ST_REIGNITE = {
    "mode": "Reignite",
    "outpins": {"fan": True, "igniter": True, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 3,
    "units": "F",
    "start_duration": 45,
    "start_time": 0,
}

ST_SHUTDOWN = {
    "mode": "Shutdown",
    "outpins": {"fan": True, "igniter": False, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 0,
    "units": "F",
    "shutdown_duration": 90,
    "start_time": 0,
}

# Cross-cuts, all on top of the canonical Smoke state.
ST_SMOKE_OUTPINS_ALL = {**ST_SMOKE, "outpins": {"fan": True, "igniter": True, "auger": True}}
ST_SMOKE_NOTIFY2 = {**ST_SMOKE, "notify_data": [{"req": True, "type": "probe"}, {"req": True, "type": "primary"}]}
ST_SMOKE_RECIPE_PAUSED = {**ST_SMOKE, "recipe_paused": True}
ST_SMOKE_RECIPE = {**ST_SMOKE, "recipe": True}
ST_SMOKE_SPLUS = {**ST_SMOKE, "s_plus": True}
ST_SMOKE_HOPPER50 = {**ST_SMOKE, "hopper_level": 50}
ST_SMOKE_HOPPER10 = {**ST_SMOKE, "hopper_level": 10}
ST_SMOKE_UNITS_C = {**ST_SMOKE, "units": "C"}

# (state_name, in_data, status_data, units)
# `units` drives base construction (self.units, which the gauge math reads);
# status_data["units"] is informational only and is not read by
# _display_current, but we keep it consistent for realism.
MODE_STATES = [
    ("startup", IN_1FOOD, ST_STARTUP, "F"),
    ("smoke", IN_1FOOD, ST_SMOKE, "F"),
    ("hold", IN_1FOOD, ST_HOLD, "F"),
    ("prime", IN_1FOOD, ST_PRIME, "F"),
    ("reignite", IN_1FOOD, ST_REIGNITE, "F"),
    ("shutdown", IN_1FOOD, ST_SHUTDOWN, "F"),
    ("smoke_outpins_all", IN_1FOOD, ST_SMOKE_OUTPINS_ALL, "F"),
    ("smoke_food0", IN_0FOOD, ST_SMOKE, "F"),
    ("smoke_food2", IN_2FOOD, ST_SMOKE, "F"),
    ("smoke_notify2", IN_1FOOD, ST_SMOKE_NOTIFY2, "F"),
    ("smoke_recipe_paused", IN_1FOOD, ST_SMOKE_RECIPE_PAUSED, "F"),
    ("smoke_recipe", IN_1FOOD, ST_SMOKE_RECIPE, "F"),
    ("smoke_splus", IN_1FOOD, ST_SMOKE_SPLUS, "F"),
    ("smoke_hopper50", IN_1FOOD, ST_SMOKE_HOPPER50, "F"),
    ("smoke_hopper10", IN_1FOOD, ST_SMOKE_HOPPER10, "F"),
    ("smoke_units_c", IN_1FOOD, ST_SMOKE_UNITS_C, "C"),
    ("smoke_zero_temps", IN_ZERO, ST_SMOKE, "F"),
]

SAMPLE_IP = "192.168.1.42"

# (short_name, module, rotations)
MODULES = [
    ("240x240", "display.base_240x240", [0]),
    ("240x320", "display.base_240x320", [0, 90]),
    ("320x480", "display.base_320x480", [0, 90]),
]

# CASES: list of (case_name, module, rotation, units, method, args_factory)
CASES = []

for short, module, rotations in MODULES:
    for rotation in rotations:
        for state_name, in_data, status_data, units in MODE_STATES:
            name = f"{short}:current:{state_name}:{rotation}"
            CASES.append(
                (
                    name,
                    module,
                    rotation,
                    units,
                    "_display_current",
                    lambda in_data=in_data, status_data=status_data: (in_data, status_data),
                )
            )

        CASES.append((f"{short}:splash:base:{rotation}", module, rotation, "F", "_display_splash", lambda: ()))
        CASES.append(
            (
                f"{short}:text:network_error:{rotation}",
                module,
                rotation,
                "F",
                "_display_text",
                lambda: (),
            )
        )
        CASES.append(
            (
                f"{short}:network:sample_ip:{rotation}",
                module,
                rotation,
                "F",
                "_display_network",
                lambda: (SAMPLE_IP,),
            )
        )


def _load_golden():
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.mark.parametrize("case", CASES, ids=[c[0] for c in CASES])
def test_matches_golden(case):
    name, module, rotation, units, method, args_factory = case
    base = make_base(module, rotation=rotation, units=units)
    if method == "_display_text":
        base.display_text("Network Error")
    h = render(base, method, *args_factory())
    golden = _load_golden()
    if os.environ.get("CAPTURE_GOLDEN") == "1":
        golden[name] = h
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(dict(sorted(golden.items())), indent=2) + "\n")
        pytest.skip(f"captured {name}")
    assert name in golden, f"no baseline for {name}; run with CAPTURE_GOLDEN=1 once"
    assert h == golden[name], f"pixel hash changed for {name}"
