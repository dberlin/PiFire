"""Latest-only asynchronous persistence for controller model snapshots."""

from __future__ import annotations

from copy import deepcopy
from threading import Condition, Thread
from typing import Protocol


class _ModelStore(Protocol):
    def save(self, name: str, snapshot: dict[str, object]) -> bool: ...


class _ErrorLogger(Protocol):
    def error(self, message: str) -> None: ...


class ModelCheckpointWorker:
    """Persist the newest pending checkpoint for each controller asynchronously."""

    def __init__(self, store: _ModelStore, logger: _ErrorLogger) -> None:
        self._store: _ModelStore = store
        self._logger: _ErrorLogger = logger
        self._condition: Condition = Condition()
        self._pending: dict[str, dict[str, object]] = {}
        self._stopping: bool = False
        self._thread: Thread | None = None

    def submit(self, name: str, snapshot: dict[str, object]) -> bool:
        """Take ownership of a snapshot and schedule its newest-only write."""
        try:
            owned_snapshot = deepcopy(snapshot)
        except Exception as error:
            self._logger.error(f"Could not own {name} model checkpoint: {error}")
            return False
        with self._condition:
            if self._stopping:
                self._logger.error(f"Could not checkpoint {name} model after teardown began")
                return False
            pending_snapshot = self._pending.get(name)
            if pending_snapshot is not None:
                pending_revision = self._revision(pending_snapshot)
                submitted_revision = self._revision(owned_snapshot)
                if pending_revision is not None and (
                    submitted_revision is None or submitted_revision <= pending_revision
                ):
                    return True
            stage_owned = getattr(self._store, "stage_owned", None)
            if callable(stage_owned):
                try:
                    if not stage_owned(name, owned_snapshot):
                        self._logger.error(f"Could not stage {name} model checkpoint")
                        return False
                except Exception as error:
                    self._logger.error(f"Could not stage {name} model checkpoint: {error}")
                    return False
            self._pending[name] = owned_snapshot
            if self._thread is None:
                self._thread = Thread(target=self._run, name="controller-model-checkpoint", daemon=True)
                self._thread.start()
            self._condition.notify()
        return True

    def flush_and_stop(self, *, timeout: float = 0.1) -> bool:
        """Stop accepting snapshots without making Hold wait for storage I/O."""
        if timeout < 0.0:
            raise ValueError("checkpoint flush timeout must be nonnegative")
        with self._condition:
            self._stopping = True
            thread = self._thread
            self._condition.notify_all()
        if thread is None:
            return True
        thread.join(timeout=timeout)
        completed = not thread.is_alive()
        if not completed:
            self._logger.error("Model checkpoint flush is still pending after Hold teardown")
        return completed

    @staticmethod
    def _revision(snapshot: dict[str, object]) -> int | None:
        revision = snapshot.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            return revision
        return None

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stopping:
                    _ = self._condition.wait()
                if not self._pending:
                    return
                name = next(iter(self._pending))
                snapshot = self._pending.pop(name)
            try:
                _ = self._store.save(name, snapshot)
            except Exception as error:
                self._logger.error(f"Could not persist {name} model checkpoint: {error}")
            finally:
                with self._condition:
                    self._condition.notify_all()
