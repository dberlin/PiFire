from unittest import mock

from common.usb_serial import discover_usb_serial_devices


class _FakePort:
    def __init__(self, device, description="", manufacturer=None, serial_number=None, vid=None, pid=None):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer
        self.serial_number = serial_number
        self.vid = vid
        self.pid = pid


def test_discover_returns_all_ports_when_unfiltered():
    ports = [
        _FakePort("/dev/ttyACM0", description="SEN0628", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyUSB0", description="FTDI adapter", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices()
    assert [d["device"] for d in result] == ["/dev/ttyACM0", "/dev/ttyUSB0"]


def test_discover_filters_by_vid_and_pid():
    ports = [
        _FakePort("/dev/ttyACM0", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyUSB0", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices(vid=0x2E8A, pid=0x000A)
    assert [d["device"] for d in result] == ["/dev/ttyACM0"]


def test_discover_filters_by_vid_only():
    ports = [
        _FakePort("/dev/ttyACM0", vid=0x2E8A, pid=0x000A),
        _FakePort("/dev/ttyACM1", vid=0x2E8A, pid=0x0009),
        _FakePort("/dev/ttyUSB0", vid=0x0403, pid=0x6001),
    ]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices(vid=0x2E8A)
    assert [d["device"] for d in result] == ["/dev/ttyACM0", "/dev/ttyACM1"]


def test_discover_returns_empty_list_on_enumeration_failure():
    with mock.patch("common.usb_serial.list_ports.comports", side_effect=OSError("no such device")):
        assert discover_usb_serial_devices() == []


def test_discover_includes_serial_number_and_manufacturer():
    ports = [_FakePort("/dev/ttyACM0", description="SEN0628", manufacturer="DFRobot", serial_number="ABC123")]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices()
    assert result[0]["manufacturer"] == "DFRobot"
    assert result[0]["serial_number"] == "ABC123"
