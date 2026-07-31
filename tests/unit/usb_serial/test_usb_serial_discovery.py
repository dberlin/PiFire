from unittest import mock

import pytest

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


# ---------------------------------------------------------------------------
# vid/pid coercion.
#
# The wizard manifest writes USB IDs the way USB IDs are always written --
# "0x2a19" -- while pyserial reports port.vid as an int. `1 != "0x2a19"` is
# silently false for every port, so a filter written in the manifest matched
# nothing and the Discover list came back empty (or, before the filter was
# passed through at all, listed every serial device on the machine).
# ---------------------------------------------------------------------------

NUMATO_PORTS = [
    _FakePort("/dev/ttyACM0", description="CP2102 USB to UART Bridge", vid=0x10C4, pid=0xEA60),
    _FakePort("/dev/ttyACM1", description="Numato Lab 4 Channel USB Relay", vid=0x2A19, pid=0x0C0C),
    _FakePort("/dev/ttyUSB0", description="FTDI adapter", vid=0x0403, pid=0x6001),
]


def test_discover_accepts_hex_string_ids_as_the_manifest_writes_them():
    with mock.patch("common.usb_serial.list_ports.comports", return_value=NUMATO_PORTS):
        result = discover_usb_serial_devices(vid="0x2a19", pid="0x0c0c")
    assert [d["device"] for d in result] == ["/dev/ttyACM1"]


def test_discover_accepts_a_bare_hex_string_without_the_prefix():
    # USB IDs are hex by convention, so "2a19" is 0x2A19 and never decimal 2419.
    with mock.patch("common.usb_serial.list_ports.comports", return_value=NUMATO_PORTS):
        result = discover_usb_serial_devices(vid="2a19", pid="0c0c")
    assert [d["device"] for d in result] == ["/dev/ttyACM1"]


def test_discover_still_accepts_plain_ints():
    with mock.patch("common.usb_serial.list_ports.comports", return_value=NUMATO_PORTS):
        result = discover_usb_serial_devices(vid=0x2A19, pid=0x0C0C)
    assert [d["device"] for d in result] == ["/dev/ttyACM1"]


def test_discover_treats_none_and_empty_string_as_no_filter():
    # The manifest writes `null` for a device whose IDs are unknown, and an
    # empty string is what an untouched form field sends.
    with mock.patch("common.usb_serial.list_ports.comports", return_value=NUMATO_PORTS):
        assert len(discover_usb_serial_devices(vid=None, pid=None)) == 3
        assert len(discover_usb_serial_devices(vid="", pid="")) == 3


def test_discover_rejects_an_unreadable_id_instead_of_listing_everything():
    # Degrading to "no filter" would offer every serial device on the machine
    # as though each one were the board -- which is how the wrong tty got
    # chosen in the first place. The scan endpoint turns this into a visible
    # "Scan failed" rather than a plausible-looking wrong list.
    with mock.patch("common.usb_serial.list_ports.comports", return_value=NUMATO_PORTS):
        with pytest.raises(ValueError, match="0x2a19"):
            discover_usb_serial_devices(vid="nonsense")


# ---------------------------------------------------------------------------
# Stable device aliases.
#
# pyserial reports the kernel's own name -- /dev/ttyACM0 -- which is assigned in
# USB enumeration order and moves when devices are replugged or another adapter
# appears. Saving THAT into settings is what leaves a configured install
# pointing at some other device later, and the failure is silent: the port
# opens, writes succeed, reads time out. auto-install/udev/99-pifire.rules
# creates /dev/pifire-numato so there is something stable to save instead --
# but pyserial will never report it, so discovery has to find it.
# ---------------------------------------------------------------------------

import common.usb_serial as usb_serial_module


def _link_farm(tmp_path, real_name="ttyACM1"):
    """A fake /dev: one real device node plus a directory for by-id links."""
    dev = tmp_path / "dev"
    byid = dev / "serial" / "by-id"
    byid.mkdir(parents=True)
    real = dev / real_name
    real.write_text("")  # stands in for the character device
    return dev, byid, real


def test_stable_path_is_none_when_nothing_links_to_the_device(tmp_path, monkeypatch):
    dev, byid, real = _link_farm(tmp_path)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    assert usb_serial_module._stable_device_path(str(real)) is None


def test_stable_path_finds_the_by_id_link(tmp_path, monkeypatch):
    dev, byid, real = _link_farm(tmp_path)
    link = byid / "usb-Numato_Lab_4_Channel_USB_Relay-if00"
    link.symlink_to(real)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    assert usb_serial_module._stable_device_path(str(real)) == str(link)


def test_stable_path_prefers_the_pifire_link_over_by_id(tmp_path, monkeypatch):
    # Both name the same hardware; /dev/pifire-numato is the one that says what
    # the device IS, and is the one our udev rule guarantees.
    dev, byid, real = _link_farm(tmp_path)
    (byid / "usb-Numato_Lab_4_Channel_USB_Relay-if00").symlink_to(real)
    ours = dev / "pifire-numato"
    ours.symlink_to(real)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    assert usb_serial_module._stable_device_path(str(real)) == str(ours)


def test_stable_path_ignores_a_link_to_a_different_device(tmp_path, monkeypatch):
    dev, byid, real = _link_farm(tmp_path)
    other = dev / "ttyACM9"
    other.write_text("")
    (dev / "pifire-numato").symlink_to(other)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    assert usb_serial_module._stable_device_path(str(real)) is None


def test_discover_reports_the_stable_alias_alongside_the_kernel_name(tmp_path, monkeypatch):
    dev, byid, real = _link_farm(tmp_path)
    ours = dev / "pifire-numato"
    ours.symlink_to(real)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    ports = [_FakePort(str(real), description="Numato Lab 4 Channel USB Relay", vid=0x2A19, pid=0x0C0C)]
    with mock.patch("common.usb_serial.list_ports.comports", return_value=ports):
        result = discover_usb_serial_devices(vid="0x2a19")
    assert result[0]["device"] == str(real)
    assert result[0]["stable_device"] == str(ours)


def test_discover_reports_no_alias_when_there_is_none(tmp_path, monkeypatch):
    dev, byid, real = _link_farm(tmp_path)
    monkeypatch.setattr(usb_serial_module, "_STABLE_LINK_GLOBS", (str(dev / "pifire-*"), str(byid / "*")))
    with mock.patch("common.usb_serial.list_ports.comports", return_value=[_FakePort(str(real))]):
        result = discover_usb_serial_devices()
    assert result[0]["stable_device"] is None
