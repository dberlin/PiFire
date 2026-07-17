"""Task 6 (Phase B): prove the base_240x240/base_240x320/base_320x480 shims
are transparent to every one of the 16 legacy display driver subclasses, and
that the wizard's display driver manifest still resolves all of them.

Each driver imports a real hardware library that is not installed in this
dev/CI environment (luma, ST7789 (Pimoroni), gpiozero, pyky040, spidev). None
of that hardware is touched: every hardware import is stubbed via a
`sys.modules` overlay for the scope of the driver module's own import only
(mirrors the `mock.patch.object(mod, HardwareClass)` pattern used for
`grillplat.x86_numato` in tests/conftest.py's `x86_platform` fixture -- same
idea, applied to modules that do not exist here at all rather than to
attributes of an existing module).

`_init_display_device` (and, for the `*e` rotary-encoder variants,
`_init_input`) starts one or two real background threads
(`threading.Thread(target=self._display_loop)` / `.../self.encoder.watch)`).
`_display_loop` is an infinite `while True` loop with real `time.sleep`
calls (characterized in test_fixed_base_loop.py) -- letting it actually
start would spin a non-daemon thread forever and hang the test process on
exit. `threading.Thread` is therefore patched to a no-op for every driver,
not just the encoder ones. `os.system` is neutralized too, mirroring
`fixed_base_harness.make_base`, because `_menu_display` shells out to
`sudo reboot` -- see the repo's history of real reboot incidents from
unmocked display `os.system` calls.

IMPORTANT ordering hazard: `display.base_fixed` (and therefore PIL/qrcode/
common, which it imports at module scope) must already be imported for real
BEFORE any hardware-stub overlay is installed. If a driver is imported for
the first time while sys.modules already contains the fake luma/pyky040/etc.
entries, `_init_background`'s call to `PIL.Image.open` on a perfectly valid
JPEG asset intermittently raises `UnidentifiedImageError` (empirically
confirmed while writing this test: instantiating st7789e first through the
overlay failed there 100% of the time; pre-warming `display.base_fixed`
before installing any overlay fixed it every time). Pre-warming below avoids
that whole hazard rather than chasing it further.
"""

import importlib
import os
import sys
import types
from unittest import mock

import pytest

import display.base_fixed  # noqa: F401  pre-warm real PIL/qrcode/common imports; see module docstring

from tests.conftest import REPO_BASE, load_wizard_manifest

FULL_DEV_PINS = {
    "display": {"dc": 24, "led": 5, "rst": 25},
    "input": {"up_clk": 16, "down_dt": 20, "enter_sw": 21},
}

# Expected (WIDTH, HEIGHT) at rotation=0 per the shim's _NOMINAL_WIDTH/_NOMINAL_HEIGHT.
EXPECTED_DIMENSIONS = {
    "240x240": (240, 240),
    "240x320": (320, 240),  # landscape
    "320x480": (480, 320),  # landscape
}

# Expected min_transition_delay per shim (Task 5): the one place the unified
# base_fixed loop still varies per resolution.
EXPECTED_DELAY = {
    "240x240": 1.0,
    "240x320": 0.1,
    "320x480": 0.1,
}


