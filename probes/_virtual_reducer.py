"""
*****************************************
PiFire Probes Virtual Reducer Base Module
*****************************************

Description:
  Shared base class for the virtual aggregator probe modules (virtual_average,
  virtual_median, virtual_highest, virtual_lowest). Each of those modules is a
  virtual probe device that combines any number of other probe inputs using a
  single reducer function (mean/median/max/min); everything else -- collecting
  the source temperatures, writing the result into the correct output group,
  zeroing Tr, and reporting empty status -- is identical across all four, so it
  lives here once. Subclasses set the `_reduce` class attribute to the reducer
  they want applied.
"""

"""
*****************************************
 Imported Libraries
*****************************************
"""

from probes.base import ProbeInterface

"""
*****************************************
 Class Definitions
*****************************************
"""


class ReducerProbe(ProbeInterface):
    applies_kalman = False  # Aggregates already-filtered probes; don't double-filter.

    # Reducer applied to the collected temp_list (e.g. statistics.mean, max, min).
    # Unset here on purpose: a subclass must set it, or calling read_all_ports
    # raises AttributeError/TypeError immediately rather than silently misbehaving.
    _reduce = None

    def __init__(self, probe_info, device_info, units):
        super().__init__(probe_info, device_info, units)

    def read_all_ports(self, output_data):
        """Find the probes to reduce"""
        for port in self.port_map:
            temp_list = []
            for probe in self.device_info["config"]["probes_list"]:
                if probe in output_data["primary"]:
                    temp_list.append(output_data["primary"][probe])
                elif probe in output_data["food"]:
                    temp_list.append(output_data["food"][probe])
                elif probe in output_data["aux"]:
                    temp_list.append(output_data["aux"][probe])

            """ Get reduced temperature and store it in the output data structure"""
            if port == self.primary_port:
                self.output_data["primary"][self.port_map[port]] = self._reduce(temp_list)
            elif port in self.food_ports:
                self.output_data["food"][self.port_map[port]] = self._reduce(temp_list)
            elif port in self.aux_ports:
                self.output_data["aux"][self.port_map[port]] = self._reduce(temp_list)

            """ Set Tr value to 0 since we are averaging temperature outputs """
            self.output_data["tr"][self.port_map[port]] = 0

        return self.output_data

    def get_device_info(self):
        self.device_info["status"] = {}
        return self.device_info
