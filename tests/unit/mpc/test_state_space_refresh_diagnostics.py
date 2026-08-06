"""Contracts for deterministic state-space refresh diagnostic evidence."""

from __future__ import annotations

import json
from dataclasses import fields
from math import isfinite
from types import SimpleNamespace

import numpy as np

import docs.superpowers.experiments.state_space_refresh_diagnostics as diagnostics


EXPECTED_FAILURE_MODES = ("wrong-gain", "wrong-pole", "wrong-delay")
EXPECTED_PLANTS = ("GrillSim", "MAKGrillSim")


def _complete_row(*, mode: str, plant: str) -> dict[str, object]:
    return {
        "cell_key": f"{mode}:{plant}",
        "mode": mode,
        "plant": plant,
        "status": "completed",
        "failure": None,
        "refresh": {
            "accepted": False,
            "terminal_reason": "no-valid-candidate",
            "selected_order": None,
            "selected_delay": None,
            "attempts": [
                {
                    "order": 1,
                    "delay": 1,
                    "sample_count": 64,
                    "hankel_shape": [8, 8],
                    "singular_values": [2.0, 0.1],
                    "effective_rank": 2,
                    "condition_number": 20.0,
                    "projection_applied": True,
                    "steady_gain": 1.0,
                    "alignment_error_c": None,
                    "prediction_score": None,
                    "braking_score": None,
                    "rejection_reasons": ["implausible-gain"],
                    "elapsed_ms": 0.1,
                }
            ],
        },
    }


def test_refresh_rejection_reason_is_complete_and_stable() -> None:
    """Refresh failures use a finite public vocabulary rather than message matching."""
    from controller.linear_mpc.state_space import RefreshRejectionReason

    assert {reason.value for reason in RefreshRejectionReason} == {
        "insufficient-samples",
        "rank-deficient",
        "ill-conditioned",
        "unstable-after-projection",
        "implausible-gain",
        "alignment-failed",
        "nonfinite",
        "no-valid-candidate",
    }


def test_fixed_matrix_contains_each_failure_mode_for_each_simulator() -> None:
    """The reproducibility script cannot silently omit a scientifically relevant cell."""
    assert diagnostics.FAILURE_MODES == EXPECTED_FAILURE_MODES
    assert diagnostics.PLANTS == EXPECTED_PLANTS
    assert diagnostics.FIXED_MATRIX == tuple(
        (mode, plant) for mode in EXPECTED_FAILURE_MODES for plant in EXPECTED_PLANTS
    )


def test_run_cell_tracks_incumbent_to_candidate_terminal_timestamp(
    monkeypatch,
) -> None:
    """Ordinary suffix evolution is aligned before its candidate is assessed."""
    record = diagnostics.SignalRecord(
        time_s=np.arange(20.0, 220.0, 20.0),
        temp_c=np.linspace(90.0, 99.0, 10),
        q=np.linspace(0.2, 0.5, 10),
        ambient_c=np.full(10, 20.0),
        provenance="simulator-realized-duty",
    )

    class _SpyInnovationStateSpace:
        instances: list["_SpyInnovationStateSpace"] = []

        def __init__(self, _config: object) -> None:
            self.tracked_end_times: list[float] = []
            self.fit_end_time: float | None = None
            self.refresh_end_time: float | None = None
            self.__class__.instances.append(self)

        def fit(self, frames: tuple[object, ...]) -> None:
            self.fit_end_time = frames[-1].frame_end_s

        def track(self, frame: object) -> None:
            self.tracked_end_times.append(frame.frame_end_s)

        def refresh(self, frames: tuple[object, ...]) -> SimpleNamespace:
            self.refresh_end_time = frames[-1].frame_end_s
            assert self.tracked_end_times[-1] == self.refresh_end_time
            return SimpleNamespace(
                accepted=True,
                terminal_reason=None,
                attempts=(),
                selected_order=2,
                selected_delay=3,
            )

    monkeypatch.setattr(
        diagnostics,
        "_identification_records",
        lambda _plant, _seed, _mode: (record, record),
    )
    monkeypatch.setattr(diagnostics, "InnovationStateSpace", _SpyInnovationStateSpace)

    cell = diagnostics._run_cell(mode="wrong-gain", plant="GrillSim")

    spy = _SpyInnovationStateSpace.instances[0]
    assert spy.fit_end_time == 100.0
    assert spy.tracked_end_times == [120.0, 140.0, 160.0, 180.0, 200.0]
    assert spy.refresh_end_time == 200.0
    assert cell["refresh"] == {
        "accepted": True,
        "terminal_reason": None,
        "selected_order": 2,
        "selected_delay": 3,
        "attempts": [],
    }


