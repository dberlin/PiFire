"""
*****************************************
PiFire Flexible Display Interface Library
*****************************************

 Description: Small helpers shared verbatim across the pygame/flex display
 drivers (_base_dsi.py, ili9341f.py). Kept out of _base_flex.py because
 DisplayBase itself is a single large class and these helpers are not part
 of its inheritance surface.

*****************************************
"""


class DummyBacklight:
    """
    Dummy backlight class for prototyping
    """

    def __init__(self):
        self.brightness = 100
        self.power = True
        self.fade_duration = 1
