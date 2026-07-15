import logging
from unittest import mock

import pytest
from EasyMCP2221.exceptions import LowSCLError, LowSDAError, NotAckError, TimeoutError

import common.i2c_bus as i2c_bus
from common import mcp2221
from common.i2c_bus import I2CBusConfigError, assert_clean_blinka_env, resolve_i2c_bus, validate_bus_kinds


def test_resolve_i2c_bus_numeric_returns_int():
    assert resolve_i2c_bus("3") == 3
    assert resolve_i2c_bus(3) == 3


def test_validate_bus_kinds_allows_workable_combos():
    # None of these raise.
    validate_bus_kinds({"ft232h", "mcp2221"})
    validate_bus_kinds({"ft232h", "extended"})
    validate_bus_kinds({"mcp2221", "extended"})
    validate_bus_kinds({"basic", "extended"})
    validate_bus_kinds({"ft232h", "mcp2221", "extended"})
    validate_bus_kinds({"", None, "basic"})  # blanks ignored


def test_validate_bus_kinds_rejects_basic_plus_usb():
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds({"basic", "ft232h"})
    with pytest.raises(I2CBusConfigError):
        validate_bus_kinds({"basic", "mcp2221"})


def test_assert_clean_blinka_env_rejects_board_forcing_vars():
    for var in ("BLINKA_FT232H", "BLINKA_MCP2221", "BLINKA_FORCEBOARD", "BLINKA_FTX232H_0"):
        with pytest.raises(I2CBusConfigError):
            assert_clean_blinka_env({var: "1"})


def test_assert_clean_blinka_env_allows_tuning_and_empty():
    assert_clean_blinka_env({})
    assert_clean_blinka_env({"BLINKA_MCP2221_HID_DELAY": "0.1", "BLINKA_MCP2221_RESET_DELAY": "0.5"})
    assert_clean_blinka_env({"PATH": "/usr/bin"})


@pytest.fixture(autouse=True)
def _clean_bus_state():
    i2c_bus.reset_bus_state()
    yield
    i2c_bus.reset_bus_state()


def test_locked_i2c_lock_and_delegate():
    backend = mock.Mock()
    wrapped = i2c_bus._LockedI2C(backend)
    assert wrapped.try_lock() is True
    wrapped.unlock()
    wrapped.unlock()  # double unlock is safe
    wrapped.writeto(0x10, b"\x01")
    backend.writeto.assert_called_once_with(0x10, b"\x01")
    wrapped.scan()
    backend.scan.assert_called_once()


class _FakeI2CDevice:
    """Stand-in for an EasyMCP2221.Device -- records every I2C_write/I2C_read
    call, returns a canned read result, and can be told to raise a canned
    exception (simulating NotAckError etc.) instead."""

    def __init__(self, read_result=b"", raise_exc=None):
        self.read_result = read_result
        self.raise_exc = raise_exc
        self.calls = []

    def I2C_write(self, addr, data, kind="regular", timeout_ms=20):
        self.calls.append(("write", addr, bytes(data), kind))
        if self.raise_exc:
            raise self.raise_exc

    def I2C_read(self, addr, size=1, kind="regular", timeout_ms=20):
        self.calls.append(("read", addr, size, kind))
        if self.raise_exc:
            raise self.raise_exc
        return self.read_result


def test_easymcp2221_backend_writeto_nonempty_calls_i2c_write():
    device = _FakeI2CDevice()
    backend = mcp2221._EasyMCP2221Backend(device)
    backend.writeto(0x40, b"\x01\x02")
    assert device.calls == [("write", 0x40, b"\x01\x02", "regular")]


def test_easymcp2221_backend_writeto_empty_does_presence_read():
    device = _FakeI2CDevice()
    backend = mcp2221._EasyMCP2221Backend(device)
    backend.writeto(0x40, b"")
    assert device.calls == [("read", 0x40, 1, "regular")]


def test_easymcp2221_backend_readfrom_into_fills_buffer():
    device = _FakeI2CDevice(read_result=b"\x0a\x0b\x0c")
    backend = mcp2221._EasyMCP2221Backend(device)
    buf = bytearray(3)
    backend.readfrom_into(0x40, buf)
    assert bytes(buf) == b"\x0a\x0b\x0c"
    assert device.calls == [("read", 0x40, 3, "regular")]


