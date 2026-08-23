"""Shared controller-learning diagnostic report contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from common.persistence.protocols import JsonValue


def _owned_json(value: object, path: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if isinstance(value, Mapping):
        owned: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} object keys must be strings")
            owned[key] = _owned_json(item, f"{path}.{key}")
        return owned
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_owned_json(item, f"{path}[]") for item in value]
    raise TypeError(f"{path} contains unsupported {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ControllerLearningReport:
    """Deeply owned final learning report from one controller provider."""

    controller: str
    schema_version: int
    revision: str
    report: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if not isinstance(self.controller, str) or not self.controller.strip():
            raise ValueError("controller must be a non-blank string")
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version < 1
        ):
            raise ValueError("schema_version must be a positive integer")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ValueError("revision must be a non-blank string")
        if not isinstance(self.report, Mapping):
            raise TypeError("report must be a mapping")
        object.__setattr__(
            self,
            "report",
            cast(Mapping[str, JsonValue], _owned_json(self.report, "report")),
        )
