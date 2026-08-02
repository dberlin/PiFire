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

*****************************************
"""

import json

from common.datastore_accessors import read_generic_key, write_generic_key

MODEL_STATE_KEY = "controller_model_state"
SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 8192


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
        snapshot = self._read_models().get(name)
        if snapshot is None:
            return None
        self._revisions[name] = snapshot["revision"]
        return snapshot

    def save(self, name, snapshot):
        if not _valid(snapshot):
            return False
        revision = snapshot["revision"]
        if name in self._revisions and revision <= self._revisions[name]:
            return False
        models = self._read_models()
        models[name] = snapshot
        try:
            self._writer(MODEL_STATE_KEY, {"version": SCHEMA_VERSION, "models": models})
        except Exception:
            return False
        self._revisions[name] = revision
        return True

    def _read_models(self):
        """Every stored snapshot, fail-closed.

        A storage error, a root-schema mismatch or a bad member yields nothing
        rather than a half-trusted mix. read_generic_key raises TypeError for an
        absent key (it calls json.loads(None)); that is caught here with
        everything else.
        """
        try:
            raw = self._reader(MODEL_STATE_KEY)
        except Exception:
            return {}
        if not isinstance(raw, dict) or raw.get("version") != SCHEMA_VERSION:
            return {}
        models = raw.get("models")
        if not isinstance(models, dict):
            return {}
        return {name: snap for name, snap in models.items() if _valid(snap)}
