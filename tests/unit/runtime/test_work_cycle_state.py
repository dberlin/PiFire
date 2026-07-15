from controller.runtime.state import WorkCycleState


def test_work_cycle_state_defaults():
    """Test that WorkCycleState initializes with correct nested defaults."""
    state = WorkCycleState()

    # CycleState
    assert state.cycle.ratio == 0.0
    assert state.cycle.raw_ratio == 0.0
    assert state.cycle.on_time == 0.0
    assert state.cycle.off_time == 0.0
    assert state.cycle.cycle_time == 0.0

    # ControllerState
    assert state.controller.output == 0.0
    assert state.controller.fan_duty is None
    assert state.controller.controls_fan is False
    assert state.controller.cycle_start == 0.0

    # FanState
    assert state.fan.assist is False
    assert state.fan.pwm_ramping is False
    assert state.fan.cycle_toggle_time == 0.0
    assert state.fan.update_time == 0.0

    # LidState
    assert state.lid.open_detected is False
    assert state.lid.expires == 0.0

    # StartupState
    assert state.startup.timer == 0.0
    assert state.startup.raw_temp == 0.0

    # PrimeState
    assert state.prime.duration == 0.0
    assert state.prime.amount == 0.0

    # Timers
    assert state.timers.start_time == 0.0
    assert state.timers.auger_toggle == 0.0
    assert state.timers.display_toggle == 0.0
    assert state.timers.hopper_toggle == 0.0
    assert state.timers.eta_toggle == 0.0
    assert state.timers.temp_toggle == 0.0

    # Top-level fields
    assert state.target_temp_achieved is False
    assert state.manual_override == {}
    assert state.metrics == {}


def test_work_cycle_state_dict_independence():
    """Test that dict fields and nested sub-dataclasses are independent
    across instances (default_factory)."""
    state1 = WorkCycleState()
    state2 = WorkCycleState()

    # Verify they are separate dict objects
    assert state1.manual_override is not state2.manual_override
    assert state1.metrics is not state2.metrics

    # Verify mutation on one doesn't affect the other
    state1.manual_override["key"] = "value"
    assert "key" not in state2.manual_override

    # Verify nested sub-dataclasses are separate objects too
    assert state1.cycle is not state2.cycle
    assert state1.controller is not state2.controller
    assert state1.fan is not state2.fan
    assert state1.lid is not state2.lid
    assert state1.startup is not state2.startup
    assert state1.prime is not state2.prime
    assert state1.timers is not state2.timers

    state1.cycle.ratio = 0.5
    assert state2.cycle.ratio == 0.0


def test_work_cycle_state_field_assignment():
    """Test that nested fields can be set after initialization."""
    state = WorkCycleState()

    # Test scalar assignment on a nested sub-dataclass
    state.cycle.ratio = 0.5
    assert state.cycle.ratio == 0.5

    # Test boolean assignment on a nested sub-dataclass
    state.fan.assist = True
    assert state.fan.assist is True

    # Test None -> value assignment on a nested sub-dataclass
    state.controller.fan_duty = 50.0
    assert state.controller.fan_duty == 50.0

    # Test dict field assignment (top-level)
    state.manual_override = {"mode": "manual"}
    assert state.manual_override == {"mode": "manual"}
