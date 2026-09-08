import time
from dataclasses import replace

from common.clock_domain import ClockStamp, RuntimeClockDomain

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
        self.read_calls = []
        self.inference_policy_calls = []
        self.last_clock_stamp: ClockStamp | None = None
        self._clock_domain = RuntimeClockDomain.for_system(monotonic=time.monotonic, wall_time=time.time)

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

    def read_probes(
        self, *, excitation=None, monotonic_s=None, wall_s=None, clock_domain: RuntimeClockDomain | None = None
    ):
        self.read_calls.append(
            {
                "excitation": excitation,
                "monotonic_s": monotonic_s,
                "wall_s": wall_s,
                "clock_domain": clock_domain,
            }
        )
        stamp = (clock_domain or self._clock_domain).capture(monotonic_s=monotonic_s, wall_s=wall_s)
        self.last_clock_stamp = stamp
        if not self._script:
            item = {"primary": {"Grill": 0}, "food": {}, "aux": {}, "tr": {}}
        else:
            item = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1

        if self._health_script:
            health = {
                label: replace(report, observed_monotonic_s=stamp.observed_monotonic_s, clock_stamp=stamp)
                for label, report in self._health_script[min(self._health_i, len(self._health_script) - 1)].items()
            }
            self._health_i += 1
            for label, current in health.items():
                previous = self._health.get(
                    label,
                    ThermocoupleHealthReport.unmonitored(current.observed_monotonic_s),
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

    def invalidate_control_history(self) -> None:
        self._health = {label: replace(report, clock_stamp=None) for label, report in self._health.items()}
        self._health_transitions.clear()
        self.last_clock_stamp = None

    def set_thermocouple_inference_policy(self, policy):
        self.inference_policy_calls.append(policy)

    def update_units(self, x):
        pass
