#!/usr/bin/env python3
"""List ThermoWorks Cloud devices and their current probe temperatures.

Reuses PiFire's `probes.thermoworks_cloud` module (its cloud client, device
discovery, and channel polling), so it needs no extra setup beyond the project
venv, which already ships the `thermoworks-cloud` + `aiohttp` dependencies.

Run from the repo root with the project venv:

    .venv/bin/python tools/thermoworks_list.py
    .venv/bin/python tools/thermoworks_list.py --units F
    .venv/bin/python tools/thermoworks_list.py --email you@example.com --password 'pw'
    .venv/bin/python tools/thermoworks_list.py --json

Credentials are resolved in order: --email/--password, then the
THERMOWORKS_EMAIL / THERMOWORKS_PASSWORD environment variables, then the
`email`/`password` of the configured thermoworks_cloud probe device in PiFire's
settings.
"""

import argparse
import asyncio
import json
import os
import sys

# Allow running as `tools/thermoworks_list.py` from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import ClientSession  # noqa: E402
from thermoworks_cloud import AuthFactory, AuthenticationError, ThermoworksCloud  # noqa: E402

from probes.thermoworks_cloud import _channel_to_celsius, discover_devices, poll_once  # noqa: E402


def resolve_credentials(email, password):
    """Return (email, password) from CLI args, then env vars, then the
    configured thermoworks_cloud probe device in PiFire's settings."""
    email = email or os.environ.get("THERMOWORKS_EMAIL")
    password = password or os.environ.get("THERMOWORKS_PASSWORD")
    if email and password:
        return email, password
    try:
        from common.common import read_settings

        devices = read_settings()["probe_settings"]["probe_map"]["probe_devices"]
    except Exception:
        devices = []
    for device in devices:
        if device.get("module") == "thermoworks_cloud":
            config = device.get("config", {}) or {}
            email = email or config.get("email")
            password = password or config.get("password")
            if email and password:
                break
    return email, password


def channel_label(channel, number):
    """A human label for a channel: its cloud label, else the channel type
    (RFX probes sense at several points and name them via `type`), else
    'Channel N'. RFX wireless "probes" are a single physical thermometer whose
    multiple internal sensors each appear here as a channel."""
    if channel is not None:
        for attr in ("label", "type"):
            value = getattr(channel, attr, None)
            if value:
                return str(value)
    return f"Channel {number}"


def format_temp(channel, units):
    """Format one channel's current temperature. `channel` is a DeviceChannel or
    None (channel absent); `units` is 'C', 'F', or None (show as the cloud
    reports it)."""
    if channel is None:
        return "(not found)"
    if channel.value is None:
        return "(no reading)"
    if units is None:
        return f"{channel.value:g} \N{DEGREE SIGN}{channel.units}"
    celsius = _channel_to_celsius(channel)
    if celsius is None:
        return "(no reading)"
    if units == "C":
        return f"{celsius:.1f} \N{DEGREE SIGN}C"
    return f"{celsius * 9 / 5 + 32:.1f} \N{DEGREE SIGN}F"


async def gather_readings(email, password):
    """Return [(device_dict, {channel_number: DeviceChannel|None}), ...] for
    every device on the account."""
    async with ClientSession() as session:
        auth = await AuthFactory(session).build_auth(email, password)
        client = ThermoworksCloud(auth)
        readings = []
        for device in await discover_devices(client):
            channels = await poll_once(client, device["serial"], device["num_channels"])
            readings.append((device, channels))
        return readings


def print_text(readings, units):
    if not readings:
        print("No ThermoWorks Cloud devices found on this account.")
        return
    print(f"ThermoWorks Cloud \N{EM DASH} {len(readings)} device(s)\n")
    for device, channels in readings:
        print(f"{device['label'] or '(unnamed)'} ({device['type']})  serial={device['serial']}")
        for number in sorted(channels):
            channel = channels[number]
            print(f"  {number}  {channel_label(channel, number):<18} {format_temp(channel, units)}")
        print()


def build_json(readings):
    payload = []
    for device, channels in readings:
        entries = []
        for number in sorted(channels):
            channel = channels[number]
            entries.append(
                {
                    "number": number,
                    "label": channel_label(channel, number),
                    "type": getattr(channel, "type", None),
                    "status": getattr(channel, "status", None),
                    "value": getattr(channel, "value", None),
                    "units": getattr(channel, "units", None),
                    "celsius": _channel_to_celsius(channel) if channel is not None else None,
                }
            )
        payload.append(
            {"serial": device["serial"], "label": device["label"], "type": device["type"], "channels": entries}
        )
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description="List ThermoWorks Cloud devices and current probe temperatures.")
    parser.add_argument("--email", help="ThermoWorks account email.")
    parser.add_argument("--password", help="ThermoWorks account password.")
    parser.add_argument(
        "--units",
        choices=["C", "F"],
        default=None,
        help="Normalize all temps to C or F (default: as reported by the cloud).",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    email, password = resolve_credentials(args.email, args.password)
    if not (email and password):
        parser.error(
            "No ThermoWorks credentials found. Pass --email/--password, set "
            "THERMOWORKS_EMAIL/THERMOWORKS_PASSWORD, or configure a thermoworks_cloud device in PiFire."
        )

    try:
        readings = asyncio.run(gather_readings(email, password))
    except AuthenticationError as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error contacting ThermoWorks Cloud: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(build_json(readings), indent=2))
    else:
        print_text(readings, args.units)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
