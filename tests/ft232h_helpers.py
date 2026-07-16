import contextlib
import types
from unittest import mock


class FakeGpio:
    """Stand-in for grillplat.ft232h.Ft232hGpio. Records per-pin direction/value
    so tests can assert what each relay pin was driven to."""

    def __init__(self):
        self.outputs = set()  # pins configured as outputs
        self.values = {}  # pin_name -> bool last written

    def setup_output(self, pin_name):
        # Mirror the real validation so bad-pin tests still exercise it.
        from grillplat.ft232h import Ft232hGpio

        if str(pin_name) not in Ft232hGpio.PIN_BITS:
            raise ValueError(f"Unknown or reserved FT232H GPIO pin {pin_name!r}")
        self.outputs.add(pin_name)
        self.values.setdefault(pin_name, None)

    def set(self, pin_name, high):
        self.values[pin_name] = bool(high)


@contextlib.contextmanager
def make_ft232h_platform(config):
    """Build a GrillPlatform with FT232H/EMC/I2C hardware faked.

    Yields (platform, harness); harness.gpio.values[pin] is the last bool
    written to that relay pin.
    """
    import grillplat.ft232h_relay as mod

    fake_gpio = FakeGpio()
    with (
        mock.patch.object(mod, "open_ft232h_gpio", return_value=fake_gpio),
        mock.patch.object(mod, "open_i2c_bus", return_value=mock.sentinel.ft232h_bus) as open_bus,
        mock.patch.object(mod, "EMC2101_LUT") as emc2101_cls,
        mock.patch.object(mod, "EMC2301") as emc2301_cls,
    ):
        platform = mod.GrillPlatform(config)
        harness = types.SimpleNamespace(
            gpio=fake_gpio, open_bus=open_bus, emc2101_cls=emc2101_cls, emc2301_cls=emc2301_cls
        )
        yield platform, harness
