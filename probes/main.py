#!/usr/bin/env python3

"""
*****************************************
PiFire Probes Main Module
*****************************************

Description:
  This module is the high level module that reports temperatures from
  the device(s) hardware.

"""

"""
*****************************************
 Imported Libraries
*****************************************
"""
import importlib
import logging


class ProbesMain:
    def __init__(self, probe_map, units, disable=False):
        self.errors = []
        self.logger = logging.getLogger("control")
        self.units = units
        self.disable = disable
        self.probe_devices = probe_map["probe_devices"]
        self.probe_info = probe_map["probe_info"]
        self.device_info_list = []
        self._setup_probe_devices(self.probe_devices)

    def _setup_probe_devices(self, probe_devices):
        """Construct one ReadProbes instance per configured device.

        Returns the errors raised THIS call (also appended to self.errors,
        which accumulates across calls). The return value matters now that
        update_probe_map() is live: a rebuild triggered from the web tier has
        no other way to report that a module failed to import.
        """
        error_event = None
        errors = []
        self.probe_device_list = []
        for device in probe_devices:
            try:
                if not self.disable:
                    modulename = device.get("module_filename", device["module"])
                else:
                    modulename = "disabled"
                devicename = device["device"]
                newmodule = importlib.import_module(f"probes.{modulename}")
            except:
                newmodule = importlib.import_module("probes.disabled")
                device["module"] = "disabled"
                error_event = (
                    f"An error occurred loading the [{modulename}] probe module for [{devicename}]. "
                    f"PiFire will not display probe data for this device ({devicename}). "
                    f"This sometimes means that the hardware is not connected properly, or the module is not configured. "
                    f"Please run the configuration wizard again from the admin panel to fix this issue. "
                )
                self.errors.append(error_event)
                errors.append(error_event)
                self.logger.error(error_event)

            """
			Send the probe information and the device information to the device module 
			"""
            instance = newmodule.ReadProbes(self.probe_info, device, self.units)

            """
			Append the probe device to the devices list
			"""
            self.probe_device_list.append(instance)

        return errors

    def read_probes(self):
        """
        Loop through all probe devices and get all data
        """
        output_data = {"primary": {}, "food": {}, "aux": {}, "tr": {}}
        for device in self.probe_device_list:
            device_data = device.read_all_ports(output_data)
            # Apply the Kalman filter uniformly, regardless of which read_all_ports
            # variant the device module implements. Virtual/derived probes read the
            # already-filtered values above and opt out via applies_kalman = False.
            device.apply_filters(device_data)
            for group in device_data:
                for probe in device_data[group]:
                    output_data[group][probe] = device_data[group][probe]

        return output_data

    def update_probe_map(self, probe_map):
        """Rebuild every probe device from a new map, in place.

        Called by the control loop when control["probe_map_update"] is set
        (controller/runtime/controller.py) -- i.e. after POST /api/probe_map
        wrote a new settings["probe_settings"]["probe_map"].

        NOT equivalent to update_probe_profiles(): that only refills per-port
        profiles on already-constructed devices (probes/base.py:393-401) and
        cannot see an added, removed or renamed probe.

        KNOWN LIMITATION: the previous device objects are dropped, not closed.
        A Bluetooth/USB-HID device holding an OS handle releases it at GC, not
        here. Callers gate this on control mode == Stop for that reason.
        """
        self.probe_devices = probe_map["probe_devices"]
        self.probe_info = probe_map["probe_info"]
        return self._setup_probe_devices(self.probe_devices)

    def update_probe_profiles(self, probe_info):
        for device in self.probe_device_list:
            device.set_profiles(probe_info)

    def update_units(self, units):
        """
        Update the units of all probe devices in the probe device list.

        :param units: The units to update the probe devices to.
        :type units: Any

        :return: None
        """
        for device in self.probe_device_list:
            device.update_units(units)

    def get_errors(self):
        return self.errors

    def get_device_info(self):
        """for each device in the self.probe_device_list, get the device info"""
        self.device_info_list = []
        for device in self.probe_device_list:
            self.device_info_list.append(device.get_device_info())
        return self.device_info_list
