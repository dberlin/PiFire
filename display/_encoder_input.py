#!/usr/bin/env python3
"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description:
   Shared rotary-encoder input mixins for the fixed-base display drivers.

   EncoderInputMixin (Group A -- debounced) is extracted verbatim from
   ili9341e.py's _init_input/_click_callback/_inc_callback/_dec_callback/
   _event_detect, which were byte-identical across ili9341e, ili9341em,
   ili9488e, ili9488em and (modulo a single blank line in _init_input, and
   the _event_detect divergence handled below via `_reset_data_on_event`)
   st7789e.

   SimpleEncoderInputMixin (Group B -- trivial, no debounce state) is
   extracted verbatim from st7789_240x320e.py, byte-identical to
   st7789v_240x320e.

   Phase C, Task 2. No behavior change: see
   tests/ui/test_driver_input_behavior.py.

*****************************************
"""

"""
 Imported Libraries
"""
import time
import threading
from pyky040 import pyky040

"""
Mixin class definitions
"""


class EncoderInputMixin:
    """Group A: debounced rotary encoder input.

    Used by ili9341e, ili9341em, ili9488e, ili9488em and st7789e.

    `_reset_data_on_event` is False by default, reproducing the shared
    _event_detect body of the first four drivers verbatim. st7789e sets
    it True on its class to additionally null `in_data`/`status_data` and
    clear `monitor_display` -- a 3-line divergence pinned by
    tests/ui/test_driver_input_behavior.py's st7789e-specific tests.
    """

    _reset_data_on_event = False

    def _init_input(self):
        self.input_enabled = True
        # Init constants and variables
        clk_pin = self.dev_pins["input"]["up_clk"]  # Clock - GPIO16
        dt_pin = self.dev_pins["input"]["down_dt"]  # DT - GPIO20
        sw_pin = self.dev_pins["input"]["enter_sw"]  # Switch - GPIO21
        self.input_event = None
        self.input_counter = 0
        self.last_direction = None
        self.last_movement_time = 0
        self.enter_received = False

        # Init Menu Structures
        self._init_menu()

        # Init Device
        self.encoder = pyky040.Encoder(CLK=clk_pin, DT=dt_pin, SW=sw_pin)
        self.encoder.setup(
            scale_min=0,
            scale_max=100,
            step=1,
            inc_callback=self._inc_callback,
            dec_callback=self._dec_callback,
            sw_callback=self._click_callback,
            polling_interval=200,
        )

        # Setup & Start Input Thread
        encoder_thread = threading.Thread(target=self.encoder.watch)
        encoder_thread.start()

    """
	============== Input Callbacks ============= 
	"""

    def _click_callback(self):
        self.input_event = "ENTER"
        self.enter_received = True

    def _inc_callback(self, v):
        current_time = time.time()
        if self.last_direction is None or self.last_direction == "UP" or current_time - self.last_movement_time > 0.5:
            if not self.enter_received:
                self.input_event = "UP"
                self.input_counter += 1
            self.last_direction = "UP"
            self.last_movement_time = current_time
            if time.time() - self.last_movement_time < 0.3:
                if self.enter_received:
                    self.enter_received = False
                    return  # if enter command is received during this time, execute the enter command and not the up

    def _dec_callback(self, v):
        current_time = time.time()
        if self.last_direction is None or self.last_direction == "DOWN" or current_time - self.last_movement_time > 0.5:
            if not self.enter_received:
                self.input_event = "DOWN"
                self.input_counter += 1
            self.last_direction = "DOWN"
            self.last_movement_time = current_time
            if time.time() - self.last_movement_time < 0.3:
                if self.enter_received:
                    self.enter_received = False
                    return  # if enter command is received during this time, execute the enter command and not the down

    """
	 ====================== Input & Menu Code ========================
	"""

    def _event_detect(self):
        """
        Called to detect input events from encoder
        """
        command = self.input_event  # Save to variable to prevent spurious changes
        if command:
            self.display_timeout = None  # If something is being displayed i.e. text, network, splash then override this

            if command != "ENTER" and self.input_counter == 0:
                return
            else:
                if command not in ["UP", "DOWN", "ENTER"]:
                    return

                if self._reset_data_on_event:
                    self.in_data = None
                    self.status_data = None
                self.display_command = None
                self.display_data = None
                self.input_event = None
                self.menu_active = True
                self.menu_time = time.time()
                if self._reset_data_on_event:
                    self.monitor_display = False
                self._menu_display(command)
                self.input_counter = 0


class SimpleEncoderInputMixin:
    """Group B: trivial rotary encoder input, no debounce state at all.

    Used by st7789_240x320e and st7789v_240x320e.
    """

    def _init_input(self):
        self.input_enabled = True
        # Init constants and variables
        clk_pin = self.dev_pins["input"]["up_clk"]  # Clock - GPIO16
        dt_pin = self.dev_pins["input"]["down_dt"]  # DT - GPIO20
        sw_pin = self.dev_pins["input"]["enter_sw"]  # Switch - GPIO21
        self.input_event = None
        self.input_counter = 0

        # Init Menu Structures
        self._init_menu()

        # Init Device
        self.encoder = pyky040.Encoder(CLK=clk_pin, DT=dt_pin, SW=sw_pin)
        self.encoder.setup(
            scale_min=0,
            scale_max=100,
            step=1,
            inc_callback=self._inc_callback,
            dec_callback=self._dec_callback,
            sw_callback=self._click_callback,
            polling_interval=200,
        )

        # Setup & Start Input Thread
        encoder_thread = threading.Thread(target=self.encoder.watch)
        encoder_thread.start()

    """
	============== Input Callbacks ============= 
	"""

    def _click_callback(self):
        self.input_event = "ENTER"

    def _inc_callback(self, v):
        self.input_event = "UP"
        self.input_counter += 1

    def _dec_callback(self, v):
        self.input_event = "DOWN"
        self.input_counter += 1

    """
	 ====================== Input & Menu Code ========================
	"""

    def _event_detect(self):
        """
        Called to detect input events from encoder
        """
        command = self.input_event  # Save to variable to prevent spurious changes
        if command:
            self.display_timeout = None  # If something is being displayed i.e. text, network, splash then override this

            if command != "ENTER" and self.input_counter == 0:
                return
            else:
                if command not in ["UP", "DOWN", "ENTER"]:
                    return

                self.display_command = None
                self.display_data = None
                self.input_event = None
                self.menu_active = True
                self.menu_time = time.time()
                self._menu_display(command)
                self.input_counter = 0
