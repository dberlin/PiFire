"""Hermetic snapshot harness for the legacy fixed DisplayBase classes.

Renders a base's `_display_*` methods to a PIL image (captured at the
`_display_canvas` sink) and hashes the raw pixel bytes. os.system is
neutralized because `_menu_display` shells out to `sudo reboot`.
"""

import hashlib
import importlib
from unittest import mock

from PIL import ImageFont

try:
    ImageFont.truetype("trebuc.ttf", 20)
    FONT_AVAILABLE = True
except OSError:
    FONT_AVAILABLE = False


def make_base(module, rotation=0, units="F"):
    mod = importlib.import_module(module)
    with mock.patch("os.system", side_effect=AssertionError("os.system blocked in snapshot harness")):
        base = mod.DisplayBase(dev_pins={}, buttonslevel="HIGH", rotation=rotation, units=units, config={})
    base._captured = None
    base._display_canvas = lambda canvas: setattr(base, "_captured", canvas)
    return base


def _pin_animation(base):
    # _display_current advances these every call (fan rotation, auger shift,
    # gauge color pulse). Pin them so a given input renders identical pixels.
    base.fan_rotation = 0
    base.auger_step = 0
    base.icon_color = 100
    base.inc_pulse_color = True


def render(base, method_name, *args):
    _pin_animation(base)
    base._captured = None
    getattr(base, method_name)(*args)
    assert base._captured is not None, f"{method_name} produced no canvas"
    return hashlib.sha256(base._captured.convert("RGBA").tobytes()).hexdigest()


SAMPLE_IN_DATA = {
    "probe_history": {"primary": {"Grill": 225}, "food": {"Probe1": 145}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 0, "Probe1": 165},
}
SAMPLE_STATUS_DATA = {
    "mode": "Smoke",
    "outpins": {"fan": True, "igniter": False, "auger": False},
    "notify_data": [],
    "recipe_paused": False,
    "recipe": False,
    "s_plus": False,
    "hopper_level_enabled": True,
    "hopper_level": 80,
    "p_mode": 2,
    "units": "F",
}
