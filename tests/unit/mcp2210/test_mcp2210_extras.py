import struct
from tests._fake_hid import FakeHID
from mcp2210 import MCP2210, _protocol as p


def make():
    fake = FakeHID()
    return MCP2210(hid_device=fake), fake


def test_read_eeprom():
    dev, fake = make()
    fake.queue(bytes([p.CMD_READ_EEPROM, p.STATUS_OK, 0x05, 0x7E]))
    assert dev.read_eeprom(0x05) == 0x7E
    assert fake.last_report[0] == p.CMD_READ_EEPROM
    assert fake.last_report[1] == 0x05


def test_write_eeprom():
    dev, fake = make()
    fake.queue(bytes([p.CMD_WRITE_EEPROM, p.STATUS_OK]))
    dev.write_eeprom(0x10, 0xAB)
    rpt = fake.last_report
    assert rpt[0] == p.CMD_WRITE_EEPROM and rpt[1] == 0x10 and rpt[2] == 0xAB


def test_interrupt_count_reads_counter():
    dev, fake = make()
    fake.queue(bytes([p.CMD_GET_INTERRUPT_COUNT, p.STATUS_OK, 0, 0]) + struct.pack("<H", 1234))
    assert dev.interrupt_count() == 1234
    assert fake.last_report[1] == 0  # no reset


def test_chip_status_fields():
    dev, fake = make()
    fake.queue(bytes([p.CMD_GET_CHIP_STATUS, p.STATUS_OK, 0x01, 0x02, 0x03, 0x01]))
    st = dev.chip_status()
    assert st["bus_owner"] == 0x02
    assert st["password_attempts"] == 0x03
    assert st["password_guessed"] is True


def test_nvram_spi_settings_roundtrip_encoding():
    dev, fake = make()
    fake.queue(bytes([p.CMD_SET_NVRAM, p.STATUS_OK]))
    dev.set_nvram_spi_settings(bitrate=750000, mode=3, transfer_size=4)
    rpt = fake.last_report
    assert rpt[0] == p.CMD_SET_NVRAM
    assert rpt[1] == 0x10  # sub-command: SPI power-up settings
    got = p.unpack_spi_settings(rpt[4 : 4 + 17])
    assert got["bitrate"] == 750000 and got["mode"] == 3


def test_send_password_places_8_bytes():
    dev, fake = make()
    fake.queue(bytes([p.CMD_SEND_PASSWORD, p.STATUS_OK]))
    dev.send_password(bytes([1, 2, 3, 4, 5, 6, 7, 8]))
    rpt = fake.last_report
    assert rpt[0] == p.CMD_SEND_PASSWORD
    assert list(rpt[1:9]) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_generic_nvram_get_returns_payload_slice():
    dev, fake = make()
    fake.queue(bytes([p.CMD_GET_NVRAM, p.STATUS_OK, 0, 0]) + bytes(range(17)))
    payload = dev.get_nvram(0x10)
    assert payload[:17] == bytes(range(17))
    assert fake.last_report[1] == 0x10
