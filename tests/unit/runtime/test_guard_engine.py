"""Unit tests for the phased guard-engine (Edge / GUARDS / evaluate_phase) in
controller/runtime/transitions.py.

Uses a fake mode_obj + fake ctx (store + notifications) and monkeypatches GUARDS
to inject test edges. Asserts evaluate_phase fires request_transition with the
edge's params (and reignite_from=mode.name when reignite_from_self), honors
priority (first match wins), includes universal "*" edges, and returns False /
writes nothing on no match.
"""

import controller.runtime.transitions as transitions_mod
from controller.runtime.transitions import Edge, evaluate_phase


class _FakeDisplay:
    def __init__(self):
        self.pushed = []

    def push(self, cmd):
        self.pushed.append(cmd)


class _FakeStore:
    def __init__(self, control):
        self._control = control
        self.writes = []
        self._display = _FakeDisplay()

    def execute_control_writes(self):
        pass

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


class _FakeMode:
    def __init__(self, name, control, settings=None):
        self.name = name
        self.control = control
        self.settings = settings or {"safety": {"maxtemp": 550}}


def _control(mode="Smoke", **over):
    c = {
        "mode": mode,
        "updated": False,
        "primary_setpoint": 0,
        "safety": {"reigniteretries": 2, "reignitelaststate": "", "startuptemp": 150, "afterstarttemp": 100},
    }
    c.update(over)
    return c


def _setup(mode_name, control, monkeypatch, guards):
    monkeypatch.setattr(transitions_mod, "GUARDS", guards)
    store = _FakeStore(control)
    notifier = _FakeNotifier()
    ctx = _FakeCtx(store, notifier)
    mode_obj = _FakeMode(mode_name, control)
    return mode_obj, ctx, store, notifier


def _true(*a):
    return True


def _false(*a):
    return False


def test_edge_fires_request_transition_with_params(monkeypatch):
    control = _control("Smoke")
    guards = {"Smoke": {"pre_act": [Edge(_true, "Error", "safety", notify="N", display=("text", "X"))]}}
    mode_obj, ctx, store, notifier = _setup("Smoke", control, monkeypatch, guards)
    fired = evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert fired is True
    assert control["mode"] == "Error"
    assert control["updated"] is True
    assert notifier.sent == ["N"]
    assert store._display.pushed == [("text", "X")]
    assert len(store.writes) == 1


def test_edge_reignite_from_self_uses_mode_name(monkeypatch):
    control = _control("Hold")
    guards = {"Hold": {"pre_act": [Edge(_true, "Reignite", "safety", reignite_from_self=True)]}}
    mode_obj, ctx, store, notifier = _setup("Hold", control, monkeypatch, guards)
    fired = evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert fired is True
    assert control["mode"] == "Reignite"
    assert control["safety"]["reigniteretries"] == 1  # decremented from 2
    assert control["safety"]["reignitelaststate"] == "Hold"  # == mode_obj.name


def test_priority_first_matching_edge_wins(monkeypatch):
    control = _control("Smoke")
    seen = []

    def _record_true(mode_obj, ctx, c, ptemp, now):
        seen.append("first")
        return True

    def _second(mode_obj, ctx, c, ptemp, now):
        seen.append("second")
        return True

    guards = {"Smoke": {"pre_act": [Edge(_record_true, "Error", "safety"), Edge(_second, "Reignite", "safety")]}}
    mode_obj, ctx, store, notifier = _setup("Smoke", control, monkeypatch, guards)
    fired = evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert fired is True
    assert control["mode"] == "Error"  # first edge won
    assert seen == ["first"]  # second guard never evaluated


def test_universal_star_edges_apply_to_any_mode(monkeypatch):
    control = _control("Monitor")
    guards = {"*": {"pre_act": [Edge(_true, "Stop", "terminal")]}}
    mode_obj, ctx, store, notifier = _setup("Monitor", control, monkeypatch, guards)
    fired = evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert fired is True
    assert control["mode"] == "Stop"


def test_star_edges_take_priority_over_mode_edges(monkeypatch):
    # Universal "*" edges are walked BEFORE mode-specific edges: this preserves
    # the live pre_act order where universal max-temp beats mode check_safety.
    control = _control("Smoke")
    guards = {
        "Smoke": {"pre_act": [Edge(_true, "Reignite", "safety")]},
        "*": {"pre_act": [Edge(_true, "Error", "safety")]},
    }
    mode_obj, ctx, store, notifier = _setup("Smoke", control, monkeypatch, guards)
    evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert control["mode"] == "Error"  # universal edge won over the mode edge


def test_no_match_returns_false_and_writes_nothing(monkeypatch):
    control = _control("Smoke")
    guards = {"Smoke": {"pre_act": [Edge(_false, "Error", "safety")]}}
    mode_obj, ctx, store, notifier = _setup("Smoke", control, monkeypatch, guards)
    fired = evaluate_phase(mode_obj, ctx, "pre_act", now=0, ptemp=100)
    assert fired is False
    assert store.writes == []
    assert control["mode"] == "Smoke"


def test_empty_phase_returns_false(monkeypatch):
    control = _control("Smoke")
    mode_obj, ctx, store, notifier = _setup("Smoke", control, monkeypatch, {})
    assert evaluate_phase(mode_obj, ctx, "pre_loop", now=0, ptemp=100) is False


# ---- predicate wraps (faithful to logic/safety) ----


def test_flameout_predicates_setup_vs_inloop_read_different_temps():
    # setup variant reads afterstarttemp; inloop variant reads ptemp.
    control = _control("Smoke")
    control["safety"]["startuptemp"] = 150
    control["safety"]["afterstarttemp"] = 200  # OK at setup
    control["safety"]["reigniteretries"] = 0
    mode_obj = _FakeMode("Smoke", control)
    # setup: afterstarttemp 200 >= 150 -> no flameout, regardless of ptemp
    assert transitions_mod.flameout_error_setup(mode_obj, None, control, ptemp=100, now=0) is False
    # inloop: ptemp 100 < 150 with retries 0 -> ERROR
    assert transitions_mod.flameout_error_inloop(mode_obj, None, control, ptemp=100, now=0) is True


def test_over_max_temp_guard_reads_mode_settings():
    control = _control("Smoke")
    mode_obj = _FakeMode("Smoke", control, settings={"safety": {"maxtemp": 500}})
    assert transitions_mod.over_max_temp_guard(mode_obj, None, control, ptemp=550, now=0) is True
    assert transitions_mod.over_max_temp_guard(mode_obj, None, control, ptemp=400, now=0) is False
