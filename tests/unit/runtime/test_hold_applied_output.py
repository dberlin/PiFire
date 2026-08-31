"""Hold reports observed framed-pulse output and explicit overrides."""

from dataclasses import replace
from types import SimpleNamespace

import controller.runtime.modes.hold as hold_mode_module
from common.control_trace import ModelObservationPayload
from controller.acados.contracts import GreyBoxMPCConfig
from controller.applied_output import FrameFeedbackDisposition, OutputSource
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc_allocator import allocate
from controller.mpc_config import MpcConfig
from controller.runtime.logic.pulse import PulseResetReason
from controller.runtime.model_fitting import CandidatePair
from controller.runtime.runner import ObservationOutcomeEnvelope
from tests.fakes.runner import FakeControllerRunner
from tests.unit.runtime.conftest import _off, _output


def _completed_output(ratio):
    return replace(
        _output(ratio),
        revision=1,
        solve_start_monotonic=1.0,
        solve_end_monotonic=1.125,
        solve_duration_seconds=0.125,
        completed_wall_time=1.125,
    )


def _mpc_admission_runtime():
    class NoopFitWorker:
        def start(self):
            pass

        def close(self):
            pass

    config = GreyBoxMPCConfig()
    components = CandidatePair(
        estimator=object(),
        controller=SimpleNamespace(config=config),
    )
    pair_factory = SimpleNamespace(
        build_estimator=lambda _config: object(),
        build_solver=lambda _config: object(),
        probe_solver=lambda _solver: None,
    )
    return GreyLearningRuntime(
        pair_factory=pair_factory,
        activation_runtime=object(),
        learning_enabled=True,
        units="C",
        cycle_data={},
        active_pair=lambda: SimpleNamespace(),
        active_components=lambda: components,
        configuration=MpcConfig,
        snapshot_parameters=dict,
        sync_configuration=lambda: None,
        append_trace=lambda _records: None,
        checkpoint_store=object(),
        fit_worker_factory=NoopFitWorker,
        installation_identity_provider=lambda: "manual-takeover-test",
    )


def test_setup_seeds_zero_until_the_first_pulse_frame(hold_cycle):
    runner = FakeControllerRunner()
    hold = hold_cycle(runner)

    hold.setup()

    assert [applied.source for applied in runner.applied] == [OutputSource.SEED]
    assert runner.applied[0].ratio == 0.0


def test_manual_auger_on_reports_full_duty(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0
    runner.applied.clear()

    hold._on_manual_output("auger", True)

    (applied,) = runner.applied
    assert applied.source is OutputSource.MANUAL_OVERRIDE
    assert applied.ratio == 1.0
    assert applied.controller_commanded is False
    assert applied.timestamp == 100.0


def test_manual_takeover_resets_the_active_frame_before_manual_feedback(hold_cycle, monkeypatch):
    class Recorder:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, now_ms):
            del now_ms

        def close(self):
            pass

    recorder = Recorder()
    monkeypatch.setattr(
        hold_mode_module,
        "ControlTraceRecorder",
        lambda **_kwargs: recorder,
    )
    runner = FakeControllerRunner(period=1.0).script(
        [
            replace(
                _completed_output(0.9),
                allocation=allocate(
                    0.9,
                    u_max=1.0,
                    fan_min_pct=0.0,
                    fan_max_pct=0.0,
                    enable_fan=False,
                ),
            )
        ]
    )
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold._last_now = 2.5
    hold._last_ptemp = 200.0
    runner.applied.clear()
    events = []
    reset_results = []
    set_output = runner.set_output
    observe_frame = runner.observe_frame
    runtime = hold._framed_pulse
    assert runtime is not None
    reset = runtime.reset
    admission = _mpc_admission_runtime()

    def record_output(applied):
        if applied.source is OutputSource.MANUAL_OVERRIDE:
            events.append(("manual-feedback", applied.feedback_disposition))
        set_output(applied)

    def record_observation(observation):
        events.append(("runner-observation", observation.reset))
        submission = observe_frame(observation)
        outcome = admission.observe_frame(observation)
        assert outcome is not None
        runner.append_observation_outcome(
            ObservationOutcomeEnvelope(
                submission.submission_sequence,
                submission.configuration_generation,
                observation,
                outcome,
            )
        )
        return submission

    def record_reset(*args, **kwargs):
        result = reset(*args, **kwargs)
        reset_results.append(result)
        events.append(("frame-reset", args[0]))
        return result

    monkeypatch.setattr(runner, "set_output", record_output)
    monkeypatch.setattr(runner, "observe_frame", record_observation)
    monkeypatch.setattr(runtime, "reset", record_reset)

    hold._on_manual_output("auger", False)

    assert events == [
        ("frame-reset", PulseResetReason.MANUAL),
        ("runner-observation", True),
        ("manual-feedback", FrameFeedbackDisposition.PROGRESS),
    ]
    (reset_result,) = reset_results
    (interrupted,) = reset_result.completions
    (observation,) = runner.observations
    assert interrupted.frame_key == (2_000, 2_500)
    assert interrupted.observation == observation
    assert (
        observation.frame_start_s,
        observation.frame_end_s,
        observation.result_revision,
        observation.observation_sequence,
    ) == (2.0, 2.5, 1, 1)
    assert (
        observation.output_source,
        observation.manual_override,
        observation.reset,
        observation.continuous,
    ) == (OutputSource.MANUAL_OVERRIDE.value, True, True, False)

    hold.on_tick(3.0, 200.0, hold.grill.get_output_status())

    scored = [
        record.payload
        for record in recorder.records
        if isinstance(record.payload, ModelObservationPayload)
    ]
    assert len(scored) == 1
    assert (
        scored[0].frame_start_ms,
        scored[0].frame_end_ms,
        scored[0].result_revision,
        scored[0].observation_sequence,
    ) == (2_000, 2_500, 1, 1)
    assert (
        scored[0].eligible,
        scored[0].rejection_reasons,
        scored[0].output_source,
        scored[0].manual_override,
        scored[0].reset,
        scored[0].continuous,
    ) == (
        False,
        ("manual",),
        OutputSource.MANUAL_OVERRIDE,
        True,
        True,
        False,
    )
    admission.close()


