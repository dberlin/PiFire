#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   Shared 3-button gpiozero input mixin for the fixed-base display drivers.

   ButtonInputMixin is extracted verbatim from ili9341b.py's
   _init_input/_enter_callback/_up_callback/_down_callback/_event_detect,
   which are byte-identical across ili9341b, ili9488b and st7789_240x320b.

   Unlike the rotary-encoder mixins in _encoder_input.py, this _event_detect
   has no `input_counter` gate.

   Phase C, Task 3. No behavior change: see
   tests/ui/test_driver_input_behavior.py.

*****************************************
"""

"""
 Imported Libraries
"""
import time
import threading
from gpiozero import Button

"""
Mixin class definition
"""


class ButtonInputMixin:
    """3-button gpiozero input, shared by ili9341b, ili9488b and st7789_240x320b."""

    def _init_input(self):
        self.input_enabled = True
        # Init GPIO for button input, setup callbacks: Uncomment to utilize GPIO input
        self.up = self.dev_pins["input"]["up_clk"]  # UP - GPIO16
        self.down = self.dev_pins["input"]["down_dt"]  # DOWN - GPIO20
        self.enter = self.dev_pins["input"]["enter_sw"]  # ENTER - GPIO21
        self.debounce_ms = 500  # number of milliseconds to debounce input
        self.input_event = None
        self.input_counter = 0

        # ==== Buttons Setup =====
        self.pull_up = self.buttonslevel == "HIGH"

        self.up_button = Button(pin=self.up, pull_up=self.pull_up, hold_time=0.25, hold_repeat=True)
        self.down_button = Button(pin=self.down, pull_up=self.pull_up, hold_time=0.25, hold_repeat=True)
        self.enter_button = Button(pin=self.enter, pull_up=self.pull_up)

        # Init Menu Structures
        self._init_menu()

        self.up_button.when_pressed = self._up_callback
        self.down_button.when_pressed = self._down_callback
        self.enter_button.when_pressed = self._enter_callback
        self.up_button.when_held = self._up_callback
        self.down_button.when_held = self._down_callback

    """
	============== Input Callbacks ============= 
	"""

    def _enter_callback(self):
        self.input_event = "ENTER"

    def _up_callback(self, held=False):
        self.input_event = "UP"

    def _down_callback(self, held=False):
        self.input_event = "DOWN"

    """
	 ====================== Input & Menu Code ========================
	"""

    def _event_detect(self):
        """
        Called to detect input events from buttons.
        """
        command = self.input_event  # Save to variable to prevent spurious changes
        if command:
            self.display_timeout = None  # If something is being displayed i.e. text, network, splash then override this

            if command not in ["UP", "DOWN", "ENTER"]:
                return

            self.display_command = None
            self.display_data = None
            self.input_event = None
            self.menu_active = True
            self.menu_time = time.time()
            self._menu_display(command)
            self.input_counter = 0
