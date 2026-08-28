"""Control-process startup preserves unfinished cook state across routine restarts."""

from types import SimpleNamespace

from common.defaults import default_control, default_metrics
from common.persistence.control import write_control_snapshot
from common.persistence.history import append_metric, read_all_metrics, read_history, write_history
from control import _initialize_runtime_state
from controller.runtime.context import ControllerContext, Devices
from controller.runtime.controller import Controller
from controller.runtime.store import SqliteStore

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
        clock=SimpleNamespace(now=lambda: 0.0),
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
