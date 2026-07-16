from common.common import default_settings


def test_triggerlevel_defaults_to_active_high():
    assert default_settings()["platform"]["triggerlevel"] == "HIGH"
