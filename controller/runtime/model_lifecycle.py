"""Fixed model activation and teardown lifecycle boundary for runners."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from common.model_evidence import ModelEvidenceRecord
from common.persistence.model_evidence import ModelActivationState
from controller.runtime.model_fitting import TeardownRefitOutcome
from controller.runtime.model_persistence import DurableActivationReceipt


class ModelLifecycleRunner(Protocol):
    def restore_activation(
        self,
        persisted: ModelActivationState,
        records: Sequence[ModelEvidenceRecord],
    ) -> bool: ...

    def activation_runtime_failure(self, reason: str) -> bool: ...

    def rollback_activation(self, reason: str) -> bool: ...

    def drain_activation_events(self) -> tuple[ModelEvidenceRecord, ...]: ...

    def submit_activation_confidence(
        self, record: ModelEvidenceRecord
    ) -> DurableActivationReceipt | None: ...

    def stop_for_refit(self) -> bool | None: ...

    def finalize_cook_refit(self, outcome: TeardownRefitOutcome) -> bool: ...

    def finish_teardown(self) -> None: ...
