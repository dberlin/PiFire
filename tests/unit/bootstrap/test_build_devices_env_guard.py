import pytest

from common.i2c_bus import I2CBusConfigError


def test_build_devices_rejects_board_forcing_env(monkeypatch):
    import controller.runtime.devices as devices

    monkeypatch.setenv("BLINKA_FT232H", "1")
    with pytest.raises(I2CBusConfigError):
        devices.build_devices({}, errors=[], event_log=None, control_log=None)
