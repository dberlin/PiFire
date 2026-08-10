#!/usr/bin/env python3

"""
*****************************************
 PiFire Controller Model Persistence
*****************************************

 Description: Per-controller learned-model snapshots in SQLite.

 A controller that identifies its grill's dynamics online spends the first hour
 of a fresh install learning what it already knew last cook. This keeps that
 model across restarts.

 Everything lives under one generic key as
 {"version": 1, "models": {<controller name>: <snapshot>}}, keyed by controller
 name so switching controllers does not cross-contaminate.

 There is no staging, no flush, no write throttle and no atomic-replace
 sequence: the SQLite transaction is the atomicity and a write is cheap. The one
 guard is that a revision which is not an advance does not write, so the
 per-tick call costs a dict lookup on the overwhelming majority of ticks where
 nothing was learned.

 Contract on `revision`: it must be monotonically non-decreasing across process
 restarts, not merely within one running process. This store has no clock and no
 cook identity to fall back on -- revision is the only signal it has for "is this
 newer". A producer whose counter is per-process (resets to 0 or 1 on every
 restart instead of surviving it) will have every save rejected as
 non-advancing once its counter falls behind the last value this store
 persisted, and it will not climb back past that value until the cook is nearly
 over, if at all.

 A non-advancing save is logged, but the two ways to get one are not the same
 event and are not logged the same way. Equal to the last persisted revision
 means nothing changed since the last save -- normal, frequent (every control
 interval where the controller learned nothing new) and quiet by design, at
 DEBUG. Strictly below the last persisted revision can only mean the producer's
 own counter went backwards, i.e. the restart scenario above -- rare, always
 wrong, and the operator has no other way to find out, so it is logged at
 ERROR. That distinction matters because `control.py` sets this module's logger
 to ERROR whenever `debug_mode` is off, which is the shipped default: a bare
 `.warning()` here would never reach a log file in production, and a `.error()`
 on every equal-revision no-op would flood it into unreadability.

*****************************************
"""

import json
from copy import deepcopy
from enum import Enum
import logging
import threading

from common import datastore
from common.datastore_accessors import read_generic_key, write_generic_key

MODEL_STATE_KEY = "controller_model_state"
SCHEMA_VERSION = 1

# Large enough that the bound is not a design constraint on what a controller
# may learn: a 25-candidate RLS bank with 3x3 covariances is ~7 KB of plain
# JSON. Plain JSON is kept over a packed encoding at this size because a model
# that drives a fire should stay readable in the datastore.
MAX_SNAPSHOT_BYTES = 65536


class CheckpointSaveOutcome(Enum):
    """The durable outcome of one controller-model checkpoint attempt."""

    SAVED = "saved"
    NONADVANCING = "nonadvancing"
    FAILED = "failed"


def _valid(snapshot):
    """Whether a snapshot satisfies the store's exact persistence boundary."""
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    revision = snapshot.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return False
    try:
        encoded = json.dumps(snapshot, allow_nan=False)
    except TypeError, ValueError:
        return False
    return len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES


def copy_valid_snapshot(snapshot):
    """Own a snapshot that has passed the store's exact persistence boundary."""
    try:
        owned_snapshot = deepcopy(snapshot)
    except Exception:
        return None
    return owned_snapshot if _valid(owned_snapshot) else None


_logger = logging.getLogger("control")
_BACKEND_STATES_LOCK = threading.Lock()
_BACKEND_STATES = []


class _BackendState:
    def __init__(self):
        self.transaction_lock = threading.Lock()
        self.latest_lock = threading.Lock()
        self.latest = {}
        self.committed = {}


def _callback_identity(callback):
    owner = getattr(callback, "__self__", None)
    return callback if owner is None else owner


def _shared_backend_state(reader, writer):
    reader_identity = _callback_identity(reader)
    writer_identity = _callback_identity(writer)
    datastore_path = datastore.DB_PATH
    with _BACKEND_STATES_LOCK:
        for saved_reader, saved_writer, saved_path, state in _BACKEND_STATES:
            if saved_reader is reader_identity and saved_writer is writer_identity and saved_path == datastore_path:
                return state
        state = _BackendState()
        _BACKEND_STATES.append((reader_identity, writer_identity, datastore_path, state))
        return state


