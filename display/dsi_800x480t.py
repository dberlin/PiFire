"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description: 800x480 pygame display.

 The DSI/pygame display class is fully resolution-agnostic — it reads
 all dimensions and layout from its JSON layout file (display_data_filename).
 This module reuses that class unchanged; the 800x480 behavior comes entirely
 from display/dsi_800x480t.json, which the wizard pairs with this module.

*****************************************
"""

from display.dsi_base import Display  # noqa: F401  # public re-export
