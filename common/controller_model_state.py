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
import logging

from common.datastore_accessors import read_generic_key, write_generic_key

MODEL_STATE_KEY = "controller_model_state"
SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 8192

_logger = logging.getLogger("control")


def _valid(snapshot):
    """Envelope validation only: bounded, JSON-safe, carrying a revision.

    Deliberately says nothing about model field names or physics. The store owns
    "is this a bounded, JSON-safe record"; the controller owns "do these numbers
    describe a possible grill" and re-checks in restore_model.
    """
    if not isinstance(snapshot, dict) or not snapshot:
        return False
    revision = snapshot.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return False
    try:
        encoded = json.dumps(snapshot, allow_nan=False)
    except ValueError, TypeError:
        return False
    return len(encoded.encode("utf-8")) <= MAX_SNAPSHOT_BYTES


class ControllerModelStore:
    def __init__(self, reader=None, writer=None):
        self._reader = reader or read_generic_key
        self._writer = writer or write_generic_key
        self._revisions = {}

    def load(self, name):
        models, _safe = self._read_state()
        snapshot = models.get(name)
        if snapshot is None:
            return None
        self._revisions[name] = snapshot["revision"]
        return snapshot

    def save(self, name, snapshot):
        if not _valid(snapshot):
            _logger.warning("controller_model_state: rejecting a malformed or oversized snapshot for %r", name)
            return False
        revision = snapshot["revision"]

        # Cheap path: this process already knows the last revision it persisted
        # for this controller, so a same-or-older revision is rejected without
        # touching storage -- the common case on ticks where nothing was learned.
        cached = self._revisions.get(name)
        if cached is not None and revision <= cached:
            self._log_non_advancing(name, revision, cached)
            return False

        models, safe = self._read_state()
        if not safe:
            _logger.warning(
                "controller_model_state: refusing to save %r at revision %s -- the existing "
                "record could not be read, and writing now would silently discard whatever it held",
                name,
                revision,
            )
            return False

        # Cold path: nothing cached yet for this controller in this process (the
        # first save since startup), so the only baseline available is whatever
        # was actually persisted last time. Skipping this would let a producer
        # whose revision counter reset across a restart silently overwrite a
        # far-newer stored model on its very first save -- see the module
        # docstring's contract on `revision`.
        if cached is None:
            existing = models.get(name)
            if existing is not None and revision <= existing["revision"]:
                self._log_non_advancing(name, revision, existing["revision"])
                return False

        models[name] = snapshot
        try:
            self._writer(MODEL_STATE_KEY, {"version": SCHEMA_VERSION, "models": models})
        except Exception:
            _logger.warning("controller_model_state: failed to persist a snapshot for %r", name, exc_info=True)
            return False
        self._revisions[name] = revision
        return True

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

    def _read_state(self):
        """Every stored snapshot, plus whether that read is trustworthy enough to write over.

        Returns (models, safe_to_write). Three outcomes:

        - the key has never been written: read_generic_key raises TypeError for an
          absent key (it calls json.loads(None)), which means "genuinely nothing here
          yet" -- safe to write.
        - the read succeeds and the envelope parses: safe to write, with any invalid
          member dropped so one bad controller cannot poison another.
        - anything else -- a reader exception that is not the absent-key TypeError, an
          unrecognized schema version, a malformed envelope: NOT safe to write. "I
          could not read it" and "there is nothing there" are different facts, and a
          caller must never turn the first into the second by writing a fresh,
          nearly-empty blob over data it failed to parse.
        """
        try:
            raw = self._reader(MODEL_STATE_KEY)
        except TypeError:
            return {}, True
        except Exception:
            _logger.warning("controller_model_state: could not read the existing model record", exc_info=True)
            return {}, False
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            _logger.warning(
                "controller_model_state: existing record has an unrecognized shape or version "
                "(expected %s); leaving it untouched",
                SCHEMA_VERSION,
            )
            return {}, False
        raw_models = raw.get("models")
        if not isinstance(raw_models, dict):
            _logger.warning(
                "controller_model_state: existing record's 'models' field is not a dict; leaving it untouched"
            )
            return {}, False
        models = {}
        for member_name, snap in raw_models.items():
            if _valid(snap):
                models[member_name] = snap
            else:
                _logger.warning("controller_model_state: dropping a malformed stored snapshot for %r", member_name)
        return models, True
