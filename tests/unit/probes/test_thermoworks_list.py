import json
import types

import pytest
from thermoworks_cloud.models.device_channel import _document_to_device_channel

from tools import thermoworks_list
from tools.thermoworks_list import channel_label, format_temp, resolve_credentials


def _channel(value, units, label=None, type=None):
    return types.SimpleNamespace(value=value, units=units, label=label, type=type)


def test_format_temp_as_reported_uses_cloud_units():
    assert format_temp(_channel(212.5, "F"), None) == "212.5 \N{DEGREE SIGN}F"
    assert format_temp(_channel(21.0, "C"), None) == "21 \N{DEGREE SIGN}C"


def test_format_temp_missing_channel_and_no_reading():
    assert format_temp(None, "F") == "(not found)"
    assert format_temp(_channel(None, "F"), "F") == "(no reading)"


def test_format_temp_normalizes_units():
    # 100 C -> 212 F, and F source shown in C.
    assert format_temp(_channel(100.0, "C"), "F") == "212.0 \N{DEGREE SIGN}F"
    assert format_temp(_channel(212.0, "F"), "C") == "100.0 \N{DEGREE SIGN}C"


@pytest.mark.parametrize(
    ("field", "units", "reported", "celsius", "fahrenheit"),
    [
        ({"integerValue": "212"}, "F", "212 °F", "100.0 °C", "212.0 °F"),
        ({"integerValue": "100"}, "C", "100 °C", "100.0 °C", "212.0 °F"),
        ({"doubleValue": 212.5}, "F", "212.5 °F", "100.3 °C", "212.5 °F"),
        ({"nullValue": None}, "F", "(no reading)", "(no reading)", "(no reading)"),
    ],
)
def test_format_temp_accepts_firestore_channel_values(field, units, reported, celsius, fahrenheit):
    channel = _document_to_device_channel({"fields": {"value": field, "units": {"stringValue": units}}})

    assert format_temp(channel, None) == reported
    assert format_temp(channel, "C") == celsius
    assert format_temp(channel, "F") == fahrenheit


@pytest.mark.parametrize("json_output", [False, True])
def test_cli_displays_firestore_integer_reading(monkeypatch, capsys, json_output):
    channel = _document_to_device_channel({"fields": {"value": {"integerValue": "212"}, "units": {"stringValue": "F"}}})

    async def gather_readings(email, password):
        return [({"serial": "SN1", "label": "Signals", "type": "signals"}, {1: channel})]

    monkeypatch.setattr(thermoworks_list, "gather_readings", gather_readings)
    argv = ["--email", "test@example.com", "--password", "test-password"]
    if json_output:
        argv.append("--json")

    assert thermoworks_list.main(argv) == 0
    output = capsys.readouterr()
    assert output.err == ""
    if json_output:
        reading = json.loads(output.out)[0]["channels"][0]
        assert reading["value"] == "212"  # Retain the value as reported by the cloud.
        assert reading["celsius"] == pytest.approx(100.0)
    else:
        assert "212 °F" in output.out


def test_channel_label_prefers_label_then_type_then_number():
    assert channel_label(_channel(1, "F", label="Brisket"), 3) == "Brisket"
    # No label -> fall back to the channel type (what RFX populates per sensor).
    assert channel_label(_channel(1, "F", label="", type="internal"), 3) == "internal"
    # Neither label nor type -> the channel number.
    assert channel_label(_channel(1, "F", label="", type=None), 3) == "Channel 3"
    assert channel_label(None, 5) == "Channel 5"


def test_resolve_credentials_prefers_cli_then_env(monkeypatch):
    monkeypatch.delenv("THERMOWORKS_EMAIL", raising=False)
    monkeypatch.delenv("THERMOWORKS_PASSWORD", raising=False)
    assert resolve_credentials("a@b.com", "pw") == ("a@b.com", "pw")

    monkeypatch.setenv("THERMOWORKS_EMAIL", "env@b.com")
    monkeypatch.setenv("THERMOWORKS_PASSWORD", "envpw")
    assert resolve_credentials(None, None) == ("env@b.com", "envpw")
