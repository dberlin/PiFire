import pytest

from app import app as flask_app


@pytest.fixture
def client(ds):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_probe_modules_lists_every_manifest_module(ds, client):
    resp = client.get("/api/probe_modules")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["result"] == "OK"
    modules = body["data"]["modules"]
    # 18 probe modules ship in wizard/wizard_manifest.json (verified 2026-07-26).
    assert len(modules) == 18
    assert "ds18b20" in modules and "virtual_average" in modules
    # The card components read exactly these keys.
    ds18b20 = modules["ds18b20"]
    assert ds18b20["friendly_name"]
    assert ds18b20["filename"] == "ds18b20"
    assert ds18b20["device_specific"]["ports"] == ["DS0"]
    assert isinstance(ds18b20["device_specific"]["config"], list)


def test_probe_modules_flags_which_modules_need_the_wizard(ds, client):
    body = client.get("/api/probe_modules").get_json()
    req = body["data"]["requires_install"]
    # The six dep-free modules, verified against the manifest 2026-07-26.
    for free in (
        "max31865",
        "prototype",
        "virtual_average",
        "virtual_highest",
        "virtual_lowest",
        "virtual_median",
    ):
        assert req[free] is False, free
    # ds18b20 has a py_dependency AND a command_list entry; bt_ibbq has all three.
    assert req["ds18b20"] is True
    assert req["bt_ibbq"] is True
    assert req["thermoworks_cloud"] is True
    assert set(req) == set(body["data"]["modules"])
