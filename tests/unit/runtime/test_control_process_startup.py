"""Control-process startup preserves unfinished cook state across routine restarts."""

from types import SimpleNamespace

from common.control_delta import CONTROL_DELTA_KEY, CONTROL_DELTA_VERSION
from common.defaults import default_control, default_metrics
from common.persistence.control import write_control_snapshot
from common.persistence.history import append_metric, read_all_metrics, read_history, write_history
from common.timer import checkpoint_timer, start_timer
from control import _initialize_runtime_state
from controller.runtime.clock import ManualClock
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.controller import Controller
from controller.runtime.store import SqliteStore
from tests.fakes.clock import clock_stamp

_HISTORY_ROW = {
    "probe_history": {"primary": {"Grill": 225}, "food": {}, "aux": {}},
    "primary_setpoint": 225,
    "notify_targets": {"Grill": 225},
}


def _seed_control(cook_id: str) -> None:
    control = default_control()
    control["cook_id"] = cook_id
    control["mode"] = "Hold"
    control["manual"]["pwm"] = 37
    write_control_snapshot(control, origin="control")


class _ProcessPersistence:
    def __init__(self) -> None:
        self.close_calls = []

    def close(self, timeout=2.0):
        self.close_calls.append(timeout)
        return True


class _ProcessTrajectoryRuntime:
    def __init__(self, persistence: _ProcessPersistence) -> None:
        self.persistence = persistence
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.persistence.close(timeout=2.0)


def test_process_shutdown_closes_shared_trajectory_and_persistence_exactly_once() -> None:
    persistence = _ProcessPersistence()
    trajectory = _ProcessTrajectoryRuntime(persistence)
    repository = object()
    grill = SimpleNamespace(cleanup_calls=0)

    def cleanup_grill():
        grill.cleanup_calls += 1

    grill.cleanup = cleanup_grill
    ctx = ControllerContext(
        devices=Devices(
            grill_platform=grill,
            probe_complex=object(),
            dist_device=object(),
        ),
        store=SimpleNamespace(read_settings=dict),
        notifications=object(),
        clock=ManualClock(),
        event_log=SimpleNamespace(info=lambda _message: None),
        control_log=SimpleNamespace(info=lambda _message: None),
        trajectory_repository=repository,
        model_persistence=persistence,
        learning_trajectory=trajectory,
    )
    controller = Controller(ctx)

    controller.cleanup()
    controller.cleanup()

    assert controller.ctx.trajectory_repository is repository
    assert controller.ctx.learning_trajectory is trajectory
    assert trajectory.close_calls == 1
    assert persistence.close_calls == [2.0]
    assert grill.cleanup_calls == 1


def test_routine_restart_preserves_prime_carry_over_identity_and_metrics(ds):
    _seed_control("prime-cook-session")
    append_metric(dict(default_metrics(), mode="Prime"))
    before_metrics = read_all_metrics()

    reset = _initialize_runtime_state(SqliteStore())

    assert reset["mode"] == "Stop"
    assert reset["manual"]["pwm"] == 100
    assert reset["cook_id"] == "prime-cook-session"
    assert read_all_metrics() == before_metrics


def test_routine_restart_preserves_only_accepted_history_clear_commands(ds):
    store = SqliteStore()
    store.system_commands().push(["scan"])
    store.system_commands().push(["clear_history"])
    store.system_commands().push(["check_alive"])

    _initialize_runtime_state(store)

    assert store.system_commands().list() == [["clear_history"]]


def test_routine_restart_preserves_failed_archive_identity_history_and_metrics(ds):
    _seed_control("failed-archive-session")
    write_history(_HISTORY_ROW)
    append_metric(dict(default_metrics(), mode="Hold"))
    before_history = read_history()
    before_metrics = read_all_metrics()

    reset = _initialize_runtime_state(SqliteStore())

    assert reset["cook_id"] == "failed-archive-session"
    assert read_history() == before_history
    assert read_all_metrics() == before_metrics


def test_routine_restart_discards_identity_without_unfinished_session_data(ds):
    _seed_control("stale-session")

    reset = _initialize_runtime_state(SqliteStore())

    assert reset["cook_id"] is None


def test_restart_retains_timer_checkpoint_and_rejects_queued_old_generation(ds, caplog):
    store = SqliteStore()
    old_stamp = clock_stamp(monotonic_s=100.0)
    checkpoint = clock_stamp(monotonic_s=103.0)
    control = default_control()
    control["timer"] = checkpoint_timer(start_timer(10.0, old_stamp), checkpoint)
    control["notify_data"] = [{"type": "timer", "label": "Timer", "req": True, "shutdown": True, "keep_warm": False}]
    store.write_control_snapshot(control, origin="test")
    store.enqueue_control_delta(
        {
            CONTROL_DELTA_KEY: CONTROL_DELTA_VERSION,
            "set": {"mode": "Shutdown"},
            "ops": [
                {
                    "op": "timer.start_or_resume",
                    "requested_wall_s": old_stamp.observed_wall_s,
                    "target_runtime_id": old_stamp.runtime_id,
                    "seconds": 10,
                }
            ],
        },
        origin="test-old-generation",
    )

    restored = _initialize_runtime_state(store)
    assert restored["timer"]["state"] == "interrupted"
    assert restored["timer"]["remaining_s"] == 7.0
    assert restored["timer"]["action_armed"] is False
    notification = next(item for item in restored["notify_data"] if item["type"] == "timer")
    assert notification["req"] is False
    assert notification["shutdown"] is True
    fresh = clock_stamp(
        monotonic_s=1000.0,
        runtime_id="01b4a094-8a2c-4780-915d-081f5a850a97",
    )
    store.execute_control_writes(timer_now=fresh)
    assert store.read_control()["mode"] == "Stop"
    assert store.read_control()["timer"]["remaining_s"] == 7.0
    assert "retired control generation" in caplog.text


def test_restart_preserves_legacy_timer_diagnostic_without_wall_recovery(ds):
    store = SqliteStore()
    control = default_control()
    legacy = {"start": 1_800_000_000.0, "end": 1_800_000_600.0, "paused": 0}
    control["timer"] = legacy
    store.write_control_snapshot(control, origin="test")
    restored = _initialize_runtime_state(store)
    assert restored["timer"]["state"] == "interrupted"
    assert restored["timer"]["remaining_s"] is None
    assert restored["timer"]["action_armed"] is False
    assert restored["timer_migration_diagnostic"] == legacy
