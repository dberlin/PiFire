from unittest import mock


def _make_hopper(tof_mod, vl_mod, dev_pins=None, range_value=100):
    with mock.patch.object(vl_mod, "VL53L0X") as VL53L0X:
        VL53L0X.return_value.range = range_value
        hopper = vl_mod.HopperLevel(dev_pins or {}, empty=22, full=4)
    return hopper, VL53L0X


def _stop(hopper):
    hopper.sensor_thread_active = False
    hopper.sensor_thread.join(timeout=2)


def test_open_sensor_constructs_vl53l0x_at_resolved_address():
    import distance._tof_base as tof_mod
    import distance.vl53l0x as vl_mod

    with mock.patch.object(tof_mod, "open_i2c_bus", return_value=mock.sentinel.bus):
        hopper, VL53L0X = _make_hopper(tof_mod, vl_mod)
        try:
            assert VL53L0X.call_args.args == (mock.sentinel.bus,)
            assert VL53L0X.call_args.kwargs["address"] == 0x29
        finally:
            _stop(hopper)


def test_open_sensor_uses_configured_address():
    import distance._tof_base as tof_mod
    import distance.vl53l0x as vl_mod

    with mock.patch.object(tof_mod, "open_i2c_bus", return_value=mock.sentinel.bus):
        hopper, VL53L0X = _make_hopper(tof_mod, vl_mod, dev_pins={"distance": {"address": "0x2a"}})
        try:
            assert VL53L0X.call_args.kwargs["address"] == 0x2A
        finally:
            _stop(hopper)


def test_open_sensor_arms_the_drivers_own_read_timeout():
    """The VL53L0X driver bounds its own status-register polls, but only when
    io_timeout_s is greater than zero -- and zero is its default. Left unset,
    this sensor holds the shared I2C bus exactly as an unbounded poll of our
    own would."""
    import distance._tof_base as tof_mod
    import distance.vl53l0x as vl_mod

    with mock.patch.object(tof_mod, "open_i2c_bus", return_value=mock.sentinel.bus):
        hopper, VL53L0X = _make_hopper(tof_mod, vl_mod)
        try:
            io_timeout_s = VL53L0X.call_args.kwargs["io_timeout_s"]
            assert io_timeout_s > 0, "io_timeout_s of 0 disables the driver's own guard"
            # The same deadline the VL53L1X and VL53L4CD reads get: same family,
            # same 50ms timing budget, same shared bus to keep free.
            assert io_timeout_s == vl_mod.HopperLevel.read_deadline_seconds
        finally:
            _stop(hopper)


def test_read_distance_mm_returns_range_directly():
    import distance._tof_base as tof_mod
    import distance.vl53l0x as vl_mod

    with mock.patch.object(tof_mod, "open_i2c_bus", return_value=mock.sentinel.bus):
        hopper, _VL53L0X = _make_hopper(tof_mod, vl_mod, range_value=123)
        try:
            assert hopper._read_distance_mm() == 123
        finally:
            _stop(hopper)
