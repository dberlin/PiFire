"""Unit tests for the request_transition() seam (controller/runtime/transitions.py).

Uses a fake ctx (fake store + notifications) that records writes / notifies /
display pushes. Asserts the resulting persisted control VALUES + side effects
(the observable contract) -- not intra-write field order.
"""

from controller.runtime.transitions import request_transition, TransitionError
import controller.runtime.transitions as transitions_mod


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


# --------------------------------------------------------------------------
# kind="natural"
# --------------------------------------------------------------------------


def test_natural_applies_when_not_updated_hold_keeps_setpoint():
    control = _base_control(mode="Smoke", updated=False, primary_setpoint=0)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Hold", kind="natural", setpoint=225)
    assert out["mode"] == "Hold"
    assert out["primary_setpoint"] == 225  # Hold => setpoint applied
    assert out["updated"] is True
    assert len(store.writes) == 1
    assert store.flushed == 1  # flushed before re-reading
    assert notifier.sent == []
    assert store._display.pushed == []


def test_natural_forces_setpoint_zero_when_not_hold():
    control = _base_control(mode="Startup", updated=False, primary_setpoint=300)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Smoke", kind="natural", setpoint=225)
    assert out["mode"] == "Smoke"
    assert out["primary_setpoint"] == 0  # non-Hold target forces 0
    assert out["updated"] is True


def test_natural_yields_when_already_updated():
    control = _base_control(mode="Error", updated=True)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Smoke", kind="natural", setpoint=0)
    assert out["mode"] == "Error"  # yielded: safety trip survives
    assert store.writes == []  # no write when yielding
    assert store.flushed == 1


# --------------------------------------------------------------------------
# kind="safety"
# --------------------------------------------------------------------------


def test_safety_error_write():
    control = _base_control(mode="Smoke", primary_setpoint=225)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Error", kind="safety", display=("text", "ERROR"), notify="Grill_Error_02")
    assert out["mode"] == "Error"
    assert out["updated"] is True
    assert store._display.pushed == [("text", "ERROR")]
    assert notifier.sent == ["Grill_Error_02"]
    assert len(store.writes) == 1
    # Authoritative kinds never touch primary_setpoint or reignite fields.
    assert out["primary_setpoint"] == 225
    assert out["safety"]["reigniteretries"] == 2


def test_safety_reignite_decrements_and_records_last_state():
    control = _base_control(mode="Smoke", primary_setpoint=225)
    ctx, store, notifier = _ctx(control)
    out = request_transition(
        ctx,
        control,
        "Reignite",
        kind="safety",
        reignite_from="Smoke",
        display=("text", "Re-Ignite"),
        notify="Grill_Error_03",
    )
    assert out["mode"] == "Reignite"
    assert out["updated"] is True
    assert out["safety"]["reigniteretries"] == 1  # decremented from 2
    assert out["safety"]["reignitelaststate"] == "Smoke"
    assert store._display.pushed == [("text", "Re-Ignite")]
    assert notifier.sent == ["Grill_Error_03"]
    assert out["primary_setpoint"] == 225  # untouched


# --------------------------------------------------------------------------
# kind="terminal"
# --------------------------------------------------------------------------


def test_terminal_stop_write():
    control = _base_control(mode="Shutdown", primary_setpoint=225)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Stop", kind="terminal")
    assert out["mode"] == "Stop"
    assert out["updated"] is True
    assert len(store.writes) == 1
    assert notifier.sent == []  # no notify
    assert store._display.pushed == []  # no display
    assert out["primary_setpoint"] == 225  # untouched


# --------------------------------------------------------------------------
# ALLOWED_EXITS legality (Task 10: populated graph, enforced)
# --------------------------------------------------------------------------

# Committed snapshot of the whole legal-exit graph -- the single-place FSM view.
# A change to ALLOWED_EXITS must be reflected here (visible in review).
_EXPECTED_GRAPH = {
    "Prime": {"Startup", "Stop", "Error"},
    "Startup": {"Prime", "Smoke", "Hold", "Monitor", "Stop", "Error", "Reignite"},
    "Smoke": {"Hold", "Monitor", "Shutdown", "Stop", "Error", "Reignite"},
    "Hold": {"Smoke", "Monitor", "Shutdown", "Stop", "Error", "Reignite"},
    "Reignite": {"Smoke", "Hold", "Startup", "Stop", "Error"},
    "Shutdown": {"Stop", "Error"},
    "Monitor": {"Stop", "Error"},
    "Manual": {"Stop", "Error"},
    "Recipe": {"Recipe", "Smoke", "Hold", "Stop", "Error", "Reignite"},
}


