"""Contracts for the normalized PID-SP live-learning projection."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from pydantic import TypeAdapter, ValidationError

from common import controller_model_state
from common.persistence import runtime as runtime_persistence
from common.web_contracts.learning import (
    PidSpCheckpointModel,
    PidSpDelayEvidence,
    PidSpFormComparisonReport,
    PidSpPredictorModel,
)
from common.web_contracts.learning import (
    PidSpLearningReport as WirePidSpLearningReport,
)
from controller import pid_sp_learning as learning
from controller.fopdt_identifier import (
    CONFIRM_WINDOW,
    MIN_ACCEPTED,
    MIN_ACCEPTED_SECONDS,
    MIN_DUTY_STD,
    MIN_TEMP_SPAN_F,
)
from controller.pid_sp_delay_evidence import DelayBasin, DelayBlocker, DelayProfile
from controller.pid_sp_learning import build_pid_sp_live_learning
from controller.pid_sp_model_selection import (
    HORIZONS_S,
    MODEL_SELECTION_SCHEMA,
    ModelComparison,
    ModelFit,
    ModelForm,
    decode_pid_sp_checkpoint,
)


def _identifier_status(**overrides):
    status = {
        "accepted": MIN_ACCEPTED,
        "accepted_seconds": MIN_ACCEPTED_SECONDS,
        "duty_std": MIN_DUTY_STD,
        "temp_span": MIN_TEMP_SPAN_F,
        "transition_seen": True,
        "duty_segments": 3,
        "raw_best_residual": 0.5,
        "raw_runner_up_residual": 1.0,
        "raw_candidates_passing": 1,
        "trusted": None,
        "distrust_count": 0,
        "distrust_ratio": 0.0,
    }
    status.update(overrides)
    return status


def _predictor_status(**overrides):
    status = {
        "active": False,
        "disabled": False,
        "x0": 225.0,
        "xd": 224.0,
        "z0": 0.0,
        "zd": 0.0,
        "residual_streak": 0,
        "truncated": 0,
        "model": None,
    }
    status.update(overrides)
    return status


def test_projection_publishes_the_five_backend_owned_excitation_gates():
    projection = build_pid_sp_live_learning(
        _identifier_status(
            accepted=MIN_ACCEPTED + 2,
            accepted_seconds=MIN_ACCEPTED_SECONDS + 20.0,
            duty_std=MIN_DUTY_STD + 0.01,
            temp_span=MIN_TEMP_SPAN_F + 1.0,
        ),
        _predictor_status(),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )

    assert projection["gates"] == [
        {
            "name": "accepted_samples",
            "passed": True,
            "observed": MIN_ACCEPTED + 2,
            "required": MIN_ACCEPTED,
            "unit": "samples",
        },
        {
            "name": "accepted_duration",
            "passed": True,
            "observed": MIN_ACCEPTED_SECONDS + 20.0,
            "required": MIN_ACCEPTED_SECONDS,
            "unit": "seconds",
        },
        {
            "name": "duty_standard_deviation",
            "passed": True,
            "observed": MIN_DUTY_STD + 0.01,
            "required": MIN_DUTY_STD,
            "unit": "ratio",
        },
        {
            "name": "duty_transition",
            "passed": True,
            "observed": True,
            "required": True,
            "unit": None,
        },
        {
            "name": "temperature_span",
            "passed": True,
            "observed": MIN_TEMP_SPAN_F + 1.0,
            "required": MIN_TEMP_SPAN_F,
            "unit": "°F",
        },
    ]


def test_projection_does_not_treat_raw_identifier_status_as_confirmation_progress():
    projection = build_pid_sp_live_learning(
        _identifier_status(),
        _predictor_status(),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )

    assert projection["confirmation"] == {
        "observed": None,
        "required": CONFIRM_WINDOW,
    }


@pytest.mark.parametrize(
    ("identifier", "predictor", "expected"),
    [
        pytest.param(
            _identifier_status(accepted=MIN_ACCEPTED - 1),
            _predictor_status(),
            "collecting",
            id="collecting-before-the-minimum-history",
        ),
        pytest.param(
            _identifier_status(duty_std=MIN_DUTY_STD - 0.001),
            _predictor_status(),
            "insufficient-excitation",
            id="history-ready-but-excitation-missing",
        ),
        pytest.param(
            _identifier_status(),
            _predictor_status(),
            "evaluating",
            id="all-excitation-gates-pass",
        ),
        pytest.param(
            _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0}),
            _predictor_status(active=True),
            "evaluating",
            id="raw-trusted-model-is-not-authority",
        ),
        pytest.param(
            _identifier_status(),
            _predictor_status(disabled=True),
            "fallback",
            id="disabled-predictor",
        ),
    ],
)
def test_projection_state_precedence(identifier, predictor, expected):
    projection = build_pid_sp_live_learning(
        identifier,
        predictor,
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )
    assert projection["status"] == expected


def test_fallback_wins_over_an_active_looking_trusted_model():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(active=True, disabled=True)

    projection = build_pid_sp_live_learning(
        identifier,
        predictor,
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )
    assert projection["status"] == "fallback"


@pytest.mark.parametrize(
    ("trusted", "active"),
    [
        pytest.param(None, True, id="active-predictor-without-trusted-model"),
        pytest.param(
            {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
            False,
            id="trusted-model-without-active-predictor",
        ),
    ],
)
def test_active_requires_both_a_trusted_model_and_an_active_predictor(trusted, active):
    projection = build_pid_sp_live_learning(
        _identifier_status(trusted=trusted),
        _predictor_status(active=active),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )

    assert projection["status"] == "evaluating"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_gate_observations_are_rejected(value):
    with pytest.raises(ValueError, match="accepted_seconds"):
        build_pid_sp_live_learning(
            _identifier_status(accepted_seconds=value),
            _predictor_status(),
            completed_episode_count=0,
            delay_profile=None,
            comparison=None,
        )


@pytest.mark.parametrize("field", ["accepted", "accepted_seconds", "duty_std", "temp_span"])
def test_booleans_are_not_accepted_as_numeric_gate_observations(field):
    with pytest.raises(TypeError, match=field):
        build_pid_sp_live_learning(
            _identifier_status(**{field: True}),
            _predictor_status(),
            completed_episode_count=0,
            delay_profile=None,
            comparison=None,
        )


def test_arbitrarily_large_integer_observations_remain_valid_numbers():
    accepted = 10**1000

    projection = build_pid_sp_live_learning(
        _identifier_status(accepted=accepted),
        _predictor_status(),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )

    assert projection["gates"][0]["observed"] == accepted


def test_projection_defensively_copies_nested_identifier_and_predictor_mappings():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(model={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    original_identifier = deepcopy(identifier)
    original_predictor = deepcopy(predictor)

    projection = build_pid_sp_live_learning(
        identifier,
        predictor,
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )
    projection["identifier"]["trusted"]["K"] = 999.0
    projection["predictor"]["model"]["theta"] = 999.0

    assert identifier == original_identifier
    assert predictor == original_predictor


def test_projection_requires_typed_missing_delay_evidence():
    projection = build_pid_sp_live_learning(
        _identifier_status(),
        _predictor_status(),
        completed_episode_count=1,
        delay_profile=None,
        comparison=None,
    )

    assert projection["delay_evidence"] == {
        "status": "insufficient-excitation-episodes",
        "completed_episode_count": 1,
        "evaluated_bound_s": 300,
        "profile_form": None,
        "raw_basin_lower_s": None,
        "raw_basin_upper_s": None,
        "raw_basin_representative_s": None,
        "confidence_lower_s": None,
        "confidence_upper_s": None,
        "confidence_method": None,
        "confidence_resamples": None,
        "blockers": ["insufficient-excitation-episodes"],
        "authorized": False,
    }


def test_delay_evidence_rejects_stable_status_with_blockers():
    with pytest.raises(ValidationError):
        PidSpDelayEvidence(
            status="delay-basin-stable",
            completed_episode_count=2,
            evaluated_bound_s=300,
            profile_form="ipdt",
            raw_basin_lower_s=190,
            raw_basin_upper_s=225,
            raw_basin_representative_s=205,
            confidence_lower_s=185,
            confidence_upper_s=230,
            confidence_method="provided",
            confidence_resamples=0,
            blockers=["delay-basin-edge"],
            authorized=False,
        )


def test_delay_evidence_rejects_authorized_exhausted_status():
    with pytest.raises(ValidationError):
        PidSpDelayEvidence(
            status="delay-range-exhausted",
            completed_episode_count=2,
            evaluated_bound_s=900,
            profile_form="ipdt",
            raw_basin_lower_s=880,
            raw_basin_upper_s=900,
            raw_basin_representative_s=895,
            confidence_lower_s=880,
            confidence_upper_s=900,
            confidence_method="provided",
            confidence_resamples=0,
            blockers=["delay-range-exhausted"],
            authorized=True,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_form": None},
        {"confidence_method": None},
        {"raw_basin_lower_s": None},
        {"raw_basin_representative_s": 230},
        {"confidence_lower_s": 235, "confidence_upper_s": 230},
    ],
)
def test_delay_evidence_rejects_incoherent_profile_audit(changes):
    values = {
        "status": "delay-basin-stable",
        "completed_episode_count": 2,
        "evaluated_bound_s": 300,
        "profile_form": "ipdt",
        "raw_basin_lower_s": 190,
        "raw_basin_upper_s": 225,
        "raw_basin_representative_s": 205,
        "confidence_lower_s": 185,
        "confidence_upper_s": 230,
        "confidence_method": "provided",
        "confidence_resamples": 0,
        "blockers": [],
        "authorized": True,
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        PidSpDelayEvidence(**values)


def test_projection_preserves_delay_blockers_and_primary_priority():
    blockers = (
        DelayBlocker.DELAY_BASIN_TOO_WIDE,
        DelayBlocker.INSUFFICIENT_EXCITATION_EPISODES,
        DelayBlocker.INSUFFICIENT_CONFIDENCE_EVIDENCE,
        DelayBlocker.DELAY_RANGE_EXHAUSTED,
    )
    basin = DelayBasin(
        lower_s=880,
        upper_s=900,
        representative_s=895,
        confidence_lower_s=800,
        confidence_upper_s=900,
        confidence_method="raw-basin",
        confidence_resamples=499,
        episode_count=1,
        interior=False,
        blockers=blockers,
    )
    profile = DelayProfile(
        model_form="ipdt",
        evaluated_bound_s=900,
        candidate_losses=((880, 1.04), (895, 1.0), (900, 1.02)),
        episode_ids=("episode-a",),
        basin=basin,
        next_evaluated_bound_s=None,
        blockers=blockers,
        authorized=False,
    )

    delay_evidence = build_pid_sp_live_learning(
        _identifier_status(),
        _predictor_status(),
        completed_episode_count=1,
        delay_profile=profile,
        comparison=None,
    )["delay_evidence"]

    assert delay_evidence == {
        "status": "delay-range-exhausted",
        "completed_episode_count": 1,
        "evaluated_bound_s": 900,
        "profile_form": "ipdt",
        "raw_basin_lower_s": 880,
        "raw_basin_upper_s": 900,
        "raw_basin_representative_s": 895,
        "confidence_lower_s": 800,
        "confidence_upper_s": 900,
        "confidence_method": "raw-basin",
        "confidence_resamples": 499,
        "blockers": [blocker.value for blocker in blockers],
        "authorized": False,
    }


def test_unavailable_form_projects_null_basin_audit_and_physical_blocker() -> None:
    profile = DelayProfile(
        model_form="fopdt",
        evaluated_bound_s=300,
        candidate_losses=tuple((delay_s, 1e300) for delay_s in range(0, 301, 5)),
        episode_ids=("episode-a", "episode-b", "episode-c"),
        basin=None,
        next_evaluated_bound_s=None,
        blockers=(DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE,),
        authorized=False,
    )
    fit = ModelFit(
        form=ModelForm.FOPDT,
        parameters=None,
        delay_profile=profile,
        one_step_loss=float("inf"),
        horizon_losses=tuple((horizon, float("inf")) for horizon in HORIZONS_S),
        fold_losses=(float("inf"), float("inf")),
        episode_ids=profile.episode_ids,
        common_row_ids=((), (), ()),
        physical_blockers=(DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value,),
    )
    comparison = ModelComparison(
        schema_version=MODEL_SELECTION_SCHEMA,
        fits=(fit,),
        best_form=None,
        best_mean_validation_loss=None,
        best_standard_error=None,
        comparison_threshold=None,
        selection_margin=None,
        selected=None,
        fit_corpus_digest="1" * 64,
        configuration_digest="2" * 64,
        authorized=False,
    )

    projection = build_pid_sp_live_learning(
        _identifier_status(),
        _predictor_status(),
        completed_episode_count=3,
        delay_profile=profile,
        comparison=comparison,
    )

    assert projection["delay_evidence"] == {
        "status": DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value,
        "completed_episode_count": 3,
        "evaluated_bound_s": 300,
        "profile_form": "fopdt",
        "raw_basin_lower_s": None,
        "raw_basin_upper_s": None,
        "raw_basin_representative_s": None,
        "confidence_lower_s": None,
        "confidence_upper_s": None,
        "confidence_method": None,
        "confidence_resamples": None,
        "blockers": [DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value],
        "authorized": False,
    }
    (form_report,) = projection["comparison"]["forms"]
    assert form_report["eligible"] is False
    assert form_report["blockers"] == [
        DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value,
    ]
    assert form_report["basin_lower_s"] is None
    assert form_report["basin_upper_s"] is None
    assert form_report["confidence_lower_s"] is None
    assert form_report["confidence_upper_s"] is None
    assert form_report["confidence_method"] is None


def test_unavailable_form_web_contract_requires_null_basin_audit() -> None:
    report = PidSpFormComparisonReport(
        form="fopdt",
        eligible=False,
        blockers=(DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value,),
        one_step_loss=None,
        horizon_losses=(),
        fold_losses=(),
        standard_error=None,
        basin_lower_s=None,
        basin_upper_s=None,
        confidence_lower_s=None,
        confidence_upper_s=None,
        confidence_method=None,
    )

    assert report.basin_lower_s is None


def _checkpoint(form, revision, parameters, theta, model_digest):
    selected = {
        "schema_version": "pid-sp-model-selection/v1",
        "form": form,
        "parameters": parameters,
        "delay_basin": {
            "lower_s": theta,
            "upper_s": theta,
            "representative_s": theta,
            "confidence_lower_s": theta,
            "confidence_upper_s": theta,
            "confidence_method": "provided",
            "confidence_resamples": 0,
            "episode_count": 3,
            "interior": True,
            "blockers": [],
        },
        "one_step_loss": 1.0,
        "horizon_losses": [[3, 1.0], [15, 1.0], [45, 1.0], [90, 1.0], [180, 1.0]],
        "fold_losses": [1.0, 1.0],
        "standard_error": 0.0,
        "comparison_threshold": 1.0,
        "selection_margin": 0.0,
        "episode_ids": ["episode-a", "episode-b", "episode-c"],
        "fit_corpus_digest": "1" * 64,
        "configuration_digest": "2" * 64,
        "common_row_digest": "e862de29171cf90e8f6b527b50fa9a9f18244547d1eced92e00235e9f381db04",
        "confirmation_observed": 20,
        "confirmation_required": 20,
        "authorized": True,
        "model_digest": model_digest,
    }
    return {
        "schema_version": 2,
        "revision": revision,
        "provenance": "common-validation",
        "selected": selected,
    }


_FOPDT_CHECKPOINT = _checkpoint(
    "fopdt",
    3,
    {"K": 800.0, "tau": 600.0, "theta": 40.0},
    40,
    "2e7d0ba075c86562bbecb85df21712281b4861663c1f2c086ac4c99beca51454",
)
_IPDT_CHECKPOINT = _checkpoint(
    "ipdt",
    4,
    {"K_i": 0.46, "c0": -0.033, "theta": 90.0},
    90,
    "a9d655a0ead182748cf03d9e6d33dd1039a27dfec4cf5f0fc244a387e682fe5b",
)


def _marked_live(**identifier_overrides):
    return build_pid_sp_live_learning(
        _identifier_status(**identifier_overrides),
        _predictor_status(),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
    )


def _report(*, status=None, checkpoint=None):
    return learning.current_pid_sp_learning_report(
        status={} if status is None else status,
        checkpoint=checkpoint,
    )


def test_empty_sources_produce_a_complete_idle_report():
    report = _report()
    payload = report.as_dict()

    assert payload == {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "idle",
        "live": False,
        "revision": report.revision,
        "gates": [],
        "identifier": None,
        "predictor": None,
        "confirmation": None,
        "delay_evidence": None,
        "comparison": None,
        "active_model": None,
        "checkpoint": None,
        "failure": None,
    }
    assert len(report.revision) == 64
    int(report.revision, 16)


@pytest.mark.parametrize(
    "checkpoint",
    [
        pytest.param(_FOPDT_CHECKPOINT, id="fopdt"),
        pytest.param(_IPDT_CHECKPOINT, id="ipdt"),
    ],
)
def test_idle_report_normalizes_each_durable_checkpoint_form(checkpoint):
    report = _report(checkpoint=checkpoint).as_dict()

    assert report["status"] == "idle"
    assert report["live"] is False
    assert report["confirmation"] is None
    assert report["checkpoint"] == checkpoint
    assert report["checkpoint"] is not checkpoint
    assert report["failure"] is None


@pytest.mark.parametrize(
    "status",
    [
        "collecting",
        "insufficient-excitation",
        "evaluating",
        "active",
        "fallback",
    ],
)
@pytest.mark.parametrize("checkpoint", [_FOPDT_CHECKPOINT, _IPDT_CHECKPOINT])
def test_pid_sp_report_contract_preserves_each_real_live_and_checkpoint_projection(status, checkpoint):
    live = _marked_live()
    live["status"] = status
    report = _report(status={"learning": live}, checkpoint=checkpoint)
    payload = report.as_dict()

    validated = WirePidSpLearningReport.model_validate_json(json.dumps(payload), strict=True)

    assert validated.model_dump(mode="json", exclude_unset=True) == payload


def test_untrusted_identifier_report_preserves_unavailable_distrust_ratio():
    live = _marked_live(distrust_ratio=None)

    payload = _report(status={"learning": live}).as_dict()

    assert payload["failure"] is None
    assert payload["identifier"]["distrust_ratio"] is None


def test_pid_sp_report_contract_preserves_idle_and_structured_failure_projections():
    idle = _report().as_dict()
    malformed_live = _marked_live()
    malformed_live["identifier"]["accepted"] = True
    failed = _report(status={"learning": malformed_live}).as_dict()

    assert (
        WirePidSpLearningReport.model_validate_json(json.dumps(idle), strict=True).model_dump(
            mode="json",
            exclude_unset=True,
        )
        == idle
    )
    assert (
        WirePidSpLearningReport.model_validate_json(json.dumps(failed), strict=True).model_dump(
            mode="json",
            exclude_unset=True,
        )
        == failed
    )


def test_checkpoint_and_live_predictor_models_remain_distinct_contracts():
    checkpoint = PidSpCheckpointModel.model_validate_json(
        json.dumps(_FOPDT_CHECKPOINT),
        strict=True,
    )
    predictor = TypeAdapter(PidSpPredictorModel).validate_python(
        {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
        strict=True,
    )

    assert checkpoint.schema_version == 2
    assert checkpoint.selected.form == "fopdt"
    assert predictor.form == "fopdt"
    with pytest.raises(ValidationError):
        TypeAdapter(PidSpPredictorModel).validate_python(_FOPDT_CHECKPOINT, strict=True)
    with pytest.raises(ValidationError):
        TypeAdapter(PidSpCheckpointModel).validate_python(
            {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
            strict=True,
        )


def test_legacy_flat_checkpoint_is_rejected():
    legacy = {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0}

    with pytest.raises(ValueError, match="checkpoint"):
        _report(checkpoint=legacy)


def test_marked_pid_sp_live_projection_is_preserved():
    live = _marked_live()

    payload = _report(status={"learning": live}, checkpoint=_FOPDT_CHECKPOINT).as_dict()

    assert payload["status"] == "evaluating"
    assert payload["live"] is True
    assert payload["gates"] == live["gates"]
    assert payload["identifier"] == live["identifier"]
    assert payload["predictor"] == live["predictor"]
    assert payload["confirmation"] == live["confirmation"]
    assert payload["delay_evidence"] == live["delay_evidence"]
    assert payload["comparison"] == live["comparison"]
    assert payload["checkpoint"] == _FOPDT_CHECKPOINT
    assert payload["failure"] is None


def test_restored_active_authority_survives_live_and_current_report_projection():
    selected = decode_pid_sp_checkpoint(_FOPDT_CHECKPOINT).selected
    live = build_pid_sp_live_learning(
        _identifier_status(),
        _predictor_status(
            active=True,
            model={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
        ),
        completed_episode_count=0,
        delay_profile=None,
        comparison=None,
        active_selected=selected,
    )

    payload = _report(
        status={"learning": live},
        checkpoint=_FOPDT_CHECKPOINT,
    ).as_dict()

    assert live["status"] == payload["status"] == "active"
    assert live["comparison"] is payload["comparison"] is None
    assert (
        live["confirmation"]
        == payload["confirmation"]
        == {
            "observed": CONFIRM_WINDOW,
            "required": CONFIRM_WINDOW,
        }
    )
    assert (
        live["active_model"]
        == payload["active_model"]
        == {
            "form": "fopdt",
            "model_digest": selected.model_digest,
        }
    )


@pytest.mark.parametrize(
    "status",
    [
        pytest.param(
            {
                "learning": {
                    "schema_version": 1,
                    "controller": "mpc",
                    "status": "collecting",
                }
            },
            id="stale-mpc-learning",
        ),
        pytest.param(
            {
                "learning": {
                    "status": "collecting",
                    "identifier": {},
                    "predictor": {},
                }
            },
            id="unmarked-learning",
        ),
    ],
)
def test_stale_or_unmarked_learning_mapping_is_not_pid_sp_live_state(status):
    payload = _report(status=status).as_dict()

    assert payload["status"] == "idle"
    assert payload["live"] is False
    assert payload["gates"] == []
    assert payload["identifier"] is None
    assert payload["predictor"] is None
    assert payload["confirmation"] is None
    assert payload["delay_evidence"] is None
    assert payload["failure"] is None


def test_malformed_marked_live_state_is_structured_error_without_hiding_checkpoint():
    malformed = {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "collecting",
        "identifier": {},
        "predictor": {},
        "confirmation": {},
        "delay_evidence": _marked_live()["delay_evidence"],
        "gates": [],
    }

    payload = _report(status={"learning": malformed}, checkpoint=_FOPDT_CHECKPOINT).as_dict()

    assert payload["status"] == "error"
    assert payload["live"] is False
    assert payload["gates"] == []
    assert payload["identifier"] is None
    assert payload["predictor"] is None
    assert payload["confirmation"] is None
    assert payload["delay_evidence"] is None
    assert payload["checkpoint"] == _FOPDT_CHECKPOINT
    assert payload["failure"]["code"] == "live-status-invalid"
    assert payload["failure"]["terminal"] is False
    assert payload["failure"]["detail"]


def _checkpoint_with_parameter(name, value, source=_FOPDT_CHECKPOINT):
    checkpoint = deepcopy(source)
    checkpoint["selected"]["parameters"][name] = value
    return checkpoint


def _checkpoint_without_parameter(name):
    checkpoint = deepcopy(_FOPDT_CHECKPOINT)
    checkpoint["selected"]["parameters"].pop(name)
    return checkpoint


def _checkpoint_with_selected_form(form):
    checkpoint = deepcopy(_FOPDT_CHECKPOINT)
    checkpoint["selected"]["form"] = form
    return checkpoint


@pytest.mark.parametrize(
    "checkpoint",
    [
        pytest.param(_checkpoint_with_parameter("K", float("nan")), id="nan"),
        pytest.param(_checkpoint_with_parameter("tau", float("inf")), id="infinity"),
        pytest.param(_checkpoint_with_selected_form("unknown"), id="unknown-form"),
        pytest.param(_checkpoint_without_parameter("theta"), id="missing"),
    ],
)
def test_malformed_or_non_finite_checkpoint_is_an_explicit_failure(checkpoint):
    with pytest.raises(ValueError, match="checkpoint"):
        _report(checkpoint=checkpoint)


def test_backend_report_uses_a_strict_checkpoint_load(monkeypatch):
    class CorruptCheckpointStore:
        def load_strict(self, name):
            assert name == "pid_sp"
            raise ValueError("malformed stored snapshot for 'pid_sp'")

    monkeypatch.setattr(runtime_persistence, "read_status", dict)
    monkeypatch.setattr(
        controller_model_state,
        "ControllerModelStore",
        CorruptCheckpointStore,
    )

    with pytest.raises(ValueError, match="malformed stored snapshot.*pid_sp"):
        learning.backend_pid_sp_learning_report()


def test_revision_is_canonical_and_covers_every_visible_field():
    live = _marked_live()
    reordered_live = {
        key: live[key]
        for key in (
            "gates",
            "confirmation",
            "predictor",
            "delay_evidence",
            "comparison",
            "active_model",
            "identifier",
            "status",
            "controller",
            "schema_version",
        )
    }
    checkpoint = dict(reversed(tuple(_FOPDT_CHECKPOINT.items())))

    first = _report(status={"learning": live}, checkpoint=_FOPDT_CHECKPOINT)
    reordered = _report(status={"learning": reordered_live}, checkpoint=checkpoint)
    changed_checkpoint = deepcopy(_FOPDT_CHECKPOINT)
    changed_checkpoint["provenance"] = "alternate-common-validation"
    changed = _report(
        status={"learning": live},
        checkpoint=changed_checkpoint,
    )
    gate_changed_live = deepcopy(live)
    gate_changed_live["gates"][0]["observed"] += 1
    gate_changed = _report(
        status={"learning": gate_changed_live},
        checkpoint=_FOPDT_CHECKPOINT,
    )

    assert first.revision == reordered.revision
    assert first.revision != changed.revision
    assert first.revision != gate_changed.revision
    first_without_revision = first.as_dict()
    first_without_revision.pop("revision")
    assert json.dumps(first_without_revision, allow_nan=False)


def test_report_has_immutable_storage_and_returns_defensive_copies():
    live = _marked_live(
        trusted={
            "form": "fopdt",
            "K": 800.0,
            "tau": 600.0,
            "theta": 40.0,
        }
    )
    status = {"controller": {"learning": live}}
    checkpoint = deepcopy(_FOPDT_CHECKPOINT)
    original_status = deepcopy(status)
    original_checkpoint = deepcopy(checkpoint)

    report = _report(status=status, checkpoint=checkpoint)
    returned = report.as_dict()
    returned["identifier"]["trusted"]["K"] = 999.0
    returned["confirmation"]["observed"] = 999
    returned["delay_evidence"]["completed_episode_count"] = 999
    returned["checkpoint"]["selected"]["parameters"]["K"] = 999.0

    assert status == original_status
    assert checkpoint == original_checkpoint
    assert report.as_dict()["identifier"]["trusted"]["K"] == 800.0
    assert report.as_dict()["confirmation"] == original_status["controller"]["learning"]["confirmation"]
    assert report.as_dict()["delay_evidence"] == original_status["controller"]["learning"]["delay_evidence"]
    assert report.as_dict()["checkpoint"]["selected"]["parameters"]["K"] == 800.0
    with pytest.raises(FrozenInstanceError):
        report.payload_bytes = b"{}"


def test_backend_report_reads_status_and_checkpoint_once(monkeypatch):
    calls = []
    live = _marked_live()

    def read_once():
        calls.append("status")
        return {"learning": live}

    class Store:
        def load_strict(self, name):
            calls.append(("checkpoint", name))
            return _FOPDT_CHECKPOINT

    monkeypatch.setattr("common.persistence.runtime.read_status", read_once)
    monkeypatch.setattr("common.controller_model_state.ControllerModelStore", Store)

    report = learning.backend_pid_sp_learning_report()

    assert report.as_dict()["status"] == "evaluating"
    assert calls == ["status", ("checkpoint", "pid_sp")]


def test_diagnostic_learning_report_wraps_the_backend_report_once(monkeypatch):
    canonical = _report()
    calls = []

    def backend_report():
        calls.append("backend")
        return canonical

    monkeypatch.setattr(learning, "backend_pid_sp_learning_report", backend_report)

    report = learning.diagnostic_learning_report()

    assert report.controller == "pid_sp"
    assert report.schema_version == 1
    assert report.revision == canonical.revision
    assert report.report == canonical.as_dict()
    assert calls == ["backend"]


def test_checkpoint_contract_is_immutable_and_typed():
    checkpoint = PidSpCheckpointModel.model_validate_json(
        json.dumps(_FOPDT_CHECKPOINT),
        strict=True,
    )

    assert checkpoint.schema_version == 2
    assert checkpoint.selected.form == "fopdt"
    with pytest.raises(ValidationError):
        checkpoint.revision = 4


@pytest.mark.parametrize(
    "checkpoint",
    [
        pytest.param(_checkpoint_with_parameter("K", True), id="fopdt-K"),
        pytest.param(_checkpoint_with_parameter("tau", True), id="fopdt-tau"),
        pytest.param(_checkpoint_with_parameter("theta", True), id="fopdt-theta"),
        pytest.param(
            _checkpoint_with_parameter("K_i", True, _IPDT_CHECKPOINT),
            id="ipdt-K-i",
        ),
        pytest.param(
            _checkpoint_with_parameter("c0", True, _IPDT_CHECKPOINT),
            id="ipdt-c0",
        ),
        pytest.param(
            {**_FOPDT_CHECKPOINT, "revision": True},
            id="revision",
        ),
    ],
)
def test_checkpoint_numeric_fields_reject_booleans_before_conversion(checkpoint):
    with pytest.raises(ValueError, match="checkpoint is invalid"):
        _report(checkpoint=checkpoint)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_bool_or_float_schema_markers_do_not_claim_pid_sp_live_state(schema_version):
    live = _marked_live()
    live["schema_version"] = schema_version

    payload = _report(status={"learning": live}).as_dict()

    assert payload["status"] == "idle"
    assert payload["live"] is False
    assert payload["failure"] is None
