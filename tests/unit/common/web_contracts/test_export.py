from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from common.web_contracts import ExtensibleWireModel, FiniteFloat, WireModel
from common.web_contracts.export import main, render_bundle_schema, render_contract_artifacts
from common.web_contracts.registry import ContractBundle


class Child(WireModel):
    count: int


class Parent(WireModel):
    child: Child
    mode: Literal["one", "two"]


BUNDLE = ContractBundle("test", (Parent, Child), "test.gen.ts")


def _files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_bundle_schema_is_deterministic_strict_and_lf_terminated():
    first = render_bundle_schema(BUNDLE)
    second = render_bundle_schema(BUNDLE)

    assert first == second
    assert first.endswith("\n")
    assert '"additionalProperties": false' in first


def test_base_models_enforce_the_wire_contract():
    with pytest.raises(ValidationError):
        Child.model_validate({"count": "1"})
    with pytest.raises(ValidationError):
        Child.model_validate({"count": 1, "future": True})

    child = Child(count=1)
    with pytest.raises(ValidationError):
        child.count = 2

    class Measurement(WireModel):
        value: FiniteFloat

    for value in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValidationError):
            Measurement(value=value)

    class Extensible(ExtensibleWireModel):
        known: int

    assert Extensible.model_validate({"known": 1, "future": True}).model_extra == {"future": True}


def test_rendered_artifacts_include_a_deterministic_manifest():
    artifacts = render_contract_artifacts((BUNDLE,))

    assert tuple(artifacts) == (
        Path("web-react/schema/contracts/manifest.json"),
        Path("web-react/schema/contracts/test.schema.json"),
    )
    assert json.loads(artifacts[Path("web-react/schema/contracts/manifest.json")]) == {
        "test.schema.json": "test.gen.ts"
    }
    assert all(content.endswith(b"\n") for content in artifacts.values())


def test_check_reports_missing_changed_and_unexpected_files_without_mutating(tmp_path, capsys):
    artifacts = render_contract_artifacts((BUNDLE,))
    for relative_path, content in artifacts.items():
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    schema_path = tmp_path / "web-react/schema/contracts/test.schema.json"
    schema_path.unlink()
    manifest_path = tmp_path / "web-react/schema/contracts/manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    typescript_dir = tmp_path / "web-react/src/helpers/contracts"
    typescript_dir.mkdir(parents=True)
    (typescript_dir / "test.gen.ts").write_text("// existing generated output\n", encoding="utf-8")
    (typescript_dir / "unexpected.gen.ts").write_text("// unexpected\n", encoding="utf-8")

    before = _files(tmp_path)
    result = main(["--check"], root=tmp_path, bundles=(BUNDLE,))
    after = _files(tmp_path)
    captured = capsys.readouterr()

    assert result == 1
    assert "missing: web-react/schema/contracts/test.schema.json" in captured.err
    assert "changed: web-react/schema/contracts/manifest.json" in captured.err
    assert "unexpected: web-react/src/helpers/contracts/unexpected.gen.ts" in captured.err
    assert after == before


def test_typescript_write_removes_nested_unexpected_generated_files(tmp_path):
    manifest_path = tmp_path / "schema/contracts/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}\n", encoding="utf-8")
    unexpected = tmp_path / "src/helpers/contracts/old/obsolete.gen.ts"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_text("// obsolete\n", encoding="utf-8")
    emitter = Path(__file__).resolve().parents[4] / "web-react/scripts/emitWebContracts.ts"

    write_result = subprocess.run(
        ["bun", str(emitter), "--write"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    check_result = subprocess.run(
        ["bun", str(emitter), "--check"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert write_result.returncode == 0, write_result.stderr
    assert not unexpected.exists()
    assert check_result.returncode == 0, check_result.stderr