def test_easymcp2221_backend_writeto_then_readfrom_uses_nonstop_restart():
    device = _FakeI2CDevice(read_result=b"\xaa\xbb")
    backend = mcp2221._EasyMCP2221Backend(device)
    out = bytearray(2)
    backend.writeto_then_readfrom(0x40, b"\x00", out)
    assert bytes(out) == b"\xaa\xbb"
    assert device.calls == [("write", 0x40, b"\x00", "nonstop"), ("read", 0x40, 2, "restart")]


def test_easymcp2221_backend_scan_collects_acking_addresses():
    device = _FakeI2CDevice()
    backend = mcp2221._EasyMCP2221Backend(device)
    assert backend.scan() == list(range(0x08, 0x78))


@pytest.mark.parametrize("exc_cls", [NotAckError, TimeoutError, LowSCLError, LowSDAError])
def test_easymcp2221_backend_translates_i2c_errors_to_oserror(exc_cls):
    device = _FakeI2CDevice(raise_exc=exc_cls("boom"))
    backend = mcp2221._EasyMCP2221Backend(device)
    with pytest.raises(OSError):
        backend.writeto(0x40, b"\x01")
    with pytest.raises(OSError):
        backend.readfrom_into(0x40, bytearray(1))


class _FailingHalfI2CDevice:
    """Stand-in for an EasyMCP2221.Device whose I2C_write and/or I2C_read can
    each independently be told to raise, so writeto_then_readfrom's two
    distinct call sites (the write half and the read half) can be tested
    separately."""

    def __init__(self, read_result=b"", raise_on_write=None, raise_on_read=None):
        self.read_result = read_result
        self.raise_on_write = raise_on_write
        self.raise_on_read = raise_on_read
        self.calls = []

    def I2C_write(self, addr, data, kind="regular", timeout_ms=20):
        self.calls.append(("write", addr, bytes(data), kind))
        if self.raise_on_write:
            raise self.raise_on_write

    def I2C_read(self, addr, size=1, kind="regular", timeout_ms=20):
        self.calls.append(("read", addr, size, kind))
        if self.raise_on_read:
            raise self.raise_on_read
        return self.read_result


@pytest.mark.parametrize("exc_cls", [NotAckError, TimeoutError, LowSCLError, LowSDAError])
def test_easymcp2221_backend_writeto_then_readfrom_translates_write_half_error(exc_cls):
    device = _FailingHalfI2CDevice(raise_on_write=exc_cls("boom"))
    backend = mcp2221._EasyMCP2221Backend(device)
    with pytest.raises(OSError):
        backend.writeto_then_readfrom(0x40, b"\x00", bytearray(2))


@pytest.mark.parametrize("exc_cls", [NotAckError, TimeoutError, LowSCLError, LowSDAError])
def test_easymcp2221_backend_writeto_then_readfrom_translates_read_half_error(exc_cls):
    device = _FailingHalfI2CDevice(read_result=b"\xaa\xbb", raise_on_read=exc_cls("boom"))
    backend = mcp2221._EasyMCP2221Backend(device)
    with pytest.raises(OSError):
        backend.writeto_then_readfrom(0x40, b"\x00", bytearray(2))
    # The write half must have actually happened before the read half raised.
    assert device.calls == [("write", 0x40, b"\x00", "nonstop"), ("read", 0x40, 2, "restart")]


class _PerAddressI2CDevice:
    """Stand-in for an EasyMCP2221.Device that only ACKs (returns normally)
    for a fixed set of addresses, raising NotAckError for every other
    address -- so scan()'s per-address inclusion/exclusion can be tested."""

    def __init__(self, acking_addresses):
        self.acking_addresses = set(acking_addresses)
        self.calls = []

    def I2C_read(self, addr, size=1, kind="regular", timeout_ms=20):
        self.calls.append(("read", addr, size, kind))
        if addr not in self.acking_addresses:
            raise NotAckError("no device")
        return b"\x00"


def test_easymcp2221_backend_scan_excludes_addresses_that_raise():
    device = _PerAddressI2CDevice(acking_addresses={0x10, 0x50})
    backend = mcp2221._EasyMCP2221Backend(device)
    assert backend.scan() == [0x10, 0x50]


def types_module_with(**attrs):
    import types

    mod = types.ModuleType("fake")
    for name, value in attrs.items():
        setattr(mod, name, value)
    return mod


