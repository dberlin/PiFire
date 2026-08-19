import copy

from common.control_trace import ActuationMode, MpcUpdatePayload
from controller.base import MpcFailureState, MpcTraceDiagnostics
import controller.model_promotion as model_promotion
from controller.mpc_allocator import allocate
from controller.runtime.runner import ControllerUpdateResult
from tests.fakes.runner import FakeControllerRunner


_MODEL = dict(C_c=306.0, h_amb=0.5, T_amb=20.0, theta=0.0, n_delay=0, K_Q=100.0, sigma=0.0)


def _report(target, revision=1, provenance="mak-fit"):
    return model_promotion.feasibility_report(_MODEL, target, model_revision=revision, model_provenance=provenance)


def _mpc_result(report, revision):
    allocation = allocate(1.0, u_max=0.9, fan_min_pct=40.0, fan_max_pct=100.0, enable_fan=False)
    diagnostics = MpcTraceDiagnostics(
        state_names=("T_c", "d"),
        state_values=(100.0, 0.0),
        disturbance_estimate=0.0,
        model_revision=report.model_revision or 0,
        model_provenance=report.model_provenance or "unidentified",
        raw_policy_firing_load=1.0,
        equilibrium_feed_forward=1.0,
        residual_move=0.0,
        bounded_firing_load=1.0,
        applied_combustion_load=1.0,
        policy_kind="net",
        failure_state=MpcFailureState.SUCCESS,
        consecutive_policy_failures=0,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        feasibility=report,
    )
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan=None,
        input_temperature=100.0,
        diagnostics=diagnostics,
        allocation=allocation,
        revision=revision,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


def test_hold_warns_once_for_an_unreachable_target_while_continuing_at_maximum_safe_authority(hold_cycle, monkeypatch):

    unreachable = _report(240.0)
    result = _mpc_result(unreachable, 1)
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script([result, result])
    hold = hold_cycle(runner, controller="mpc")
    warnings = []

    hold.setup()
    monkeypatch.setattr(hold.ctx.event_log, "warning", warnings.append)
    records = []
    assert hold._control_trace is not None
    hold._control_trace.record = lambda kind, payload, timestamp: records.append(payload) or True
    before_settings = copy.deepcopy(hold.settings)
    hold.on_tick(2.0, 100.0, hold.grill.get_output_status())
    hold.on_tick(4.0, 100.0, hold.grill.get_output_status())

    assert len(warnings) == 1
    assert hold.state.controller.pulse_requested_duty == 0.9
    assert hold.state.controller.pulse_combustion_load == 1.0
    payload = next(payload for payload in records if isinstance(payload, MpcUpdatePayload))
    assert payload.predicted_feasible is False
    assert payload.predicted_steady_load == 1.0
    assert hold.settings == before_settings


def test_hold_advisory_rearms_only_after_target_model_or_reachability_changes(hold_cycle, monkeypatch):

    unknown = model_promotion.feasibility_report(None, 240.0, model_revision=None, model_provenance=None)
    reachable = _report(120.0)
    unreachable = _report(240.0)
    target_changed = _report(250.0)
    model_changed = _report(250.0, revision=2, provenance="mak-fit-v2")
    recovered_then_unreachable = _report(250.0, revision=2, provenance="mak-fit-v2")
    results = [
        _mpc_result(unknown, 1),
        _mpc_result(reachable, 2),
        _mpc_result(unreachable, 3),
        _mpc_result(unreachable, 3),  # stale poll of the same result
        _mpc_result(unreachable, 4),  # newer solve, same advisory key
        _mpc_result(target_changed, 5),
        _mpc_result(model_changed, 6),
        _mpc_result(reachable, 7),
        _mpc_result(recovered_then_unreachable, 8),
    ]
    runner = FakeControllerRunner(period=1.0, actuation_mode=ActuationMode.FRAMED_PULSE).script(results)
    hold = hold_cycle(runner, controller="mpc")
    warnings = []

    hold.setup()
    monkeypatch.setattr(hold.ctx.event_log, "warning", warnings.append)
    for now in range(2, 20, 2):
        hold.on_tick(float(now), 100.0, hold.grill.get_output_status())

    assert unknown.state is model_promotion.ReachabilityState.UNKNOWN_MODEL
    assert reachable.state is model_promotion.ReachabilityState.REACHABLE
    assert len(warnings) == 4
