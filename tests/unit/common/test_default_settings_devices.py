from common.defaults import default_settings


def test_distance_defaults_include_sen0628_device_path():
    distance_defaults = default_settings()["platform"]["devices"]["distance"]
    assert distance_defaults["device"] == "/dev/ttyACM0"