def _fake_easymcp2221_module(not_found_serials=frozenset()):
    """Build a fake EasyMCP2221 module exposing a Device class that records
    every (usbserial, scan_serial) construction as its own independent
    instance -- never a shared singleton, which is the whole point of this
    swap -- and raises RuntimeError like the real library when usbserial is
    in not_found_serials.

    Returns (modules_dict_for_sys_modules, the fake Device class)."""
    import types

    class _FakeDevice:
        instances = []

        def __init__(self, usbserial=None, scan_serial=False):
            if usbserial in not_found_serials:
                raise RuntimeError(f"No device found with serial number {usbserial}.")
            self.usbserial = usbserial
            self.scan_serial = scan_serial
            _FakeDevice.instances.append(self)

    mod = types.ModuleType("EasyMCP2221")
    mod.Device = _FakeDevice
    return {"EasyMCP2221": mod}, _FakeDevice


def test_open_mcp2221_no_selector_constructs_backend():
    modules, FakeDevice = _fake_easymcp2221_module()
    with mock.patch.dict("sys.modules", modules):
        bus = i2c_bus.open_i2c_bus("mcp2221", "")
    assert isinstance(bus, i2c_bus._LockedI2C)
    assert len(FakeDevice.instances) == 1
    assert FakeDevice.instances[0].usbserial is None
    assert FakeDevice.instances[0].scan_serial is False


def test_open_mcp2221_selector_opens_matching_serial():
    modules, FakeDevice = _fake_easymcp2221_module()
    with mock.patch.dict("sys.modules", modules):
        bus = i2c_bus.open_i2c_bus("mcp2221", "BBBB")
    assert isinstance(bus, i2c_bus._LockedI2C)
    assert len(FakeDevice.instances) == 1
    assert FakeDevice.instances[0].usbserial == "BBBB"
    assert FakeDevice.instances[0].scan_serial is True


def test_open_mcp2221_selector_not_found_raises():
    modules, FakeDevice = _fake_easymcp2221_module(not_found_serials={"ZZZZ"})
    with mock.patch.dict("sys.modules", modules):
        with pytest.raises(i2c_bus.I2CBusConfigError):
            i2c_bus.open_i2c_bus("mcp2221", "ZZZZ")


def test_open_mcp2221_two_selectors_stay_independently_live():
    """Regression test for the bug this whole change fixes: Blinka's MCP2221
    backend was a single process-wide singleton, so opening a second serial
    silently re-pointed the first bus's HID handle at the second device.
    EasyMCP2221.Device is per-adapter, so two different selectors must
    produce two distinct, independently-live Device instances."""
    modules, FakeDevice = _fake_easymcp2221_module()
    with mock.patch.dict("sys.modules", modules):
        bus_a = i2c_bus.open_i2c_bus("mcp2221", "AAAA")
        bus_b = i2c_bus.open_i2c_bus("mcp2221", "BBBB")
    assert bus_a is not bus_b
    assert len(FakeDevice.instances) == 2
    dev_a, dev_b = FakeDevice.instances
    assert dev_a is not dev_b
    assert dev_a.usbserial == "AAAA"
    assert dev_b.usbserial == "BBBB"


def _fake_easymcp2221_module_with_catalog(first_device_serial="FIRST"):
    """Build a fake EasyMCP2221 module whose Device mimics the real
    library's object-identity dedup (EasyMCP2221.Device.__new__ returns the
    SAME Python object for the SAME physical adapter, per its own class
    docstring, regardless of which selector spelling found it): a blank
    selector always resolves to `first_device_serial`; any other usbserial
    is treated as a distinct physical device. This is the exact aliasing
    shape (blank selector vs. that same device's own explicit serial) the
    selector-aliasing fix targets -- the plain `_fake_easymcp2221_module`
    fake above has no catalog behavior at all, so it cannot express this.

    Returns (modules_dict_for_sys_modules, the fake Device class)."""
    import types

    class _FakeDevice:
        catalog = {}  # identity (usbserial, or first_device_serial for blank) -> instance

        def __new__(cls, usbserial=None, scan_serial=False):
            identity = first_device_serial if usbserial is None else usbserial
            existing = cls.catalog.get(identity)
            if existing is not None:
                return existing
            self = super().__new__(cls)
            cls.catalog[identity] = self
            return self

        def __init__(self, usbserial=None, scan_serial=False):
            # Mirrors the real library: __init__ runs every time, even on a
            # cataloged/reused object (Python always calls __init__ on
            # whatever __new__ returns).
            self.usbserial = first_device_serial if usbserial is None else usbserial
            self.scan_serial = scan_serial

    mod = types.ModuleType("EasyMCP2221")
    mod.Device = _FakeDevice
    return {"EasyMCP2221": mod}, _FakeDevice


