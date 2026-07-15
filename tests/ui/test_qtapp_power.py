from display.qtapp import bind_backend_power


class FakeSignal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self):
        for cb in self._cbs:
            cb()


class FakeBackend:
    def __init__(self):
        self.asleep = False
        self.asleepChanged = FakeSignal()


class FakeController:
    def __init__(self):
        self.calls = []

    def set_output_power(self, on):
        self.calls.append(on)


def test_applies_once_on_bind_awake():
    b, c = FakeBackend(), FakeController()
    bind_backend_power(b, c)
    assert c.calls == [True]  # not asleep -> power on


def test_sleep_then_wake_toggles_power():
    b, c = FakeBackend(), FakeController()
    bind_backend_power(b, c)
    b.asleep = True
    b.asleepChanged.emit()
    b.asleep = False
    b.asleepChanged.emit()
    assert c.calls == [True, False, True]
