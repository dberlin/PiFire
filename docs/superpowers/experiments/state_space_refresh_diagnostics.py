"""Reproduce state-space candidate-refresh outcomes on the fixed simulator matrix.

A typed rejection is experimental evidence, not process failure.  This module exits
nonzero only when a required cell cannot produce complete diagnostic evidence.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from typing import Protocol

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from controller.linear_mpc.state_space import (
    InnovationStateSpace,
    RefreshRejectionReason,
    StateSpaceConfig,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import SignalRecord
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    _identification_records,
    _record_frames,
)

ARTIFACT_SCHEMA_VERSION = 1
FAILURE_MODES = ("wrong-gain", "wrong-pole", "wrong-delay")
PLANTS = ("GrillSim", "MAKGrillSim")
FIXED_SEED = 0
FIXED_MATRIX = tuple((mode, plant) for mode in FAILURE_MODES for plant in PLANTS)

_ATTEMPT_FIELDS = frozenset(
    {
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
    }
)

_REFRESH_FIELDS = frozenset(
    {
        "accepted",
        "terminal_reason",
        "selected_order",
        "selected_delay",
        "attempts",
    }
)


class _Attempt(Protocol):
    @property
    def order(self) -> int: ...

    @property
    def delay(self) -> int: ...

    @property
    def sample_count(self) -> int: ...

    @property
    def hankel_shape(self) -> tuple[int, int]: ...

    @property
    def singular_values(self) -> tuple[float, ...]: ...

    @property
    def effective_rank(self) -> int: ...

    @property
    def condition_number(self) -> float | None: ...

    @property
    def projection_applied(self) -> bool: ...

    @property
    def steady_gain(self) -> float | None: ...

    @property
    def alignment_error_c(self) -> float | None: ...

    @property
    def prediction_score(self) -> float | None: ...

    @property
    def braking_score(self) -> float | None: ...

    @property
    def rejection_reasons(self) -> tuple[RefreshRejectionReason, ...]: ...

    @property
    def elapsed_ms(self) -> float: ...


class _RefreshDiagnostics(Protocol):
    @property
    def accepted(self) -> bool: ...

    @property
    def terminal_reason(self) -> RefreshRejectionReason | None: ...

    @property
    def attempts(self) -> tuple[_Attempt, ...]: ...

    @property
    def selected_order(self) -> int | None: ...

    @property
    def selected_delay(self) -> int | None: ...


def _record_prefix(record: SignalRecord, stop: int) -> SignalRecord:
    return SignalRecord(
        record.time_s[:stop],
        record.temp_c[:stop],
        record.q[:stop],
        record.ambient_c[:stop],
        record.provenance,
        metadata=record.metadata,
    )


def _record_suffix(record: SignalRecord, start: int) -> SignalRecord:
    return SignalRecord(
        record.time_s[start:],
        record.temp_c[start:],
        record.q[start:],
        record.ambient_c[start:],
        record.provenance,
        metadata=record.metadata,
    )


def _finite_json(value: object) -> bool:
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _finite_json(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return all(_finite_json(item) for item in value)
    return value is None or isinstance(value, (bool, int, str))


def _attempt_document(attempt: _Attempt) -> dict[str, object]:
    return {
        "order": attempt.order,
        "delay": attempt.delay,
        "sample_count": attempt.sample_count,
        "hankel_shape": list(attempt.hankel_shape),
        "singular_values": list(attempt.singular_values),
        "effective_rank": attempt.effective_rank,
        "condition_number": attempt.condition_number,
        "projection_applied": attempt.projection_applied,
        "steady_gain": attempt.steady_gain,
        "alignment_error_c": attempt.alignment_error_c,
        "prediction_score": attempt.prediction_score,
        "braking_score": attempt.braking_score,
        "rejection_reasons": [reason.value for reason in attempt.rejection_reasons],
        "elapsed_ms": attempt.elapsed_ms,
    }


def _refresh_document(diagnostics: _RefreshDiagnostics) -> dict[str, object]:
    return {
        "accepted": diagnostics.accepted,
        "terminal_reason": diagnostics.terminal_reason.value if diagnostics.terminal_reason else None,
        "selected_order": diagnostics.selected_order,
        "selected_delay": diagnostics.selected_delay,
        "attempts": [_attempt_document(attempt) for attempt in diagnostics.attempts],
    }


def _run_cell(*, mode: str, plant: str) -> dict[str, object]:
    """Identify the fixed mismatched record and retain the terminal typed evidence."""
    _reference, initialized = _identification_records(plant, FIXED_SEED, mode)
    model = InnovationStateSpace(StateSpaceConfig(orders=(1, 2, 3), delays=(1, 2, 3), refresh_interval_s=1e12))
    split = initialized.time_s.size // 2
    suffix_frames = _record_frames(_record_suffix(initialized, split))
    try:
        model.fit(_record_frames(_record_prefix(initialized, split)))
        for frame in suffix_frames:
            model.track(frame)
        refresh = model.refresh(suffix_frames)
    except ValueError as error:
        return {
            "cell_key": f"{mode}:{plant}",
            "mode": mode,
            "plant": plant,
            "status": "infrastructure-failed",
            "failure": f"setup failure: {error}",
            "refresh": None,
        }
    return {
        "cell_key": f"{mode}:{plant}",
        "mode": mode,
        "plant": plant,
        "status": "completed",
        "failure": None,
        "refresh": _refresh_document(refresh),
    }


def collect_diagnostics() -> dict[str, object]:
    """Return compact, complete, JSON-finite evidence for every fixed matrix cell."""
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "seed": FIXED_SEED,
        "matrix": [_run_cell(mode=mode, plant=plant) for mode, plant in FIXED_MATRIX],
    }


def artifact_contract_errors(document: object) -> list[str]:
    """Return complete-artifact violations; no error means a runnable evidence matrix."""
    if not isinstance(document, Mapping):
        return ["artifact must be a mapping"]
    if document.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        return ["unexpected schema_version"]
    if document.get("seed") != FIXED_SEED:
        return ["unexpected fixed seed"]
    rows = document.get("matrix")
    if not isinstance(rows, list):
        return ["matrix must be a list"]
    expected = {f"{mode}:{plant}" for mode, plant in FIXED_MATRIX}
    actual = {row.get("cell_key") for row in rows if isinstance(row, Mapping)}
    errors: list[str] = []
    missing = sorted(expected - actual)
    if missing:
        errors.append(f"missing fixed matrix cells: {', '.join(missing)}")
    if len(rows) != len(FIXED_MATRIX):
        errors.append("matrix contains duplicate or unexpected cells")
    for row in rows:
        if not isinstance(row, Mapping):
            errors.append("matrix row must be a mapping")
            continue
        key = row.get("cell_key")
        if row.get("status") != "completed":
            errors.append(f"{key}: infrastructure failure")
            continue
        refresh = row.get("refresh")
        if not isinstance(refresh, Mapping):
            errors.append(f"{key}: missing refresh diagnostics")
            continue
        missing_refresh_fields = sorted(_REFRESH_FIELDS - set(refresh))
        if missing_refresh_fields:
            errors.append(f"{key}: refresh missing {', '.join(missing_refresh_fields)}")
        terminal_reason = refresh.get("terminal_reason")
        accepted = refresh.get("accepted")
        selected_order = refresh.get("selected_order")
        selected_delay = refresh.get("selected_delay")
        if not isinstance(accepted, bool):
            errors.append(f"{key}: refresh accepted must be a bool")
            continue
        if accepted:
            if terminal_reason is not None:
                errors.append(f"{key}: accepted refresh has a terminal rejection")
            if (
                type(selected_order) is not int
                or selected_order < 1
                or type(selected_delay) is not int
                or selected_delay < 1
            ):
                errors.append(f"{key}: accepted refresh must identify selected order and delay")
        else:
            if terminal_reason not in {reason.value for reason in RefreshRejectionReason}:
                errors.append(f"{key}: missing typed terminal reason")
            if selected_order is not None or selected_delay is not None:
                errors.append(f"{key}: rejected refresh must not identify a selected candidate")
        attempts = refresh.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            errors.append(f"{key}: missing candidate attempts")
            continue
        if accepted:
            selected_candidate = next(
                (
                    attempt
                    for attempt in attempts
                    if isinstance(attempt, Mapping)
                    and attempt.get("order") == selected_order
                    and attempt.get("delay") == selected_delay
                ),
                None,
            )
            if selected_candidate is None:
                errors.append(f"{key}: selected candidate is absent from attempts")
            elif selected_candidate.get("rejection_reasons") != []:
                errors.append(f"{key}: selected candidate is rejected")
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, Mapping):
                errors.append(f"{key}: attempt {index} must be a mapping")
                continue
            missing_fields = sorted(_ATTEMPT_FIELDS - set(attempt))
            if missing_fields:
                errors.append(f"{key}: attempt {index} missing {', '.join(missing_fields)}")
            elif not _finite_json(attempt):
                errors.append(f"{key}: attempt {index} contains non-finite JSON")
    return errors


def main() -> int:
    document = collect_diagnostics()
    errors = artifact_contract_errors(document)
    json.dump(document, sys.stdout, allow_nan=False, separators=(",", ":"), sort_keys=True)
    sys.stdout.write("\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
