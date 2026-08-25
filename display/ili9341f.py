"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description: This library supports using ILI9341 TFT Flex Display
 on the Raspberry Pi.

*****************************************
"""

"""
 Imported Libraries
"""
# import multiprocessing
import threading
import time

from gpiozero import Button

# from pygame import image as PyImage
from luma.core.interface.serial import spi
from luma.lcd.device import ili9341
from PIL import ImageFilter
from pyky040 import pyky040

from display._base_flex import DisplayBase

"""
Display class definition
"""


class Display(DisplayBase):
    def __init__(
        self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config=None, *, event_log=None, control_log=None
    ):
        config = {} if config is None else config
        # Set display profile based on rotation
        self.rotation = config.get("rotation", 0)
        if self.rotation in [0, 2]:
            self.display_profile = "profile_1"
        else:
            self.display_profile = "profile_2"
        self.config = config
        super().__init__(dev_pins, buttonslevel, rotation, units, config, event_log=event_log, control_log=control_log)
        self.controlLogger.debug("Display Initialized.")

    def _init_display_device(self):
        # Init Device
        dc_pin = self.dev_pins["display"]["dc"]
        led_pin = self.dev_pins["display"]["led"]
        rst_pin = self.dev_pins["display"]["rst"]
        spi_device = self.config.get("spi_device", 0)

        if self.rotation in [0, 2]:
            translated_width = self.WIDTH
            translated_height = self.HEIGHT
        else:
            translated_width = self.HEIGHT
            translated_height = self.WIDTH

        # self.controlLogger.debug(f'Display Rotation: {self.rotation}')
        # self.controlLogger.debug(f'Display Width: {translated_width}')
        # self.controlLogger.debug(f'Display Height: {translated_height}')

        self.serial = spi(
            port=0,
            device=spi_device,
            gpio_DC=dc_pin,
            gpio_RST=rst_pin,
            bus_speed_hz=32000000,
            reset_hold_time=0.2,
            reset_release_time=0.2,
        )
        self.device = ili9341(
            self.serial,
            active_low=False,
            width=translated_width,
            height=translated_height,
            gpio_LIGHT=led_pin,
            rotate=self.rotation,
        )

        # Setup & Start Display Loop Worker
        display_worker = threading.Thread(target=self._display_loop)
        display_worker.start()

    def _init_input(self):
        self.input_enabled = True
        self.input_event = None
        self.touch_pos = (0, 0)
        self.DEBOUNCE = 100  # ms

        if "touch" in self.config["input_types_supported"]:
            # TODO: Implement Touch Input
            self.controlLogger.debug("Touch Initialized.")

        if "button" in self.config["input_types_supported"]:
            # Init GPIO for button input, setup callbacks: Uncomment to utilize GPIO input
            self.up = self.dev_pins["input"]["up_clk"]  # UP - GPIO16
            self.down = self.dev_pins["input"]["down_dt"]  # DOWN - GPIO20
            self.enter = self.dev_pins["input"]["enter_sw"]  # ENTER - GPIO21
            self.debounce_ms = 500  # number of milliseconds to debounce input
            self.input_counter = 0

            # ==== Buttons Setup =====
            self.pull_up = self.buttonslevel == "HIGH"

            self.up_button = Button(pin=self.up, pull_up=self.pull_up, hold_time=0.25, hold_repeat=True)
            self.down_button = Button(pin=self.down, pull_up=self.pull_up, hold_time=0.25, hold_repeat=True)
            self.enter_button = Button(pin=self.enter, pull_up=self.pull_up)

            self.up_button.when_pressed = self._up_callback
            self.down_button.when_pressed = self._down_callback
            self.enter_button.when_pressed = self._enter_callback
            self.up_button.when_held = self._up_callback
            self.down_button.when_held = self._down_callback
            self.controlLogger.debug("Buttons Initialized.")

        if "encoder" in self.config["input_types_supported"]:
            # Init constants and variables
            clk_pin = self.dev_pins["input"]["up_clk"]  # Clock - GPIO16
            dt_pin = self.dev_pins["input"]["down_dt"]  # DT - GPIO20
            sw_pin = self.dev_pins["input"]["enter_sw"]  # Switch - GPIO21
            self.input_event = None
            self.input_counter = 0
            self.last_direction = None
            self.last_movement_time = 0
            self.enter_received = False

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
            self.controlLogger.debug("Encoder Initialized.")

        if "none" in self.config["input_types_supported"]:
            self.input_enabled = False
            self.controlLogger.debug("Input Disabled.")

    def _display_loop(self):
        """
        Main display loop worker
        """
        self.display_loop_active = True

        """ Display the Splash Screen on Startup """
        self._display_splash()
        time.sleep(self.SPLASH_DELAY * 0.001)
        self._display_clear()

        self.command = None
        self.display_active = None
        self.display_timeout = None
        self.display_init = True
        self.display_updated = False

        self.dash_object_list = []

        refresh_data = 0

        """ Display Loop """
        while self.display_loop_active:
            """ Fetch display data every 200ms """
            now = time.time()
            if now - refresh_data > 0.2:
                self._fetch_data()
                refresh_data = now

            """ Normal display loop"""
            if self.input_enabled:
                self._event_detect()

            self._display_loop_render_step()

            time.sleep(1 / self.FRAMERATE)

        # self.controlLogger.debug('Display Loop Ended.')

    """
	============== Graphics / Display / Draw Methods ============= 
	"""

    def _wake_display(self):
        # self.controlLogger.debug('_wake_display() called.')
        self.device.backlight(True)
        self.device.show()

    def _sleep_display(self):
        self.device.backlight(False)
        self.device.hide()

    def _display_clear(self):
        # self.controlLogger.debug('_display_clear() called.')
        self.device.clear()
        self.device.backlight(False)
        self.device.hide()

    def _display_canvas(self):
        # Display Image
        self.device.backlight(True)
        self.device.show()
        self.device.display(self.display_canvas.convert(mode="RGB"))

    def _display_background(self):
        self.display_canvas.paste(self.background, (0, 0))

    def _capture_background(self):
        self.menu_background = self.display_canvas.filter(ImageFilter.GaussianBlur(radius=5))

    def _display_menu_background(self):
        self.display_canvas.paste(self.menu_background, (0, 0))

    def _init_dash(self):
        self._init_framework()
        self._configure_dash()
        self._build_objects(None)
        self._build_dash_map()
        self._store_dash_objects()

    """
	 ====================== Input & Menu Code ========================
	"""

    def _debounce(self):
        time.sleep(self.DEBOUNCE * 0.001)

    """ Button Callbacks """

    def _enter_callback(self):
        self.input_event = "ENTER"
        # self.controlLogger.debug('Enter Button Pressed.')

    def _up_callback(self, held=False):
        self.input_event = "UP"
        # self.controlLogger.debug('Up Button Pressed.')

    def _down_callback(self, held=False):
        self.input_event = "DOWN"
        # self.controlLogger.debug('Down Button Pressed.')

    """ Encoder Callbacks """

    def _click_callback(self):
        self.input_event = "ENTER"
        self.enter_received = True

    def _inc_callback(self, v):
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

    def _dec_callback(self, v):
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

    def _process_touch(self):
        """
        No touch-specific transform needed (unlike _base_dsi.py, which
        rotates self.touch_pos for a real touchscreen); touch input is not
        yet implemented for this driver (see _init_input), so this just
        delegates straight to the shared collision-detection loop.
        """
        if self.display_active:
            """
			Loop through current displayed objects and check for touch collisions
			"""
            self._process_touch_areas()

        else:
            """
			Wake the display & go to home/dash
			"""
            self._wake_and_activate_display()
