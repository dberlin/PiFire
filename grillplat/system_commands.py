#!/usr/bin/env python3

# *****************************************
# PiFire Generic-Host System / Platform Commands
# *****************************************
#
# Description: System/platform info commands (supported_commands, CPU temp via
#   psutil, wifi quality, bluetooth scan, os/network/hardware info) shared by
#   non-Raspberry-Pi platforms (x86_numato, ft232h_relay).  Raspberry-Pi
#   platforms keep their own vcgencmd-based variants.
#
#   Consuming classes must provide self.logger.
# *****************************************

from common.common import is_float
from common.system import get_os_info, get_wifi_quality


class SystemCommandsMixin:
    # MARK: System / Platform Commands
    def supported_commands(self, arglist):
        supported_commands = [
            "check_throttled",
            "check_wifi_quality",
            "check_cpu_temp",
            "supported_commands",
            "check_alive",
            "scan_bluetooth",
            "os_info",
            "network_info",
            "hardware_info",
        ]
        return {
            "result": "OK",
            "message": 'Supported commands listed in "data".',
            "data": {"supported_cmds": supported_commands},
        }

    def check_throttled(self, arglist):
        # Not applicable on generic-host hardware.
        return {
            "result": "OK",
            "message": "No under-voltage or throttling detected.",
            "data": {"cpu_under_voltage": False, "cpu_throttled": False},
        }

    def check_cpu_temp(self, arglist):
        import psutil

        temp = 0.0
        result = "OK"
        message = "Successfully obtained CPU temperature."
        try:
            sensors = psutil.sensors_temperatures()
            readings = []
            for label in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if sensors.get(label):
                    readings = sensors[label]
                    break
            if not readings:
                for entries in sensors.values():
                    if entries:
                        readings = entries
                        break
            if readings:
                temp = float(readings[0].current)
            else:
                message = "No CPU temperature sensors available."
        except Exception as exc:
            result = "ERROR"
            message = "Error obtaining CPU temperature: " + str(exc)
        if not is_float(str(temp)):
            temp = 0.0
        return {"result": result, "message": message, "data": {"cpu_temp": float(temp)}}

    def check_wifi_quality(self, arglist):
        return get_wifi_quality(logger=self.logger)

    def check_alive(self, arglist):
        return {"result": "OK", "message": "The control script is running.", "data": {}}

    def scan_bluetooth(self, arglist):
        import asyncio

        try:
            from bleak import BleakScanner
        except ImportError:
            return {
                "result": "ERROR",
                "message": "bleak is not installed. Run: pip install bleak",
                "data": {"bt_devices": []},
            }

        bt_devices = []
        result = "OK"
        message = "Bluetooth scan completed successfully."

        async def _scan():
            discovered = await BleakScanner.discover(timeout=5.0)
            for dev in discovered:
                name = dev.name or "Unknown"
                bt_devices.append({"name": name, "hw_id": dev.address.lower(), "info": ""})

        try:
            asyncio.run(_scan())
        except Exception as exc:
            result = "ERROR"
            message = "Bluetooth scan error: " + str(exc)
            self.logger.error("scan_bluetooth: Error during scan - " + str(exc))

        return {"result": result, "message": message, "data": {"bt_devices": bt_devices}}

    def os_info(self, arglist):
        return {"result": "OK", "message": "OS information retrieved successfully.", "data": get_os_info()}

    def network_info(self, arglist):
        import netifaces

        net_info = {}
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            ip_addr = addrs.get(netifaces.AF_INET, [{}])[0].get("addr", "N/A")
            mac_addr = addrs.get(netifaces.AF_LINK, [{}])[0].get("addr", "N/A")
            net_info[iface] = {"ip_address": ip_addr, "mac_address": mac_addr}
        return {"result": "OK", "message": "Network information retrieved successfully.", "data": net_info}

    def hardware_info(self, arglist):
        import psutil

        cpu_info = {
            "hardware": "Unknown",
            "model": "Unknown",
            "model_name": "Unknown",
            "cores": psutil.cpu_count(logical=True),
            "frequency": psutil.cpu_freq().current if psutil.cpu_freq() else "Unknown",
        }
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line.lower():
                        cpu_info["model_name"] = line.strip().split(":")[1].strip()
        except OSError:
            pass
        mem_info = psutil.virtual_memory()
        return {
            "result": "OK",
            "message": "Hardware information retrieved successfully.",
            "data": {"cpu_info": cpu_info, "total_ram": mem_info.total, "available_ram": mem_info.available},
        }
