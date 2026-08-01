"""Typed I2C bus configuration.

A bus is one of four kinds, and each kind addresses hardware in its own
namespace: `basic` uses the board's own pins, `kernel` a /dev/i2c-N adapter,
`ft232h` a pyftdi URL over libusb, `mcp2221` a device serial over USB HID.
Carrying that as a `(kind, selector)` string pair gave one field five meanings
and no way to say "this field does not apply", so a kernel adapter name could
sit in a config whose kind was `ft232h`, naming a bus pyftdi can never open.

Here the kind IS the class. `BasicBus` has no field to hold a stale selector,
and the three `KernelBus` variants make "a kernel bus is addressed exactly one
way" a property of the type rather than a rule someone has to remember to
check.

Discovery imports are deferred: common.i2c_bus imports this module, so a
top-level import back would be a cycle.
"""

from dataclasses import dataclass
from typing import ClassVar

BUS_KINDS = ("basic", "kernel", "ft232h", "mcp2221")


class I2CBusConfigError(Exception):
    """Raised for an I2C bus configuration that cannot be built or opened."""


@dataclass(frozen=True)
class I2CBus:
    """Base for the four bus kinds. Frozen so an instance can key the
    process-wide bus cache: two devices naming the same hardware then share one
    open bus, which is what stops a single USB adapter being opened twice."""

    kind: ClassVar[str] = ""

    def to_config(self):
        """The stored mapping form: `kind` plus exactly the live field."""
        raise NotImplementedError

    def describe(self):
        """One human phrase naming the hardware, for logs and error messages."""
        raise NotImplementedError


@dataclass(frozen=True)
class BasicBus(I2CBus):
    kind: ClassVar[str] = "basic"

    def to_config(self):
        return {"kind": "basic"}

    def describe(self):
        return "the board's integrated I2C bus"


@dataclass(frozen=True)
class KernelBus(I2CBus):
    """A kernel i2c-dev adapter, addressed exactly one of three ways."""

    kind: ClassVar[str] = "kernel"

    def resolve_bus_num(self):
        """The /dev/i2c-N number this bus names, discovering it if needed."""
        raise NotImplementedError


@dataclass(frozen=True)
class KernelBusNumber(KernelBus):
    bus_num: int

    def resolve_bus_num(self):
        return self.bus_num

    def to_config(self):
        return {"kind": "kernel", "bus_num": self.bus_num}

    def describe(self):
        return f"/dev/i2c-{self.bus_num}"


@dataclass(frozen=True)
class KernelAdapterName(KernelBus):
    adapter: str

    def resolve_bus_num(self):
        from common.i2c_bus import find_i2c_bus

        return find_i2c_bus(self.adapter)

    def to_config(self):
        return {"kind": "kernel", "adapter": self.adapter}

    def describe(self):
        return f"the kernel I2C adapter named {self.adapter!r}"


@dataclass(frozen=True)
class KernelSerialMatch(KernelBus):
    serial: str

    def resolve_bus_num(self):
        from common.i2c_bus import find_i2c_bus_by_serial

        return find_i2c_bus_by_serial(self.serial)

    def to_config(self):
        return {"kind": "kernel", "serial": self.serial}

    def describe(self):
        return f"the kernel I2C adapter with USB serial {self.serial!r}"


@dataclass(frozen=True)
class FT232HBus(I2CBus):
    kind: ClassVar[str] = "ft232h"
    url: str = ""

    def __post_init__(self):
        # '' and '1' both mean "the first FT232H" (grillplat/ft232h.py), so they
        # have to compare equal -- an unequal pair keys two cache entries and
        # opens one physical adapter twice.
        if self.url == "1":
            object.__setattr__(self, "url", "")

    def to_config(self):
        return {"kind": "ft232h", "url": self.url}

    def describe(self):
        return f"the FT232H at {self.url!r}" if self.url else "the first FT232H found"


@dataclass(frozen=True)
class MCP2221Bus(I2CBus):
    kind: ClassVar[str] = "mcp2221"
    serial: str = ""

    def to_config(self):
        return {"kind": "mcp2221", "serial": self.serial}

    def describe(self):
        return f"the MCP2221 with serial {self.serial!r}" if self.serial else "the first MCP2221 found"


_KERNEL_VARIANTS = {
    "bus_num": KernelBusNumber,
    "adapter": KernelAdapterName,
    "serial": KernelSerialMatch,
}


def parse_i2c_bus(data):
    """Build the I2CBus a stored mapping names.

    Raises rather than falling back to BasicBus: a bus we cannot name is a
    configuration the operator has to fix, and quietly opening the board's own
    pins instead would drive a grill from the wrong hardware.
    """
    if isinstance(data, I2CBus):
        return data
    if not isinstance(data, dict):
        raise I2CBusConfigError(f"An I2C bus configuration must be a mapping, got {data!r}.")

    kind = str(data.get("kind", "")).strip().lower()
    fields = set(data) - {"kind"}

    if kind == "basic":
        if fields:
            raise I2CBusConfigError(f"A basic I2C bus takes no other fields; got {sorted(fields)}.")
        return BasicBus()

    if kind == "kernel":
        unknown = sorted(fields - set(_KERNEL_VARIANTS))
        if unknown:
            raise I2CBusConfigError(f"A kernel I2C bus has no field(s) {unknown}.")
        chosen = sorted(fields & set(_KERNEL_VARIANTS))
        if len(chosen) != 1:
            raise I2CBusConfigError(
                "A kernel I2C bus is addressed by exactly one of bus_num, adapter or serial; "
                f"got {chosen if chosen else 'none of them'}."
            )
        name = chosen[0]
        value = data[name]
        if name == "bus_num":
            try:
                return KernelBusNumber(bus_num=int(value))
            except TypeError, ValueError:
                raise I2CBusConfigError(f"A kernel bus_num must be a number, got {value!r}.") from None
        text = str(value).strip()
        if not text:
            raise I2CBusConfigError(f"A kernel I2C bus addressed by {name} needs a value.")
        return _KERNEL_VARIANTS[name](**{name: text})

    if kind == "ft232h":
        unknown = sorted(fields - {"url"})
        if unknown:
            raise I2CBusConfigError(f"An ft232h I2C bus has no field(s) {unknown}.")
        return FT232HBus(url=str(data.get("url", "")).strip())

    if kind == "mcp2221":
        unknown = sorted(fields - {"serial"})
        if unknown:
            raise I2CBusConfigError(f"An mcp2221 I2C bus has no field(s) {unknown}.")
        return MCP2221Bus(serial=str(data.get("serial", "")).strip())

    raise I2CBusConfigError(f"Unknown I2C bus kind {kind!r}; expected one of {', '.join(BUS_KINDS)}.")
