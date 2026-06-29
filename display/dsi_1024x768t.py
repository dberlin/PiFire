"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description: 1024x768 pygame display.

 The 800x480 DSI/pygame display class is fully resolution-agnostic — it reads
 all dimensions and layout from its JSON layout file (display_data_filename).
 This module reuses that class unchanged; the 1024x768 behavior comes entirely
 from display/dsi_1024x768t.json, which the wizard pairs with this module.

*****************************************
"""

from display.dsi_800x480t import Display
