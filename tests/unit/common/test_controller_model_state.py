"""The store owns "is this a bounded, JSON-safe record" and nothing else.

It never judges model fields or physics -- that is the controller's job in
restore_model -- so these tests use two unrelated snapshot shapes to prove it is
genuinely model-agnostic.
"""

import json
import logging

import pytest

from common.controller_model_state import (
    MAX_SNAPSHOT_BYTES,
    MODEL_STATE_KEY,
    SCHEMA_VERSION,
    ControllerModelStore,
)

FOPDT = {"revision": 1, "K": 761.0, "tau": 635.0, "theta": 25.0}
UNRELATED = {"revision": 1, "coefficients": [0.1, 0.2], "label": "something else entirely"}


class _FakeStore:
    def __init__(self, initial=None):
        self.blobs = dict(initial or {})
        self.writes = 0

    def read(self, key):
        # matches read_generic_key: json.loads(None) on an absent key
        return json.loads(self.blobs[key]) if key in self.blobs else json.loads(None)

    def write(self, key, value):
        self.writes += 1
        self.blobs[key] = json.dumps(value)


def _store():
    fake = _FakeStore()
    return ControllerModelStore(reader=fake.read, writer=fake.write), fake


@pytest.mark.parametrize("snapshot", [FOPDT, UNRELATED])
def test_round_trips_any_json_safe_shape(snapshot):
    store, _ = _store()
    assert store.save("pid_sp", snapshot) is True
    assert store.load("pid_sp") == snapshot


def test_load_returns_none_for_an_absent_key():
    store, _ = _store()
    assert store.load("pid_sp") is None


def test_load_returns_none_for_an_absent_controller():
    store, _ = _store()
    store.save("pid_sp", FOPDT)
    assert store.load("mpc") is None


def test_controllers_do_not_cross_contaminate():
    store, _ = _store()
    store.save("pid_sp", FOPDT)
    store.save("mpc", UNRELATED)
    assert store.load("pid_sp") == FOPDT
    assert store.load("mpc") == UNRELATED


def test_skips_a_non_advancing_revision():
    store, fake = _store()
    assert store.save("pid_sp", {"revision": 5, "K": 700.0}) is True
    writes = fake.writes
    assert store.save("pid_sp", {"revision": 5, "K": 999.0}) is False
    assert store.save("pid_sp", {"revision": 4, "K": 999.0}) is False
    assert fake.writes == writes
    assert store.load("pid_sp")["K"] == 700.0
    assert store.save("pid_sp", {"revision": 6, "K": 999.0}) is True


def test_load_primes_the_revision_cache():
    fake = _FakeStore()
    first = ControllerModelStore(reader=fake.read, writer=fake.write)
    first.save("pid_sp", {"revision": 5, "K": 700.0})
    second = ControllerModelStore(reader=fake.read, writer=fake.write)
    second.load("pid_sp")
    assert second.save("pid_sp", {"revision": 5, "K": 999.0}) is False


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        {},
        [],
        "not a dict",
        {"K": 700.0},  # no revision
        {"revision": "1"},  # revision not an int
        {"revision": True},  # bool is not an acceptable int here
        {"revision": -1},
        {"revision": 1, "K": float("nan")},
        {"revision": 1, "K": float("inf")},
        {"revision": 1, "obj": object()},
    ],
)
def test_rejects_a_malformed_snapshot(snapshot):
    store, fake = _store()
    assert store.save("pid_sp", snapshot) is False
    assert fake.writes == 0


def test_rejects_an_oversized_snapshot():
    store, fake = _store()
    assert store.save("pid_sp", {"revision": 1, "blob": "x" * MAX_SNAPSHOT_BYTES}) is False
    assert fake.writes == 0


@pytest.mark.parametrize(
    "raw",
    [
        "not a dict",
        {"version": SCHEMA_VERSION + 1, "models": {"pid_sp": FOPDT}},
        {"models": {"pid_sp": FOPDT}},
        {"version": SCHEMA_VERSION, "models": "not a dict"},
        {"version": SCHEMA_VERSION},
    ],
)
def test_reads_are_fail_closed_on_a_bad_envelope(raw):
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps(raw)})
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.load("pid_sp") is None


def test_a_bad_member_does_not_poison_a_good_one():
    fake = _FakeStore(
        {
            MODEL_STATE_KEY: json.dumps(
                {"version": SCHEMA_VERSION, "models": {"pid_sp": FOPDT, "mpc": {"no": "revision"}}}
            )
        }
    )
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.load("pid_sp") == FOPDT
    assert store.load("mpc") is None


