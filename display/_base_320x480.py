"""Compat shim: 320x480 fixed display base. Real implementation in _base_fixed.

Kept intentionally: this is the resolution-profile layer, not a temporary
shim slated for deletion. It carries _NOMINAL_WIDTH/_NOMINAL_HEIGHT/_SQUARE
and min_transition_delay, which _base_fixed's shared loop reads per instance.
Deleting it would force all 16 drivers to re-declare those attributes, and a
driver that forgot one would silently inherit a default rather than fail --
st7789e, for instance, would lose its slow-panel post-transition settle delay.
See _base_240x240.py/_base_240x320.py for the other two resolution profiles."""

from display._base_fixed import _DisplayBase as _Base


class DisplayBase(_Base):
    _NOMINAL_WIDTH = 480
    _NOMINAL_HEIGHT = 320
    _SQUARE = False
    min_transition_delay = 0.1  # fast panel: no post-transition settle
