from unittest import mock

import pytest


class FakeI2C:
    """In-memory stand-in for an Adafruit I2CDevice: stores a register map and
    honors the context-manager + write / write_then_readinto protocol the driver
    uses."""

    def __init__(self):
        self.registers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def write(self, data):
        # Register writes are two bytes: [register, value].
        if len(data) == 2:
            self.registers[data[0]] = data[1]

    def write_then_readinto(self, out_buf, in_buf):
        in_buf[0] = self.registers.get(out_buf[0], 0)


def _build_emc(seed=None, poles=2):
    """Construct an EMC2301 with a FakeI2C, optionally pre-seeding registers
    before __init__ runs. Returns (emc, fake)."""
    import grillplat.emc2301 as mod

    fake = FakeI2C()
    if seed:
        fake.registers.update(seed)
    with mock.patch.object(mod, "I2CDevice", return_value=fake):
        emc = mod.EMC2301(object(), address=0x2F, poles=poles)
    return emc, fake


def _seed_tach(count):
    """Return a register seed dict encoding a 13-bit tach `count` into the
    TACH high/low registers (inverse of the driver's ((msb<<8)|lsb)>>3)."""
    return {0x3E: (count >> 5) & 0xFF, 0x3F: (count << 3) & 0xF8}


def test_init_disables_timeout_and_continuous_watchdog():
    _, fake = _build_emc()
    # DIS_TO (bit6) set, WD_EN (bit5) clear.
    assert fake.registers[0x20] & 0x40 == 0x40
    assert fake.registers[0x20] & 0x20 == 0x00


def test_init_preserves_other_config_bits():
    # 0xAA has unrelated bits set; init must keep them, set DIS_TO, clear WD_EN.
    _, fake = _build_emc(seed={0x20: 0xAA})
    assert fake.registers[0x20] == 0xCA


def test_init_sets_26khz_base_divide_one_and_fan_off():
    _, fake = _build_emc()
    assert fake.registers[0x2D] == 0x00  # 26 kHz base
    assert fake.registers[0x31] == 0x01  # divide by 1
    assert fake.registers[0x30] == 0x00  # fan stopped


def test_manual_fan_speed_sets_fan_register():
    emc, fake = _build_emc()
    emc.manual_fan_speed = 100
    assert fake.registers[0x30] == 255
    emc.manual_fan_speed = 20
    assert fake.registers[0x30] == 51
    emc.manual_fan_speed = 0
    assert fake.registers[0x30] == 0


def test_manual_fan_speed_reads_back_percent():
    emc, fake = _build_emc()
    fake.registers[0x30] = 255
    assert emc.manual_fan_speed == 100.0
    fake.registers[0x30] = 51
    assert emc.manual_fan_speed == 20.0


def test_manual_fan_speed_out_of_range_raises():
    emc, _ = _build_emc()
    with pytest.raises(ValueError):
        emc.manual_fan_speed = 150
    with pytest.raises(ValueError):
        emc.manual_fan_speed = -1


def test_pwm_frequency_maps_to_nearest_base():
    emc, fake = _build_emc()
    emc.pwm_frequency = 25000  # nearest selectable base is 26 kHz
    assert fake.registers[0x2D] == 0x00
    assert fake.registers[0x31] == 0x01
    assert emc.pwm_frequency == 26000.0


def test_init_sets_edges_for_default_two_poles():
    _, fake = _build_emc()
    # EDGES bits [4:3] == poles-1 == 1 (0b01) for the default 2-pole fan.
    assert (fake.registers[0x32] >> 3) & 0x03 == 1


def test_init_sets_edges_for_four_poles_preserving_other_bits():
    # Seed 0x32 with RANGE=0b11 (bits 6:5) and update-time bits 0b101; init must
    # set EDGES to 0b11 (4 poles) while preserving RANGE and update-time bits.
    _, fake = _build_emc(seed={0x32: 0b0110_0101}, poles=4)
    assert (fake.registers[0x32] >> 3) & 0x03 == 3  # EDGES == poles-1 == 3
    assert (fake.registers[0x32] >> 5) & 0x03 == 3  # RANGE preserved (0b11)
    assert fake.registers[0x32] & 0x07 == 0b101  # update-time bits preserved


def test_init_rejects_invalid_poles():
    import grillplat.emc2301 as mod

    with mock.patch.object(mod, "I2CDevice", return_value=FakeI2C()):
        for bad in (0, 5):
            with pytest.raises(ValueError):
                mod.EMC2301(object(), address=0x2F, poles=bad)


def test_fan_speed_default_range_multiplier_two():
    # Power-on default Fan Config 1 0x2B has RANGE bits 0b01 -> m=2.
    seed = {0x32: 0x2B}
    seed.update(_seed_tach(1024))
    emc, _ = _build_emc(seed=seed)
    assert emc.fan_speed == round((2 * 3932160) / 1024, 2)


def test_fan_speed_reads_range_multiplier_one_live():
    # RANGE bits 0b00 -> m=1; the same count must yield half the RPM of the
    # m=2 case, proving the multiplier is read from the register, not assumed.
    seed = {0x32: 0x03}  # RANGE=00; EDGES/UDT bits are irrelevant to m
    seed.update(_seed_tach(1024))
    emc, _ = _build_emc(seed=seed)
    assert emc.fan_speed == round((1 * 3932160) / 1024, 2)


def test_fan_speed_stopped_fan_reports_zero_via_stall_bit():
    # Real registers captured from hardware with the fan stopped: the tach
    # count saturates near (but not exactly at) its max -- 0x3E=0xFF, 0x3F=0xF0
    # -> count 0x1FFE -- so a count-threshold check misses it and computes a
    # phantom ~960 RPM. The Fan Stall Status bit (0x25 b0) is the authoritative
    # stopped signal and must force 0.0.
    seed = {0x32: 0x2B, 0x3E: 0xFF, 0x3F: 0xF0, 0x25: 0x01}
    emc, _ = _build_emc(seed=seed)
    assert emc.fan_speed == 0.0


def test_fan_speed_running_reports_measured_rpm():
    # Real registers captured at full speed: count 0x0B80 (2944), RANGE m=2,
    # stall bit clear -> the measured RPM is reported.
    seed = {0x32: 0x2B, 0x3E: 0x5C, 0x3F: 0x00, 0x25: 0x00}
    emc, _ = _build_emc(seed=seed)
    assert emc.fan_speed == 2671.3


def test_fan_speed_zero_count_returns_zero():
    emc, _ = _build_emc(seed=_seed_tach(0))
    assert emc.fan_speed == 0.0
