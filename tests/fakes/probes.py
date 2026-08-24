from probes.thermocouple_health import ThermocoupleHealthReport, ThermocoupleHealthTransition


class FakeProbes:
    def __init__(self):
        self._script = []
        self._i = 0
        self._info = {}
        self._errors = []
        self._health_script = []
        self._health_i = 0
        self._health = {}
        self._health_transitions = []
        self.update_probe_map_calls = []

    def script(self, items):
        norm = []
        for it in items:
            if isinstance(it, dict):
                norm.append(it)
            else:
                norm.append({"primary": {"Grill": it}, "food": {}, "aux": {}, "tr": {}})
        self._script = norm
        self._i = 0
        return self

    def script_health(self, reports):
        self._health_script = list(reports)
        self._health_i = 0
        self._health = {}
        self._health_transitions = []
        return self

    def read_probes(self):
        if not self._script:
            item = {"primary": {"Grill": 0}, "food": {}, "aux": {}, "tr": {}}
        else:
            item = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1

        if self._health_script:
            health = self._health_script[min(self._health_i, len(self._health_script) - 1)]
            self._health_i += 1
            for label, current in health.items():
                previous = self._health.get(
                    label,
                    ThermocoupleHealthReport.unmonitored(current.observed_at),
                )
                if (previous.state, previous.faults) != (current.state, current.faults):
                    self._health_transitions.append(ThermocoupleHealthTransition(label, previous, current))
            self._health = dict(health)

        return item

    def get_thermocouple_health(self):
        return dict(self._health)

    def consume_thermocouple_health_transitions(self):
        transitions = tuple(self._health_transitions)
        self._health_transitions.clear()
        return transitions

    def get_device_info(self):
        return self._info

    def get_errors(self):
        return self._errors

    def update_probe_profiles(self, x):
        pass

    def update_probe_map(self, probe_map):
        self.update_probe_map_calls.append(probe_map)
        return []

    def update_units(self, x):
        pass
