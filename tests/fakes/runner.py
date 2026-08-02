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
        self.snapshot = None
        # A single ordered log across restore_model()/set_output() calls, since
        # `restored` and `applied` are separate lists and so cannot express
        # relative ordering between a restore and the report that follows it.
        self.calls = []

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

    def stop(self):
        pass

    def set_output(self, applied):
        self.applied.append(applied)
        self.calls.append(("apply", applied))

    def get_model_snapshot(self):
        return self.snapshot

    def restore_model(self, snapshot):
        self.restored.append(snapshot)
        self.calls.append(("restore", snapshot))
        return snapshot is not None

    def controller_state(self):
        return {"fake": True}

    def latest(self):
        if not self._script:
            return None
        out = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return out
