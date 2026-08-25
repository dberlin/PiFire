def _proto_settings():
    return {
        "modules": {"grillplat": "prototype", "dist": "prototype", "display": "none", "probes": "prototype"},
        "platform": {
            "devices": {},
            "buttonslevel": "HIGH",
            "outputs": {"auger": 14, "dc_fan": 26, "fan": 15, "igniter": 18, "power": 4, "pwm": 13},
            "inputs": {"selector": 17, "shutdown": 17},
            "dc_fan": False,
            "standalone": True,
        },
        "pelletlevel": {"empty": 22, "full": 4},
        "globals": {"units": "F", "debug_mode": False},
        "pwm": {"frequency": 100},
        "probe_settings": {"probe_map": {"probe_info": [], "probe_devices": []}},
    }


class _FakeLogger:
    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass


def test_build_devices_prototype_platform_headless():
    from controller.runtime.devices import build_devices

    devices, _errors = build_devices(_proto_settings(), errors=[], event_log=_FakeLogger(), control_log=_FakeLogger())
    assert devices.grill_platform is not None
    assert devices.probe_complex is not None
    assert devices.dist_device is not None
    from probes.thermocouple_inference import ThermocoupleInferencePolicy

    assert devices.probe_complex.thermocouple_inference_policy is ThermocoupleInferencePolicy.OBSERVE


def test_build_devices_disabled_probe_fallback_observes_with_legacy_settings(monkeypatch):
    import probes.main as probes_main
    from controller.runtime.devices import build_devices
    from probes.thermocouple_inference import ThermocoupleInferencePolicy

    calls = []

    class FailThenDisable:
        def __init__(self, _probe_map, _units, disable=False, inference_policy=None):
            calls.append((disable, inference_policy))
            if not disable:
                raise RuntimeError("force disabled probe fallback")
            self.thermocouple_inference_policy = ThermocoupleInferencePolicy(inference_policy)

        def get_errors(self):
            return []

        def get_device_info(self):
            return []

    monkeypatch.setattr(probes_main, "ProbesMain", FailThenDisable)

    devices, _errors = build_devices(_proto_settings(), errors=[], event_log=_FakeLogger(), control_log=_FakeLogger())

    assert calls == [(False, "observe"), (True, "observe")]
    assert devices.probe_complex.thermocouple_inference_policy is ThermocoupleInferencePolicy.OBSERVE


def test_build_display_prototype_none():
    from controller.runtime.devices import build_display

    settings = _proto_settings()
    settings["display"] = {"config": {"none": {}}}
    display, _errors = build_display(settings, errors=[], event_log=_FakeLogger(), control_log=_FakeLogger())
    assert display is not None
