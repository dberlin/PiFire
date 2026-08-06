"""CLI contracts for the linear-MPC bake-off runner."""

from __future__ import annotations

import json
import gzip
import re
import pytest
from pathlib import Path
from docs.superpowers.experiments.linear_mpc_bakeoff import runner as runner_module
from docs.superpowers.experiments.linear_mpc_bakeoff.__main__ import main
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _select_validation_horizon
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import _source_revision
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import load_artifact  # pyright: ignore[reportAttributeAccessIssue]
from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_tiny_matrix


def _checkpoint_rows(checkpoint: Path) -> list[dict[str, object]]:
    manifest = json.loads(checkpoint.read_text())
    assert manifest["checkpoint_schema"] == "incremental-cas/v2"
    rows: list[dict[str, object]] = []
    entries, _ = runner_module._checkpoint_delta_entries(  # pyright: ignore[reportAttributeAccessIssue]
        checkpoint, manifest["head"], manifest["run_fingerprint"], manifest["accepted_count"]
    )
    for entry in entries:
        payload = json.loads(gzip.decompress((checkpoint.parent / entry["name"]).read_bytes()))
        assert payload["cell_ordinals"] == entry["cell_ordinals"]
        rows.extend(payload["rows"])
    return rows


def test_quick_mode_writes_requested_output_and_table(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "quick.manifest.json"

    assert main(["--quick", "--output", str(output)]) == 0

    assert "arm" in capsys.readouterr().out
    assert load_artifact(output).to_document()["schema_version"] == 1
    # Full has 1,080 rows versus quick's 144; eight quick bundles bound its transport.
    assert output.stat().st_size * 8 < 100 * 1024 * 1024


def test_source_revision_is_a_plain_commit_id() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", _source_revision())


def test_validation_horizon_selection_uses_only_validation_scores() -> None:
    selection = _select_validation_horizon({600: (1.0,), 800: (0.995,), 1000: (0.991,)})

    assert selection["selected_horizon_s"] == 600
    assert selection["validation_scores"] == {"600": 1.0, "800": 0.995, "1000": 0.991}
    assert selection["tie_rationale"] == "600 seconds is within 1% of the validation best"


def test_resume_requires_existing_checkpoint(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "missing.manifest.json"

    with pytest.raises(SystemExit) as error:
        main(["--quick", "--resume", "--output", str(output)])

    assert error.value.code == 2
    assert "checkpoint" in capsys.readouterr().err.lower()


def test_cli_rejects_non_manifest_output_before_running(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "unsafe.json.gz"

    with pytest.raises(SystemExit) as error:
        main(["--quick", "--output", str(output)])

    assert error.value.code == 2
    assert ".manifest.json" in capsys.readouterr().err
    assert not output.exists()


def test_quick_resume_consumes_partial_checkpoint_to_clean_equivalent_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "resume.manifest.json"
    run_tiny_matrix(tmp_path / "partial", resume=False, interrupt_after=3, output=output)
    checkpoint = output.with_name("resume.checkpoint.manifest.json")
    partial_rows = _checkpoint_rows(checkpoint)

    assert main(["--quick", "--resume", "--output", str(output)]) == 0

    assert "arm" in capsys.readouterr().out
    restored = load_artifact(output)
    assert len(restored.scenarios) + len(restored.failures) == 144
    key = lambda row: (row["arm"], row["initialization"], row["plant"], row["scenario"], row["mode"], row["seed"])
    restored_rows = {key(row.to_document()): row.to_document() for row in restored.scenarios}
    assert all(restored_rows[key(row)] == row for row in partial_rows)
    assert not checkpoint.exists()