def _hardware_stubs(*, luma=False, st7789_pimoroni=False, gpiozero=False, pyky040=False, spidev=False):
    """Build a sys.modules overlay stubbing exactly the hardware libraries one
    driver needs at import time. None of luma/ST7789/gpiozero/pyky040/spidev
    is installed in this environment (verified: all raise ModuleNotFoundError)."""
    overlay = {}
    if luma:
        overlay["luma"] = types.ModuleType("luma")
        overlay["luma.core"] = types.ModuleType("luma.core")
        overlay["luma.core.interface"] = types.ModuleType("luma.core.interface")
        serial_mod = types.ModuleType("luma.core.interface.serial")
        serial_mod.spi = mock.MagicMock(name="spi")
        overlay["luma.core.interface.serial"] = serial_mod
        overlay["luma.lcd"] = types.ModuleType("luma.lcd")
        device_mod = types.ModuleType("luma.lcd.device")
        device_mod.ili9341 = mock.MagicMock(name="ili9341_device")
        device_mod.ili9488 = mock.MagicMock(name="ili9488_device")
        device_mod.st7789 = mock.MagicMock(name="st7789_device")
        overlay["luma.lcd.device"] = device_mod
    if st7789_pimoroni:
        st7789_mod = types.ModuleType("ST7789")
        st7789_cls = mock.MagicMock(name="ST7789_class")
        # Default device geometry matches the 240x320 shim's landscape nominal
        # size; test_st7789_device_geometry_override_still_works overrides it.
        st7789_cls.return_value.width = 320
        st7789_cls.return_value.height = 240
        st7789_mod.ST7789 = st7789_cls
        overlay["ST7789"] = st7789_mod
    if gpiozero:
        gz_mod = types.ModuleType("gpiozero")
        gz_mod.Button = mock.MagicMock(name="Button")
        overlay["gpiozero"] = gz_mod
    if pyky040:
        inner = types.ModuleType("pyky040.pyky040")
        inner.Encoder = mock.MagicMock(name="Encoder")
        outer = types.ModuleType("pyky040")
        outer.pyky040 = inner
        overlay["pyky040"] = outer
        overlay["pyky040.pyky040"] = inner
    if spidev:
        spidev_mod = types.ModuleType("spidev")
        spidev_mod.SpiDev = mock.MagicMock(name="SpiDev")
        overlay["spidev"] = spidev_mod
    return overlay


def _load_driver(module_path, **stub_kwargs):
    """Import a driver module with its hardware libraries stubbed for the
    duration of the import only. The module then stays cached in
    sys.modules exactly like any normal import (a second call is a cache hit
    and does not need the overlay any more)."""
    overlay = _hardware_stubs(**stub_kwargs)
    with mock.patch.dict(sys.modules, overlay):
        return importlib.import_module(module_path)


def _instantiate(mod, **overrides):
    """Construct mod.Display with the display/encoder thread(s) and
    os.system blocked, so no real SPI/pygame thread ever starts and no
    `sudo reboot` can be shelled out."""
    kwargs = dict(dev_pins=FULL_DEV_PINS, buttonslevel="HIGH", rotation=0, units="F", config={})
    kwargs.update(overrides)
    with (
        mock.patch.object(mod.threading, "Thread") as mock_thread,
        mock.patch("os.system", side_effect=AssertionError(f"os.system blocked for {mod.__name__}")),
    ):
        mock_thread.return_value.start = lambda: None
        return mod.Display(**kwargs)


# (short id, module path, resolution family, hardware-stub kwargs)
DRIVERS = [
    ("st7789e", "display.st7789e", "240x240", dict(luma=True, pyky040=True)),
    ("ili9341", "display.ili9341", "240x320", dict(luma=True)),
    ("ili9341b", "display.ili9341b", "240x320", dict(luma=True, gpiozero=True)),
    ("ili9341e", "display.ili9341e", "240x320", dict(luma=True, pyky040=True)),
    ("ili9341em", "display.ili9341em", "240x320", dict(luma=True, pyky040=True, spidev=True)),
    ("pygame_240x320", "display.pygame_240x320", "240x320", dict()),
    ("pygame_240x320b", "display.pygame_240x320b", "240x320", dict()),
    ("st7789_240x320", "display.st7789_240x320", "240x320", dict(st7789_pimoroni=True)),
    ("st7789_240x320b", "display.st7789_240x320b", "240x320", dict(st7789_pimoroni=True, gpiozero=True)),
    ("st7789_240x320e", "display.st7789_240x320e", "240x320", dict(st7789_pimoroni=True, pyky040=True)),
    ("st7789v_240x320", "display.st7789v_240x320", "240x320", dict(st7789_pimoroni=True)),
    ("st7789v_240x320e", "display.st7789v_240x320e", "240x320", dict(st7789_pimoroni=True, pyky040=True)),
    ("ili9488", "display.ili9488", "320x480", dict(luma=True)),
    ("ili9488b", "display.ili9488b", "320x480", dict(luma=True, gpiozero=True)),
    ("ili9488e", "display.ili9488e", "320x480", dict(luma=True, pyky040=True)),
    ("ili9488em", "display.ili9488em", "320x480", dict(luma=True, pyky040=True, spidev=True)),
]

