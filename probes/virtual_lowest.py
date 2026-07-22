#!/usr/bin/env python3

"""
*****************************************
PiFire Probes Virtual Probe Lowest Module
*****************************************

Description:
  This module is a virtual probe device that will take the lowest of any number of other probe inputs.  Probe labels must be defined in config data.

        Ex Device Definition:

        device = {
                        'device' : 'your_device_name',	# Unique name for the device
                        'module' : 'virtual_lowest',	# Must be populated for this module to load properly
                        'ports' : ['VIRT0'], 			# A single port must be defined, with the labels of the probes to utilize in config data
                        'config' : {
                          "probes_list" : ["Grill1", "Grill2"]	# List of probe labels to utilize
                        }
                }
"""

"""
*****************************************
 Imported Libraries
*****************************************
"""

from probes._virtual_reducer import ReducerProbe

"""
*****************************************
 Class Definitions
*****************************************
"""


class ReadProbes(ReducerProbe):
    _reduce = staticmethod(min)
