from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

from common.web_contracts.inventory import JSON_WEB_CONTRACT_INVENTORY, NON_JSON_WEB_TRANSPORTS
from common.web_contracts.registry import WEB_CONTRACT_BUNDLES, WEB_ROOT_CONTRACTS

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
WEB_REACT_ROOT = REPOSITORY_ROOT / "web-react"
SCHEMA_ROOT = WEB_REACT_ROOT / "schema" / "contracts"
TYPESCRIPT_ROOT = WEB_REACT_ROOT / "src" / "helpers" / "contracts"
APPROVED_NON_JSON_CATEGORIES = {
    "browser_file_handles",
    "downloaded_bytes",
    "multipart_form_data",
    "text_range_streams",
}
EXPECTED_BUNDLES = {
    "content",
    "control",
    "controller",
    "core",
    "learning",
    "operations",
    "settings",
    "wizard",
}
RETIRED_CONTRACT_PATHS = {
    "scripts/emitControllerTypes.ts",
    "src/helpers/modelEvidence/types.ts",
    "src/helpers/pidSpLearning/types.ts",
    "src/helpers/pellets/pelletTypes.ts",
    "src/helpers/files/recipeTypes.ts",
    "src/helpers/wizard/probeTypes.ts",
}


def _registered_models() -> dict[type[BaseModel], list[str]]:
    owners: dict[type[BaseModel], list[str]] = {}
    for bundle in WEB_CONTRACT_BUNDLES:
        for model in bundle.models:
            owners.setdefault(model, []).append(bundle.name)
    for root in WEB_ROOT_CONTRACTS:
        owners.setdefault(root.model, []).append(root.name)
    return owners


def _artifact_paths() -> dict[str, tuple[Path, Path]]:
    paths = {
        bundle.name: (
            SCHEMA_ROOT / f"{bundle.name}.schema.json",
            (TYPESCRIPT_ROOT / bundle.typescript_output).resolve(),
        )
        for bundle in WEB_CONTRACT_BUNDLES
    }
    paths.update(
        {
            root.name: (
                (SCHEMA_ROOT / root.schema_output).resolve(),
                (TYPESCRIPT_ROOT / root.typescript_output).resolve(),
            )
            for root in WEB_ROOT_CONTRACTS
        }
    )
    return paths


def _typescript_exports(source: str) -> set[str]:
    return set(re.findall(r"^export (?:interface|type) ([A-Za-z_$][\w$]*)", source, re.MULTILINE))


def test_inventory_names_every_frontend_json_transport_once():
    keys = [(item.transport, item.name) for item in JSON_WEB_CONTRACT_INVENTORY]
    assert len(keys) == len(set(keys))
    assert all(item.name and item.bundle for item in JSON_WEB_CONTRACT_INVENTORY)
    assert {item.transport for item in JSON_WEB_CONTRACT_INVENTORY} == {"http", "socketio"}
    assert {item.bundle for item in JSON_WEB_CONTRACT_INVENTORY} == EXPECTED_BUNDLES
    assert len(JSON_WEB_CONTRACT_INVENTORY) >= 50


def test_every_inventory_model_has_exactly_one_registered_owner():
    owners = _registered_models()
    for contract in JSON_WEB_CONTRACT_INVENTORY:
        for model in (contract.request, contract.response):
            if model is None:
                continue
            assert owners.get(model) is not None, f"{contract.name}: {model.__name__} is not registered"
            assert len(owners[model]) == 1, f"{contract.name}: {model.__name__} owners={owners[model]}"


def test_every_bundle_has_committed_schema_generated_types_and_manifest_entry():
    artifacts = _artifact_paths()
    assert set(artifacts) == EXPECTED_BUNDLES
    for schema, typescript in artifacts.values():
        assert schema.is_file(), schema
        assert typescript.is_file(), typescript

    manifest = json.loads((SCHEMA_ROOT / "manifest.json").read_text())
    expected_manifest = {
        str(schema.relative_to(SCHEMA_ROOT, walk_up=True)): str(
            typescript.relative_to(TYPESCRIPT_ROOT, walk_up=True)
        )
        for schema, typescript in artifacts.values()
    }
    assert manifest == dict(sorted(expected_manifest.items()))


def test_generated_types_export_every_registered_model_title():
    artifacts = _artifact_paths()
    bundle_by_name = {bundle.name: bundle for bundle in WEB_CONTRACT_BUNDLES}
    for name, bundle in bundle_by_name.items():
        exports = _typescript_exports(artifacts[name][1].read_text())
        missing = {model.model_json_schema().get("title", model.__name__) for model in bundle.models} - exports
        assert not missing, f"{name}: generated TypeScript is missing {sorted(missing)}"
    for root in WEB_ROOT_CONTRACTS:
        exports = _typescript_exports(artifacts[root.name][1].read_text())
        title = root.model.model_json_schema().get("title", root.model.__name__)
        assert title in exports


def test_registered_contract_titles_are_unique_across_generated_bundles():
    titles: list[str] = []
    for bundle in WEB_CONTRACT_BUNDLES:
        titles.extend(model.model_json_schema().get("title", model.__name__) for model in bundle.models)
    titles.extend(root.model.model_json_schema().get("title", root.model.__name__) for root in WEB_ROOT_CONTRACTS)
    duplicates = sorted(title for title, count in Counter(titles).items() if count > 1)
    assert duplicates == []


def test_non_json_allowlist_contains_only_the_four_approved_categories():
    assert {item.name for item in NON_JSON_WEB_TRANSPORTS} == APPROVED_NON_JSON_CATEGORIES
    assert len(NON_JSON_WEB_TRANSPORTS) == 4
    assert all(item.transport and item.reason.strip() for item in NON_JSON_WEB_TRANSPORTS)


def test_retired_declarations_and_generators_are_absent():
    remaining = sorted(path for path in RETIRED_CONTRACT_PATHS if (WEB_REACT_ROOT / path).exists())
    assert remaining == []


def test_generated_contract_artifacts_are_not_stale():
    result = subprocess.run(
        ["bun", "run", "gen:types:check"],
        cwd=WEB_REACT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
