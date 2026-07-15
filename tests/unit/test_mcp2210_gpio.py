# tests/test_mcp2210_gpio.py
import struct
import pytest
from tests._fake_hid import FakeHID
from mcp2210 import MCP2210, Pin, DigitalInOut, _protocol as p


def make():
	fake = FakeHID()
	return MCP2210(hid_device=fake), fake


def _chip_settings_resp(designations, output, direction, other=0):
	payload = p.pack_chip_settings(designations, output, direction, other)
	return bytes([p.CMD_GET_CHIP_SETTINGS, p.STATUS_OK, 0, 0]) + payload


def test_set_gpio_value_writes_mask_at_offset_4():
	dev, fake = make()
	fake.queue(bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]))
	dev.set_gpio_value(0x0102)
	report = fake.last_report
	assert report[0] == p.CMD_SET_GPIO_VALUE
	assert struct.unpack_from('<H', report, 4)[0] == 0x0102


def test_get_gpio_value_reads_mask():
	dev, fake = make()
	fake.queue(bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + struct.pack('<H', 0x0011))
	assert dev.get_gpio_value() == 0x0011


def test_pin_init_output_sets_designation_and_direction():
	dev, fake = make()
	# init() reads chip settings, rewrites them as GPIO, then sets direction.
	fake.queue(
		_chip_settings_resp([2, 2, 2, 2, 2, 2, 2, 2, 2], 0x0000, 0x01FF),  # 0x20 get
		bytes([p.CMD_SET_CHIP_SETTINGS, p.STATUS_OK]),  # 0x21 set
		bytes([p.CMD_GET_GPIO_DIRECTION, p.STATUS_OK, 0, 0]) + struct.pack('<H', 0x01FF),
		bytes([p.CMD_SET_GPIO_DIRECTION, p.STATUS_OK]),
	)
	pin = dev.get_pin(0)
	pin.init(mode=Pin.OUT)
	# GP0 designation must now be GPIO (0) in the 0x21 write.
	set_report = fake.written[1][1:]
	assert set_report[0] == p.CMD_SET_CHIP_SETTINGS
	assert set_report[4] == p.PIN_GPIO  # GP0 designation byte
	# GP0 direction bit cleared (output) in the 0x32 write.
	dir_report = fake.written[3][1:]
	assert dir_report[0] == p.CMD_SET_GPIO_DIRECTION
	assert struct.unpack_from('<H', dir_report, 4)[0] & 0x01 == 0


def test_pin_value_write_sets_single_bit():
	dev, fake = make()
	pin = dev.get_pin(2)
	# value(1): read current outputs, set bit 2, write back.
	fake.queue(
		bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + struct.pack('<H', 0x0000),
		bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]),
	)
	pin.value(1)
	set_report = fake.written[1][1:]
	assert struct.unpack_from('<H', set_report, 4)[0] == 0x0004  # bit 2


def test_pull_raises_not_implemented():
	dev, fake = make()
	pin = dev.get_pin(0)
	with pytest.raises(NotImplementedError):
		pin.init(mode=Pin.IN, pull=Pin.PULL_UP)


def test_digitalinout_switch_to_output_and_set_value():
	dev, fake = make()
	cs = dev.digital_inout(3)
	assert isinstance(cs, DigitalInOut)
	fake.queue(
		_chip_settings_resp([2] * 9, 0x0000, 0x01FF),
		bytes([p.CMD_SET_CHIP_SETTINGS, p.STATUS_OK]),
		bytes([p.CMD_GET_GPIO_DIRECTION, p.STATUS_OK, 0, 0]) + struct.pack('<H', 0x01FF),
		bytes([p.CMD_SET_GPIO_DIRECTION, p.STATUS_OK]),
		bytes([p.CMD_GET_GPIO_VALUE, p.STATUS_OK, 0, 0]) + struct.pack('<H', 0x0000),
		bytes([p.CMD_SET_GPIO_VALUE, p.STATUS_OK]),
	)
	cs.switch_to_output(value=True)
	last = fake.last_report
	assert last[0] == p.CMD_SET_GPIO_VALUE
	assert struct.unpack_from('<H', last, 4)[0] == 0x0008  # bit 3 high