def test_open_mcp2221_blank_and_explicit_serial_alias_share_one_bus():
    """Regression test for the selector-aliasing bug: a blank selector and
    the explicit serial of that same first device resolve to the SAME
    EasyMCP2221.Device object (per EasyMCP2221's own object-identity dedup).
    The returned bus must be shared too -- one lock, not two independently
    locked wrappers around one physical adapter."""
    modules, FakeDevice = _fake_easymcp2221_module_with_catalog(first_device_serial="FIRST")
    with mock.patch.dict("sys.modules", modules):
        bus_blank = i2c_bus.open_i2c_bus("mcp2221", "")
        bus_serial = i2c_bus.open_i2c_bus("mcp2221", "FIRST")
    assert bus_blank is bus_serial
    assert len(FakeDevice.catalog) == 1


def test_open_mcp2221_explicit_serial_and_blank_alias_share_one_bus_reverse_order():
    """Same aliasing scenario as above, opened in the opposite order --
    proves the dedup doesn't depend on which selector spelling was seen
    first."""
    modules, FakeDevice = _fake_easymcp2221_module_with_catalog(first_device_serial="FIRST")
    with mock.patch.dict("sys.modules", modules):
        bus_serial = i2c_bus.open_i2c_bus("mcp2221", "FIRST")
        bus_blank = i2c_bus.open_i2c_bus("mcp2221", "")
    assert bus_blank is bus_serial
    assert len(FakeDevice.catalog) == 1


def test_open_mcp2221_non_aliasing_selectors_stay_independent():
    """A blank selector and an explicit serial that do NOT match the first
    device must still produce two independent buses -- confirms the dedup
    only merges genuine aliases of the same physical device, not every
    mcp2221 bus indiscriminately."""
    modules, FakeDevice = _fake_easymcp2221_module_with_catalog(first_device_serial="FIRST")
    with mock.patch.dict("sys.modules", modules):
        bus_blank = i2c_bus.open_i2c_bus("mcp2221", "")
        bus_other = i2c_bus.open_i2c_bus("mcp2221", "SECOND")
    assert bus_blank is not bus_other
    assert len(FakeDevice.catalog) == 2


def test_probes_base_reexports_bus_helpers():
    import common.i2c_bus as cib
    import probes.base as base

    assert base.resolve_i2c_bus is cib.resolve_i2c_bus
    assert base.find_i2c_bus is cib.find_i2c_bus


def test_find_i2c_bus_debug_logs_match_and_result(tmp_path, caplog):
    bus = tmp_path / "i2c-5"
    bus.mkdir()
    (bus / "name").write_text("CP2112 SMBus Bridge\n")

    with caplog.at_level(logging.DEBUG, logger="control"):
        assert i2c_bus.find_i2c_bus("CP2112", devices_path=str(tmp_path)) == 5

    messages = [record.getMessage() for record in caplog.records]
    assert any("CP2112" in m for m in messages)
    assert any("i2c-5" in m for m in messages)


def test_open_i2c_bus_debug_logs_kind_and_selector(caplog):
    from common import ft232h

    fake_controller = type("FakeController", (), {})()
    with mock.patch.object(ft232h, "_new_controller", return_value=fake_controller):
        with caplog.at_level(logging.DEBUG, logger="control"):
            i2c_bus.open_i2c_bus("ft232h", "ftdi://ftdi:232h:FT9/1")

    text = caplog.text
    assert "ft232h" in text  # the kind being opened
    assert "ftdi://ftdi:232h:FT9/1" in text  # the exact selector/URL


def test_read_usb_serial_resolves_via_sysfs_walk(tmp_path):
    usb_device = tmp_path / "devices" / "usb1" / "1-1"
    usb_device.mkdir(parents=True)
    (usb_device / "serial").write_text("AB12\n")
    (usb_device / "idVendor").write_text("04d8\n")
    iface = usb_device / "1-1:1.0"
    iface.mkdir()
    bus_dir = iface / "i2c-7"
    bus_dir.mkdir()
    (bus_dir / "name").write_text("MCP2221 usb-i2c bridge\n")

    assert i2c_bus._read_usb_serial(str(bus_dir)) == "AB12"


def test_read_usb_serial_returns_none_without_usb_ancestor(tmp_path):
    bus_dir = tmp_path / "i2c-1"
    bus_dir.mkdir()
    (bus_dir / "name").write_text("bcm2835 I2C adapter\n")

    assert i2c_bus._read_usb_serial(str(bus_dir)) is None


