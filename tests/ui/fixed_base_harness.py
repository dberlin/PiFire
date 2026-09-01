"""Hermetic snapshot harness for the legacy fixed DisplayBase classes.

Renders a base's `_display_*` methods to a PIL image (captured at the
`_display_canvas` sink) and hashes the raw pixel bytes. The power actions are
neutralized because `_menu_display` can reboot or halt the host.
"""

import hashlib
import importlib
from pathlib import Path
from unittest import mock

from PIL import ImageFont, features
from PIL import __version__ as PILLOW_VERSION

from tests.ui._driver_helpers import block_power_actions

_REFERENCE_TREBUCHET_SHA256 = "b69a5b33e997c3bc55f35dde8267cb93fe5fbdc3ecbc23b1d987602a9fd2b1f2"
_REFERENCE_PILLOW_VERSION = "12.3.0"
_REFERENCE_FREETYPE_VERSION = "2.14.3"


def _is_reference_renderer(
    font_path: Path,
    *,
    pillow_version: str,
    freetype_version: str | None,
) -> bool:
    try:
        font_sha256 = hashlib.sha256(font_path.read_bytes()).hexdigest()
    except OSError:
        return False
    return (
        font_sha256 == _REFERENCE_TREBUCHET_SHA256
        and pillow_version == _REFERENCE_PILLOW_VERSION
        and freetype_version == _REFERENCE_FREETYPE_VERSION
    )


try:
    _trebuchet = ImageFont.truetype("trebuc.ttf", 20)
    _trebuchet_path = Path(_trebuchet.path)
    FONT_AVAILABLE = True
except OSError:
    _trebuchet_path = None
    FONT_AVAILABLE = False

GOLDEN_ENVIRONMENT_AVAILABLE = _trebuchet_path is not None and _is_reference_renderer(
    _trebuchet_path,
    pillow_version=PILLOW_VERSION,
    freetype_version=features.version_module("freetype2"),
)


def make_base(module, rotation=0, units="F"):
    mod = importlib.import_module(module)
    with (
        mock.patch("os.system", side_effect=AssertionError("os.system blocked in snapshot harness")),
        block_power_actions("snapshot harness"),
    ):
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
