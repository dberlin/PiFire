"""The store owns "is this a bounded, JSON-safe record" and nothing else.

It never judges model fields or physics -- that is the controller's job in
restore_model -- so these tests use two unrelated snapshot shapes to prove it is
genuinely model-agnostic.
"""

import json
import logging
import threading

import pytest
from common import datastore

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


def test_round_trips_a_composite_mpc_snapshot_without_interpreting_the_nested_model():
    snapshot = {
        "revision": 7,
        "version": 3,
        "params": {"C_c": 2520.0, "h_amb": 0.224},
        "online_adaptation": {
            "schema": "online-adaptation/v1",
            "active_model_kind": "scheduled-arx",
            "role_generation": 2,
            "challenger": {"schema": "scheduled-arx/v2", "regions": []},
        },
    }
    store, _ = _store()

    assert store.save("mpc", snapshot) is True
    assert store.load("mpc") == snapshot


def test_accepts_the_exact_default_json_byte_limit_and_rejects_one_more_byte():
    snapshot = {
        "revision": 1,
        "online_adaptation": {"schema": "online-adaptation/v1", "payload": ""},
    }
    base_size = len(json.dumps(snapshot, allow_nan=False).encode("utf-8"))
    snapshot["online_adaptation"]["payload"] = "x" * (MAX_SNAPSHOT_BYTES - base_size)
    assert len(json.dumps(snapshot, allow_nan=False).encode("utf-8")) == MAX_SNAPSHOT_BYTES

    store, fake = _store()
    assert store.save("mpc", snapshot) is True
    writes = fake.writes

    oversized = json.loads(json.dumps(snapshot))
    oversized["revision"] = 2
    oversized["online_adaptation"]["payload"] += "x"
    assert store.save("mpc", oversized) is False
    assert fake.writes == writes


def test_restarted_store_requires_a_newer_composite_mpc_revision():
    fake = _FakeStore()
    first = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert first.save("mpc", {"revision": 41, "online_adaptation": {"role_generation": 3}}) is True

    restarted = ControllerModelStore(reader=fake.read, writer=fake.write)
    assert restarted.save("mpc", {"revision": 41, "online_adaptation": {"role_generation": 4}}) is False
    assert restarted.save("mpc", {"revision": 42, "online_adaptation": {"role_generation": 4}}) is True


def test_load_returns_none_for_an_absent_key():
    store, _ = _store()
    assert store.load("pid_sp") is None


def test_load_returns_none_for_an_absent_controller():
    store, _ = _store()
    store.save("pid_sp", FOPDT)
    assert store.load("mpc") is None


def test_strict_load_returns_none_only_for_an_absent_controller():
    store, _ = _store()

    assert store.load_strict("pid_sp") is None


def test_strict_load_rejects_a_malformed_target_without_poisoning_a_good_controller():
    fake = _FakeStore(
        {
            MODEL_STATE_KEY: json.dumps(
                {
                    "version": SCHEMA_VERSION,
                    "models": {
                        "pid_sp": {"revision": "not-an-integer"},
                        "mpc": UNRELATED,
                    },
                }
            )
        }
    )
    store = ControllerModelStore(reader=fake.read, writer=fake.write)

    with pytest.raises(ValueError, match="malformed stored snapshot.*pid_sp"):
        store.load_strict("pid_sp")

    assert store.load("mpc") == UNRELATED


def test_strict_load_revalidates_persistence_even_when_shared_cache_is_warm():
    store, fake = _store()
    assert store.save("pid_sp", FOPDT) is True
    assert store.load("pid_sp") == FOPDT
    fake.blobs[MODEL_STATE_KEY] = json.dumps(
        {
            "version": SCHEMA_VERSION,
            "models": {"pid_sp": {"revision": "interrupted-write"}},
        }
    )

    with pytest.raises(ValueError, match="malformed stored snapshot.*pid_sp"):
        store.load_strict("pid_sp")


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


def test_load_sees_a_newer_revision_written_after_the_shared_cache_warmed():
    # The in-process cache is shared by every store instance and only ever
    # advances when THIS process remembers a snapshot. A process that only
    # reads -- the web worker serving the learning report -- therefore has no
    # path by which a checkpoint written by the control process can reach it,
    # and pins whatever it first read for as long as it lives.
    store, fake = _store()
    assert store.save("mpc", {"revision": 1, "K": 700.0}) is True
    assert store.load("mpc")["K"] == 700.0

    fake.blobs[MODEL_STATE_KEY] = json.dumps(
        {
            "version": SCHEMA_VERSION,
            "models": {"mpc": {"revision": 2, "K": 999.0}},
        }
    )

    assert store.load("mpc") == {"revision": 2, "K": 999.0}


