# tests/test_mcp2210_integration.py
"""End-to-end: emulate the SPIDevice transaction pattern Adafruit libs use."""

from tests._fake_hid import FakeHID
from mcp2210 import MCP2210, _protocol as p


def _spi_ok():
	return bytes([p.CMD_SET_SPI_SETTINGS, p.STATUS_OK])


def _xfer_resp(rx, engine):
	return bytes([p.CMD_SPI_TRANSFER, p.STATUS_OK, len(rx), engine]) + bytes(rx)


def test_spidevice_style_transaction():
	fake = FakeHID()
	dev = MCP2210(hid_device=fake)
	spi = dev.spi
	cs = dev.digital_inout(0)

	# CS setup (init + drive high idle), then a 2-byte exchange, then CS toggling.
	fake.queue(
		# cs.switch_to_output(value=True):
		bytes([p.CMD_GET_CHIP_SETTINGS, p.STATUS_OK, 0, 0]) + p.pack_chip_settings([2] * 9, 0x0000, 0x01FF, 0),
		bytes([p.CMD_SET_CHIP_SETTINGS, p.STATUS_OK]),
		bytes([p.CMD_GET_GPIO_DIRECTION, p.STATUS_OK, 0, 0]) + bytes([0xFF, 0x01]),
		bytes([p.CMD_SET_GPIO_DIRECTION, p.STATUS_OK]),
		bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + bytes([0x00, 0x00]),
		bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]),  # high
		# cs low:
		bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + bytes([0x01, 0x00]),
		bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]),
		# spi exchange (settings + one transfer):
		_spi_ok(),
		_xfer_resp([0xDE, 0xAD], p.ENGINE_FINISHED),
		# cs high:
		bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + bytes([0x00, 0x00]),
		bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]),
	)

	cs.switch_to_output(value=True)
	assert spi.try_lock()
	spi.configure(baudrate=1_000_000, polarity=0, phase=0)
	cs.value = False
	inp = bytearray(2)
	spi.write_readinto(bytes([0x01, 0x02]), inp)
	cs.value = True
	spi.unlock()

	assert bytes(inp) == bytes([0xDE, 0xAD])
