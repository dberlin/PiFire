"""Characterization for TransitionKind (controller/runtime/transitions.py):
pins str-interop of the enum plus the existing kind-dispatch behavior in
request_transition when called with enum members instead of plain strings.
Reuses the fake-ctx pattern from test_request_transition.py.
"""

from controller.runtime.transitions import request_transition, TransitionKind


class _FakeDisplay:
    def __init__(self):
        self.pushed = []

    def push(self, cmd):
        self.pushed.append(cmd)


class _FakeStore:
    def __init__(self, control):
        self._control = control
        self.writes = []
        self.flushed = 0
        self._display = _FakeDisplay()

    def execute_control_writes(self):
        self.flushed += 1

    def read_control(self):
        return self._control

    def write_control(self, control, kind, origin=None):
        self._control = control
        self.writes.append((kind, origin))

    def display_commands(self):
        return self._display


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, name):
        self.sent.append(name)


class _FakeCtx:
    def __init__(self, store, notifier):
        self.store = store
        self.notifications = notifier


def _ctx(control):
    store = _FakeStore(control)
    notifier = _FakeNotifier()
    return _FakeCtx(store, notifier), store, notifier


def _base_control(**over):
    control = {
        "mode": "Smoke",
        "updated": False,
        "primary_setpoint": 0,
        "safety": {"reigniteretries": 2, "reignitelaststate": ""},
    }
    control.update(over)
    return control


def test_transitionkind_members_are_their_strings():
    assert TransitionKind.NATURAL == "natural"
    assert TransitionKind.SAFETY == "safety"
    assert TransitionKind.TERMINAL == "terminal"
    assert str(TransitionKind.NATURAL) == "natural"


def test_natural_enum_kind_yields_when_updated():
    control = _base_control(mode="Smoke", updated=False, primary_setpoint=0)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Hold", kind=TransitionKind.NATURAL, setpoint=225)
    assert out["mode"] == "Hold"
    assert out["primary_setpoint"] == 225
    assert out["updated"] is True


def test_safety_enum_kind_applies_authoritatively():
    control = _base_control(mode="Smoke", primary_setpoint=225)
    ctx, store, notifier = _ctx(control)
    out = request_transition(
        ctx, control, "Error", kind=TransitionKind.SAFETY, display=("text", "ERROR"), notify="Grill_Error_02"
    )
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert store._display.pushed == [("text", "ERROR")]
    assert notifier.sent == ["Grill_Error_02"]


def test_terminal_enum_kind_applies_authoritatively():
    control = _base_control(mode="Shutdown", primary_setpoint=225)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Stop", kind=TransitionKind.TERMINAL)
    assert out["mode"] == "Stop"
    assert out["updated"] is True