def test_load_keeps_a_staged_snapshot_that_storage_has_not_caught_up_to():
    # The other half of the same contract: staging exists so a worker can read
    # back what it has published but not yet written, so a re-read of older
    # stored bytes must not clobber it.
    store, _fake = _store()
    assert store.save("mpc", {"revision": 1, "K": 700.0}) is True
    assert store.stage_owned("mpc", {"revision": 2, "K": 999.0}) is True

    assert store.load("mpc") == {"revision": 2, "K": 999.0}


def test_load_keeps_the_warm_cache_when_the_stored_record_cannot_be_read():
    store, fake = _store()
    assert store.save("mpc", {"revision": 1, "K": 700.0}) is True
    fake.blobs[MODEL_STATE_KEY] = json.dumps({"version": SCHEMA_VERSION + 1, "models": {}})

    assert store.load("mpc") == {"revision": 1, "K": 700.0}


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


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), object()])
def test_rejects_non_json_safe_values_nested_in_a_composite_snapshot(bad_value):
    store, fake = _store()
    snapshot = {
        "revision": 1,
        "online_adaptation": {
            "schema": "online-adaptation/v1",
            "challenger": {"state": [0.0, bad_value]},
        },
    }

    assert store.save("mpc", snapshot) is False
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


def test_concurrent_store_instances_recheck_persisted_revision_after_waiting():
    fake = _FakeStore({MODEL_STATE_KEY: json.dumps({"version": SCHEMA_VERSION, "models": {"mpc": {"revision": 0}}})})
    advanced_write_entered = threading.Event()
    release_advanced_write = threading.Event()
    stale_finished = threading.Event()
    stale_results = []

    def blocking_write(key, value):
        revision = value["models"]["mpc"]["revision"]
        if revision == 2:
            advanced_write_entered.set()
            assert release_advanced_write.wait(1.0)
        fake.write(key, value)

    advanced = ControllerModelStore(reader=fake.read, writer=blocking_write)
    stale = ControllerModelStore(reader=fake.read, writer=blocking_write)
    assert stale.load("mpc") == {"revision": 0}
    advanced_thread = threading.Thread(target=lambda: advanced.save("mpc", {"revision": 2}))
    stale_thread = threading.Thread(
        target=lambda: (stale_results.append(stale.save("mpc", {"revision": 1})), stale_finished.set())
    )

    advanced_thread.start()
    assert advanced_write_entered.wait(1.0)
    stale_thread.start()
    stale_finished_before_release = stale_finished.wait(0.05)
    release_advanced_write.set()
    advanced_thread.join(timeout=1.0)
    stale_thread.join(timeout=1.0)

    assert not stale_finished_before_release
    assert not advanced_thread.is_alive()
    assert not stale_thread.is_alive()
    assert stale_results == [False]
    assert fake.read(MODEL_STATE_KEY)["models"]["mpc"]["revision"] == 2


def test_a_reset_revision_is_logged_at_error_under_the_shipped_default_log_level(caplog):
    # control.py sets the "control" logger to ERROR whenever debug_mode is off
    # -- the shipped default -- so a bare .warning() never reaches a log file
    # in production. This test does NOT loosen that threshold with
    # caplog.at_level(); it reproduces the exact production configuration and
    # proves the message survives anyway, because a revision going backwards is
    # the one outcome that must be logged at ERROR, not merely observable when
    # a test raises the level for it.
    control_logger = logging.getLogger("control")
    previous_level = control_logger.level
    control_logger.setLevel(logging.ERROR)  # matches control.py's debug_mode=False branch
    try:
        fake = _FakeStore()
        first = ControllerModelStore(reader=fake.read, writer=fake.write)
        first.save("pid_sp", {"revision": 57, "K": 700.0})

        second = ControllerModelStore(reader=fake.read, writer=fake.write)
        result = second.save("pid_sp", {"revision": 1, "K": 999.0})
    finally:
        control_logger.setLevel(previous_level)

    assert result is False
    assert fake.writes == 1  # only the first, genuine save landed
    assert any(record.levelno >= logging.ERROR for record in caplog.records)
    assert "pid_sp" in caplog.text
    assert "57" in caplog.text  # names the stored baseline it lost to


