"""Layer-1 exact-evidence campaigns for the sanitized real-cook corpus."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from common import datastore
from common.controller_model_state import MODEL_STATE_KEY, ControllerModelStore
from common.learning_trajectory import TrajectoryBreakReason
from common.persistence.runtime import write_generic_key
from tests.e2e.real_cook_replay import (
    ExactReplayBlocked,
    FixtureDigestMismatch,
    copy_campaign_baseline,
    load_campaign_baseline,
    load_real_cook_manifest,
    load_replay_cook,
    replay_compatibility_digests,
    run_mpc_campaign,
)

_EXPECTED_CAMPAIGNS = (
    ("mpc-aug27", "mpc", ("thermal-smoke-only", "exact-evidence")),
    ("pid-sp-aug28", "pid_sp", ("exact-evidence",)),
    ("mpc-aug29", "mpc", ("exact-evidence", "exact-evidence")),
)


def test_manifest_hash_schema_campaign_and_cook_order_are_authoritative(tmp_path: Path) -> None:
    manifest = load_real_cook_manifest()

    assert manifest.schema_version == 1
    assert (
        tuple(
            (campaign.campaign_id, campaign.controller, tuple(cook.replay_kind for cook in campaign.cooks))
            for campaign in manifest.campaigns
        )
        == _EXPECTED_CAMPAIGNS
    )
    assert all(campaign.baseline.schema_version == 8 for campaign in manifest.campaigns)

    for campaign in manifest.campaigns:
        copied = copy_campaign_baseline(campaign.campaign_id, tmp_path / f"{campaign.campaign_id}.sqlite")
        assert copied.campaign_id == campaign.campaign_id
        assert copied.schema_version == campaign.baseline.schema_version
        assert copied.retained_state == "factory-fallback"
        assert copied.table_counts == {
            "control_trace": 0,
            "kv": 0,
            "model_activation_state": 0,
            "model_evidence": 0,
        }


def test_august_22_is_typed_thermal_smoke_only_not_invented_exact_evidence() -> None:
    manifest = load_real_cook_manifest()
    cook = manifest.campaign("mpc-aug27").cooks[0]

    with pytest.raises(ExactReplayBlocked, match="no source-supported exact actuation-frame join exists") as blocked:
        load_replay_cook(cook)

    assert blocked.value.cook_path == cook.path
    assert blocked.value.replay_kind == "thermal-smoke-only"
    assert cook.expected_input_frame_count is None
    assert cook.trace_schema_version is None

    with pytest.raises(ValueError, match="metadata differs from manifest"):
        load_replay_cook(replace(cook, cook_end_ms=cook.cook_end_ms + 1))
    assert cook.source_outcome_counts is None


def test_baseline_digest_and_malformed_checkpoint_fail_closed_on_cold_restart(tmp_path: Path) -> None:
    corrupt_path = tmp_path / "corrupt.sqlite"
    copy_campaign_baseline("mpc-aug29", corrupt_path)
    corrupt_path.write_bytes(corrupt_path.read_bytes() + b"corrupt")

    with pytest.raises(FixtureDigestMismatch, match="expected .* got"):
        load_campaign_baseline("mpc-aug29", corrupt_path)

    clean_path = tmp_path / "malformed-checkpoint.sqlite"
    copy_campaign_baseline("mpc-aug29", clean_path)
    datastore._reset_for_tests(str(clean_path))
    datastore.init()
    try:
        write_generic_key(
            MODEL_STATE_KEY,
            {"version": 1, "models": {"mpc": {"revision": -1}}},
        )
        with pytest.raises(ValueError, match="snapshot failed persistence validation"):
            ControllerModelStore().load_strict("mpc")
    finally:
        datastore._reset_for_tests(None)


@pytest.mark.parametrize(
    ("campaign_id", "cook_index", "expected_count", "expected_outcomes"),
    (
        ("mpc-aug27", 1, 196, {"accepted": 0, "gap": 0, "rejected": 196, "unmatched": 0}),
        ("mpc-aug29", 0, 296, {"accepted": 295, "gap": 0, "rejected": 1, "unmatched": 0}),
        ("mpc-aug29", 1, 247, {"accepted": 246, "gap": 0, "rejected": 1, "unmatched": 0}),
    ),
)
def test_exact_decoder_preserves_every_join_and_terminal_source_account(
    campaign_id: str,
    cook_index: int,
    expected_count: int,
    expected_outcomes: dict[str, int],
) -> None:
    campaign = load_real_cook_manifest().campaign(campaign_id)
    replay = load_replay_cook(campaign.cooks[cook_index])

    assert replay.metadata.cook_id == campaign.cooks[cook_index].fixture_cook_id
    assert len(replay.frames) == expected_count
    assert [frame.trace_sequence for frame in replay.frames] == list(range(1, expected_count + 1))
    assert Counter(frame.source_outcome.kind for frame in replay.frames) == {
        kind: count for kind, count in expected_outcomes.items() if count
    }
    assert len({frame.terminal_identity for frame in replay.frames}) == expected_count
    assert all(
        frame.observation.observation_sequence == frame.observation_sequence
        and frame.observation.result_revision == frame.result_revision
        and frame.observation.role_generation == frame.role_generation
        and round(frame.observation.frame_start_s * 1_000) == frame.frame_start_ms
        and round(frame.observation.frame_end_s * 1_000) == frame.frame_end_ms
        and frame.cook_id == replay.metadata.cook_id
        and frame.session_id in {session.session_id for session in replay.sessions}
        for frame in replay.frames
    )
    assert replay.metadata.controller == campaign.controller
    assert replay.metadata.replay_kind == campaign.cooks[cook_index].replay_kind
    with pytest.raises(ValueError, match="metadata differs from manifest"):
        load_replay_cook(replace(campaign.cooks[cook_index], controller="pid_sp"))


def test_aug29_cooks_share_compatibility_partition_inputs_without_cook_identity() -> None:
    campaign = load_real_cook_manifest().campaign("mpc-aug29")
    first = load_replay_cook(campaign.cooks[0])
    second = load_replay_cook(campaign.cooks[1])
    first_digests = replay_compatibility_digests(first)
    second_digests = replay_compatibility_digests(second)
    config = dict(first.sessions[0].controller_config)
    config["theta"] = float(config["theta"]) + 1.0
    changed_session = replace(first.sessions[0], controller_config=tuple(sorted(config.items())))
    changed = replace(first, sessions=(changed_session, *first.sessions[1:]))

    assert first_digests == second_digests
    assert all(len(value) == 64 for value in first_digests)
    assert replay_compatibility_digests(changed)[3] != first_digests[3]


@pytest.mark.parametrize(
    ("campaign_id", "expected_paths"),
    (
        (
            "mpc-aug27",
            ("cookfiles/2026-08-22--1636.pifire", "cookfiles/2026-08-27--2015.pifire"),
        ),
        (
            "mpc-aug29",
            ("cookfiles/2026-08-29--1219.pifire", "cookfiles/2026-08-29--1625.pifire"),
        ),
    ),
)
def test_exact_mpc_campaigns_replay_in_manifest_order_with_cold_restart_and_terminal_accounting(
    tmp_path: Path,
    campaign_id: str,
    expected_paths: tuple[str, ...],
) -> None:
    result = run_mpc_campaign(campaign_id, tmp_path / f"{campaign_id}.sqlite")

    assert tuple(cook.cook_path for cook in result.cooks) == expected_paths
    assert result.restart_count == len(result.exact_cooks) + 1
    assert result.final_open_segment_count == 0
    assert result.final_live_fit_worker_count == 0
    assert result.unauthorized_activation_count == 0
    assert result.cold_corpus_digest == result.corpus_digest
    assert result.cold_lifecycle == result.lifecycle
    assert result.canonical_corpus == b"[]"
    assert result.canonical_fit_requests == b"[]"
    assert result.canonical_assessments == b"[]"
    assert result.canonical_lifecycle == b"[]"

    for cook in result.exact_cooks:
        assert cook.terminal_count == cook.input_frame_count
        assert sum(cook.production_outcome_counts.values()) == cook.input_frame_count
        assert cook.joined_terminal_count == cook.input_frame_count
        assert cook.pending_observation_count == 0
        assert cook.open_segment_count == 0
        assert cook.live_fit_worker_count == 0
        assert cook.diagnostic_outcome_counts == cook.source_outcome_counts
        assert cook.diagnostic_outcome_counts != cook.production_outcome_counts
        assert cook.cold_corpus_digest == cook.corpus_digest
        assert cook.cold_lifecycle == cook.lifecycle

    blocker_codes = {blocker.code for blocker in result.scientific_blockers}
    assert blocker_codes >= {
        "exact-pre-roll-actuation-unavailable",
        "exact-fan-delivery-unavailable",
        "cumulative-fit-unreachable",
        "source-diagnostic-terminal-kind-mismatch",
    }
    assert all((cook.segment_delta, cook.pre_roll_delta, cook.scored_delta) == (0, 0, 0) for cook in result.exact_cooks)
    if campaign_id == "mpc-aug27":
        assert [cook.production_outcome_counts for cook in result.exact_cooks] == [
            {"accepted": 195, "gap": 1, "rejected": 0}
        ]
        assert result.cooks[0].replay_kind == "thermal-smoke-only"
        assert result.cooks[0].typed_outcome == "exact-actuation-join-unavailable"
        assert "no source-supported exact actuation-frame join exists" in result.cooks[0].detail
    else:
        assert [cook.production_outcome_counts for cook in result.exact_cooks] == [
            {"accepted": 295, "gap": 1, "rejected": 0},
            {"accepted": 246, "gap": 1, "rejected": 0},
        ]
        assert blocker_codes >= {
            "assessment-digest-mismatch-unreachable",
            "per-cook-regression-unreachable",
        }


def test_duplicate_mpc_aug29_replay_is_byte_identical_across_two_fresh_baseline_copies(tmp_path: Path) -> None:
    first = run_mpc_campaign("mpc-aug29", tmp_path / "first.sqlite")
    second = run_mpc_campaign("mpc-aug29", tmp_path / "second.sqlite")

    assert first.canonical_corpus == second.canonical_corpus
    assert first.canonical_fit_requests == second.canonical_fit_requests
    assert first.canonical_assessments == second.canonical_assessments
    assert first.canonical_lifecycle == second.canonical_lifecycle
    assert first.canonical_evidence == second.canonical_evidence
    assert first.canonical_state == second.canonical_state
    assert json.loads(first.canonical_state)[MODEL_STATE_KEY] is None
    assert first.primary_identities == second.primary_identities
    assert len(first.primary_identities) == len(set(first.primary_identities))


def test_applicable_failure_recovery_matrix_is_terminal_and_idempotent(tmp_path: Path) -> None:
    evicted = run_mpc_campaign(
        "mpc-aug27",
        tmp_path / "evicted.sqlite",
        reconcile_each_frame=False,
    )
    evicted_cook = evicted.exact_cooks[0]
    assert evicted_cook.terminal_count == evicted_cook.input_frame_count
    assert Counter(evicted_cook.terminal_reasons) == {
        "pending-observation-overflow": 130,
        "runner-outcome-evicted": 30,
        "accepted": 30,
        "model-persistence-unavailable": 6,
    }
    assert evicted_cook.pending_observation_count == 0
    assert evicted_cook.open_segment_count == 0
    assert evicted_cook.live_fit_worker_count == 0
    assert evicted.unauthorized_activation_count == 0
    assert evicted.cold_corpus_digest == evicted.corpus_digest

    unavailable = run_mpc_campaign(
        "mpc-aug27",
        tmp_path / "persistence-unavailable.sqlite",
        evidence_available_before_submission=False,
    )
    unavailable_cook = unavailable.exact_cooks[0]
    assert unavailable_cook.production_outcome_counts == {"accepted": 0, "gap": 196, "rejected": 0}
    assert set(unavailable_cook.terminal_reasons) == {"model-persistence-unavailable"}
    assert unavailable_cook.pending_observation_count == 0
    assert unavailable_cook.open_segment_count == 0
    assert unavailable_cook.live_fit_worker_count == 0
    assert unavailable.unauthorized_activation_count == 0

    error = run_mpc_campaign(
        "mpc-aug27",
        tmp_path / "error.sqlite",
        terminal_reason=TrajectoryBreakReason.ERROR,
    )
    assert error.terminal_reason is TrajectoryBreakReason.ERROR
    assert error.final_open_segment_count == 0
    assert error.final_live_fit_worker_count == 0
    assert error.cold_corpus_digest == error.corpus_digest
    assert error.unauthorized_activation_count == 0

    for replay_result in (evicted, unavailable, error):
        assert replay_result.canonical_corpus == b"[]"
        assert replay_result.canonical_fit_requests == b"[]"
        assert replay_result.canonical_assessments == b"[]"
        assert replay_result.canonical_lifecycle == b"[]"

    unreachable = {blocker.code for blocker in error.scientific_blockers}
    assert unreachable >= {
        "evidence-after-learner-completion-unreachable",
        "fit-worker-failure-unreachable",
        "stale-fit-result-unreachable",
        "process-restart-open-segment-unreachable",
        "model-persistence-after-qualification-unreachable",
        "incompatibility-quarantine-unreachable",
        "later-coherent-fit-unreachable",
        "missing-probe-case-unavailable",
    }
