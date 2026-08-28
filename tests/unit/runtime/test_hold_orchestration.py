"""Cross-component Hold ordering and exactly-once ownership contracts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from common.control_trace import ResultStaleState, TraceEventKind
from common.model_evidence import EvidenceKind, FallbackEvidence, ModelEvidenceRecord
from controller.applied_output import FrameFeedbackDisposition, OutputSource
from controller.runtime.model_fitting import TeardownRefitOutcome, TeardownRefitResult
from controller.runtime.model_persistence import EvidenceSubmission
from controller.runtime.runner import ControllerUpdateResult
from tests.fakes.runner import FakeControllerRunner


class _OrderedRunner(FakeControllerRunner):
    def __init__(self, events, *, failure=None, duty=0.9):
        super().__init__(period=1.0)
        self.events = events
        self.failure = failure
        self.snapshot = {"version": 1, "revision": 1, "params": {}}
        self.refit_verdict = TeardownRefitResult.insufficient("minimum-samples")
        self.result = ControllerUpdateResult(
            cycle_ratio=duty,
            fan=None,
            input_temperature=200.0,
            revision=1,
            solve_start_monotonic=1.0,
            solve_end_monotonic=1.125,
            solve_duration_seconds=0.125,
            completed_wall_time=1.125,
        )
        self.script([self.result])

    def latest(self):
        self.events.append("runner:result")
        return super().latest()

    def set_output(self, applied):
        self.events.append(
            (
                "runner:feedback",
                applied.source,
                applied.feedback_disposition,
                applied.producing_result_revision,
                applied.producing_calibration_revision,
            )
        )
        super().set_output(applied)

    def observe_frame(self, observation):
        self.events.append(
            (
                "runner:observation",
                observation.result_revision,
                observation.calibration_command_revision,
                observation.reset,
            )
        )
        return super().observe_frame(observation)

    def reconfigure(self, settings, control, logger=None):
        self.events.append("runner:reconfigure")
        return super().reconfigure(settings, control, logger=logger)

    def restore_model(self, snapshot):
        self.events.append("runner:restore")
        return super().restore_model(snapshot)

    def bind_evidence_context(self, generation, session_id, cook_id):
        self.events.append(("runner:bind-evidence", generation))
        super().bind_evidence_context(generation, session_id, cook_id)

    def retire_evidence_context(self, generation):
        self.events.append(("runner:retire-evidence", generation))
        super().retire_evidence_context(generation)

    def stop_for_refit(self):
        self.events.append("runner:stop")
        self.stop()
        if self.failure == "runner-stop":
            raise RuntimeError("runner stop failed")

    def refit_from_cook(self):
        self.events.append("runner:refit")
        if self.failure == "refit":
            raise RuntimeError("refit failed")
        return super().refit_from_cook()

    def finalize_cook_refit(self, outcome):
        normalized = outcome if isinstance(outcome, TeardownRefitOutcome) else TeardownRefitOutcome(outcome)
        self.events.append(("runner:checkpoint-outcome", normalized))
        return super().finalize_cook_refit(normalized)

    def finish_teardown(self):
        self.events.append("runner:finish")
        super().finish_teardown()


class _OrderedPersistence:
    failed = False

    def __init__(self, events, *, failure=None):
        self.events = events
        self.failure = failure
        self.evidence_batches = []
        self.checkpoints = []

    @property
    def evidence_blocked(self):
        return False

    def submit_evidence_batch(self, records):
        owned = tuple(records)
        self.evidence_batches.append(owned)
        self.events.append(("persistence:evidence", tuple(record.evidence_id for record in owned)))
        return EvidenceSubmission(accepted=True)

    def submit_checkpoint(self, name, snapshot):
        self.checkpoints.append((name, snapshot))
        self.events.append("persistence:checkpoint")
        return self.failure != "checkpoint"

    def barrier(self, timeout=2.0):
        del timeout
        self.events.append("persistence:barrier")
        return self.failure != "persistence-barrier"


class _OrderedTrace:
    def __init__(self, events, *, failure=None):
        self.events = events
        self.failure = failure
        self.close_calls = 0

    def record(self, record):
        self.events.append(("trace:record", record.event_kind))

    def flush_due(self, _now_ms):
        return None

    def close(self):
        self.close_calls += 1
        self.events.append("trace:close")
        if self.failure == "trace-close":
            raise RuntimeError("trace close failed")


def _install_boundaries(monkeypatch, events, *, failure=None):
    import controller.runtime.modes.hold as hold_module
    import controller.runtime.modes.hold_learning as hold_learning_module

    persistence = _OrderedPersistence(events, failure=failure)
    trace = _OrderedTrace(events, failure=failure)
    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", lambda _store, _logger: persistence)
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: trace)
    monkeypatch.setattr(hold_learning_module, "read_model_activation", lambda: None)
    monkeypatch.setattr(hold_learning_module, "read_model_evidence", lambda: ())
    return persistence, trace


def _record_hardware(monkeypatch, events, grill):
    for method_name in ("auger_off", "fan_off", "igniter_off", "power_off"):
        original = getattr(grill, method_name)

        def record(original=original, method_name=method_name):
            events.append(f"hardware:{method_name.replace('_', '-')}")
            original()

        monkeypatch.setattr(grill, method_name, record)


def _assert_relative_order(events, expected):
    assert all(event in events for event in expected), (events, expected)
    positions = [events.index(event) for event in expected]
    assert positions == sorted(positions)


def _activation_record(evidence_id, timestamp_ms):
    return ModelEvidenceRecord(
        evidence_id=evidence_id,
        kind=EvidenceKind.FALLBACK,
        session_id="activation-order",
        cook_id=None,
        timestamp_ms=timestamp_ms,
        role_generation=2,
        model_digest="a" * 64,
        provenance_digest="b" * 64,
        payload=FallbackEvidence(
            decision_id="activation-decision",
            reason="confidence-window-regressed",
            failed_digest="a" * 64,
            failed_generation=2,
        ),
    )


def test_frame_boundary_commands_transition_before_one_identity_aligned_terminal_delivery(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedRunner(events, duty=0.1)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "frame-order"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    events.clear()
    auger_on = hold.grill.auger_on

    def record_auger_on():
        events.append("hardware:auger-on")
        auger_on()

    monkeypatch.setattr(hold.grill, "auger_on", record_auger_on)

    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    terminal = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "runner:feedback" and event[2] is FrameFeedbackDisposition.COMPLETE
    ]
    observations = [event for event in events if isinstance(event, tuple) and event[0] == "runner:observation"]
    assert len(terminal) == len(observations) == 1
    assert terminal[0][3:] == observations[0][1:3]
    assert observations[0][3] is False
    _assert_relative_order(events, ["hardware:auger-on", terminal[0], observations[0]])


@pytest.mark.parametrize("scenario", ["lid-opening", "safety-inhibit", "stale-result"])
def test_inhibit_turns_actuator_off_before_terminal_feedback_and_safety_trace(hold_cycle, monkeypatch, scenario):
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = f"inhibit-{scenario}"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    events.clear()
    auger_off = hold.grill.auger_off

    def record_auger_off():
        events.append("hardware:auger-off")
        auger_off()

    monkeypatch.setattr(hold.grill, "auger_off", record_auger_off)

    if scenario == "lid-opening":
        hold.state.target_temp_achieved = True
        hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
        hold.on_tick(4.0, 100.0, hold.grill.get_output_status())
    elif scenario == "safety-inhibit":
        hold._on_safety_event("temperature_guard", 4.0)
    else:
        stale = replace(runner.result, stale_state=ResultStaleState.STALE)
        runner.script([stale])
        hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    terminal = next(
        event
        for event in events
        if isinstance(event, tuple)
        and event[0] == "runner:feedback"
        and event[2] is not FrameFeedbackDisposition.PROGRESS
    )
    safety_trace = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "trace:record" and event[1] is TraceEventKind.SAFETY_EVENT
    )
    _assert_relative_order(events, ["hardware:auger-off", terminal, safety_trace])


def test_reconfigure_retires_old_frame_and_generation_before_replacement_is_used(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedRunner(events)
    store = SimpleNamespace(
        load=lambda _name: {"version": 1, "revision": 3, "params": {}},
        save_outcome=lambda _name, _snapshot: True,
    )
    hold = hold_cycle(runner, controller="mpc", model_store=store)
    _install_boundaries(monkeypatch, events)
    hold.setup()
    hold.control["cook_id"] = "reconfigure-order"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    events.clear()
    hold.control["controller_update"] = True

    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    terminal = next(
        event for event in events if isinstance(event, tuple) and event[0] == "runner:observation" and event[3] is True
    )
    seed = next(
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "runner:feedback" and event[1] is OutputSource.SEED
    )
    _assert_relative_order(
        events,
        [
            terminal,
            "runner:reconfigure",
            ("runner:retire-evidence", 0),
            "runner:restore",
            ("runner:bind-evidence", 1),
            "runner:result",
            seed,
        ],
    )
    assert events.count(("runner:retire-evidence", 0)) == 1
    assert events.count(("runner:bind-evidence", 1)) == 1


def test_unknown_safety_event_leaves_hardware_and_runner_unchanged(
    hold_cycle,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    output_before = hold.grill.get_output_status()
    applied_before = tuple(runner.applied)

    hold._on_safety_event("future_guard", 2.0)

    assert hold.grill.get_output_status() == output_before
    assert tuple(runner.applied) == applied_before
    assert runner.calibration_cancellations == []


def test_activation_lifecycle_evidence_keeps_fifo_ahead_of_checkpoint_and_trace_closure(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedRunner(events)
    persistence, _trace = _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "activation-order"
    hold.state.metrics = {"augerontime": 0.0}
    first = _activation_record("fallback-1", 1_000)
    second = _activation_record("fallback-2", 2_000)
    runner.activation_events[:] = [first, second]
    events.clear()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    evidence = ("persistence:evidence", ("fallback-1", "fallback-2"))
    _assert_relative_order(events, [evidence, "persistence:checkpoint"])
    assert persistence.evidence_batches == [(first, second)]

    hold.ctx.clock.advance(3.0)
    hold.teardown(200.0)
    _assert_relative_order(
        events,
        [evidence, "persistence:barrier", "trace:close", "runner:finish"],
    )


@pytest.mark.parametrize(
    ("failure", "propagates"),
    [
        (None, False),
        ("runner-stop", True),
        ("persistence-barrier", False),
        ("trace-close", False),
        ("refit", False),
        ("checkpoint", False),
    ],
    ids=["success", "runner-stop", "persistence-barrier", "trace-close", "refit", "checkpoint"],
)
def test_teardown_orders_cleanup_and_owns_each_resource_at_most_once(hold_cycle, monkeypatch, failure, propagates):
    events = []
    runner = _OrderedRunner(events, failure=failure)
    persistence, _trace = _install_boundaries(monkeypatch, events, failure=failure)
    hold = hold_cycle(runner, controller="mpc")
    hold.settings["controller"].setdefault("config", {}).setdefault("mpc", {})["enable_identification"] = True
    hold.setup()
    hold.control["cook_id"] = f"teardown-{failure or 'success'}"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.grill.igniter_on()
    hold.ctx.clock.advance(3.0)
    stops_before_teardown = runner.stops
    finishes_before_teardown = runner.finished_teardowns
    events.clear()
    _record_hardware(monkeypatch, events, hold.grill)

    if propagates:
        with pytest.raises(RuntimeError, match="runner stop failed"):
            hold.teardown(200.0)
    else:
        hold.teardown(200.0)
    hold.teardown(200.0)

    terminal = next(
        event for event in events if isinstance(event, tuple) and event[0] == "runner:observation" and event[3] is True
    )
    _assert_relative_order(
        events,
        [
            "hardware:auger-off",
            "hardware:fan-off",
            "hardware:igniter-off",
            "hardware:power-off",
            terminal,
            "runner:stop",
        ],
    )
    if failure != "runner-stop":
        _assert_relative_order(
            events,
            [
                "runner:stop",
                "runner:refit",
                "persistence:barrier",
                "trace:close",
                "runner:finish",
            ],
        )
    else:
        _assert_relative_order(
            events,
            ["runner:stop", "persistence:barrier", "trace:close", "runner:finish"],
        )

    assert events.count("runner:stop") == 1
    assert events.count("runner:refit") <= 1
    assert events.count("persistence:barrier") == 1
    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1
    assert (
        sum(isinstance(event, tuple) and event[0] == "runner:observation" and event[3] is True for event in events) == 1
    )
    assert (
        sum(
            isinstance(event, tuple)
            and event[0] == "runner:feedback"
            and event[2] is not FrameFeedbackDisposition.PROGRESS
            for event in events
        )
        == 1
    )
    assert runner.stops - stops_before_teardown == 1
    assert runner.finished_teardowns - finishes_before_teardown == 1
    assert len(persistence.checkpoints) <= (2 if failure == "checkpoint" else 1)
    assert hold.grill.get_output_status() == {
        "dc_fan": False,
        "auger": False,
        "fan": False,
        "igniter": False,
        "power": False,
    }


def test_teardown_retry_completes_cleanup_after_pre_cleanup_failure(hold_cycle, monkeypatch):
    events = []
    runner = _OrderedRunner(events)
    _persistence, _trace = _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "teardown-retry"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    runtime = hold._framed_pulse
    assert runtime is not None
    original_advance = runtime.advance
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient advance failure")
        return original_advance(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", fail_once)
    events.clear()

    with pytest.raises(RuntimeError, match="transient advance failure"):
        hold.teardown(200.0)
    hold.teardown(200.0)
    hold.teardown(200.0)

    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1
    assert events.count("runner:stop") == 1


def test_teardown_retry_resumes_delivery_after_scheduler_advance(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _persistence, _trace = _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {
        "id": "teardown-delivery-retry",
        "augerontime": 0.0,
    }
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    runtime = hold._framed_pulse
    learning = hold._hold_learning
    assert runtime is not None
    assert learning is not None
    calls = {"advance": 0, "feedback": 0, "reset": 0}
    original_advance = runtime.advance
    original_feedback = runtime.report_feedback
    original_reset = runtime.reset
    original_submit = learning.submit_completed_observation
    delivery_attempts = 0

    def count_advance(*args, **kwargs):
        calls["advance"] += 1
        return original_advance(*args, **kwargs)

    def count_feedback(*args, **kwargs):
        calls["feedback"] += 1
        return original_feedback(*args, **kwargs)

    def count_reset(*args, **kwargs):
        calls["reset"] += 1
        return original_reset(*args, **kwargs)

    def fail_delivery_once(*args, **kwargs):
        nonlocal delivery_attempts
        delivery_attempts += 1
        if delivery_attempts == 1:
            raise RuntimeError("transient observation delivery failure")
        return original_submit(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", count_advance)
    monkeypatch.setattr(runtime, "report_feedback", count_feedback)
    monkeypatch.setattr(runtime, "reset", count_reset)
    monkeypatch.setattr(
        learning,
        "submit_completed_observation",
        fail_delivery_once,
    )
    events.clear()

    with pytest.raises(
        RuntimeError,
        match="transient observation delivery failure",
    ):
        hold.teardown(200.0)
    hold.teardown(200.0)
    hold.teardown(200.0)

    assert calls == {"advance": 1, "feedback": 1, "reset": 1}
    assert delivery_attempts == 2
    assert (
        sum(isinstance(event, tuple) and event[0] == "runner:observation" and event[3] is True for event in events) == 1
    )
    assert (
        sum(
            isinstance(event, tuple)
            and event[0] == "runner:feedback"
            and event[2] is not FrameFeedbackDisposition.PROGRESS
            for event in events
        )
        == 1
    )
    assert events.count("runner:stop") == 1
    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1


def test_teardown_retry_reprepares_feedback_without_repeating_advance(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {
        "id": "teardown-feedback-preparation-retry",
        "augerontime": 0.0,
    }
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    runtime = hold._framed_pulse
    assert runtime is not None
    calls = {"advance": 0, "feedback": 0, "reset": 0}
    original_advance = runtime.advance
    original_feedback = runtime.report_feedback
    original_reset = runtime.reset

    def count_advance(*args, **kwargs):
        calls["advance"] += 1
        return original_advance(*args, **kwargs)

    def fail_feedback_once(*args, **kwargs):
        calls["feedback"] += 1
        if calls["feedback"] == 1:
            raise RuntimeError("transient feedback preparation failure")
        return original_feedback(*args, **kwargs)

    def count_reset(*args, **kwargs):
        calls["reset"] += 1
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", count_advance)
    monkeypatch.setattr(runtime, "report_feedback", fail_feedback_once)
    monkeypatch.setattr(runtime, "reset", count_reset)

    with pytest.raises(
        RuntimeError,
        match="transient feedback preparation failure",
    ):
        hold.teardown(200.0)
    hold.teardown(200.0)
    hold.teardown(200.0)

    assert calls == {"advance": 1, "feedback": 2, "reset": 1}
    assert events.count("runner:stop") == 1
    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1


def test_teardown_retry_reuses_prepared_feedback_after_dispatch_failure(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {
        "id": "teardown-feedback-dispatch-retry",
        "augerontime": 0.0,
    }
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    runtime = hold._framed_pulse
    assert runtime is not None
    calls = {"advance": 0, "feedback": 0, "dispatch": 0, "reset": 0}
    original_advance = runtime.advance
    original_feedback = runtime.report_feedback
    original_dispatch = hold._dispatch_framed_feedback
    original_reset = runtime.reset

    def count_advance(*args, **kwargs):
        calls["advance"] += 1
        return original_advance(*args, **kwargs)

    def count_feedback(*args, **kwargs):
        calls["feedback"] += 1
        return original_feedback(*args, **kwargs)

    def fail_dispatch_once(*args, **kwargs):
        calls["dispatch"] += 1
        if calls["dispatch"] == 1:
            raise RuntimeError("transient feedback dispatch failure")
        return original_dispatch(*args, **kwargs)

    def count_reset(*args, **kwargs):
        calls["reset"] += 1
        return original_reset(*args, **kwargs)

    monkeypatch.setattr(runtime, "advance", count_advance)
    monkeypatch.setattr(runtime, "report_feedback", count_feedback)
    monkeypatch.setattr(hold, "_dispatch_framed_feedback", fail_dispatch_once)
    monkeypatch.setattr(runtime, "reset", count_reset)

    with pytest.raises(
        RuntimeError,
        match="transient feedback dispatch failure",
    ):
        hold.teardown(200.0)
    hold.teardown(200.0)
    hold.teardown(200.0)

    assert calls == {
        "advance": 1,
        "feedback": 1,
        "dispatch": 2,
        "reset": 1,
    }
    assert events.count("runner:stop") == 1
    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1


def test_teardown_retry_latches_first_timestamp_and_temperature(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.metrics = {
        "id": "teardown-latched-inputs",
        "augerontime": 0.0,
    }
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    runtime = hold._framed_pulse
    assert runtime is not None
    feedback_times = []
    reset_inputs = []
    original_feedback = runtime.report_feedback
    original_reset = runtime.reset

    def fail_feedback_once(now, *args, **kwargs):
        feedback_times.append(now)
        if len(feedback_times) == 1:
            raise RuntimeError("transient feedback preparation failure")
        return original_feedback(now, *args, **kwargs)

    def capture_reset(reason, now, inhibit, **kwargs):
        reset_inputs.append((reason, now, kwargs["sample"].temperature))
        return original_reset(reason, now, inhibit, **kwargs)

    monkeypatch.setattr(runtime, "report_feedback", fail_feedback_once)
    monkeypatch.setattr(runtime, "reset", capture_reset)

    with pytest.raises(
        RuntimeError,
        match="transient feedback preparation failure",
    ):
        hold.teardown(200.0)
    hold.ctx.clock.advance(10.0)
    hold.teardown(500.0)

    assert feedback_times == [3.0, 3.0]
    assert [(item[1], item[2]) for item in reset_inputs] == [(3.0, 200.0)]
    assert reset_inputs[0][0].value == "mode_change"
    assert events.count("runner:stop") == 1
    assert events.count("trace:close") == 1
    assert events.count("runner:finish") == 1


def test_repeated_setup_starts_a_fresh_teardown_transaction(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")

    hold.setup()
    first_runtime = hold._framed_pulse
    hold.state.metrics = {
        "id": "first-setup",
        "augerontime": 0.0,
    }
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(3.0)
    hold.teardown(200.0)

    hold.setup()
    second_runtime = hold._framed_pulse
    hold.state.metrics = {
        "id": "second-setup",
        "augerontime": 0.0,
    }
    hold.on_tick(6.0, 300.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(4.0)
    hold.grill.igniter_on()
    hold.teardown(300.0)

    assert second_runtime is not first_runtime
    assert runner.stops == 2
    assert runner.finished_teardowns == 2
    assert events.count("trace:close") == 2
    assert hold.grill.get_output_status()["igniter"] is False


@pytest.mark.parametrize(
    "failure",
    ("fan_on", "power_on"),
)
def test_early_hardware_setup_failure_closes_created_trace_and_outputs(
    hold_cycle,
    monkeypatch,
    failure,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    hold = hold_cycle(runner, controller="mpc")

    def unavailable_output():
        raise RuntimeError(f"{failure} output unavailable")

    monkeypatch.setattr(
        hold.grill,
        failure,
        unavailable_output,
    )

    with pytest.raises(
        RuntimeError,
        match=f"{failure} output unavailable",
    ):
        hold.setup()
    trace = hold._control_trace
    assert trace is not None
    assert trace.status.closed is False
    status = hold.status_fragment()

    hold.teardown(200.0)

    assert trace.status.closed is True
    assert "pulse" not in status
    assert hold.grill.get_output_status() == {
        "dc_fan": False,
        "auger": False,
        "fan": False,
        "igniter": False,
        "power": False,
    }


def test_factory_failure_after_persistence_creation_closes_all_created_owners(
    hold_cycle,
    monkeypatch,
) -> None:
    import controller.runtime.modes.hold as hold_module

    events = []
    _, trace_recorder = _install_boundaries(monkeypatch, events)

    def unavailable_runner(*args, **kwargs):
        assert len(args) == 2
        assert "logger" in kwargs
        raise RuntimeError("runner factory unavailable")

    hold = hold_cycle(_OrderedRunner(events), controller="mpc")
    monkeypatch.setattr(
        hold_module._runner_mod,
        "build_runner",
        unavailable_runner,
    )
    assert events == []

    with pytest.raises(RuntimeError, match="runner factory unavailable"):
        hold.setup()
    trace = hold._control_trace
    assert trace is not None
    assert "persistence:barrier" not in events

    hold.teardown(200.0)

    assert events.count("trace:close") == 1
    assert events.count("persistence:barrier") == 1
    assert trace_recorder is not None


def test_runner_revision_failure_before_learning_closes_every_created_owner(
    hold_cycle,
    monkeypatch,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")

    def unavailable_revision():
        raise RuntimeError("runner revision unavailable")

    monkeypatch.setattr(
        runner,
        "configuration_revision",
        unavailable_revision,
    )

    with pytest.raises(
        RuntimeError,
        match="runner revision unavailable",
    ):
        hold.setup()
    events.clear()

    hold.teardown(200.0)

    assert events.count("runner:stop") == 1
    assert events.count("runner:finish") == 1
    assert events.count("persistence:barrier") == 1
    assert events.count("trace:close") == 1
    assert hold.grill.get_output_status()["auger"] is False


@pytest.mark.parametrize(
    ("failure", "expected_warning"),
    (
        (
            "persistence-barrier",
            "Model persistence barrier failed: persistence barrier failed",
        ),
        (
            "trace-flush",
            "Control trace pending flush failed: trace flush failed",
        ),
        (
            "runner-finish",
            "Controller teardown close failed: runner finish failed",
        ),
    ),
)
def test_partial_setup_cleanup_attempts_every_owner_once_after_boundary_failure(
    hold_cycle,
    monkeypatch,
    failure,
    expected_warning,
) -> None:
    events = []
    runner = _OrderedRunner(events)
    _, trace_recorder = _install_boundaries(monkeypatch, events)
    hold = hold_cycle(runner, controller="mpc")

    def unavailable_revision():
        raise RuntimeError("runner revision unavailable")

    monkeypatch.setattr(runner, "configuration_revision", unavailable_revision)
    with pytest.raises(RuntimeError, match="runner revision unavailable"):
        hold.setup()

    persistence = hold._persistence_worker
    trace = hold._control_trace
    assert persistence is not None
    assert trace is not None
    warnings = []
    hold.ctx.control_log = SimpleNamespace(warning=warnings.append)

    if failure == "persistence-barrier":

        def fail_persistence_flush(timeout=2.0):
            del timeout
            events.append("persistence:barrier")
            raise RuntimeError("persistence barrier failed")

        monkeypatch.setattr(persistence, "barrier", fail_persistence_flush)
    elif failure == "trace-flush":

        def fail_trace_flush():
            events.append("trace:flush-pending")
            raise RuntimeError("trace flush failed")

        monkeypatch.setattr(trace, "flush_pending", fail_trace_flush)
    else:

        def fail_runner_finish():
            events.append("runner:finish")
            raise RuntimeError("runner finish failed")

        monkeypatch.setattr(runner, "finish_teardown", fail_runner_finish)

    events.clear()
    hold.teardown(200.0)
    hold.teardown(200.0)

    assert events.count("runner:stop") == 1
    assert events.count("persistence:barrier") == 1
    assert events.count("trace:flush-pending") == (1 if failure == "trace-flush" else 0)
    assert trace_recorder.close_calls == 1
    assert trace.status.closed
    assert events.count("runner:finish") == 1
    assert warnings == [expected_warning]


def test_partial_setup_failures_still_close_the_runner_once(hold_cycle, monkeypatch):
    import controller.runtime.modes.hold as hold_module

    events = []
    runner = _OrderedRunner(events)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("boundary unavailable")

    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", unavailable)
    monkeypatch.setattr(hold_module, "ControlTraceRecorder", unavailable)
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.control["cook_id"] = "partial-setup"
    hold.state.metrics = {"augerontime": 0.0}
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.ctx.clock.advance(1.0)
    stops_before_teardown = runner.stops
    finishes_before_teardown = runner.finished_teardowns
    events.clear()

    hold.teardown(200.0)
    hold.teardown(200.0)

    assert events.count("runner:stop") == 1
    assert events.count("runner:finish") == 1
    assert runner.stops - stops_before_teardown == 1
    assert runner.finished_teardowns - finishes_before_teardown == 1
    assert (
        sum(
            isinstance(event, tuple)
            and event[0] == "runner:feedback"
            and event[2] is not FrameFeedbackDisposition.PROGRESS
            for event in events
        )
        <= 1
    )
