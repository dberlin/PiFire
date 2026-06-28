import struct
from mcp2210 import _protocol as p


def test_command_constants():
    assert p.CMD_SPI_TRANSFER == 0x42
    assert p.CMD_SET_SPI_SETTINGS == 0x40
    assert p.CMD_SET_CHIP_SETTINGS == 0x21
    assert p.STATUS_BUS_UNAVAILABLE == 0xF7
    assert p.ENGINE_FINISHED == 0x10


def test_pack_spi_settings_layout():
    payload = p.pack_spi_settings(
        bitrate=1_000_000, idle_cs=0xFFFF, active_cs=0xFFFF,
        cs_to_data=0, data_to_cs=0, between_bytes=0,
        transfer_size=3, mode=2,
    )
    assert len(payload) == 17
    assert struct.unpack_from("<I", payload, 0)[0] == 1_000_000   # bitrate at 0-3
    assert struct.unpack_from("<H", payload, 4)[0] == 0xFFFF      # idle cs at 4-5
    assert struct.unpack_from("<H", payload, 6)[0] == 0xFFFF      # active cs at 6-7
    assert struct.unpack_from("<H", payload, 14)[0] == 3          # transfer size at 14-15
    assert payload[16] == 2                                       # mode at byte 16


def test_spi_settings_roundtrip():
    payload = p.pack_spi_settings(
        bitrate=500000, idle_cs=0x01FF, active_cs=0x01FE,
        cs_to_data=1, data_to_cs=2, between_bytes=3,
        transfer_size=8, mode=1,
    )
    got = p.unpack_spi_settings(payload)
    assert got["bitrate"] == 500000
    assert got["active_cs"] == 0x01FE
    assert got["transfer_size"] == 8
    assert got["mode"] == 1


def test_pack_chip_settings_layout():
    payload = p.pack_chip_settings(
        designations=[1, 0, 0, 0, 0, 0, 0, 0, 0],
        gpio_output=0x01FF, gpio_direction=0x0000, other=0,
    )
    assert len(payload) == 14
    assert list(payload[0:9]) == [1, 0, 0, 0, 0, 0, 0, 0, 0]     # designations at 0-8
    assert struct.unpack_from("<H", payload, 9)[0] == 0x01FF     # output at 9-10
    assert struct.unpack_from("<H", payload, 11)[0] == 0x0000    # direction at 11-12
    assert payload[13] == 0                                      # other at byte 13