def test_an_equal_revision_is_rejected_quietly_not_as_an_error(caplog):
    # The other half of the same rejection path: a revision equal to the last
    # persisted one means nothing changed -- the expected, frequent outcome on
    # a control interval where the controller learned nothing new. This must
    # stay below ERROR, or the shipped default log would drown in noise from
    # every quiescent tick of every controller.
    store, fake = _store()
    store.save("pid_sp", {"revision": 5, "K": 700.0})
    with caplog.at_level(logging.DEBUG, logger="control"):
        result = store.save("pid_sp", {"revision": 5, "K": 999.0})
    assert result is False
    assert fake.writes == 1  # only the first, genuine save landed
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


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


def test_load_priming_avoids_a_read_on_a_subsequent_cache_hit_save():
    # load()'s priming of the revision cache is a pure performance optimization,
    # not a correctness requirement: I3's cold-path fallback already re-derives
    # the baseline from a fresh read whenever the cache is empty, so a correct
    # reject-or-accept answer does not depend on load() having run first. What
    # priming buys is skipping that read entirely on a cache hit -- pin it so a
    # future change cannot silently turn the cheap, common "nothing changed"
    # path back into one that reads storage on every call.
    fake = _FakeStore()
    fake.reads = 0
    underlying_read = fake.read

    def counting_read(key):
        fake.reads += 1
        return underlying_read(key)

    first = ControllerModelStore(reader=counting_read, writer=fake.write)
    first.save("pid_sp", {"revision": 5, "K": 700.0})

    second = ControllerModelStore(reader=counting_read, writer=fake.write)
    second.load("pid_sp")
    reads_before = fake.reads
    assert second.save("pid_sp", {"revision": 5, "K": 999.0}) is False
    assert fake.reads == reads_before  # cache hit primed by load(): no read needed


def test_a_full_identifier_bank_round_trips():
    """A full identifier bank is 25 RLS candidates: coefficients (25x3), covariances
    (25x3x3) and residuals (25). That is ~7 KB of JSON -- under the old cap,
    but with too little headroom to build on."""
    import numpy as np

    rng = np.random.default_rng(0)
    snapshot = {
        "revision": 3,
        "Theta": rng.normal(size=(25, 3)).tolist(),
        "P": rng.normal(size=(25, 3, 3)).tolist(),
        "resid_ew": rng.normal(size=25).tolist(),
        "trusted": {"K": 1.23, "tau": 3750.0, "theta": 95.0},
    }
    store, _ = _store()
    assert store.save("mpc", snapshot) is True
    assert store.load("mpc")["revision"] == 3


def test_a_bank_larger_than_the_identifier_will_ever_build_still_fits():
    # A bank of this size (70 candidates) exceeds anything the identifier is
    # expected to grow to; it exists to discriminate the raised cap from the
    # old one, not to model a realistic snapshot.
    import numpy as np

    rng = np.random.default_rng(1)
    n = 70
    snapshot = {
        "revision": 3,
        "Theta": rng.normal(size=(n, 3)).tolist(),
        "P": rng.normal(size=(n, 3, 3)).tolist(),
        "resid_ew": rng.normal(size=n).tolist(),
        "trusted": {"K": 1.23, "tau": 3750.0, "theta": 95.0},
    }
    store, _ = _store()
    assert store.save("mpc", snapshot) is True
    assert store.load("mpc") == snapshot


def test_round_trips_through_the_real_datastore_seam(ds):
    # The fake in every other test matches read_generic_key's documented
    # behavior on purpose, but only this test exercises the real path:
    # ControllerModelStore() with no injected reader/writer, backed by a real
    # (temp-file) SQLite datastore via common.persistence.runtime.
    store = ControllerModelStore()
    assert store.load("pid_sp") is None
    assert store.save("pid_sp", FOPDT) is True
    assert store.load("pid_sp") == FOPDT
    assert ControllerModelStore().load("pid_sp") == FOPDT


def test_default_store_backend_identity_follows_repointed_datastore_path(ds, tmp_path):
    first = ControllerModelStore()
    assert first.save("mpc", {"revision": 3, "model": "first"}) is True

    datastore._reset_for_tests(str(tmp_path / "second.db"))
    datastore.init()

    second = ControllerModelStore()
    assert second.load("mpc") is None
    assert second.save("mpc", {"revision": 0, "model": "second"}) is True
