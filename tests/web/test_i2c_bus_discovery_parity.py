"""Pins the wizard scan endpoint's kernel Discover group titles against
I2cBusField.tsx's GROUP_FOR, the frontend table that selects among them.
Nothing else ties the two sides together -- a fixture hand-written on either
side only ever proves it agrees with itself, so this derives the backend
side by calling POST /api/wizard/scan (kind="kernel") with a stubbed
discover_extended_i2c_buses, and the frontend side by parsing GROUP_FOR out
of the real TSX source, rather than hard-coding both.
"""

import re
from pathlib import Path

import pytest

_I2C_BUS_FIELD_TSX = (
    Path(__file__).resolve().parents[2] / "web-react" / "src" / "components" / "wizard" / "fields" / "I2cBusField.tsx"
)


def _frontend_group_titles():
    """The titles I2cBusField.tsx selects Discover groups by, in GROUP_FOR's
    bus_num / adapter / serial declaration order -- the same order the
    /scan endpoint below emits its three kernel groups in."""
    source = _I2C_BUS_FIELD_TSX.read_text()
    match = re.search(r"const GROUP_FOR:\s*Record<KernelBy,\s*string>\s*=\s*\{(.*?)\}", source, re.DOTALL)
    assert match, "GROUP_FOR not found in I2cBusField.tsx"
    titles = re.findall(r'"([^"]+)"', match.group(1))
    assert titles, "no titles parsed out of GROUP_FOR"
    return titles


def test_scan_kernel_group_titles_match_the_frontends_group_for(client, monkeypatch):
    monkeypatch.setattr(
        "blueprints.api_wizard.routes.discover_extended_i2c_buses",
        lambda: [{"bus_num": 7, "name": "CP2112 SMBus Bridge", "serial": "AB12"}],
    )
    body = client.post("/api/wizard/scan", json={"kind": "kernel"}).get_json()
    backend_titles = [group["title"] for group in body["groups"]]

    assert backend_titles == _frontend_group_titles()
