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
import spidev
from luma.lcd.device import ili9488
from display.base_320x480 import DisplayBase
from display._luma_panel import LumaPanelMixin
from display._encoder_input import EncoderInputMixin

"""
Display class definition
"""


class Display(EncoderInputMixin, LumaPanelMixin, DisplayBase):
    _LUMA_PANEL_CLASS = ili9488
    _LUMA_USE_EXPLICIT_SPIDEV = True
    _LUMA_SPIDEV_MODULE = spidev

    def __init__(self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={}):
        self.config = config
        super().__init__(dev_pins, buttonslevel, rotation, units, config)