assert len(DRIVERS) == 16, "expected exactly 16 fixed-base drivers (verified-facts inventory)"


@pytest.mark.parametrize("case", DRIVERS, ids=[c[0] for c in DRIVERS])
def test_driver_imports_and_instantiates(case):
    _short, module_path, resolution, stub_kwargs = case
    mod = _load_driver(module_path, **stub_kwargs)
    assert hasattr(mod, "Display"), f"{module_path} has no Display class"

    d = _instantiate(mod)

    assert (d.WIDTH, d.HEIGHT) == EXPECTED_DIMENSIONS[resolution], (
        f"{module_path}: expected {EXPECTED_DIMENSIONS[resolution]} at rotation 0, got {(d.WIDTH, d.HEIGHT)}"
    )
    # Task 5 added min_transition_delay as a class attribute on the shim;
    # this proves it reaches every driver subclass unchanged.
    assert d.min_transition_delay == EXPECTED_DELAY[resolution], (
        f"{module_path}: expected min_transition_delay {EXPECTED_DELAY[resolution]}, got {d.min_transition_delay}"
    )


def test_non_square_driver_swaps_dimensions_at_rotation_90():
    # ili9341 sits on the 240x320 shim: 320x240 landscape at rotation 0 (see
    # the parametrized test above), 240x320 portrait at rotation 90 --
    # proving _init_globals' rotation branch still reaches the driver
    # through the shim unchanged.
    mod = _load_driver("display.ili9341", luma=True)
    d = _instantiate(mod, rotation=90)
    assert (d.WIDTH, d.HEIGHT) == (240, 320)


def test_st7789_device_geometry_override_still_works():
    # The 5 st7789* (Pimoroni ST7789) drivers overwrite self.WIDTH/HEIGHT
    # from self.device.width/height in _init_display_device, which runs
    # *after* the shim's _init_globals has already set WIDTH/HEIGHT from
    # _NOMINAL_WIDTH/_NOMINAL_HEIGHT. Confirm that override still reaches
    # through the shim by giving the mocked device a non-default geometry.
    mod = _load_driver("display.st7789_240x320", st7789_pimoroni=True)
    mod.ST7789.ST7789.return_value.width = 111
    mod.ST7789.ST7789.return_value.height = 222
    d = _instantiate(mod)
    assert (d.WIDTH, d.HEIGHT) == (111, 222)


ALL_16_FILENAMES = [c[1].removeprefix("display.") for c in DRIVERS]


def test_manifest_lists_all_16_driver_identifiers():
    """wizard/wizard_manifest.json['modules']['display'] is the manifest the
    wizard/settings UI uses to build the selectable-display list --
    controller/runtime/devices.py does
    `importlib.import_module(f"display.{display_name}")` using this same
    'filename' field at runtime. Confirm all 16 fixed-base driver
    identifiers are still present and each resolves to a real
    display/<filename>.py module: the shims kept every driver's module
    name/path unchanged, so the manifest <-> module mapping is unaffected."""
    manifest_display = load_wizard_manifest()["modules"]["display"]
    for filename in ALL_16_FILENAMES:
        assert filename in manifest_display, f"{filename} missing from wizard_manifest.json"
        entry = manifest_display[filename]
        assert entry["filename"] == filename
        assert os.path.exists(os.path.join(REPO_BASE, "display", f"{filename}.py")), (
            f"manifest entry {filename!r} does not resolve to a display/ module"
        )
