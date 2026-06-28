"""Host-side driver for the Microchip MCP2210 USB-to-SPI bridge with
CircuitPython busio.SPI / digitalio compatible classes."""
from . import _protocol  # noqa: F401  (re-exported for tests / advanced use)

from .mcp2210 import (  # noqa: E402
    MCP2210,
    MCP2210Error,
    MCP2210BusUnavailableError,
    MCP2210InProgressError,
)
from .spi import SPI  # noqa: F401

__all__ = [
    "MCP2210",
    "MCP2210Error",
    "MCP2210BusUnavailableError",
    "MCP2210InProgressError",
    "SPI",
]
