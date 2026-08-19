"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description: This library supports using pygame
 with a DSI attached touch display on the Raspberry Pi
 like the official Raspberry Pi 7 inch DSI attached display.

 This module is the resolution-agnostic DSI/pygame flex engine — it reads
 all dimensions and layout from its JSON layout file (display_data_filename),
 so a single Display class serves every DSI resolution. Per-resolution
 modules (dsi_800x480t, dsi_320x240t) are thin stubs that re-export this
 module's Display and pair it with their own JSON layout file. The larger
 resolutions this engine used to drive are served by the Qt Quick display
 instead (display/qtquick_dsi_*).

 This version supports mouse for development.

*****************************************
"""

"""
 Imported Libraries
"""
import time
import multiprocessing
import pygame
from pygame import image as PyImage

from PIL import ImageFilter
from display._base_flex import DisplayBase
from display._flex_helpers import DummyBacklight  # noqa: F401  # re-exported for isinstance checks
from pathlib import Path

"""
Display class definition
"""


class Display(DisplayBase):
    def __init__(
        self, dev_pins, buttonslevel="HIGH", rotation=0, units="F", config={}, *, event_log=None, control_log=None
    ):
        # Set display profile based on rotation
        self.rotation = config.get("rotation", 0)
        if self.rotation in [0, 180]:
            self.display_profile = "profile_1"
        else:
            self.display_profile = "profile_2"
        super().__init__(dev_pins, buttonslevel, rotation, units, config, event_log=event_log, control_log=control_log)

    def _init_display_device(self):
        """Init backlight"""
        backlight_hardware = False
        # Use pathlib to check if /sys/class/backlight/rpi_backlight/ exists
        if Path("/sys/class/backlight/").exists():
            backlight_hardware = True

        if self.real_hardware and backlight_hardware:
            # Use the rpi-backlight module if running on the RasPi
            try:
                from rpi_backlight import Backlight

                self.backlight = Backlight()
            except:
                self.eventLogger.error("Falling back to dummy backlight class. Backlight control will not work.")
                self.real_hardware = False
                self.backlight = DummyBacklight()
        else:
            # Else use a fake module class for backlight
            self.backlight = DummyBacklight()

        self._wake_display()

        # Setup & Start Display Loop Worker
        display_worker = multiprocessing.Process(target=self._display_loop)
        display_worker.start()

    def _init_input(self):
        self.input_enabled = True
        self.input_event = None
        self.touch_pos = (0, 0)
        self.DEBOUNCE = 100  # ms

    def _display_loop(self):
        """
        Main display loop worker
        """
        # Init Device
        pygame.init()
        # Set the pygame window name (for debug)
        pygame.display.set_caption("PiFire Device Display")
        # Create Display Surface

        if self.real_hardware:
            flags = pygame.FULLSCREEN | pygame.DOUBLEBUF
            self.display_surface = pygame.display.set_mode((0, 0), flags)
            pygame.mouse.set_visible(False)  # make mouse pointer invisible
        else:
            self.display_surface = pygame.display.set_mode(size=(self.WIDTH, self.HEIGHT), flags=pygame.SHOWN)

        self.clock = pygame.time.Clock()

        self.display_loop_active = True

        """ Display the Splash Screen on Startup """
        self._display_splash()
        pygame.time.delay(self.SPLASH_DELAY)  # Hold splash screen for designated time
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

            """ Poll for PyGame Events """
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.display_loop_active = False
                    break
                # Check for mouse inputs
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    mouse_x, mouse_y = pygame.mouse.get_pos()
                    # print(f'{mouse_x}, {mouse_y}')
                    self.touch_pos = (mouse_x, mouse_y)
                    self.touch_held = True
                elif event.type == pygame.MOUSEBUTTONUP:
                    self.touch_held = False

                # Check for touch inputs
                elif event.type == pygame.FINGERDOWN:
                    touch_x = int(event.x * self.display_surface.get_width())
                    touch_y = int(event.y * self.display_surface.get_height())
                    # print(f'{touch_x}, {touch_y}')
                    self.touch_pos = (touch_x, touch_y)
                    self.touch_held = True
                elif event.type == pygame.FINGERUP:
                    self.touch_held = False

            """ Check for pressed keys """
            keys = pygame.key.get_pressed()
            if keys[pygame.K_UP]:
                self.input_event = "UP"
            elif keys[pygame.K_DOWN]:
                self.input_event = "DOWN"
            elif keys[pygame.K_RETURN]:
                self.input_event = "ENTER"
            elif keys[pygame.K_x] or keys[pygame.K_q]:
                self.display_loop_active = False
                break
            elif self.touch_pos != (0, 0):
                self.input_event = "TOUCH"

            """ Normal display loop"""
            self._event_detect()

            self._display_loop_render_step()

            self.clock.tick(self.FRAMERATE)

        pygame.quit()

    """
	============== Graphics / Display / Draw Methods ============= 
	"""

    def _wake_display(self):
        self.backlight.power = True
        self.backlight.brightness = 100
        self.backlight.fade_duration = 1

    def _sleep_display(self):
        self.backlight.fade_duration = 1
        self.backlight.brightness = 0
        pygame.time.delay(1000)  # give time for the screen to fade
        self.backlight.power = False

    def _display_clear(self):
        self._sleep_display()
        self.display_surface.fill((0, 0, 0, 255))
        pygame.display.update()
        self.display_canvas.paste((0, 0, 0, 255), (0, 0, self.WIDTH, self.HEIGHT))
        self.eventLogger.info("Screen Cleared.")

    def _display_canvas(self):
        self._canvas_to_surface()
        pygame.display.update()

    def _canvas_to_surface(self):
        if self.ROTATION in [90, 180, 270]:
            # Rotate the canvas for 90, 180, or 270 degrees
            self.transform_canvas = self.display_canvas.rotate(self.ROTATION, expand=True)
        else:
            # Use the canvas as is for landscape mode
            self.transform_canvas = self.display_canvas.copy()
        # Convert temporary canvas to PyGame surface
        strFormat = self.transform_canvas.mode
        size = self.transform_canvas.size
        raw_str = self.transform_canvas.tobytes("raw", strFormat)
        self.display_surface.blit(PyImage.frombytes(raw_str, size, strFormat), (0, 0))

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
        pygame.time.delay(self.DEBOUNCE)

    def _process_touch(self):
        """
        Applies the DSI-specific rotation correction to self.touch_pos (real
        touchscreen coordinates need it; ili9341f.py does not touch-transform
        since it has no touch input implemented) then delegates to the shared
        collision-detection loop on the base class.
        """
        if self.display_active:
            """
			Loop through current displayed objects and check for touch collisions
			"""
            if self.ROTATION == 90:
                self.touch_pos = (self.WIDTH - self.touch_pos[1], self.touch_pos[0])
            elif self.ROTATION == 180:
                self.touch_pos = (self.WIDTH - self.touch_pos[0], self.HEIGHT - self.touch_pos[1])
            elif self.ROTATION == 270:
                self.touch_pos = (self.touch_pos[1], self.HEIGHT - self.touch_pos[0])

            self._process_touch_areas()

        else:
            """
			Wake the display & go to home/dash
			"""
            self._wake_and_activate_display()
