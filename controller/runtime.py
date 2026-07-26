"""Optional adaptive-controller runtime integration helpers."""


def supports(controller, function_name):
    """Return whether a controller declares an optional function available."""
    supported_functions = getattr(controller, "supported_functions", None)
    if not callable(supported_functions):
        return False
    try:
        return function_name in supported_functions()
    except (AttributeError, TypeError):
        return False


def record_output(controller, duty, identification_allowed=True):
    """Record an applied output when the controller supports adaptive history."""
    method = _supported_method(controller, "set_output")
    if method is not None:
        method(duty, identification_allowed)



def record_lid_open_transition(controller):
    """Record the exact auger-off output enforced for a lid-open event."""
    record_output(controller, 0.0, identification_allowed=False)


def identification_allowed(lid_open, manual_override_active, fan_pid_active):
    """Return whether applied output can contribute to model identification."""
    return not (lid_open or manual_override_active or fan_pid_active)


def manual_override_duty(output):
    """Convert a manual auger command into an exact applied duty."""
    return 1.0 if output else 0.0


def controller_reinit_output_seed(
    cycle_ratio,
    lid_open,
    manual_override_active,
    fan_pid_active,
    auger_output,
):
    """Return the applied output and identification gate for a replacement PID."""
    if manual_override_active:
        return manual_override_duty(auger_output), False
    return cycle_ratio, identification_allowed(
        lid_open, manual_override_active, fan_pid_active
    )

def restore_model(controller, store, name):
    """Restore a persisted trusted model only for an adaptive controller."""
    method = _supported_method(controller, "restore_model")
    if method is None:
        return False

    snapshot = store.load(name)
    if snapshot is None:
        return False
    return bool(method(snapshot))


def stage_model(controller, store, name):
    """Stage a controller's current trusted model when it is available."""
    method = _supported_method(controller, "get_model_snapshot")
    if method is None:
        return False

    snapshot = method()
    if snapshot is None:
        return False
    return bool(store.stage(name, snapshot))


def diagnostics(controller):
    """Return adaptive diagnostics or preserve the legacy plain-controller payload."""
    method = _supported_method(controller, "get_status")
    if method is not None:
        return method()
    return dict(controller.__dict__)


def apply_live_hold_target(controller, active_mode, control):
    """Apply only a target-only Hold update without consuming other changes."""
    if active_mode != "Hold" or control.get("mode") != "Hold":
        return False
    if (
        not control.get("updated")
        or control.get("units_change") is not False
        or control.get("controller_update", False)
    ):
        return False
    if "primary_setpoint" not in control or not hasattr(controller, "set_point"):
        return False

    target = control["primary_setpoint"]
    if target == controller.set_point:
        return False

    method = _supported_method(controller, "set_target")
    if method is None:
        return False

    method(target)
    control["updated"] = False
    return True


def apply_live_hold_target_and_restart_cycle(controller, active_mode, control, now):
    """Apply a live Hold target and return its new PID cycle start time."""
    if not apply_live_hold_target(controller, active_mode, control):
        return None
    return now


def hold_pid_update_due(now, controller_cycle_start, cycle_time):
    """Return whether a Hold PID update is due."""
    return now - controller_cycle_start > cycle_time


def normal_pid_output_recording_allowed(manual_override_until, now):
    """Return whether a PID output may replace manual auger history."""
    return manual_override_until < now


def _supported_method(controller, function_name):
    if not supports(controller, function_name):
        return None
    method = getattr(controller, function_name, None)
    return method if callable(method) else None