def test_allowed_exits_matches_committed_snapshot():
    # Whole state-machine graph in one asserted view (Step 3 inspectability).
    assert transitions_mod.ALLOWED_EXITS == _EXPECTED_GRAPH


# Committed snapshot of the whole declarative guard graph -- {mode: {phase:
# [(guard_name, to, kind)]}} -- the second half of the single-place FSM view.
# A change to GUARDS must be reflected here (visible in review).
_EXPECTED_GUARDS = {
    "*": {
        "pre_act": [("over_max_temp_guard", "Error", "safety")],
    },
    "Smoke": {
        "pre_loop": [
            ("flameout_error_setup", "Error", "safety"),
            ("flameout_reignite_setup", "Reignite", "safety"),
        ],
        "pre_act": [
            ("flameout_error_inloop", "Error", "safety"),
            ("flameout_reignite_inloop", "Reignite", "safety"),
        ],
    },
    "Hold": {
        "pre_loop": [
            ("flameout_error_setup", "Error", "safety"),
            ("flameout_reignite_setup", "Reignite", "safety"),
        ],
        "pre_act": [
            ("flameout_error_inloop", "Error", "safety"),
            ("flameout_reignite_inloop", "Reignite", "safety"),
        ],
    },
}


def _guard_dump():
    return {
        mode: {phase: [(edge.guard.__name__, edge.to, edge.kind) for edge in edges] for phase, edges in phases.items()}
        for mode, phases in transitions_mod.GUARDS.items()
    }


def test_guards_match_committed_snapshot():
    # The whole declarative guard graph in one asserted view (Task 17 Step 1).
    assert _guard_dump() == _EXPECTED_GUARDS


def test_every_guard_edge_target_is_a_legal_exit():
    # Cross-check the two declarations agree: every GUARDS edge's `to` is in the
    # source mode's ALLOWED_EXITS (Task 17 Step 2). The universal "*" edges apply
    # to every mode, so their target must be a legal exit for EVERY declared mode.
    allowed = transitions_mod.ALLOWED_EXITS
    for mode, phases in transitions_mod.GUARDS.items():
        for phase, edges in phases.items():
            for edge in edges:
                if mode == "*":
                    for source_mode, exits in allowed.items():
                        assert edge.to in exits, f"universal edge -> {edge.to} illegal from {source_mode}"
                else:
                    assert edge.to in allowed[mode], f"{mode} -> {edge.to} ({phase}) not in ALLOWED_EXITS"


def test_illegal_edge_raises_transition_error():
    # Manual's declared exits are {Stop, Error}; Manual -> Reignite is illegal.
    control = _base_control(mode="Manual", updated=False)
    ctx, store, notifier = _ctx(control)
    try:
        request_transition(ctx, control, "Reignite", kind="safety")
    except TransitionError:
        pass
    else:
        raise AssertionError("expected TransitionError for illegal Manual -> Reignite")


def test_legal_edges_pass():
    # Every edge exercised by the characterization suite is legal (no raise).
    for from_mode, to_mode, kind in [
        ("Smoke", "Error", "safety"),
        ("Smoke", "Reignite", "safety"),
        ("Smoke", "Stop", "terminal"),
        ("Hold", "Error", "safety"),
        ("Hold", "Reignite", "safety"),
        ("Recipe", "Stop", "terminal"),
    ]:
        control = _base_control(mode=from_mode, updated=False)
        ctx, store, notifier = _ctx(control)
        out = request_transition(ctx, control, to_mode, kind=kind)
        assert out["mode"] == to_mode


def test_unlisted_source_mode_is_noop_passthrough():
    # Terminal Stop/Error are omitted from the graph -> _check_legal is a no-op
    # for them (models the post-trip natural next_mode read that then yields).
    assert "Stop" not in transitions_mod.ALLOWED_EXITS
    assert "Error" not in transitions_mod.ALLOWED_EXITS
    control = _base_control(mode="Error", updated=True)
    ctx, store, notifier = _ctx(control)
    out = request_transition(ctx, control, "Stop", kind="natural", setpoint=0)
    assert out["mode"] == "Error"  # yielded, no TransitionError from unlisted source