def test_read_usb_serial_ignores_serial_file_without_idvendor(tmp_path):
    # A directory with a 'serial' file but no 'idVendor' isn't a USB device
    # level (e.g. a power_supply sysfs node) -- must not be mistaken for one.
    not_usb = tmp_path / "not_a_usb_device"
    not_usb.mkdir()
    (not_usb / "serial").write_text("DECOY\n")
    bus_dir = not_usb / "i2c-2"
    bus_dir.mkdir()
    (bus_dir / "name").write_text("some adapter\n")

    assert i2c_bus._read_usb_serial(str(bus_dir)) is None


def test_enumerate_i2c_adapters_includes_serial(tmp_path):
    usb_device = tmp_path / "devices" / "usb1" / "1-1"
    usb_device.mkdir(parents=True)
    (usb_device / "serial").write_text("AB12")
    (usb_device / "idVendor").write_text("04d8")
    devices_dir = usb_device / "1-1:1.0"
    devices_dir.mkdir()
    bus_dir = devices_dir / "i2c-7"
    bus_dir.mkdir()
    (bus_dir / "name").write_text("MCP2221 usb-i2c bridge")

    adapters = i2c_bus._enumerate_i2c_adapters(devices_path=str(devices_dir))
    assert adapters == [{"bus_num": 7, "name": "MCP2221 usb-i2c bridge", "serial": "AB12"}]


def _make_usb_i2c_adapter(root, usb_name, serial, bus_num, adapter_name, devices_dir):
    usb_dev = root / usb_name
    usb_dev.mkdir(parents=True)
    (usb_dev / "serial").write_text(serial)
    (usb_dev / "idVendor").write_text("04d8")
    iface = usb_dev / f"{usb_name}:1.0"
    iface.mkdir()
    bus_dir = iface / f"i2c-{bus_num}"
    bus_dir.mkdir()
    (bus_dir / "name").write_text(adapter_name)
    (devices_dir / f"i2c-{bus_num}").symlink_to(bus_dir)


def test_find_i2c_bus_by_serial_matches(tmp_path):
    devices_dir = tmp_path / "devices_path"
    devices_dir.mkdir()
    _make_usb_i2c_adapter(tmp_path, "usb1", "AB12", 7, "MCP2221 usb-i2c bridge", devices_dir)

    assert i2c_bus.find_i2c_bus_by_serial("AB12", devices_path=str(devices_dir)) == 7


