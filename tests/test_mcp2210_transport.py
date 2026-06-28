import pytest
from tests._fake_hid import FakeHID
from mcp2210 import MCP2210, MCP2210Error, MCP2210BusUnavailableError


def make_device():
    fake = FakeHID()
    dev = MCP2210(hid_device=fake)
    return dev, fake


def test_xfer_frames_report_with_report_id_and_padding():
    dev, fake = make_device()
    fake.queue(bytes([0x10, 0x00]))
    dev._xfer(bytes([0x10]))
    written = fake.written[-1]
    assert written[0] == 0x00            # report-ID prefix
    assert len(written) == 65            # 1 + 64
    assert written[1] == 0x10            # command byte
    assert all(b == 0 for b in written[2:])  # padded


def test_xfer_returns_64_byte_response():
    dev, fake = make_device()
    fake.queue(bytes([0x10, 0x00, 0xAB]))
    resp = dev._xfer(bytes([0x10]))
    assert len(resp) == 64
    assert resp[2] == 0xAB


def test_xfer_raises_on_bus_unavailable():
    dev, fake = make_device()
    fake.queue(bytes([0x10, 0xF7]))
    with pytest.raises(MCP2210BusUnavailableError):
        dev._xfer(bytes([0x10]))


def test_xfer_no_raise_returns_status_for_spi_layer():
    dev, fake = make_device()
    fake.queue(bytes([0x42, 0xF8]))
    resp = dev._xfer(bytes([0x42]), raise_on_status=False)
    assert resp[1] == 0xF8


def test_close_closes_handle():
    dev, fake = make_device()
    dev.close()
    assert fake.closed is True
