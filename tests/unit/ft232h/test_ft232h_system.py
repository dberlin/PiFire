from tests.ft232h_helpers import make_ft232h_platform


def _config():
    return {
        "outputs": {"power": "C0", "igniter": "C1", "auger": "C2", "fan": "C3"},
        "fan_controller": {"chip": "none"},
        "triggerlevel": "LOW",
    }


def test_supported_commands_lists_expected():
    with make_ft232h_platform(_config()) as (plat, harness):
        result = plat.supported_commands([])
        assert result["result"] == "OK"
        cmds = result["data"]["supported_cmds"]
        assert "check_alive" in cmds
        assert "hardware_info" in cmds


def test_check_alive_ok():
    with make_ft232h_platform(_config()) as (plat, harness):
        assert plat.check_alive([])["result"] == "OK"


def test_check_throttled_reports_not_throttled():
    with make_ft232h_platform(_config()) as (plat, harness):
        data = plat.check_throttled([])["data"]
        assert data["cpu_under_voltage"] is False
        assert data["cpu_throttled"] is False


def test_check_cpu_temp_returns_float():
    with make_ft232h_platform(_config()) as (plat, harness):
        result = plat.check_cpu_temp([])
        assert isinstance(result["data"]["cpu_temp"], float)
