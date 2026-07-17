#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   This library supports using
 the ST7789 display with 240Hx240W resolution.
 This module utilizes Luma.LCD to interface
 this display.

*****************************************
"""

"""
 Imported Libraries
"""
import threading
from luma.core.interface.serial import spi
from luma.lcd.device import st7789
from display.base_240x240 import DisplayBase
from PIL import Image
from display._encoder_input import EncoderInputMixin

"""
Display class definition
"""


class Display(EncoderInputMixin, DisplayBase):
    _reset_data_on_event = True

    def __init__(self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={}):
        self.config = config
        super().__init__(dev_pins, buttonslevel, rotation, units, config)

    def _init_display_device(self):
        # Init Device
        dc_pin = self.dev_pins["display"]["dc"]
        led_pin = self.dev_pins["display"]["led"]
        rst_pin = self.dev_pins["display"]["rst"]
        spi_device = self.config.get("spi_device", 0)

        # bus_speed_hz in [mhz * 1000000 for mhz in [0.5, 1, 2, 4, 8, 16, 20, 24, 28, 32, 36, 40, 44, 48, 50, 52]
        self.serial = spi(port=0, device=spi_device, gpio_DC=dc_pin, gpio_RST=rst_pin)
        self.device = st7789(
            self.serial, active_low=False, width=240, height=240, gpio_LIGHT=led_pin, bus_speed=4000000
        )

        # Setup & Start Display Loop Thread
        display_thread = threading.Thread(target=self._display_loop)
        display_thread.start()

    """
	============== Graphics / Display / Draw Methods ============= 
	"""

    def _display_clear(self):
        img = Image.new("RGB", (self.WIDTH, self.HEIGHT), color=(0, 0, 0))
        # self.device.clear()
        self.device.backlight(False)
        # self.device.hide()
        self.device.display(img)

    def _display_canvas(self, canvas):
        # Display Image
        self.device.backlight(True)
        # self.device.show()
        """Luma.lcd's rotation settings are counter clockwise"""
        if self.rotation == 1:
            canvas = canvas.rotate(270)
        elif self.rotation == 2:
            canvas = canvas.rotate(180)
        elif self.rotation == 3:
            canvas = canvas.rotate(90)

        self.device.display(canvas.convert(mode="RGB"))
