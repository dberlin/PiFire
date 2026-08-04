from typing import Any


class FakeControllerRunner:
    def __init__(self, period=None, commands_fan=False, wants_async=False):
        self._script = []
        self._i = 0
        self.target = None
        self._period = period
        self.submitted_temps = []
        self._commands_fan = commands_fan
        self._wants_async = wants_async
        self.applied = []
        self.restored = []
        self.snapshot: dict[str, Any] | None = None
        # A single ordered log across restore_model()/set_output() calls, since
        # `restored` and `applied` are separate lists and so cannot express
        # relative ordering between a restore and the report that follows it.
        self.calls = []
        self.refits = 0
        self.refit_raises = None
        self.refit_verdict: object | None = None
        self.stops = 0
        # How many stop() calls had happened at each refit_from_cook() call, so
        # a test can hold the refit to after the worker was asked to stop
        # without reading the two counters as if they were ordered.
        self.stops_before_each_refit = []

    def script(self, outputs):
        self._script = list(outputs)
        self._i = 0
        return self

    def set_target(self, setpoint):
        self.target = setpoint

    def submit(self, temp):
        self.submitted_temps.append(temp)

    def reconfigure(self, settings, control, logger=None):
        return "Active"

    def control_period(self):
        return self._period

    def commands_fan(self):
        return self._commands_fan

    def wants_async(self):
        return self._wants_async

    def runs_async(self):
        return self._wants_async

    def stop(self):
        self.stops += 1

    def set_output(self, applied):
        self.applied.append(applied)
        self.calls.append(("apply", applied))

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored.append(snapshot)
        self.calls.append(("restore", snapshot))
        return snapshot is not None

    def refit_from_cook(self):
        self.refits += 1
        self.stops_before_each_refit.append(self.stops)
        if self.refit_raises:
            raise self.refit_raises
        return self.refit_verdict

    def controller_state(self):
        return {"fake": True}

    def latest(self):
        if not self._script:
            return None
        out = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return out
