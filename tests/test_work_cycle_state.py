import pytest
from controller.runtime.state import WorkCycleState


def test_work_cycle_state_defaults():
    """Test that WorkCycleState initializes with correct defaults."""
    state = WorkCycleState()

    # Scalar float defaults
    assert state.cycle_ratio == 0.0
    assert state.raw_cycle_ratio == 0.0
    assert state.on_time == 0.0
    assert state.off_time == 0.0
    assert state.cycle_time == 0.0
    assert state.controller_output == 0.0
    assert state.lid_open_expires == 0.0
    assert state.prime_duration == 0.0
    assert state.prime_amount == 0.0
    assert state.startup_timer == 0.0
    assert state.raw_startup_temp == 0.0

    # Optional default
    assert state.controller_fan_duty is None

    # Boolean defaults
    assert state.fan_assist is False
    assert state.lid_open_detect is False
    assert state.target_temp_achieved is False
    assert state.pwm_fan_ramping is False

    # Toggle timestamp defaults (all float 0.0)
    assert state.start_time == 0.0
    assert state.auger_toggle_time == 0.0
    assert state.display_toggle_time == 0.0
    assert state.fan_cycle_toggle_time == 0.0
    assert state.hopper_toggle_time == 0.0
    assert state.fan_update_time == 0.0
    assert state.eta_toggle_time == 0.0
    assert state.temp_toggle_time == 0.0
    assert state.controller_cycle_start == 0.0

    # Mutable defaults via field(default_factory=dict)
    assert state.manual_override == {}
    assert state.metrics == {}


def test_work_cycle_state_dict_independence():
    """Test that dict fields are independent across instances (default_factory)."""
    state1 = WorkCycleState()
    state2 = WorkCycleState()

    # Verify they are separate dict objects
    assert state1.manual_override is not state2.manual_override
    assert state1.metrics is not state2.metrics

    # Verify mutation on one doesn't affect the other
    state1.manual_override['key'] = 'value'
    assert 'key' not in state2.manual_override


def test_work_cycle_state_field_assignment():
    """Test that fields can be set after initialization."""
    state = WorkCycleState()

    # Test scalar assignment
    state.cycle_ratio = 0.5
    assert state.cycle_ratio == 0.5

    # Test boolean assignment
    state.fan_assist = True
    assert state.fan_assist is True

    # Test None assignment
    state.controller_fan_duty = 50.0
    assert state.controller_fan_duty == 50.0

    # Test dict field assignment
    state.manual_override = {'mode': 'manual'}
    assert state.manual_override == {'mode': 'manual'}
