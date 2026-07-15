import pytest

import distance.sen0628 as sen_mod


class _FakeSerial:
    """Minimal stand-in for pyserial's Serial: records written bytes and
    serves reads from a pre-loaded byte queue."""

    def __init__(self, rx_bytes=b""):
        self.written = bytearray()
        self._rx = bytearray(rx_bytes)

    def write(self, data):
        self.written += data

    def read(self, length):
        chunk = self._rx[:length]
        del self._rx[:length]
        return bytes(chunk)

    def reset_input_buffer(self):
        pass


def _success_packet(cmd, data=b""):
    """Build a raw response frame (status, cmd, len_lo, len_hi, *data) as
    the sensor would send it -- the mirror of sen0628._recv_packet's
    parsing."""
    length = len(data)
    return bytes([sen_mod.STATUS_SUCCESS, cmd, length & 0xFF, (length >> 8) & 0xFF]) + data


def test_build_packet_frames_fixed_point_request():
    pkt = sen_mod._build_packet(sen_mod.CMD_FIXED_POINT, args=[3, 4])
    assert pkt == bytes([0, 3, sen_mod.CMD_FIXED_POINT, 3, 4])


def test_build_packet_frames_setmode_request():
    pkt = sen_mod._build_packet(sen_mod.CMD_SETMODE, args=[0, 0, 0, 8])
    assert pkt == bytes([0, 5, sen_mod.CMD_SETMODE, 0, 0, 0, 8])


def test_recv_packet_parses_success_response():
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_FIXED_POINT, data=bytes([0x2C, 0x01])))  # 300mm
    data = sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT)
    assert data == [0x2C, 0x01]


def test_recv_packet_returns_none_on_failure_status():
    frame = bytes([sen_mod.STATUS_FAILED, sen_mod.CMD_FIXED_POINT, 2, 0, 0x00, 0x00])
    ser = _FakeSerial(rx_bytes=frame)
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT) is None


def test_recv_packet_returns_none_on_timeout():
    ser = _FakeSerial(rx_bytes=b"")
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT, timeout=0.05) is None


def test_recv_packet_returns_none_on_command_mismatch():
    frame = bytes([sen_mod.STATUS_SUCCESS, sen_mod.CMD_SETMODE, 0, 0])
    ser = _FakeSerial(rx_bytes=frame)
    assert sen_mod._recv_packet(ser, sen_mod.CMD_FIXED_POINT) is None


def test_open_sensor_sends_setmode_and_succeeds_on_first_ack(monkeypatch):
    monkeypatch.setattr(sen_mod.time, "sleep", lambda seconds: None)
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_SETMODE))
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper._open_sensor(ser)
    assert hopper.ser is ser
    assert ser.written == sen_mod._SYNC_BYTE + sen_mod._build_packet(sen_mod.CMD_SETMODE, args=[0, 0, 0, 8])


def test_open_sensor_raises_after_repeated_failure(monkeypatch):
    monkeypatch.setattr(sen_mod.time, "sleep", lambda seconds: None)
    ser = _FakeSerial(rx_bytes=b"")  # never responds
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper._setmode_recv_timeout = 0.02  # keep the 3 retries fast in this test
    with pytest.raises(RuntimeError):
        hopper._open_sensor(ser)


def test_read_distance_mm_averages_center_block(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    readings = {(3, 3): 100, (3, 4): 200, (4, 3): 100, (4, 4): 200}
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: readings[(x, y)])
    assert hopper._read_distance_mm() == 150


def test_read_distance_mm_ignores_invalid_points(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    readings = {(3, 3): 0, (3, 4): 200, (4, 3): 0, (4, 4): 200}
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: readings[(x, y)])
    assert hopper._read_distance_mm() == 200


def test_read_distance_mm_returns_zero_when_all_invalid(monkeypatch):
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    monkeypatch.setattr(hopper, "_get_fixed_point_mm", lambda x, y: 0)
    assert hopper._read_distance_mm() == 0


def test_get_fixed_point_mm_sends_request_and_parses_response():
    ser = _FakeSerial(rx_bytes=_success_packet(sen_mod.CMD_FIXED_POINT, data=bytes([0x2C, 0x01])))  # 300mm
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper.ser = ser
    assert hopper._get_fixed_point_mm(3, 3) == 300
    assert ser.written == sen_mod._SYNC_BYTE + sen_mod._build_packet(sen_mod.CMD_FIXED_POINT, args=[3, 3])


def test_get_fixed_point_mm_returns_zero_on_no_response():
    ser = _FakeSerial(rx_bytes=b"")
    hopper = sen_mod.HopperLevel.__new__(sen_mod.HopperLevel)
    hopper.ser = ser
    hopper._read_recv_timeout = 0.02
    assert hopper._get_fixed_point_mm(3, 3) == 0
