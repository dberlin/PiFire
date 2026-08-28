"""Fixed lifecycle protocol shared by Hold and controller runners."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import get_type_hints

from common.model_evidence import ModelEvidenceRecord
from common.persistence.model_evidence import ModelActivationState
from controller.model_learning.contracts import CandidateOrigin
from controller.runtime.model_lifecycle import ModelLifecycleRunner
from controller.runtime.model_persistence import DurableActivationReceipt


def test_model_lifecycle_runner_publishes_exact_fixed_contract() -> None:
    expected = {
        "restore_activation": (
            {
                "persisted": ModelActivationState,
                "records": Sequence[ModelEvidenceRecord],
            },
            bool,
        ),
        "activation_runtime_failure": ({"reason": str}, bool),
        "rollback_activation": ({"reason": str}, bool),
        "drain_activation_events": ({}, tuple[ModelEvidenceRecord, ...]),
        "submit_activation_confidence": (
            {"record": ModelEvidenceRecord},
            DurableActivationReceipt | None,
        ),
        "stop_for_refit": ({}, bool | None),
        "schedule_corpus_fit": ({"origin": CandidateOrigin}, bool),
        "finish_teardown": ({}, type(None)),
    }

    methods = {
        name: value
        for name, value in ModelLifecycleRunner.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert set(methods) == set(expected)
    for name, method in methods.items():
        signature = inspect.signature(method)
        hints = get_type_hints(method)
        expected_parameters, expected_return = expected[name]
        assert tuple(signature.parameters) == ("self", *expected_parameters)
        for parameter, annotation in expected_parameters.items():
            assert hints[parameter] == annotation
        assert hints["return"] == expected_return
