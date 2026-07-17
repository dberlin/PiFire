#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   This library supports using
 the ILI9488 display with 320Hx480W resolution.
 This module utilizes Luma.LCD to interface
 this display.

*****************************************
"""

"""
 Imported Libraries
"""
import threading  # noqa: F401  re-exported for tests/ui/test_fixed_base_drivers_load.py's mod.threading.Thread patch
from luma.lcd.device import ili9488
from display.base_320x480 import DisplayBase
from display._luma_panel import LumaPanelMixin

"""
Display class definition
"""


class Display(LumaPanelMixin, DisplayBase):
    _LUMA_PANEL_CLASS = ili9488

    def __init__(self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={}):
        self.config = config
        super().__init__(dev_pins, buttonslevel, rotation, units, config)
