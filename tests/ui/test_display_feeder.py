import pytest

import display_process
from controller.runtime.clock import ManualClock
from controller.runtime.store import InMemoryStore
from display_process import DisplayFeeder


def test_main_refuses_to_create_missing_database(tmp_path):
    original_db_path = display_process.datastore.DB_PATH
    database_path = tmp_path / "pifire.db"
    try:
        display_process.datastore._reset_for_tests(str(database_path))
        with pytest.raises(display_process.datastore.DatabaseNotFoundError):
            display_process.main()
        assert not database_path.exists()
    finally:
        display_process.datastore._reset_for_tests(original_db_path)


class _FakeDisplay:
    def __init__(self):
        self.calls = []

    def display_status(self, i, s):
        self.calls.append(("status", i, s))

    def display_text(self, t):
        self.calls.append(("text", t))

    def clear_display(self):
        self.calls.append(("clear",))

    def display_splash(self):
        self.calls.append(("splash",))


def test_feeder_pushes_status_and_drains_display_queue():
    store = InMemoryStore(current={"P": {}}, status={"mode": "Hold", "units": "F"})
    store.display_commands().push(("text", "ERROR"))
    store.display_commands().push(("clear", None))
    disp = _FakeDisplay()
    DisplayFeeder(disp, store, ManualClock()).tick()
    assert ("status", {"P": {}}, {"mode": "Hold", "units": "F"}) in disp.calls
    assert ("text", "ERROR") in disp.calls
    assert ("clear",) in disp.calls
    assert disp.calls.index(("text", "ERROR")) < disp.calls.index(("clear",))


def test_feeder_skips_status_when_current_or_status_empty():
    store = InMemoryStore(current={}, status={})
    disp = _FakeDisplay()
    DisplayFeeder(disp, store, ManualClock()).tick()
    assert not any(call[0] == "status" for call in disp.calls)


def test_feeder_skips_status_when_only_current_present():
    store = InMemoryStore(current={"P": {}}, status={})
    disp = _FakeDisplay()
    DisplayFeeder(disp, store, ManualClock()).tick()
    assert not any(call[0] == "status" for call in disp.calls)
