from __future__ import annotations

import hashlib
import json

import pytest

from common.persistence.model_challenger import read_model_challenger
from controller.model_learning.installation_identity import (
    InstallationIdentityUnavailable,
    installation_identity_digest,
)
from controller.mpc_snapshot import GreySnapshotInvalid, migrate_grey_learning_snapshot, new_grey_learning_snapshot
from tests.unit.mpc._grey_learning_runtime_helpers import _harness


def test_installation_identity_is_domain_separated_before_persistence() -> None:
    raw = "installation-secret"

    digest = installation_identity_digest(lambda: raw)

    assert digest == hashlib.sha256(b"pifire:model-installation-identity:v1\x00installation-secret").hexdigest()
    assert raw not in digest


def test_installation_identity_fails_closed_when_authority_is_unavailable() -> None:
    with pytest.raises(InstallationIdentityUnavailable, match="unavailable"):
        installation_identity_digest(lambda: None)


@pytest.mark.parametrize("raw", ["", "  ", b""])
def test_installation_identity_rejects_blank_authority(raw: str | bytes) -> None:
    with pytest.raises(InstallationIdentityUnavailable, match="unavailable"):
        installation_identity_digest(lambda: raw)


_PARAMETERS = {
    "C_c": 320.0,
    "h_amb": 0.5,
    "T_amb": 20.0,
    "theta": 50.0,
    "n_delay": 8,
    "K_Q": 350.0,
    "sigma": 1.4e-9,
}
_METADATA = {"rmse": 1.0, "samples": 10, "band_c": [80.0, 120.0], "nfev": 3}


def test_current_checkpoint_persists_only_installation_digest() -> None:
    installation_digest = "a" * 64

    snapshot = new_grey_learning_snapshot(
        revision=1,
        parameters=_PARAMETERS,
        metadata=_METADATA,
        installation_identity_digest=installation_digest,
    )

    assert snapshot["version"] == 7
    assert snapshot["schema"] == "pifire-grey-learning/v7"
    assert snapshot["installation_identity_digest"] == installation_digest


def test_runtime_checkpoint_never_persists_raw_installation_identity() -> None:
    raw = "raw-installation-secret"
    harness = _harness(installation_identity_provider=lambda: raw)
    try:
        snapshot = harness.runtime.get_model_snapshot()
        assert snapshot is not None
        encoded = json.dumps(snapshot, sort_keys=True)
        assert raw not in encoded
        assert snapshot["installation_identity_digest"] == installation_identity_digest(lambda: raw)
    finally:
        harness.runtime.close()
        harness.activation.close()


def test_legacy_checkpoint_migrates_without_installation_authority() -> None:
    current = new_grey_learning_snapshot(
        revision=1,
        parameters=_PARAMETERS,
        metadata=_METADATA,
        installation_identity_digest="a" * 64,
    )
    legacy = dict(current)
    legacy.pop("installation_identity_digest")
    legacy["version"] = 6
    legacy["schema"] = "pifire-grey-learning/v6"

    migrated = migrate_grey_learning_snapshot(legacy)

    assert migrated["installation_identity_digest"] is None


def test_checkpoint_rejects_malformed_installation_digest() -> None:
    with pytest.raises(GreySnapshotInvalid, match="invalid-installation-identity"):
        new_grey_learning_snapshot(
            revision=1,
            parameters=_PARAMETERS,
            metadata=_METADATA,
            installation_identity_digest="raw-installation-secret",
        )


