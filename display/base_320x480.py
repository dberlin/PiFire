"""Compat shim: 320x480 fixed display base. Real implementation in base_fixed.
Phase C repoints drivers straight at base_fixed and deletes this module."""

from display.base_fixed import DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 480
    _NOMINAL_HEIGHT = 320
    _SQUARE = False
    min_transition_delay = 0.1  # fast panel: no post-transition settle
