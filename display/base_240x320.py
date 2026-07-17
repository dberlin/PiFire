"""Compat shim: 240x320 fixed display base. Real implementation in base_fixed."""

from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 320
    _NOMINAL_HEIGHT = 240
    _SQUARE = False