def test_a_read_failure_does_not_raise():
    def boom(key):
        raise RuntimeError("datastore is down")

    store = ControllerModelStore(reader=boom, writer=lambda k, v: None)
    assert store.load("pid_sp") is None


def test_a_write_failure_returns_false():
    def boom(key, value):
        raise RuntimeError("datastore is down")

    store = ControllerModelStore(reader=lambda k: json.loads(None), writer=boom)
    assert store.save("pid_sp", FOPDT) is False


def test_a_save_without_a_prior_load_still_honors_the_stored_revision():
    # I3: the revision guard must not depend on the caller having called load()
    # first. A fresh instance's first save() has an empty cache -- it must fall
    # back to whatever is actually persisted, not treat "nothing cached yet" as
    # "nothing to protect".
    fake = _FakeStore()
    first = ControllerModelStore(reader=fake.read, writer=fake.write)
    first.save("pid_sp", {"revision": 57, "K": 700.0})

    second = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert second.save("pid_sp", {"revision": 1, "K": 999.0}) is False
    assert second.load("pid_sp")["K"] == 700.0
    assert second.save("pid_sp", {"revision": 58, "K": 999.0}) is True
    assert second.load("pid_sp")["K"] == 999.0


def test_a_reset_revision_after_a_restart_is_rejected_and_logged(caplog):
    # C1: a producer whose revision counter is per-process (resets on restart)
    # must be rejected -- and that rejection must be visible, not a bare False
    # that looks identical to every other reason a save can fail.
    fake = _FakeStore()
    first = ControllerModelStore(reader=fake.read, writer=fake.write)
    first.save("pid_sp", {"revision": 57, "K": 700.0})

    second = ControllerModelStore(reader=fake.read, writer=fake.write)
    with caplog.at_level(logging.WARNING, logger="control"):
        result = second.save("pid_sp", {"revision": 1, "K": 999.0})
    assert result is False
    assert fake.writes == 1  # only the first, genuine save landed
    assert "pid_sp" in caplog.text
    assert "57" in caplog.text  # names the stored baseline it lost to


def test_a_transient_read_failure_blocks_the_write_instead_of_erasing_other_entries():
    # I2: "I could not read it" must not collapse into "there is nothing there".
    # A flaky reader must never cause save() to persist a fresh blob that drops
    # every other controller's snapshot.
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps({"version": SCHEMA_VERSION, "models": {"mpc": UNRELATED}})})

    def flaky_read(key):
        raise RuntimeError("datastore is down")

    store = ControllerModelStore(reader=flaky_read, writer=fake.write)
    assert store.save("pid_sp", FOPDT) is False
    assert fake.writes == 0
    assert json.loads(fake.blobs[MODEL_STATE_KEY])["models"] == {"mpc": UNRELATED}


def test_an_unrecognized_version_blocks_the_write_instead_of_downgrading_it():
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps({"version": SCHEMA_VERSION + 1, "models": {"mpc": UNRELATED}})})
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.save("pid_sp", FOPDT) is False
    assert fake.writes == 0
    stored = json.loads(fake.blobs[MODEL_STATE_KEY])
    assert stored["version"] == SCHEMA_VERSION + 1
    assert stored["models"] == {"mpc": UNRELATED}


@pytest.mark.parametrize(
    "raw",
    [
        "not a dict",
        {"version": SCHEMA_VERSION + 1, "models": {"mpc": UNRELATED}},
        {"models": {"mpc": UNRELATED}},
        {"version": SCHEMA_VERSION, "models": "not a dict"},
        {"version": SCHEMA_VERSION},
    ],
)
def test_save_is_fail_closed_on_a_bad_envelope(raw):
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps(raw)})
    store = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert store.save("pid_sp", FOPDT) is False
    assert fake.writes == 0


def test_round_trips_through_the_real_datastore_seam(ds):
    # The fake in every other test matches read_generic_key's documented
    # behavior on purpose, but only this test exercises the actual seam:
    # ControllerModelStore() with no injected reader/writer, backed by a real
    # (temp-file) SQLite datastore via common.datastore_accessors.
    store = ControllerModelStore()
    assert store.load("pid_sp") is None
    assert store.save("pid_sp", FOPDT) is True
    assert store.load("pid_sp") == FOPDT
    assert ControllerModelStore().load("pid_sp") == FOPDT
