#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   Shared Luma-panel display mixin for the ili9341/ili9488 clone-matrix
   fixed-base display drivers.

   LumaPanelMixin is extracted verbatim from ili9341.py's
   _init_display_device/_display_clear/_display_canvas, which are
   byte-identical (modulo panel class, width/height, and the `em`
   variants' explicit spidev.SpiDev()) across ili9341, ili9341b, ili9341e,
   ili9341em, ili9488, ili9488b, ili9488e and ili9488em.

   Each driver sets `_LUMA_PANEL_CLASS` to its own luma.lcd.device panel
   class and imports that class itself -- this mixin does not hard-import
   any specific panel. Width/height are read from the shim's
   `_NOMINAL_WIDTH`/`_NOMINAL_HEIGHT` class attributes (Phase B), which
   equal the literals the drivers used to hardcode (ili9341: 320/240,
   ili9488: 480/320).

   The `em` variants (ili9341em, ili9488em) additionally pass an explicit
   `spidev.SpiDev()` instance into `spi(...)`; those two drivers set
   `_LUMA_USE_EXPLICIT_SPIDEV = True` to opt in, plus `_LUMA_SPIDEV_MODULE`
   pointing at their own already-`import`ed `spidev` module. `spidev` is
   deliberately NOT imported here: it is a real-hardware-only library not
   installed in dev/CI (see tests/ui/test_fixed_base_drivers_load.py's
   module docstring), and the two `em` drivers' own module-scope `import
   spidev` is what lets the test's `sys.modules` stub-overlay (installed
   only for the scope of each driver's own import) get captured correctly.

   Passing the already-imported module in via a class attr, rather than
   importing `spidev` (even lazily) from this shared module, avoids two
   failure modes: (1) a module-scope import here would run on the *first*
   driver to pull in this mixin regardless of whether it needs spidev
   (this module is cached after that), breaking the 6 non-`em` drivers
   when spidev isn't installed; (2) a lazy import inside
   `_init_display_device` would run at instantiation time, which is
   *after* the test harness's `sys.modules` stub-overlay for the driver's
   import has already been torn down, so it would raise
   ModuleNotFoundError even for the `em` drivers themselves.

   st7789e is Luma-backed too, but it is EXCLUDED from this mixin's
   _init_display_device: its `spi(...)` call omits bus_speed_hz/
   reset_hold_time/reset_release_time, its device ctor uses
   bus_speed=4000000 with no rotate=, and its _display_clear/_display_canvas
   are not byte-identical either (extra rotation handling, no
   .clear()/.hide()/.show()). st7789e keeps its own inline methods.

   Phase C, Task 4. No behavior change: see
   tests/ui/test_fixed_base_drivers_load.py.

*****************************************
"""

"""
 Imported Libraries
"""
import threading
from luma.core.interface.serial import spi

"""
Mixin class definition
"""


class LumaPanelMixin:
    """Shared Luma-panel display init/clear/canvas for the ili9341/ili9488 clone matrix.

    Set `_LUMA_PANEL_CLASS` on the inheriting class to the luma.lcd.device
    panel class to construct (e.g. `ili9341`, `ili9488`). Width/height are
    read from the shim's `_NOMINAL_WIDTH`/`_NOMINAL_HEIGHT`.

    Set `_LUMA_USE_EXPLICIT_SPIDEV = True` for the `em` variants, which
    pass an explicit `spidev.SpiDev()` into `spi(...)`. Those two drivers
    also set `_LUMA_SPIDEV_MODULE` to their own already-`import`ed `spidev`
    module -- the import itself stays in ili9341em.py/ili9488em.py (not
    here), so this mixin never touches `spidev` for the other 6 drivers
    that don't need it.
    """

    _LUMA_PANEL_CLASS = None
    _LUMA_USE_EXPLICIT_SPIDEV = False
    _LUMA_SPIDEV_MODULE = None

    def _init_display_device(self):
        # Init Device
        dc_pin = self.dev_pins["display"]["dc"]
        led_pin = self.dev_pins["display"]["led"]
        rst_pin = self.dev_pins["display"]["rst"]
        spi_device = self.config.get("spi_device", 0)

        spi_kwargs = {}
        if self._LUMA_USE_EXPLICIT_SPIDEV:
            spi_kwargs["spi"] = self._LUMA_SPIDEV_MODULE.SpiDev()

        self.serial = spi(
            port=0,
            device=spi_device,
            gpio_DC=dc_pin,
            gpio_RST=rst_pin,
            bus_speed_hz=32000000,
            reset_hold_time=0.2,
            reset_release_time=0.2,
            **spi_kwargs,
        )
        self.device = self._LUMA_PANEL_CLASS(
            self.serial,
            active_low=False,
            width=self._NOMINAL_WIDTH,
            height=self._NOMINAL_HEIGHT,
            gpio_LIGHT=led_pin,
            rotate=self.rotation,
        )

        # Setup & Start Display Loop Thread
        display_thread = threading.Thread(target=self._display_loop)
        display_thread.start()

    """
	============== Graphics / Display / Draw Methods ============= 
	"""

    def _display_clear(self):
        self.device.clear()
        self.device.backlight(False)
        self.device.hide()

    def _display_canvas(self, canvas):
        # Display Image
        self.device.backlight(True)
        self.device.show()
        self.device.display(canvas.convert(mode="RGB"))