class ControllerModelStore:
    def __init__(self, reader=None, writer=None, conditional_writer=None):
        self._reader = reader or read_generic_key
        self._writer = writer or write_generic_key
        self._conditional_writer = conditional_writer
        self._backend = _shared_backend_state(self._reader, self._writer)
        self._revisions = {}

    def load(self, name):
        return self._load(name, strict=False)

    def load_strict(self, name):
        """Load one model while preserving absent versus malformed storage."""

        return self._load(name, strict=True)

    def _load(self, name, *, strict):
        snapshot = self._latest_owned(name)
        if snapshot is None:
            models, _safe = self._read_state(strict_name=name if strict else None)
            persisted = models.get(name)
            if persisted is None:
                return None
            self._remember_owned(name, persisted, committed=True)
            snapshot = self._latest_owned(name)
        committed_revision = self._committed_revision(name)
        if committed_revision is not None:
            self._revisions[name] = committed_revision
        return snapshot

    def save(self, name, snapshot):
        """Persist a strictly newer snapshot using the legacy boolean contract."""
        return self.save_outcome(name, snapshot) is CheckpointSaveOutcome.SAVED

    def save_outcome(self, name, snapshot):
        """Persist once and report whether it saved, coalesced, or failed."""
        owned_snapshot = copy_valid_snapshot(snapshot)
        if owned_snapshot is None:
            _logger.warning("controller_model_state: rejecting a malformed or oversized snapshot for %r", name)
            return CheckpointSaveOutcome.FAILED
        revision = owned_snapshot["revision"]
        if self._conditional_writer is not None:
            try:
                written = self._conditional_writer(name, owned_snapshot)
            except Exception:
                _logger.warning(
                    "controller_model_state: failed to persist a snapshot for %r",
                    name,
                    exc_info=True,
                )
                return CheckpointSaveOutcome.FAILED
            if written:
                self._revisions[name] = revision
                self._remember_owned(name, owned_snapshot, committed=True)
                return CheckpointSaveOutcome.SAVED
            models, safe = self._read_state()
            if not safe:
                return CheckpointSaveOutcome.FAILED
            existing = models.get(name)
            if existing is not None and revision <= existing["revision"]:
                self._remember_owned(name, existing, committed=True)
                self._log_non_advancing(name, revision, existing["revision"])
                return CheckpointSaveOutcome.NONADVANCING
            return CheckpointSaveOutcome.FAILED

        # Workers can outlive a Hold teardown. Serialize the complete
        # read-check-write transaction across store instances so an older
        # orphan cannot overwrite a newer mode's checkpoint.
        with self._backend.transaction_lock:
            latest = self._latest_owned(name)
            if latest is not None and revision < latest["revision"]:
                self._log_non_advancing(name, revision, latest["revision"])
                return CheckpointSaveOutcome.NONADVANCING

            # Cheap path: this process already knows the last revision it
            # persisted for this controller, so a same-or-older revision is
            # rejected without touching storage -- the common case on ticks
            # where nothing was learned.
            cached = self._revisions.get(name)
            if cached is not None and revision <= cached:
                self._log_non_advancing(name, revision, cached)
                return CheckpointSaveOutcome.NONADVANCING

            models, safe = self._read_state()
            if not safe:
                _logger.warning(
                    "controller_model_state: refusing to save %r at revision %s -- the existing "
                    "record could not be read, and writing now would silently discard whatever it held",
                    name,
                    revision,
                )
                return CheckpointSaveOutcome.FAILED

            # The persisted revision remains authoritative after a cache miss:
            # another store instance may have advanced it while this instance
            # retained an older cache entry.
            existing = models.get(name)
            if existing is not None:
                self._remember_owned(name, existing, committed=True)
                if revision <= existing["revision"]:
                    self._log_non_advancing(name, revision, existing["revision"])
                    return CheckpointSaveOutcome.NONADVANCING

            models[name] = owned_snapshot
            try:
                self._writer(MODEL_STATE_KEY, {"version": SCHEMA_VERSION, "models": models})
            except Exception:
                _logger.warning(
                    "controller_model_state: failed to persist a snapshot for %r",
                    name,
                    exc_info=True,
                )
                return CheckpointSaveOutcome.FAILED
            self._revisions[name] = revision
            self._remember_owned(name, owned_snapshot, committed=True)
            return CheckpointSaveOutcome.SAVED

    def stage_owned(self, name, snapshot):
        """Publish a worker-owned snapshot before potentially blocking I/O."""
        if not _valid(snapshot):
            return False
        revision = snapshot["revision"]
        with self._backend.latest_lock:
            latest = self._backend.latest.get(name)
            if latest is not None and revision <= latest["revision"]:
                return True
            self._backend.latest[name] = snapshot
        return True

    def _latest_owned(self, name):
        with self._backend.latest_lock:
            snapshot = self._backend.latest.get(name)
        return None if snapshot is None else deepcopy(snapshot)

    def _committed_revision(self, name):
        with self._backend.latest_lock:
            return self._backend.committed.get(name)

    def _remember_owned(self, name, snapshot, *, committed):
        owned_snapshot = deepcopy(snapshot)
        revision = owned_snapshot["revision"]
        with self._backend.latest_lock:
            latest = self._backend.latest.get(name)
            if latest is None or revision > latest["revision"]:
                self._backend.latest[name] = owned_snapshot
            if committed:
                committed_revision = self._backend.committed.get(name)
                if committed_revision is None or revision > committed_revision:
                    self._backend.committed[name] = revision

    @staticmethod
    def _log_non_advancing(name, revision, baseline):
        """Log a rejected save -- at a level that matches how alarming it is.

        `revision == baseline`: nothing changed since the last save. This is the
        expected, frequent outcome on a control interval where the controller
        learned nothing new, so it stays quiet at DEBUG.

        `revision < baseline`: the incoming revision went backwards. The only way
        that happens is a producer whose revision counter did not survive a
        restart (see the module docstring's contract on `revision`) -- from here
        every save for the rest of the cook is rejected the same way, and there
        is no other signal that it happened. Logged at ERROR, matching the
        precedent at common/control_delta.py's unsupported-version drop: it is
        always wrong, and ERROR is the only level that survives `control.py`
        setting this logger to ERROR whenever `debug_mode` is off.
        """
        if revision == baseline:
            _logger.debug(
                "controller_model_state: %r at revision %s matches the last persisted revision; nothing to save",
                name,
                revision,
            )
        else:
            _logger.error(
                "controller_model_state: revision %s for %r is BEHIND the last persisted revision %s -- its "
                "revision counter likely reset across a restart, and its model will not persist again until "
                "the counter climbs back past %s",
                revision,
                name,
                baseline,
                baseline,
            )

    def _read_state(self, *, strict_name=None):
        """Return stored snapshots and whether the read is safe to overwrite.

        A strict read additionally raises when the target controller cannot be
        distinguished from malformed storage. The legacy callers retain their
        fail-closed, non-raising behavior.

        Three storage outcomes remain:

        - the key has never been written: read_generic_key raises TypeError for an
          absent key (it calls json.loads(None)), which means "genuinely nothing here
          yet" -- safe to write;
        - the read succeeds and the envelope parses: safe to write, with any invalid
          member dropped so one bad controller cannot poison another;
        - anything else -- a reader exception that is not the absent-key TypeError, an
          unrecognized schema version, a malformed envelope: NOT safe to write.
        """

        def strict_error(detail):
            if strict_name is not None:
                raise ValueError(f"malformed stored snapshot for {strict_name!r}: {detail}")

        try:
            raw = self._reader(MODEL_STATE_KEY)
        except TypeError:
            return {}, True
        except Exception:
            _logger.warning("controller_model_state: could not read the existing model record", exc_info=True)
            strict_error("model state read failed")
            return {}, False
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            _logger.warning(
                "controller_model_state: existing record has an unrecognized shape or version "
                "(expected %s); leaving it untouched",
                SCHEMA_VERSION,
            )
            strict_error("model state envelope is invalid")
            return {}, False
        raw_models = raw.get("models")
        if not isinstance(raw_models, dict):
            _logger.warning(
                "controller_model_state: existing record's 'models' field is not a dict; leaving it untouched"
            )
            strict_error("model state models field is invalid")
            return {}, False
        models = {}
        for member_name, snap in raw_models.items():
            if _valid(snap):
                models[member_name] = snap
            else:
                _logger.warning("controller_model_state: dropping a malformed stored snapshot for %r", member_name)
                if member_name == strict_name:
                    strict_error("snapshot failed persistence validation")
        return models, True