def test_refresh_document_records_selection_only_for_accepted_refresh() -> None:
    """Every document makes installed-candidate identity explicit or impossible."""
    from controller.linear_mpc.state_space import RefreshRejectionReason

    accepted = diagnostics._refresh_document(
        SimpleNamespace(
            accepted=True,
            terminal_reason=None,
            attempts=(),
            selected_order=2,
            selected_delay=3,
        )
    )
    rejected = diagnostics._refresh_document(
        SimpleNamespace(
            accepted=False,
            terminal_reason=RefreshRejectionReason.NO_VALID_CANDIDATE,
            attempts=(),
            selected_order=None,
            selected_delay=None,
        )
    )

    assert accepted["selected_order"] == 2
    assert accepted["selected_delay"] == 3
    assert rejected["selected_order"] is None
    assert rejected["selected_delay"] is None


def test_complete_diagnostic_document_is_json_finite_and_contract_valid() -> None:
    """Every completed cell preserves all attempted candidate evidence."""
    document = {
        "schema_version": diagnostics.ARTIFACT_SCHEMA_VERSION,
        "seed": diagnostics.FIXED_SEED,
        "matrix": [_complete_row(mode=mode, plant=plant) for mode, plant in diagnostics.FIXED_MATRIX],
    }

    assert diagnostics.artifact_contract_errors(document) == []
    for row in document["matrix"]:
        refresh = row["refresh"]
        assert refresh["attempts"]
        for attempt in refresh["attempts"]:
            assert attempt["sample_count"] > 0
            assert len(attempt["hankel_shape"]) == 2
            assert all(isfinite(value) for value in attempt["singular_values"])
            assert isfinite(attempt["condition_number"])
            assert isfinite(attempt["steady_gain"])
            assert isfinite(attempt["elapsed_ms"])
    json.dumps(document, allow_nan=False)


def test_contract_rejects_missing_matrix_cell_and_incomplete_attempt() -> None:
    """Incomplete evidence is infrastructure failure rather than scientific rejection."""
    document = {
        "schema_version": diagnostics.ARTIFACT_SCHEMA_VERSION,
        "seed": diagnostics.FIXED_SEED,
        "matrix": [_complete_row(mode=mode, plant=plant) for mode, plant in diagnostics.FIXED_MATRIX[1:]],
    }
    document["matrix"][0]["refresh"]["attempts"][0].pop("condition_number")

    errors = diagnostics.artifact_contract_errors(document)

    assert any("missing fixed matrix cells" in error for error in errors)
    assert any("condition_number" in error for error in errors)


def test_contract_requires_accepted_selection_and_rejects_rejected_selection() -> None:
    """Selection fields are a truthful installed-model claim, never diagnostics."""
    document = {
        "schema_version": diagnostics.ARTIFACT_SCHEMA_VERSION,
        "seed": diagnostics.FIXED_SEED,
        "matrix": [_complete_row(mode=mode, plant=plant) for mode, plant in diagnostics.FIXED_MATRIX],
    }
    accepted = document["matrix"][0]["refresh"]
    accepted["accepted"] = True
    accepted["terminal_reason"] = None
    accepted["selected_order"] = 1
    accepted["selected_delay"] = 1
    accepted["attempts"][0]["rejection_reasons"] = []

    assert diagnostics.artifact_contract_errors(document) == []

    accepted["attempts"][0]["rejection_reasons"] = ["implausible-gain"]
    assert any("selected candidate is rejected" in error for error in diagnostics.artifact_contract_errors(document))

    accepted["attempts"][0]["rejection_reasons"] = []

    accepted["selected_delay"] = 3
    assert any(
        "selected candidate is absent from attempts" in error
        for error in diagnostics.artifact_contract_errors(document)
    )

    accepted["selected_delay"] = 1

    accepted.pop("selected_order")
    assert any(
        "accepted refresh must identify selected order and delay" in error
        for error in diagnostics.artifact_contract_errors(document)
    )

    accepted["selected_order"] = 1
    document["matrix"][1]["refresh"]["selected_order"] = 1
    document["matrix"][1]["refresh"]["selected_delay"] = 1
    assert any(
        "rejected refresh must not identify a selected candidate" in error
        for error in diagnostics.artifact_contract_errors(document)
    )


def test_production_attempt_contract_keeps_all_observed_candidate_values() -> None:
    """The shared immutable type is rich enough for experiment diagnostics."""
    from controller.linear_mpc.state_space import CandidateAttempt

    names = {field.name for field in fields(CandidateAttempt)}

    assert {
        "order",
        "delay",
        "sample_count",
        "hankel_shape",
        "singular_values",
        "effective_rank",
        "condition_number",
        "projection_applied",
        "steady_gain",
        "alignment_error_c",
        "prediction_score",
        "braking_score",
        "rejection_reasons",
        "elapsed_ms",
    } <= names
