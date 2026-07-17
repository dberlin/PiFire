from common.defaults import default_settings


def test_platform_defaults_include_ft232h_block():
    platform = default_settings()["platform"]
    assert platform["ft232h"] == {"url": "1"}
