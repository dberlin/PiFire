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
import time
from dataclasses import replace

from probes.thermocouple_health import (
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
    ThermocoupleHealthTransition,
)
from probes.thermocouple_inference import (
    ThermocoupleExcitationContext,
    ThermocoupleInferenceEngine,
    ThermocoupleInferencePolicy,
    ThermocoupleJunctionSample,
    ThermocoupleWitnessSample,
    fuse_thermocouple_health,
)


class ProbesMain:
    def __init__(
        self,
        probe_map,
        units,
        disable=False,
        inference_policy=ThermocoupleInferencePolicy.OBSERVE,
    ):
        policy = ThermocoupleInferencePolicy(inference_policy)
        self.errors = []
        self.logger = logging.getLogger("control")
        self.units = units
        self.disable = disable
        self.probe_devices = probe_map["probe_devices"]
        self.probe_info = probe_map["probe_info"]
        self.device_info_list = []
        self.thermocouple_inference_policy = policy
        self._thermocouple_inference_engines: dict[tuple[str, str], ThermocoupleInferenceEngine] = {}
        self._thermocouple_health: dict[str, ThermocoupleHealthReport] = {}
        self._thermocouple_health_by_device: dict[str, dict[str, ThermocoupleHealthReport]] = {}
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
        self._thermocouple_inference_engines.clear()
        self._thermocouple_health.clear()
        self._thermocouple_health_by_device.clear()
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

    def _reproject_cached_health_without_inference(self) -> None:
        probe_is_primary = {
            (str(probe["device"]), str(probe["label"])): probe["type"] == "Primary" for probe in self.probe_info
        }
        health = {}
        health_by_device = {}
        for device in self.probe_device_list:
            device_name = str(getattr(device, "device_info", {}).get("device", ""))
            hardware = dict(device.get_thermocouple_health())
            reports = dict(hardware)
            for label, previous in self._thermocouple_health_by_device.get(device_name, {}).items():
                reports[label] = fuse_thermocouple_health(
                    hardware.get(label),
                    previous,
                    ThermocoupleInferencePolicy.OFF,
                    probe_is_primary.get((device_name, label), False),
                )
            health.update(reports)
            health_by_device[device_name] = reports
        self._thermocouple_health = health
        self._thermocouple_health_by_device = health_by_device
        self._thermocouple_health_transitions.clear()

    def set_thermocouple_inference_policy(self, policy) -> None:
        next_policy = ThermocoupleInferencePolicy(policy)
        if next_policy is ThermocoupleInferencePolicy.OFF:
            self._thermocouple_inference_engines.clear()
            self._reproject_cached_health_without_inference()
        self.thermocouple_inference_policy = next_policy

    def read_probes(self, *, excitation=None, now=None):
        """Read probes, fuse hardware and inferred health, then invalidate output."""
        observed_at = time.monotonic() if now is None else now
        base_excitation = excitation or ThermocoupleExcitationContext(
            active_cook=False,
            primary_setpoint_c=0.0,
            delivered_heat_on_s=0.0,
        )
        output_data = {"primary": {}, "food": {}, "aux": {}, "tr": {}}
        probe_by_identity = {(str(probe["device"]), str(probe["port"])): probe for probe in self.probe_info}
        current_samples = {}
        hardware_health = {}
        hardware_by_device = {}

        # Phase A owns all hardware reads. No inferred state changes until every
        # current sample and hardware report has been captured.
        for device in self.probe_device_list:
            device_data = device.read_all_ports(output_data)
            device.apply_filters(device_data)
            for group in device_data:
                for probe in device_data[group]:
                    output_data[group][probe] = device_data[group][probe]

            device_info = getattr(device, "device_info", {})
            device_name = str(device_info.get("device", ""))
            device_health = device.get_thermocouple_health()
            hardware_health.update(device_health)
            hardware_by_device[device_name] = dict(device_health)
            get_samples = getattr(device, "get_thermocouple_samples", None)
            samples = get_samples() if get_samples is not None else {}
            for port, sample in samples.items():
                if not isinstance(sample, ThermocoupleJunctionSample):
                    continue
                identity = (device_name, str(port))
                probe = probe_by_identity.get(identity)
                if probe is None:
                    continue
                current_samples[identity] = (
                    probe,
                    sample,
                    device_health.get(probe["label"]),
                )

        policy = self.thermocouple_inference_policy
        if policy is not ThermocoupleInferencePolicy.OFF:
            for identity in current_samples:
                if identity not in self._thermocouple_inference_engines:
                    self._thermocouple_inference_engines[identity] = ThermocoupleInferenceEngine()

        # Witness eligibility is frozen before observation. Consequently a
        # report produced for one thermocouple in this pass cannot affect any
        # other thermocouple's witnesses in the same pass.
        prepass_health = {}
        if policy is not ThermocoupleInferencePolicy.OFF:
            for identity, (probe, _sample, hardware) in current_samples.items():
                prepass_health[identity] = fuse_thermocouple_health(
                    hardware,
                    self._thermocouple_inference_engines[identity].current_report(),
                    policy,
                    probe["type"] == "Primary",
                )

        health = dict(hardware_health)
        fused_by_device = {device_name: dict(reports) for device_name, reports in hardware_by_device.items()}
        if policy is not ThermocoupleInferencePolicy.OFF:
            for identity in sorted(current_samples):
                probe, sample, hardware = current_samples[identity]
                witnesses = tuple(
                    ThermocoupleWitnessSample(
                        source=candidate_identity,
                        temperature_c=candidate_sample.hot_c,
                    )
                    for candidate_identity, (
                        candidate_probe,
                        candidate_sample,
                        _candidate_hardware,
                    ) in sorted(current_samples.items())
                    if candidate_identity != identity
                    and candidate_probe["type"] in ("Primary", "Aux")
                    and prepass_health[candidate_identity].state is ThermocoupleHealthState.HEALTHY
                    and prepass_health[candidate_identity].temperature_valid
                )
                inferred = self._thermocouple_inference_engines[identity].observe(
                    sample,
                    replace(base_excitation, witnesses=witnesses),
                    probe["type"] == "Primary",
                    observed_at,
                )
                fused = fuse_thermocouple_health(
                    hardware,
                    inferred,
                    policy,
                    probe["type"] == "Primary",
                )
                label = probe["label"]
                health[label] = fused
                fused_by_device.setdefault(identity[0], {})[label] = fused

        normalized_health = {}
        normalized_by_device = {}
        for device_name, reports in fused_by_device.items():
            normalized_reports = {}
            for label, report in reports.items():
                detail = dict(report.detail)
                detail["policy"] = policy.value
                normalized = replace(
                    report,
                    observed_at=observed_at,
                    detail=detail,
                )
                normalized_reports[label] = normalized
                normalized_health[label] = normalized
            normalized_by_device[device_name] = normalized_reports
        health = normalized_health
        fused_by_device = normalized_by_device

        for probe in self.probe_info:
            report = health.get(probe["label"])
            if report is None or report.temperature_valid:
                continue
            group = {
                "Primary": "primary",
                "Food": "food",
                "Aux": "aux",
            }.get(probe["type"])
            if group is not None and probe["label"] in output_data[group]:
                output_data[group][probe["label"]] = None

        for label, current in health.items():
            previous = self._thermocouple_health.get(label, ThermocoupleHealthReport.unmonitored(current.observed_at))
            if (previous.state, previous.faults) != (current.state, current.faults):
                self._thermocouple_health_transitions.append(ThermocoupleHealthTransition(label, previous, current))
        self._thermocouple_health = health
        self._thermocouple_health_by_device = fused_by_device
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
        """Return driver information with the fused health used by safety."""
        self.device_info_list = []
        for device in self.probe_device_list:
            driver_info = device.get_device_info()
            info = dict(driver_info)
            status = dict(info.get("status", {}))
            device_name = str(info.get("device", ""))
            fused = self._thermocouple_health_by_device.get(device_name, {})
            if fused:
                status["thermocouple_health"] = {label: report.as_dict() for label, report in fused.items()}
            else:
                status.pop("thermocouple_health", None)
            info["status"] = status
            self.device_info_list.append(info)
        return self.device_info_list
