"""Compat shim: 240x320 fixed display base. Real implementation in base_fixed.
Kept intentionally as the resolution-profile layer; see base_320x480.py."""

from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 320
    _NOMINAL_HEIGHT = 240
    _SQUARE = False
    min_transition_delay = 0.1  # fast panel: no post-transition settle
