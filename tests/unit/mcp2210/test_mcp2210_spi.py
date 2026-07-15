import pytest
from tests._fake_hid import FakeHID
from mcp2210 import MCP2210, MCP2210Error, _protocol as p


def make():
    fake = FakeHID()
    return MCP2210(hid_device=fake), fake


def _spi_ok():
    # 0x40 settings-set acknowledgement
    return bytes([p.CMD_SET_SPI_SETTINGS, p.STATUS_OK])


def _xfer_resp(rx_bytes, engine):
    # 0x42 response: status ok, rx size, engine sub-status, then rx data
    r = bytes([p.CMD_SPI_TRANSFER, p.STATUS_OK, len(rx_bytes), engine]) + bytes(rx_bytes)
    return r


def test_configure_requires_lock():
    dev, _ = make()
    spi = dev.spi
    with pytest.raises(RuntimeError):
        spi.configure(baudrate=100000)


def test_configure_rejects_non_8_bits():
    dev, _ = make()
    spi = dev.spi
    spi.try_lock()
    with pytest.raises(ValueError):
        spi.configure(baudrate=100000, bits=16)


def test_configure_encodes_mode_from_polarity_phase():
    dev, fake = make()
    spi = dev.spi
    spi.try_lock()
    spi.configure(baudrate=250000, polarity=1, phase=0)  # -> mode 2
    fake.queue(_spi_ok(), _xfer_resp([0xAA], p.ENGINE_FINISHED))
    spi.write(bytes([0x55]))
    # First written report is the 0x40 settings; inspect its payload at offset 4.
    settings_report = fake.written[0][1:]  # strip report-ID
    payload = settings_report[4 : 4 + 17]
    got = p.unpack_spi_settings(payload)
    assert got["bitrate"] == 250000
    assert got["mode"] == 2
    assert got["transfer_size"] == 1


def test_single_transfer_returns_rx():
    dev, fake = make()
    spi = dev.spi
    spi.try_lock()
    spi.configure(baudrate=100000)
    fake.queue(_spi_ok(), _xfer_resp([0x01, 0x02, 0x03], p.ENGINE_FINISHED))
    rx = bytearray(3)
    spi.readinto(rx)
    assert bytes(rx) == bytes([0x01, 0x02, 0x03])
    # Outgoing 0x42 carried write_value padding (0x00) and chunk length 3.
    xfer_report = fake.written[1][1:]
    assert xfer_report[0] == p.CMD_SPI_TRANSFER
    assert xfer_report[1] == 3
    assert list(xfer_report[4:7]) == [0, 0, 0]


def test_transfer_chunks_over_60_bytes():
    dev, fake = make()
    spi = dev.spi
    spi.try_lock()
    spi.configure(baudrate=100000)
    # 100 bytes -> chunk 60 (started/no-data), then chunk 40 returning 60+40 rx.
    fake.queue(
        _spi_ok(),
        _xfer_resp([], p.ENGINE_STARTED),  # first 60 sent, no rx yet
        _xfer_resp(list(range(60)), p.ENGINE_NOT_FINISHED),
        _xfer_resp(list(range(60, 100)), p.ENGINE_FINISHED),
    )
    out = bytes(range(100))
    inp = bytearray(100)
    spi.write_readinto(out, inp)
    assert bytes(inp) == bytes(range(100))
    # Two 0x42 chunks were sent with the data payload.
    chunk1 = fake.written[1][1:]
    chunk2 = fake.written[2][1:]
    assert chunk1[1] == 60 and list(chunk1[4:64]) == list(range(60))
    assert chunk2[1] == 40 and list(chunk2[4:44]) == list(range(60, 100))


def test_transfer_retries_on_in_progress():
    dev, fake = make()
    spi = dev.spi
    spi.try_lock()
    spi.configure(baudrate=100000)
    fake.queue(
        _spi_ok(),
        bytes([p.CMD_SPI_TRANSFER, p.STATUS_IN_PROGRESS]),  # busy, retry same chunk
        _xfer_resp([0x09], p.ENGINE_FINISHED),
    )
    rx = bytearray(1)
    spi.readinto(rx)
    assert rx[0] == 0x09


def test_frequency_reports_configured_baudrate():
    dev, _ = make()
    spi = dev.spi
    spi.try_lock()
    spi.configure(baudrate=2_000_000)
    assert spi.frequency == 2_000_000


def test_poll_loop_bounded_when_engine_never_finishes():
    """After all TX data is sent the poll loop must raise instead of hanging.

    The cap is lowered to 3 so we only need a handful of queued responses.
    We queue:
      - one 0x40 settings-OK
      - one 0x42 with ENGINE_NOT_FINISHED (carries the single TX byte; now
        idx >= total, poll phase begins)
      - three more 0x42 STATUS_OK / ENGINE_NOT_FINISHED responses (each burns
        one poll_retries increment; the 3rd push exceeds the cap of 3)

    Without the fix the loop would spin until FakeHID returns all-zero reports,
    which would raise a command-echo mismatch instead of MCP2210Error.  With
    the fix it raises MCP2210Error("SPI transfer never completed") at poll
    iteration 4.
    """
    dev, fake = make()
    dev._SPI_RETRY_MAX = 3  # lower cap so the test terminates instantly

    fake.queue(
        _spi_ok(),  # 0x40 settings-OK
        _xfer_resp([0xAB], p.ENGINE_NOT_FINISHED),  # chunk sent, idx>=total
        _xfer_resp([], p.ENGINE_NOT_FINISHED),  # poll 1
        _xfer_resp([], p.ENGINE_NOT_FINISHED),  # poll 2
        _xfer_resp([], p.ENGINE_NOT_FINISHED),  # poll 3 -> exceeds cap
    )

    with pytest.raises(MCP2210Error, match="never completed"):
        dev.spi_exchange(b"\x55", bitrate=100000, mode=0)
