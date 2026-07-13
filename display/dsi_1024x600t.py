"""
*****************************************
PiFire Display Interface Library
*****************************************

 Description: 1024x600 pygame display.

 The 800x480 DSI/pygame display class is fully resolution-agnostic — it reads
 all dimensions and layout from its JSON layout file (display_data_filename).
 This module reuses that class unchanged; the 1024x600 behavior comes entirely
 from display/dsi_1024x600t.json, which the wizard pairs with this module.

*****************************************
"""

from display.dsi_800x480t import Display
