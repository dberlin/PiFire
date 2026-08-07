from common.controller_model_state import CheckpointSaveOutcome
from common.control_trace import TraceEventKind
from common.model_evidence import EvidenceKind
from controller.linear_mpc.calibration import CalibrationDecision, CalibrationProgress
from controller.mpc_allocator import allocate
from controller.runtime.runner import ControllerUpdateResult

from tests.fakes.runner import FakeControllerRunner


def _result(revision=1, *, baseline=0.3, probe=0.0, command_revision=1, command_action="start"):
    baseline_allocation = allocate(baseline, u_max=0.9, fan_min_pct=0.0, fan_max_pct=100.0, enable_fan=False)
    allocation = allocate(baseline + probe, u_max=0.9, fan_min_pct=0.0, fan_max_pct=100.0, enable_fan=False)
    return ControllerUpdateResult(
        cycle_ratio=allocation.auger_duty,
        fan=None,
        input_temperature=200.0,
        allocation=allocation,
        baseline_allocation=baseline_allocation,
        calibration=CalibrationDecision(
            probe != 0.0,
            probe,
            "low" if probe else None,
            CalibrationProgress(),
            command_revision=command_revision,
            command_action=command_action,
        ),
        revision=revision,
        solve_start_monotonic=0.0,
        solve_end_monotonic=0.0,
        solve_duration_seconds=0.0,
        completed_wall_time=0.0,
    )


def test_hold_consumes_latest_calibration_revision_once_across_reconfiguration(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(), _result(2), _result(3)])
    hold = hold_cycle(runner, controller="mpc")
    hold.control["mpc_calibration"] = {
        "action": "start",
        "revision": 1,
        "maximum_temperature_c": 130.0,
        "ambient_c": 20.0,
        "ambient_source": "configured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())

    assert [command.command_revision for command in runner.calibration_requests] == [1]


def test_hold_cancels_active_probe_without_reserving_an_operator_revision(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.lid.open_detected = True
    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    assert runner.calibration_cancellations == ["lid_open"]
    assert runner.calibration_requests == []



def test_hold_records_baseline_and_probe_on_framed_observation(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(22.0, 200.0, hold.grill.get_output_status())

    assert runner.observations[0].baseline_q == 0.3
    assert runner.observations[0].probe_q == 0.1
    assert runner.observations[0].requested_q == 0.4


def test_default_five_second_polls_terminalize_only_the_twenty_second_frame(hold_cycle):
    runner = FakeControllerRunner(period=5.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.state.controller.cycle_start = -6.0

    for now in (0.0, 6.0, 12.0, 18.0, 26.0):
        hold.on_tick(now, 200.0, hold.grill.get_output_status())

    terminal = [item for item in runner.applied if item.feedback_disposition.value != "progress"]
    routine = [item for item in runner.applied if item.feedback_disposition.value == "progress"]
    assert len(routine) >= 3
    assert [item.feedback_disposition.value for item in terminal] == ["complete"]
    assert len(runner.observations) == 1


def test_hold_stamps_latched_probe_frame_before_lid_reset(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.result_revision == 1
    assert cancelled.calibration_command_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "lid_open"
    assert cancelled.cancellation_command_action == "safety-cancel"
    assert cancelled.cancellation_command_revision == 0



def test_boundary_cancellation_preserves_completed_frame_and_marks_reset_partial(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(23.0, 200.0, hold.grill.get_output_status())

    completed, cancelled = runner.observations[-2:]
    assert completed.result_revision == 1
    assert completed.calibration_status == "active"
    assert completed.calibration_cancellation_reason is None
    assert cancelled.result_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "lid_open"
    assert cancelled.cancellation_command_action == "safety-cancel"

def test_hold_stamps_latched_probe_frame_before_reconfigure_reset(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())

    cancelled = runner.observations[-1]
    assert cancelled.result_revision == 1
    assert cancelled.calibration_status == "cancelled"
    assert cancelled.calibration_cancellation_reason == "reset"
    assert cancelled.cancellation_command_action == "safety-cancel"
    assert cancelled.cancellation_command_revision == 0


def test_hold_does_not_carry_cancelled_frame_status_into_later_baseline(hold_cycle):
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1), _result(2)])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.control["controller_update"] = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(24.0, 200.0, hold.grill.get_output_status())

    cancelled, baseline = runner.observations[-2:]
    assert cancelled.calibration_status == "cancelled"
    assert baseline.result_revision == 2
    assert baseline.probe_q == 0.0
    assert baseline.calibration_status == "inactive"
    assert baseline.calibration_cancellation_reason is None
    assert baseline.cancellation_command_action == "none"


def test_cancelled_frame_persists_matching_raw_and_compact_evidence_once(hold_cycle, monkeypatch):
    import controller.runtime.modes.hold as hold_module

    class Recorder:
        def __init__(self, *, warning):
            self.records = []

        def record(self, record):
            self.records.append(record)

        def flush_due(self, _now_ms):
            pass

        def close(self):
            pass

    class Store:

        def load(self, _name):
            return None
        def save_outcome(self, _name, _snapshot):
            return CheckpointSaveOutcome.SAVED

    recorder = Recorder(warning=lambda _message: None)
    persisted = []
    real_worker = hold_module.ModelPersistenceWorker

    class CapturingWorker(real_worker):
        def __init__(self, store, logger):
            super().__init__(store, logger, append_evidence=persisted.extend)

    monkeypatch.setattr(hold_module, "ControlTraceRecorder", lambda *, warning: recorder)
    monkeypatch.setattr(hold_module, "ModelPersistenceWorker", CapturingWorker)
    runner = FakeControllerRunner(period=1.0).script([_result(probe=0.1)])
    runner.observation_outcome = {
        "role_generation": 0,
        "eligible": False,
        "rejection_reasons": ("reset",),
        "input_variance": 0.0,
        "input_levels": 0,
        "incumbent_innovation_c": None,
        "challenger_innovation_c": None,
        "effective_updates": 0,
        "model_digest": None,
    }
    hold = hold_cycle(runner, model_store=Store(), controller="mpc")
    hold.setup()
    hold.state.metrics = {"id": "cook-calibration"}

    hold.on_tick(2.0, 200.0, hold.grill.get_output_status())
    hold.state.lid.open_detected = True
    hold.on_tick(4.0, 200.0, hold.grill.get_output_status())
    hold.on_tick(6.0, 200.0, hold.grill.get_output_status())
    assert hold._persistence_worker.flush_and_stop(timeout=1.0)

    raw = next(record.payload for record in recorder.records if record.event_kind is TraceEventKind.MODEL_OBSERVATION)
    compact = next(record for record in persisted if record.kind is EvidenceKind.CALIBRATION_SUMMARY).payload
    assert compact.result_revision == raw.result_revision == 1
    assert compact.command_revision == raw.calibration_command_revision == 1
    assert compact.command_action == raw.calibration_command_action == "start"
    assert compact.status == raw.calibration_status == "cancelled"
    assert compact.cancellation_reason == raw.calibration_cancellation_reason == "lid_open"
    assert compact.cancellation_command_revision == raw.cancellation_command_revision == 0
    assert compact.cancellation_command_action == raw.cancellation_command_action == "safety-cancel"
    assert compact.probe_count == 0
    assert len(persisted) == 1
