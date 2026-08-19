"""MCP2210 USB-HID command/status constants and (de)serializers.

Byte offsets verified against the MCP2210 datasheet (DS20005286) and the
jeremyherbert/mcp2210-python implementation. These helpers operate on the
SETTINGS PAYLOAD ONLY (the bytes that follow the 4-byte command header), so
the same packers serve both the volatile (0x21/0x40) and NVRAM (0x60) paths.
"""

import struct

# --- command codes ---
CMD_GET_CHIP_STATUS = 0x10
CMD_SPI_CANCEL = 0x11
CMD_GET_INTERRUPT_COUNT = 0x12
CMD_GET_CHIP_SETTINGS = 0x20
CMD_SET_CHIP_SETTINGS = 0x21
CMD_SET_GPIO_VALUE = 0x30
CMD_GET_GPIO_VALUE = 0x31
CMD_SET_GPIO_DIRECTION = 0x32
CMD_GET_GPIO_DIRECTION = 0x33
CMD_SET_SPI_SETTINGS = 0x40
CMD_GET_SPI_SETTINGS = 0x41
CMD_SPI_TRANSFER = 0x42
CMD_READ_EEPROM = 0x50
CMD_WRITE_EEPROM = 0x51
CMD_SET_NVRAM = 0x60
CMD_GET_NVRAM = 0x61
CMD_SEND_PASSWORD = 0x70
CMD_REQUEST_BUS_RELEASE = 0x80

# --- response status (byte 1) ---
STATUS_OK = 0x00
STATUS_BUS_UNAVAILABLE = 0xF7
STATUS_IN_PROGRESS = 0xF8

# --- SPI engine sub-status (byte 3 of 0x42 response) ---
ENGINE_FINISHED = 0x10
ENGINE_STARTED = 0x20
ENGINE_NOT_FINISHED = 0x30

# --- pin designations (chip settings bytes 0-8 of payload) ---
PIN_GPIO = 0x00
PIN_CHIP_SELECT = 0x01
PIN_DEDICATED = 0x02

_SPI_FMT = "<IHHHHHHB"  # 17 bytes
_CHIP_FMT = "<BBBBBBBBBHHB"  # 14 bytes


def pack_spi_settings(bitrate, idle_cs, active_cs, cs_to_data, data_to_cs, between_bytes, transfer_size, mode):
    return struct.pack(
        _SPI_FMT, bitrate, idle_cs, active_cs, cs_to_data, data_to_cs, between_bytes, transfer_size, mode
    )


def unpack_spi_settings(payload):
    (bitrate, idle_cs, active_cs, cs_to_data, data_to_cs, between_bytes, transfer_size, mode) = struct.unpack_from(
        _SPI_FMT, payload, 0
    )
    return {
        "bitrate": bitrate,
        "idle_cs": idle_cs,
        "active_cs": active_cs,
        "cs_to_data": cs_to_data,
        "data_to_cs": data_to_cs,
        "between_bytes": between_bytes,
        "transfer_size": transfer_size,
        "mode": mode,
    }


def pack_chip_settings(designations, gpio_output, gpio_direction, other):
    if len(designations) != 9:
        raise ValueError("designations must have 9 entries (GP0-GP8)")
    return struct.pack(_CHIP_FMT, *designations, gpio_output, gpio_direction, other)


def unpack_chip_settings(payload):
    fields = struct.unpack_from(_CHIP_FMT, payload, 0)
    return {
        "designations": list(fields[0:9]),
        "gpio_output": fields[9],
        "gpio_direction": fields[10],
        "other": fields[11],
    }
