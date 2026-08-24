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

from probes.thermocouple_health import (
    ThermocoupleHealthReport,
    ThermocoupleHealthTransition,
)


class ProbesMain:
    def __init__(self, probe_map, units, disable=False):
        self.errors = []
        self.logger = logging.getLogger("control")
        self.units = units
        self.disable = disable
        self.probe_devices = probe_map["probe_devices"]
        self.probe_info = probe_map["probe_info"]
        self.device_info_list = []
        self._thermocouple_health: dict[str, ThermocoupleHealthReport] = {}
        self._thermocouple_health_transitions: list[ThermocoupleHealthTransition] = []
        self._setup_probe_devices(self.probe_devices)

    def _close_probe_devices(self):
        """Release the resources of the devices built by the previous call.

        Called before the device list is rebuilt so a device that owns an OS
        handle (a bluepy Peripheral and its helper process, a spidev fd, an
        smbus2 fd, a polling thread) gives it up deterministically, rather than
        at garbage collection -- or never, when a live background thread still
        references the device object. The replacement instance re-opens the same
        hardware immediately afterwards, so this ordering is the whole point.

        Every device is closed even if one of them raises: the rebuild is the
        recovery path, so a failure here is logged and stepped over rather than
        allowed to abort it. ProbeInterface.close() is a no-op by default, so
        modules with nothing to release need no special-casing here.
        """
        for device in getattr(self, "probe_device_list", []):
            try:
                device.close()
            except Exception as exception:
                self.logger.error(
                    f"An error occurred closing probe device "
                    f"[{getattr(device, 'device_info', {}).get('device', 'unknown')}]: {exception}"
                )

    def _setup_probe_devices(self, probe_devices):
        """Construct one ReadProbes instance per configured device.

        Closes the previously constructed devices first (see
        _close_probe_devices), then rebinds self.probe_device_list.

        Returns the errors raised THIS call (also appended to self.errors,
        which accumulates across calls). The return value matters now that
        update_probe_map() is live: a rebuild triggered from the web tier has
        no other way to report that a module failed to import. Close failures
        are NOT reported that way: they are logged, because they say something
        about the configuration that is going away rather than about the one
        being applied.
        """
        error_event = None
        errors = []
        self._close_probe_devices()
        self._thermocouple_health.clear()
        self._thermocouple_health_transitions.clear()
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

        health = {}
        for device in self.probe_device_list:
            health.update(device.get_thermocouple_health())
        for label, current in health.items():
            previous = self._thermocouple_health.get(
                label, ThermocoupleHealthReport.unmonitored(current.observed_at)
            )
            if (previous.state, previous.faults) != (current.state, current.faults):
                self._thermocouple_health_transitions.append(
                    ThermocoupleHealthTransition(label, previous, current)
                )
        self._thermocouple_health = health

        return output_data

    def update_probe_map(self, probe_map):
        """Rebuild every probe device from a new map, in place.

        Called by the control loop when control["probe_map_update"] is set
        (controller/runtime/controller.py) -- i.e. after POST /api/probe_map
        wrote a new settings["probe_settings"]["probe_map"].

        NOT equivalent to update_probe_profiles(): that only refills per-port
        profiles on already-constructed devices (probes/base.py:393-401) and
        cannot see an added, removed or renamed probe.

        The previous devices are closed before the new ones are built (see
        _close_probe_devices), so a Bluetooth/USB-HID/SPI handle is released
        before its replacement re-opens the same hardware. Callers still gate
        this on control mode == Stop, for the independent reason that the
        rebuild leaves the probes unreadable for its duration.
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

    def get_thermocouple_health(self) -> dict[str, ThermocoupleHealthReport]:
        return dict(self._thermocouple_health)

    def consume_thermocouple_health_transitions(
        self,
    ) -> tuple[ThermocoupleHealthTransition, ...]:
        transitions = tuple(self._thermocouple_health_transitions)
        self._thermocouple_health_transitions.clear()
        return transitions

    def get_errors(self):
        return self.errors

    def get_device_info(self):
        """for each device in the self.probe_device_list, get the device info"""
        self.device_info_list = []
        for device in self.probe_device_list:
            self.device_info_list.append(device.get_device_info())
        return self.device_info_list