def test_manual_release_reseeds_before_fresh_controller_authority(hold_cycle, monkeypatch):
    runner = FakeControllerRunner(period=1.0).script([_completed_output(0.9)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold._last_now = 1.0
    hold._last_ptemp = 200.0
    hold._on_manual_output("auger", True)
    hold.state.manual_override["auger"] = 1.0
    events = []
    set_output = runner.set_output
    latest = runner.latest
    auger_on = hold.grill.auger_on

    def record_output(applied):
        events.append(("feedback", applied.source))
        set_output(applied)

    def record_latest():
        events.append(("runner", "latest"))
        return latest()

    def record_auger_on():
        events.append(("hardware", "auger-on"))
        auger_on()

    monkeypatch.setattr(runner, "set_output", record_output)
    monkeypatch.setattr(runner, "latest", record_latest)
    monkeypatch.setattr(hold.grill, "auger_on", record_auger_on)

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    seed = ("feedback", OutputSource.SEED)
    result = ("runner", "latest")
    authority = ("hardware", "auger-on")
    assert events.index(seed) < events.index(result) < events.index(authority)


def test_manual_auger_off_reports_zero(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0
    runner.applied.clear()

    hold._on_manual_output("auger", False)

    assert runner.applied[0].ratio == 0.0


def test_lid_open_reports_zero_with_the_pulse_request(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.target_temp_achieved = True
    hold.settings["cycle_data"]["LidOpenDetectEnabled"] = True
    hold.control["primary_setpoint"] = 225.0
    hold.state.controller.output = 0.5
    runner.applied.clear()

    hold.on_tick(now=100.0, ptemp=100.0, current_output_status=_off())

    (applied,) = [applied for applied in runner.applied if applied.source is OutputSource.LID_OPEN]
    assert applied.ratio == 0.0
    assert applied.requested == 0.5
    assert applied.timestamp == 100.0


def test_manual_output_does_not_report_other_actuators(hold_cycle):
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()

    for name in ("fan", "igniter", "power", "pwm"):
        hold._on_manual_output(name, True)

    assert runner.applied == []


def test_manual_auger_off_commands_active_hardware_off(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold._last_now = 100.0
    hold.grill.auger_on()
    runner.applied.clear()

    hold._on_manual_output("auger", False)

    assert hold.grill.get_output_status()["auger"] is False
    assert runner.applied[-1].source is OutputSource.MANUAL_OVERRIDE
    assert runner.applied[-1].ratio == 0.0


def test_lid_pause_expiry_restarts_fan_and_clears_status(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.lid.open_detected = True
    hold.state.lid.expires = 5.0
    hold.grill.fan_off()
    hold.ctx.clock.advance(6.0)

    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())

    assert hold.status_fragment()["lid_open_detected"] is False
    assert hold.grill.get_output_status()["fan"] is True


def test_operator_lid_toggle_clears_open_pause(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.state.lid.open_detected = True
    hold.state.lid.expires = 100.0
    hold.control["lid_open_toggle"] = True

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    assert hold.control["lid_open_toggle"] is False
    assert hold.status_fragment()["lid_open_detected"] is False


def test_non_auger_manual_release_leaves_controller_feedback_unchanged(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["fan"] = 50.0

    hold._on_manual_release("fan", 10.0)

    assert hold.state.manual_override["fan"] == 50.0
    assert runner.applied == []


def test_manual_release_without_reseed_still_turns_auger_off(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["auger"] = 50.0
    hold.grill.auger_on()

    hold._on_manual_release("auger", 10.0, reseed=False)

    assert hold.state.manual_override["auger"] == 0
    assert hold.grill.get_output_status()["auger"] is False
    assert runner.applied == []


def test_manual_release_without_reseed_preserves_already_off_auger(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    runner.applied.clear()
    hold.state.manual_override["auger"] = 50.0

    hold._on_manual_release("auger", 10.0, reseed=False)

    assert hold.state.manual_override["auger"] == 0
    assert hold.grill.get_output_status()["auger"] is False
    assert runner.applied == []


def test_operator_lid_toggle_opens_pause_and_turns_fan_off(
    hold_cycle,
) -> None:
    runner = FakeControllerRunner(period=999)
    hold = hold_cycle(runner)
    hold.setup()
    hold.control["lid_open_toggle"] = True
    runner.applied.clear()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())

    assert hold.control["lid_open_toggle"] is False
    assert hold.status_fragment()["lid_open_detected"] is True
    assert hold.grill.get_output_status()["fan"] is False
    assert runner.applied[-1].source is OutputSource.LID_OPEN
    assert runner.applied[-1].ratio == 0.0
