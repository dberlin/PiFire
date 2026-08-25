"""
*****************************************
PiFire Probes Virtual Probe Averaging Module
*****************************************

Description:
  This module is a virtual probe device that will average any number of other probe inputs.  Probe labels must be defined in config data.

        Ex Device Definition:

        device = {
                        'device' : 'your_device_name',	# Unique name for the device
                        'module' : 'virtual_average',	# Must be populated for this module to load properly
                        'ports' : ['VIRT0'], 			# A port must be defined, with the labels of the probes to utilize in config data
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

from statistics import mean

from probes._virtual_reducer import ReducerProbe

"""
*****************************************
 Class Definitions
*****************************************
"""


class ReadProbes(ReducerProbe):
    _reduce = staticmethod(mean)
