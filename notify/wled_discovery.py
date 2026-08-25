"""
WLED Device Discovery Module
Discovers WLED devices on the network using mDNS/Bonjour and HTTP queries
"""

import socket
import threading
import time
from typing import Dict, List

import requests
from zeroconf import ServiceBrowser, Zeroconf


class WLEDDeviceInfo:
    """Class to store WLED device information"""

    def __init__(self, name: str, ip: str, port: int = 80):
        self.name = name
        self.ip = ip
        self.port = port
        self.led_count = 0
        self.version = ""
        self.product = ""
        self.mac = ""
        self.online = False

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "led_count": self.led_count,
            "version": self.version,
            "product": self.product,
            "mac": self.mac,
            "online": self.online,
        }


class WLEDDiscovery:
    """WLED Device Discovery using mDNS and HTTP"""

    def __init__(self, discovery_timeout: int = 10):
        self.discovery_timeout = discovery_timeout
        self.discovered_devices: list[WLEDDeviceInfo] = []
        self.zeroconf = None
        self.browser = None

    def __enter__(self):
        self.zeroconf = Zeroconf()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.browser:
                self.browser.cancel()
        except Exception:
            pass  # Ignore cleanup errors
        try:
            if self.zeroconf:
                self.zeroconf.close()
        except Exception:
            pass  # Ignore cleanup errors

    def add_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Callback when a new service is discovered"""
        try:
            info = zeroconf.get_service_info(type_, name)
            if info:
                # Parse the service info
                device_name = name.replace("._http._tcp.local.", "")

                addresses = info.addresses
                if not addresses:
                    print(f"Skipping service {name}: no addresses advertised")
                    return
                raw_address = addresses[0]
                if len(raw_address) == 4:
                    ip_address = socket.inet_ntoa(raw_address)
                elif len(raw_address) == 16:
                    ip_address = socket.inet_ntop(socket.AF_INET6, raw_address)
                else:
                    print(f"Skipping service {name}: unrecognized address length {len(raw_address)}")
                    return
                port = info.port

                # _is_wled_device previously existed here as a name/TXT-record
                # filter, but it unconditionally returned True regardless of
                # its own matching logic -- it was dead code that could never
                # reject a device. Removed; every HTTP service reaching this
                # point is still validated for real via the HTTP probe below
                # (_get_device_info / device.online), which is the actual
                # filter in effect today.
                device = WLEDDeviceInfo(device_name, ip_address, port)

                # Try to get device details via HTTP
                self._get_device_info(device)

                # Only add if we got valid device info
                if device.online:
                    self.discovered_devices.append(device)
                    print(f"Discovered WLED device: {device.name} at {device.ip}:{device.port}")

        except Exception as e:
            # Suppress common zeroconf errors that don't affect functionality
            if "NoneType" not in str(e):
                print(f"Error processing service {name}: {e}")

    def remove_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Callback when a service is removed"""

    def update_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        """Callback when a service is updated"""

    def _get_device_info(self, device: WLEDDeviceInfo) -> None:
        """Get device information via HTTP/JSON API"""
        try:
            # Try the WLED JSON info endpoint
            url = f"http://{device.ip}:{device.port}/json/info"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                info_data = response.json()

                # Check if this is actually a WLED device
                if "ver" in info_data or "name" in info_data:
                    device.version = info_data.get("ver", "")
                    device.name = info_data.get("name", device.name)
                    device.mac = info_data.get("mac", "")
                    device.product = info_data.get("product", "WLED")

                    # Get LED count from state endpoint
                    state_url = f"http://{device.ip}:{device.port}/json/state"
                    state_response = requests.get(state_url, timeout=5)

                    if state_response.status_code == 200:
                        state_data = state_response.json()

                        # Extract LED count from segments
                        if "seg" in state_data and len(state_data["seg"]) > 0:
                            segment = state_data["seg"][0]
                            if "stop" in segment:
                                device.led_count = segment["stop"]
                            elif "len" in segment:
                                device.led_count = segment["len"]

                        # Alternative: get from info if available
                        if device.led_count == 0 and "leds" in info_data:
                            device.led_count = info_data["leds"].get("count", 0)

                    device.online = True

        except Exception as e:
            print(f"Error getting device info for {device.ip}: {e}")
            # Still mark as online if we can reach it, even if we can't get full info.
            # Only fill in placeholder values for fields that are still unset --
            # a partial parse before the exception (e.g. version/mac/product
            # from a good /json/info response, followed by a crash while
            # parsing "leds") already has good data that must not be
            # clobbered by the generic fallback below.
            try:
                simple_response = requests.get(f"http://{device.ip}:{device.port}/", timeout=3)
                if simple_response.status_code == 200:
                    device.online = True
                    if not device.product:
                        device.product = "WLED (Unknown Version)"
            except:
                pass

    def discover_mdns_devices(self) -> list[WLEDDeviceInfo]:
        """Discover WLED devices using mDNS/Bonjour"""
        print(f"Starting mDNS discovery for {self.discovery_timeout} seconds...")

        # Start the service browser
        self.browser = ServiceBrowser(self.zeroconf, "_http._tcp.local.", self)

        # Wait for discovery
        time.sleep(self.discovery_timeout)

        # Stop browsing with better error handling
        try:
            if self.browser:
                self.browser.cancel()
        except Exception:
            pass  # Ignore cleanup errors

        print(f"mDNS discovery completed. Found {len(self.discovered_devices)} WLED devices.")
        return self.discovered_devices.copy()

    def discover_network_scan(self, network_range: str = "192.168.1.0/24") -> list[WLEDDeviceInfo]:
        """Discover WLED devices by scanning IP range (fallback method)"""
        print(f"Starting network scan for WLED devices in {network_range}...")
        devices = []

        # This is a basic implementation - could be enhanced with proper network scanning
        # For now, just scan the /24 implied by network_range's first three octets
        # (the IP sweep below always covers .1-.254 of a single /24, matching the
        # "x.x.x.0/24" default). Falls back to 192.168.1. if network_range doesn't
        # look like a dotted-quad.
        network_octets = network_range.split("/")[0].split(".")
        if len(network_octets) >= 3:
            base_ip = ".".join(network_octets[:3]) + "."
        else:
            base_ip = "192.168.1."

        def check_ip(ip):
            try:
                device = WLEDDeviceInfo(f"WLED-{ip.split('.')[-1]}", ip)
                self._get_device_info(device)
                if device.online:
                    devices.append(device)
            except:
                pass

        # Use threading to speed up scanning
        threads = []
        for i in range(1, 255):
            ip = base_ip + str(i)
            thread = threading.Thread(target=check_ip, args=(ip,))
            threads.append(thread)
            thread.start()

            # Limit concurrent threads
            if len(threads) >= 20:
                for t in threads:
                    t.join()
                threads = []

        # Wait for remaining threads
        for thread in threads:
            thread.join()

        print(f"Network scan completed. Found {len(devices)} WLED devices.")
        return devices

    def discover_all(self) -> list[WLEDDeviceInfo]:
        """Discover WLED devices using all available methods"""
        all_devices = []
        device_ips = set()

        # Try mDNS first
        try:
            mdns_devices = self.discover_mdns_devices()
            for device in mdns_devices:
                if device.ip not in device_ips:
                    all_devices.append(device)
                    device_ips.add(device.ip)
        except Exception as e:
            print(f"mDNS discovery failed: {e}")

        # Note: Network scan disabled by default as it can be slow
        # Uncomment the following lines to enable network scanning
        """
        try:
            scan_devices = self.discover_network_scan()
            for device in scan_devices:
                if device.ip not in device_ips:
                    all_devices.append(device)
                    device_ips.add(device.ip)
        except Exception as e:
            print(f"Network scan failed: {e}")
        """

        return all_devices


def discover_wled_devices(timeout: int = 10) -> list[dict]:
    """
    Convenience function to discover WLED devices.
    Returns a list of device dictionaries suitable for JSON serialization.

    Runs zeroconf/mDNS discovery in-process. This is safe under the webapp's
    gthread worker (real OS threads, no eventlet/gevent monkey-patching); the
    old subprocess fallback existed only to dodge that patching and is no longer
    needed.
    """
    try:
        with WLEDDiscovery(timeout) as discovery:
            devices = discovery.discover_all()
            return [device.to_dict() for device in devices]
    except Exception as e:
        print(f"WLED discovery error: {e}")
        return []


# Test function
if __name__ == "__main__":
    print("Testing WLED Discovery...")
    devices = discover_wled_devices()

    if devices:
        print(f"\nFound {len(devices)} WLED devices:")
        for device in devices:
            print(f"  Name: {device['name']}")
            print(f"  IP: {device['ip']}:{device['port']}")
            print(f"  LED Count: {device['led_count']}")
            print(f"  Version: {device['version']}")
            print(f"  Product: {device['product']}")
            print(f"  Online: {device['online']}")
            print()
    else:
        print("No WLED devices found.")