def test_find_i2c_bus_by_serial_no_match_raises(tmp_path):
    devices_dir = tmp_path / "devices_path"
    devices_dir.mkdir()
    _make_usb_i2c_adapter(tmp_path, "usb1", "AB12", 7, "MCP2221 usb-i2c bridge", devices_dir)

    with pytest.raises(RuntimeError, match="No i2c adapter found with serial"):
        i2c_bus.find_i2c_bus_by_serial("DEADBEEF", devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_ambiguous_raises(tmp_path):
    devices_dir = tmp_path / "devices_path"
    devices_dir.mkdir()
    _make_usb_i2c_adapter(tmp_path, "usb1", "AB12", 1, "MCP2221 usb-i2c bridge", devices_dir)
    _make_usb_i2c_adapter(tmp_path, "usb2", "AB12", 2, "MCP2221 usb-i2c bridge", devices_dir)

    with pytest.raises(RuntimeError, match="Multiple i2c adapters have serial"):
        i2c_bus.find_i2c_bus_by_serial("AB12", devices_path=str(devices_dir))


def test_find_i2c_bus_by_serial_is_exact_not_substring(tmp_path):
    devices_dir = tmp_path / "devices_path"
    devices_dir.mkdir()
    _make_usb_i2c_adapter(tmp_path, "usb1", "AB1234", 7, "MCP2221 usb-i2c bridge", devices_dir)

    with pytest.raises(RuntimeError, match="No i2c adapter found with serial"):
        i2c_bus.find_i2c_bus_by_serial("AB12", devices_path=str(devices_dir))


def test_resolve_i2c_bus_serial_prefix_dispatches(monkeypatch):
    monkeypatch.setattr(i2c_bus, "find_i2c_bus_by_serial", lambda serial: 42 if serial == "AB12" else None)
    assert resolve_i2c_bus("serial:AB12") == 42
    assert resolve_i2c_bus("SERIAL:AB12") == 42  # prefix keyword is case-insensitive


def test_discover_extended_i2c_buses_wraps_enumeration(tmp_path):
    usb_device = tmp_path / "devices" / "usb1" / "1-1"
    usb_device.mkdir(parents=True)
    (usb_device / "serial").write_text("AB12")
    (usb_device / "idVendor").write_text("04d8")
    iface = usb_device / "1-1:1.0"
    iface.mkdir()
    bus_dir = iface / "i2c-7"
    bus_dir.mkdir()
    (bus_dir / "name").write_text("MCP2221 usb-i2c bridge")

    assert i2c_bus.discover_extended_i2c_buses(devices_path=str(iface)) == [
        {"bus_num": 7, "name": "MCP2221 usb-i2c bridge", "serial": "AB12"}
    ]


def test_discover_extended_i2c_buses_empty_when_missing_path():
    assert i2c_bus.discover_extended_i2c_buses(devices_path="/no/such/path") == []


def test_enumerate_i2c_adapters_sorts_by_bus_num(tmp_path, monkeypatch):
    devices_dir = tmp_path / "devices_path"
    devices_dir.mkdir()
    _make_usb_i2c_adapter(tmp_path, "usb1", "AB12", 9, "adapter nine", devices_dir)
    _make_usb_i2c_adapter(tmp_path, "usb2", "CD34", 3, "adapter three", devices_dir)

    # glob.glob order isn't guaranteed to match bus_num order; force the
    # out-of-order case explicitly rather than relying on filesystem quirks.
    real_glob = i2c_bus.glob.glob
    monkeypatch.setattr(i2c_bus.glob, "glob", lambda pattern: sorted(real_glob(pattern), reverse=True))

    adapters = i2c_bus._enumerate_i2c_adapters(devices_path=str(devices_dir))
    assert [a["bus_num"] for a in adapters] == [3, 9]


def test_discover_mcp2221_devices_lists_serials():
    hid_mod = types_module_with(
        enumerate=lambda vid, pid: [
            {"serial_number": "AAAA", "path": b"/dev/hidraw0"},
            {"serial_number": "BBBB", "path": b"/dev/hidraw1"},
        ]
    )
    with mock.patch.dict("sys.modules", {"hid": hid_mod}):
        devices = i2c_bus.discover_mcp2221_devices()
    assert devices == [{"serial": "AAAA", "path": b"/dev/hidraw0"}, {"serial": "BBBB", "path": b"/dev/hidraw1"}]


def test_discover_mcp2221_devices_sorts_by_serial():
    hid_mod = types_module_with(
        enumerate=lambda vid, pid: [
            {"serial_number": "BBBB", "path": b"/dev/hidraw1"},
            {"serial_number": "AAAA", "path": b"/dev/hidraw0"},
        ]
    )
    with mock.patch.dict("sys.modules", {"hid": hid_mod}):
        devices = i2c_bus.discover_mcp2221_devices()
    assert [d["serial"] for d in devices] == ["AAAA", "BBBB"]


def test_discover_mcp2221_devices_empty_without_hid_module():
    with mock.patch.dict("sys.modules", {"hid": None}):
        assert i2c_bus.discover_mcp2221_devices() == []


def test_discover_ft232h_devices_lists_urls():
    descriptor = types_module_with(sn="FT9", description="Single RS232-HS")

    class FakeFtdi:
        @staticmethod
        def list_devices(url):
            return [(descriptor, 1)]

    fake_mod = types_module_with(Ftdi=FakeFtdi)
    with mock.patch.dict("sys.modules", {"pyftdi.ftdi": fake_mod}):
        devices = i2c_bus.discover_ft232h_devices()
    assert devices == [{"url": "ftdi://ftdi:232h:FT9/1", "serial": "FT9", "description": "Single RS232-HS"}]


def test_discover_ft232h_devices_sorts_by_serial_and_handles_missing_serial():
    descriptor_b = types_module_with(sn="FTB", description="Second")
    descriptor_a = types_module_with(sn="FTA", description="First")
    descriptor_none = types_module_with(sn=None, description="No Serial")

    class FakeFtdi:
        @staticmethod
        def list_devices(url):
            return [(descriptor_b, 1), (descriptor_none, 1), (descriptor_a, 1)]

    fake_mod = types_module_with(Ftdi=FakeFtdi)
    with mock.patch.dict("sys.modules", {"pyftdi.ftdi": fake_mod}):
        devices = i2c_bus.discover_ft232h_devices()
    assert [d["serial"] for d in devices] == [None, "FTA", "FTB"]


def test_discover_ft232h_devices_empty_without_pyftdi():
    with mock.patch.dict("sys.modules", {"pyftdi.ftdi": None}):
        assert i2c_bus.discover_ft232h_devices() == []