def test_same_installation_restores_active_checkpoint_immediately() -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    target = _harness(installation_identity_provider=lambda: "installation-a")
    snapshot = source.runtime.get_model_snapshot()
    assert snapshot is not None

    try:
        assert target.runtime.restore_model(snapshot)
        assert target.activation.active_pair.descriptor.model_digest == snapshot["identities"]["active_digest"]
        assert target.activation.active_pair.authorized
    finally:
        target.runtime.close()
        target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_mismatched_installation_keeps_fallback_authorized_and_candidate_inert() -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    target = _harness(
        learning_enabled=True,
        installation_identity_provider=lambda: "installation-b",
    )
    snapshot = source.runtime.get_model_snapshot()
    assert snapshot is not None
    snapshot["active"]["metadata"] = dict(_METADATA)
    snapshot["identification"] = {"status": "identified"}
    incumbent = target.activation.active_pair

    try:
        assert target.runtime.restore_model(snapshot) is True
        assert target.activation.active_pair is incumbent
        assert incumbent.authorized
        assert not incumbent.closed
        assert target.activation.inert_record is None
        rebound = target.runtime.get_model_snapshot()
        assert rebound is not None
        assert rebound["identities"]["active_digest"] == incumbent.descriptor.model_digest
        assert rebound["challenger_authority"] is not None
    finally:
        target.runtime.close()
        target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_failed_revalidation_authority_commit_releases_inert_candidate(
    ds,
    monkeypatch,
) -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    target = _harness(
        learning_enabled=True,
        installation_identity_provider=lambda: "installation-b",
    )
    snapshot = source.runtime.get_model_snapshot()
    assert snapshot is not None
    snapshot["active"]["metadata"] = dict(_METADATA)
    snapshot["identification"] = {"status": "identified"}
    incumbent = target.activation.active_pair
    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.commit_restore_revalidation_authority",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("disk unavailable")),
    )

    try:
        assert target.runtime.restore_model(snapshot) is False
        assert target.activation.active_pair is incumbent
        assert incumbent.authorized
        assert target.runtime._restore_revalidation_candidate_digest is None
        assert target.runtime._learning is not None
        assert target.runtime._learning.prepared is None
        assert read_model_challenger() is None
    finally:
        target.runtime.close()
        target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_legacy_checkpoint_without_installation_digest_is_revalidated_inertly() -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    target = _harness(
        learning_enabled=True,
        installation_identity_provider=lambda: "installation-b",
    )
    snapshot = source.runtime.get_model_snapshot()
    assert snapshot is not None
    snapshot["version"] = 6
    snapshot["schema"] = "pifire-grey-learning/v6"
    snapshot.pop("installation_identity_digest")
    snapshot["active"]["metadata"] = dict(_METADATA)
    snapshot["identification"] = {"status": "identified"}
    incumbent = target.activation.active_pair

    try:
        assert target.runtime.restore_model(migrate_grey_learning_snapshot(snapshot)) is True
        assert target.activation.active_pair is incumbent
        assert incumbent.authorized
        rebound = target.runtime.get_model_snapshot()
        assert rebound is not None
        assert rebound["installation_identity_digest"] == installation_identity_digest(lambda: "installation-b")
        assert rebound["challenger_authority"] is not None
    finally:
        target.runtime.close()
        target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_digestless_restore_challenger_resumes_from_its_durable_projection(
    ds,
) -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    first_target = _harness(
        learning_enabled=True,
        installation_identity_provider=lambda: "installation-b",
    )
    source_snapshot = source.runtime.get_model_snapshot()
    assert source_snapshot is not None
    learned_digest = source_snapshot["identities"]["active_digest"]
    source_snapshot["version"] = 6
    source_snapshot["schema"] = "pifire-grey-learning/v6"
    source_snapshot.pop("installation_identity_digest")
    source_snapshot["active"]["metadata"] = dict(_METADATA)
    source_snapshot["identification"] = {"status": "identified"}
    migrated = migrate_grey_learning_snapshot(source_snapshot)

    restarted = None
    try:
        assert first_target.runtime.restore_model(migrated)
        fallback_snapshot = first_target.runtime.get_model_snapshot()
        assert fallback_snapshot is not None
        first_target.runtime.close()
        first_target.activation.close()

        restarted = _harness(
            learning_enabled=True,
            installation_identity_provider=lambda: "installation-b",
        )
        assert restarted.runtime.restore_model(fallback_snapshot)
        assert restarted.runtime._learning is not None
        preparation = restarted.runtime._learning.prepared
        assert preparation is not None
        assert preparation.candidate_digest == learned_digest
        assert restarted.runtime._restore_revalidation_candidate_digest == learned_digest
        durable = read_model_challenger()
        assert durable is not None
        assert durable.phase == "evaluating"
        assert durable.evaluation_epoch == 1
    finally:
        if restarted is not None:
            restarted.runtime.close()
            restarted.activation.close()
        if not first_target.runtime._closed:
            first_target.runtime.close()
            first_target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_unavailable_installation_authority_refuses_learned_restore() -> None:
    source = _harness(installation_identity_provider=lambda: "installation-a")
    target = _harness(installation_identity_provider=lambda: None)
    snapshot = source.runtime.get_model_snapshot()
    assert snapshot is not None
    incumbent = target.activation.active_pair

    try:
        assert target.runtime.restore_model(snapshot) is False
        assert target.activation.active_pair is incumbent
        assert incumbent.authorized
    finally:
        target.runtime.close()
        target.activation.close()
        source.runtime.close()
        source.activation.close()


def test_unavailable_installation_authority_emits_no_checkpoint() -> None:
    harness = _harness(installation_identity_provider=lambda: None)
    try:
        assert harness.runtime.get_model_snapshot() is None
    finally:
        harness.runtime.close()
        harness.activation.close()
