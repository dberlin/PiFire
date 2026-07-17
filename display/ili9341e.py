#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   This library supports using
 the ILI9341 display with 240Hx320W resolution.
 This module utilizes Luma.LCD to interface
 this display.

*****************************************
"""

"""
 Imported Libraries
"""
from luma.lcd.device import ili9341
from display.base_240x320 import DisplayBase
from display._luma_panel import LumaPanelMixin
from display._encoder_input import EncoderInputMixin

"""
Display class definition
"""


class Display(EncoderInputMixin, LumaPanelMixin, DisplayBase):
    _LUMA_PANEL_CLASS = ili9341

    def __init__(self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={}):
        self.config = config
        super().__init__(dev_pins, buttonslevel, rotation, units, config)
