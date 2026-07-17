"""Compat shim: 240x240 fixed display base. Real implementation in base_fixed."""

from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 240
    _NOMINAL_HEIGHT = 240
    _SQUARE = True
    min_transition_delay = 1.0  # st7789e is a slow SPI panel; hold the first
    #                             frame after a transition so it can fully draw.
