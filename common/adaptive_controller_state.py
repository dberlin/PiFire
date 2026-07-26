"""Durable storage for trusted adaptive controller model snapshots."""

import logging
import json
import math
import os
from pathlib import Path
import tempfile
import time


LOGGER = logging.getLogger(__name__)


_ROOT_VERSION = 1
_MODEL_VERSION = 1
_ROOT_KEYS = {"version", "models"}
_MODEL_KEYS = {
    "version",
    "gain_f_per_duty",
    "tau_seconds",
    "theta_seconds",
    "confidence",
    "residual",
    "observations",
    "revision",
}
_NUMERIC_MODEL_KEYS = (
    "gain_f_per_duty",
    "tau_seconds",
    "theta_seconds",
    "confidence",
    "residual",
)


class AdaptiveControllerStateStore:
    """Atomically persist only validated, physical controller model snapshots."""

    def __init__(
        self,
        path=Path("adaptive_controller_state.json"),
        clock=time.time,
        min_write_interval=1800.0,
    ):
        interval = float(min_write_interval)
        if not math.isfinite(interval) or interval < 0.0:
            raise ValueError("min_write_interval must be a non-negative finite number")

        self.path = Path(path)
        self._clock = clock
        self._min_write_interval = interval
        loaded_models = self._read_models()
        self._models = {} if loaded_models is None else loaded_models
        self._pending = {}
        self._last_successful_write = (
            self._now() if loaded_models is not None else None
        )

    def load(self, name):
        """Return a copy of a persisted trusted snapshot, if it is valid."""
        snapshot = self._models.get(name)
        return None if snapshot is None else dict(snapshot)

    def stage(self, name, snapshot):
        """Stage a newer trusted snapshot without writing dynamic controller state."""
        if not isinstance(name, str) or not name:
            return False

        validated = self._validate_model(snapshot)
        if validated is None:
            return False

        current = self._pending.get(name, self._models.get(name))
        if current is not None and validated["revision"] <= current["revision"]:
            return False

        self._pending[name] = validated
        return True

    def flush(self, force=False):
        """Atomically write pending models when the routine-write interval permits."""
        if not self._pending:
            return False
        if (
            not force
            and self._last_successful_write is not None
            and self._now() - self._last_successful_write
            < self._min_write_interval
        ):
            return False

        models = dict(self._models)
        models.update(self._pending)
        root = {"version": _ROOT_VERSION, "models": models}
        if not self._write(root):
            return False

        self._models = models
        self._pending.clear()
        self._last_successful_write = self._now()
        return True

    def _now(self):
        return float(self._clock())

    def _read_models(self):
        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                root = json.load(state_file)
        except (OSError, TypeError, ValueError):
            return None

        if type(root) is not dict or set(root) != _ROOT_KEYS:
            return None
        if type(root["version"]) is not int or root["version"] != _ROOT_VERSION:
            return None
        if type(root["models"]) is not dict:
            return None

        models = {}
        for name, snapshot in root["models"].items():
            if not isinstance(name, str) or not name:
                return None
            validated = self._validate_model(snapshot)
            if validated is None:
                return None
            models[name] = validated
        return models

    def _validate_model(self, snapshot):
        if type(snapshot) is not dict or set(snapshot) != _MODEL_KEYS:
            return None
        if type(snapshot["version"]) is not int or snapshot["version"] != _MODEL_VERSION:
            return None
        if any(
            isinstance(snapshot[key], bool)
            or not isinstance(snapshot[key], (int, float))
            or not math.isfinite(snapshot[key])
            for key in _NUMERIC_MODEL_KEYS
        ):
            return None
        if any(type(snapshot[key]) is not int for key in ("observations", "revision")):
            return None
        if not 50.0 <= snapshot["gain_f_per_duty"] <= 2000.0:
            return None
        if not 300.0 <= snapshot["tau_seconds"] <= 20000.0:
            return None
        if not 0.0 <= snapshot["theta_seconds"] <= 120.0:
            return None
        if not 0.0 <= snapshot["confidence"] <= 1.0:
            return None
        if snapshot["residual"] < 0.0:
            return None
        if snapshot["observations"] < 0 or snapshot["revision"] < 0:
            return None
        return dict(snapshot)

    def _write(self, root):
        temporary_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.path.parent,
                prefix=self.path.name + ".tmp-",
                delete=False,
                encoding="utf-8",
            ) as state_file:
                temporary_path = Path(state_file.name)
                json.dump(root, state_file, sort_keys=True, indent=2)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
            return True
        except Exception:
            LOGGER.exception(
                "Unable to atomically write adaptive controller model state"
            )
            return False
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass
