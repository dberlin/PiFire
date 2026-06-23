import importlib


def test_base_default_control_period_is_none():
    from controller.base import ControllerBase
    cb = ControllerBase({}, 'C', {})
    assert cb.get_control_period() is None


def test_normalize_handles_float_and_dict():
    control = importlib.import_module('control')
    # legacy float
    ratio, fan = control.normalize_controller_output(0.42)
    assert ratio == 0.42 and fan is None
    # mpc dict
    ratio, fan = control.normalize_controller_output(
        {'cycle_ratio': 0.3, 'fan': {'duty': 80.0}})
    assert ratio == 0.3 and fan == {'duty': 80.0}
    # dict without fan
    ratio, fan = control.normalize_controller_output({'cycle_ratio': 0.5})
    assert ratio == 0.5 and fan is None
