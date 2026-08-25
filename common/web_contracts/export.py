from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic.json_schema import models_json_schema

from .registry import ContractBundle, RootContract, WEB_CONTRACT_BUNDLES, WEB_ROOT_CONTRACTS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIRECTORY = Path("web-react/schema/contracts")
TYPESCRIPT_DIRECTORY = Path("packages/pifire-core/src/contracts")
MANIFEST_PATH = SCHEMA_DIRECTORY / "manifest.json"
TYPESCRIPT_EXPORTS_KEY = "x-pifire-typescript-exports"
TYPESCRIPT_DEFINITION_OWNER_OVERRIDES = {
    "ProbeMap": "wizard",
}
ADDITIONAL_TYPESCRIPT_EXPORTS = {
    "settings": ("AfterStartupMode", "Units"),
    "wizard": ("Config",),
}


def _validate_output(base: Path, output: str, suffix: str, label: str) -> None:
    path = Path(output)
    if path.is_absolute() or not output.endswith(suffix):
        raise ValueError(f"invalid {label}: {output!r}")
    try:
        (base / path).resolve().relative_to(base.parent.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes {base.parent}: {output!r}") from exc


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
        _validate_output(
            TYPESCRIPT_DIRECTORY,
            bundle.typescript_output,
            ".gen.ts",
            "web contract TypeScript output",
        )
    return ordered


def _ordered_roots(roots: Iterable[RootContract]) -> tuple[RootContract, ...]:
    ordered = tuple(roots)
    names = tuple(root.name for root in ordered)
    if names != tuple(sorted(names)):
        raise ValueError("web root contracts must be sorted by name")
    if len(names) != len(set(names)):
        raise ValueError("web root contract names must be unique")
    for root in ordered:
        if not root.name or Path(root.name).name != root.name:
            raise ValueError(f"invalid web root contract name: {root.name!r}")
        _validate_output(
            SCHEMA_DIRECTORY,
            root.schema_output,
            ".schema.json",
            "web root schema output",
        )
        _validate_output(
            TYPESCRIPT_DIRECTORY,
            root.typescript_output,
            ".gen.ts",
            "web root TypeScript output",
        )
    return ordered


def render_bundle_schema(bundle: ContractBundle) -> str:
    _, schema = models_json_schema(
        [(model, "serialization") for model in bundle.models],
        title=f"PiFire {bundle.name} web contracts",
    )
    return json.dumps(schema, indent=2, sort_keys=True, allow_nan=False) + "\n"


def render_root_schema(root: RootContract) -> str:
    return (
        json.dumps(
            root.model.model_json_schema(mode="serialization"),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )


def render_contract_artifacts(
    bundles: Iterable[ContractBundle] | None = None,
    roots: Iterable[RootContract] | None = None,
) -> dict[Path, bytes]:
    if bundles is None:
        ordered = _ordered_bundles(WEB_CONTRACT_BUNDLES)
        ordered_roots = _ordered_roots(WEB_ROOT_CONTRACTS if roots is None else roots)
    else:
        ordered = _ordered_bundles(bundles)
        ordered_roots = _ordered_roots(() if roots is None else roots)
    manifest = {
        **{f"{bundle.name}.schema.json": bundle.typescript_output for bundle in ordered},
        **{root.schema_output: root.typescript_output for root in ordered_roots},
    }
    rendered_schemas = {
        **{bundle.name: json.loads(render_bundle_schema(bundle)) for bundle in ordered},
        **{root.name: json.loads(render_root_schema(root)) for root in ordered_roots},
    }

    definition_owners: dict[str, str] = {}
    # Direct root schemas own their dependency names before bundles that embed
    # those roots (notably controller responses embedding SettingsSchema).
    for root in ordered_roots:
        title = rendered_schemas[root.name].get("title")
        if isinstance(title, str) and title.isidentifier():
            definition_owners[title] = root.name
    for item in (*ordered_roots, *ordered):
        for definition in rendered_schemas[item.name].get("$defs", {}):
            definition_owners.setdefault(definition, item.name)
    for definition, owner in TYPESCRIPT_DEFINITION_OWNER_OVERRIDES.items():
        if definition not in definition_owners:
            continue
        if definition not in rendered_schemas[owner].get("$defs", {}):
            raise ValueError(f"TypeScript definition owner {owner!r} does not define {definition!r}")
        definition_owners[definition] = owner

    for name, schema in rendered_schemas.items():
        exports = set(ADDITIONAL_TYPESCRIPT_EXPORTS.get(name, ()))
        exports.update(definition for definition in schema.get("$defs", {}) if definition_owners[definition] == name)
        title = schema.get("title")
        if isinstance(title, str) and title.isidentifier():
            exports.add(title)
        schema[TYPESCRIPT_EXPORTS_KEY] = sorted(exports)

    artifacts = {
        MANIFEST_PATH: (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    }
    artifacts.update(
        (
            SCHEMA_DIRECTORY / f"{bundle.name}.schema.json",
            (json.dumps(rendered_schemas[bundle.name], indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
        )
        for bundle in ordered
    )
    artifacts.update(
        (
            SCHEMA_DIRECTORY / root.schema_output,
            (json.dumps(rendered_schemas[root.name], indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
        )
        for root in ordered_roots
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


def _expected_typescript_paths(
    root: Path,
    bundles: Sequence[ContractBundle],
    roots: Sequence[RootContract],
) -> set[Path]:
    outputs = [bundle.typescript_output for bundle in bundles]
    outputs.extend(item.typescript_output for item in roots)
    return {(root / TYPESCRIPT_DIRECTORY / output).resolve() for output in outputs}


def _write_artifacts(
    root: Path,
    bundles: tuple[ContractBundle, ...],
    roots: tuple[RootContract, ...],
) -> None:
    artifacts = render_contract_artifacts(bundles, roots)
    for relative_path, content in artifacts.items():
        _atomic_write(root / relative_path, content)
        print(f"Wrote {relative_path}")

    schema_directory = root / SCHEMA_DIRECTORY
    expected_schema_paths = {root / relative_path for relative_path in artifacts}
    for unexpected in sorted(_files_below(schema_directory) - expected_schema_paths):
        unexpected.unlink()
        print(f"Removed {unexpected.relative_to(root)}")

    (root / TYPESCRIPT_DIRECTORY).mkdir(parents=True, exist_ok=True)


def _check_artifacts(
    root: Path,
    bundles: tuple[ContractBundle, ...],
    roots: tuple[RootContract, ...],
) -> bool:
    artifacts = render_contract_artifacts(bundles, roots)
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

    expected_typescript_paths = _expected_typescript_paths(root, bundles, roots)
    for missing in sorted(path for path in expected_typescript_paths if not path.is_file()):
        print(f"missing: {missing.relative_to(root)}", file=sys.stderr)
        stale = True
    contract_typescript_paths = _files_below(root / TYPESCRIPT_DIRECTORY)
    for unexpected in sorted(contract_typescript_paths - expected_typescript_paths):
        print(f"unexpected: {unexpected.relative_to(root)}", file=sys.stderr)
        stale = True

    if not stale:
        print("Pydantic web contract artifacts are up to date.")
    return not stale


def main(
    argv: Sequence[str] | None = None,
    *,
    root: Path = REPOSITORY_ROOT,
    bundles: Iterable[ContractBundle] | None = None,
    roots: Iterable[RootContract] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Export Pydantic web contracts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write generated schema artifacts")
    mode.add_argument("--check", action="store_true", help="check committed generated artifacts")
    arguments = parser.parse_args(argv)
    if bundles is None:
        ordered = _ordered_bundles(WEB_CONTRACT_BUNDLES)
        ordered_roots = _ordered_roots(WEB_ROOT_CONTRACTS if roots is None else roots)
    else:
        ordered = _ordered_bundles(bundles)
        ordered_roots = _ordered_roots(() if roots is None else roots)

    if arguments.write:
        _write_artifacts(root, ordered, ordered_roots)
        return 0
    return 0 if _check_artifacts(root, ordered, ordered_roots) else 1


if __name__ == "__main__":
    raise SystemExit(main())
