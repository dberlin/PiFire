def _settings(**overrides):
    settings = {
        "modules": {"grillplat": "prototype", "dist": "prototype", "display": "none"},
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
        "thermocouple_health": {"inference_policy": "observe"},
        "display": {"config": {"none": {}}},
    }
    settings.update(overrides)
    return settings


class _RecordingLogger:
    """Fake event/control logger that records what was logged, instead of a
    silent no-op, so tests can assert *which* failure path actually ran."""

    def __init__(self):
        self.infos = []
        self.errors = []
        self.exceptions = []

    def info(self, msg, *a, **k):
        self.infos.append(msg)

    def error(self, msg, *a, **k):
        self.errors.append(msg)

    def exception(self, msg, *a, **k):
        self.exceptions.append(msg)
