from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic.json_schema import models_json_schema

from .registry import ContractBundle, WEB_CONTRACT_BUNDLES

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIRECTORY = Path("web-react/schema/contracts")
TYPESCRIPT_DIRECTORY = Path("web-react/src/helpers/contracts")
MANIFEST_PATH = SCHEMA_DIRECTORY / "manifest.json"


def _ordered_bundles(bundles: Iterable[ContractBundle]) -> tuple[ContractBundle, ...]:
    ordered = tuple(bundles)
    names = tuple(bundle.name for bundle in ordered)
    if names != tuple(sorted(names)):
        raise ValueError("web contract bundles must be sorted by name")
    if len(names) != len(set(names)):
        raise ValueError("web contract bundle names must be unique")

    outputs = tuple(bundle.typescript_output for bundle in ordered)
    if len(outputs) != len(set(outputs)):
        raise ValueError("web contract TypeScript outputs must be unique")
    for bundle in ordered:
        if not bundle.name or Path(bundle.name).name != bundle.name:
            raise ValueError(f"invalid web contract bundle name: {bundle.name!r}")
        if Path(bundle.typescript_output).name != bundle.typescript_output or not bundle.typescript_output.endswith(
            ".gen.ts"
        ):
            raise ValueError(f"invalid web contract TypeScript output: {bundle.typescript_output!r}")
    return ordered


def render_bundle_schema(bundle: ContractBundle) -> str:
    _, schema = models_json_schema(
        [(model, "serialization") for model in bundle.models],
        title=f"PiFire {bundle.name} web contracts",
    )
    return json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_contract_artifacts(
    bundles: Iterable[ContractBundle] = WEB_CONTRACT_BUNDLES,
) -> dict[Path, bytes]:
    ordered = _ordered_bundles(bundles)
    manifest = {f"{bundle.name}.schema.json": bundle.typescript_output for bundle in ordered}
    artifacts = {
        MANIFEST_PATH: (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    }
    artifacts.update(
        (SCHEMA_DIRECTORY / f"{bundle.name}.schema.json", render_bundle_schema(bundle).encode())
        for bundle in ordered
    )
    return artifacts


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _files_below(directory: Path) -> set[Path]:
    if not directory.exists():
        return set()
    return {path for path in directory.rglob("*") if path.is_file()}


def _expected_typescript_paths(root: Path, bundles: Sequence[ContractBundle]) -> set[Path]:
    return {root / TYPESCRIPT_DIRECTORY / bundle.typescript_output for bundle in bundles}


def _write_artifacts(root: Path, bundles: tuple[ContractBundle, ...]) -> None:
    artifacts = render_contract_artifacts(bundles)
    for relative_path, content in artifacts.items():
        _atomic_write(root / relative_path, content)
        print(f"Wrote {relative_path}")

    schema_directory = root / SCHEMA_DIRECTORY
    expected_schema_paths = {root / relative_path for relative_path in artifacts}
    for unexpected in sorted(_files_below(schema_directory) - expected_schema_paths):
        unexpected.unlink()
        print(f"Removed {unexpected.relative_to(root)}")

    (root / TYPESCRIPT_DIRECTORY).mkdir(parents=True, exist_ok=True)


def _check_artifacts(root: Path, bundles: tuple[ContractBundle, ...]) -> bool:
    artifacts = render_contract_artifacts(bundles)
    stale = False

    expected_schema_paths = {root / relative_path for relative_path in artifacts}
    for relative_path, expected in artifacts.items():
        destination = root / relative_path
        try:
            actual = destination.read_bytes()
        except FileNotFoundError:
            print(f"missing: {relative_path}", file=sys.stderr)
            stale = True
            continue
        if actual != expected:
            print(f"changed: {relative_path}", file=sys.stderr)
            stale = True

    schema_directory = root / SCHEMA_DIRECTORY
    for unexpected in sorted(_files_below(schema_directory) - expected_schema_paths):
        print(f"unexpected: {unexpected.relative_to(root)}", file=sys.stderr)
        stale = True

    expected_typescript_paths = _expected_typescript_paths(root, bundles)
    for missing in sorted(expected_typescript_paths - _files_below(root / TYPESCRIPT_DIRECTORY)):
        print(f"missing: {missing.relative_to(root)}", file=sys.stderr)
        stale = True
    for unexpected in sorted(_files_below(root / TYPESCRIPT_DIRECTORY) - expected_typescript_paths):
        print(f"unexpected: {unexpected.relative_to(root)}", file=sys.stderr)
        stale = True

    if not stale:
        print("Pydantic web contract artifacts are up to date.")
    return not stale


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPOSITORY_ROOT,
    bundles: Iterable[ContractBundle] = WEB_CONTRACT_BUNDLES,
) -> int:
    parser = argparse.ArgumentParser(description="Export Pydantic web contracts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write generated schema artifacts")
    mode.add_argument("--check", action="store_true", help="check committed generated artifacts")
    arguments = parser.parse_args(argv)
    ordered = _ordered_bundles(bundles)

    if arguments.write:
        _write_artifacts(root, ordered)
        return 0
    return 0 if _check_artifacts(root, ordered) else 1


if __name__ == "__main__":
    raise SystemExit(main())
