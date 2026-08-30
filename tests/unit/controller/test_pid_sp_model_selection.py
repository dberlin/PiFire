from __future__ import annotations

import copy
import itertools
import math
from dataclasses import fields, replace

import numpy as np
import pytest

import controller.pid_sp_model_selection as selection
from controller.pid_sp_delay_evidence import (
    DelayBasin,
    DelayBlocker,
    DelayProfile,
    EpisodeInputHistory,
    ExcitationEpisode,
)
from controller.pid_sp_model_selection import (
    CONFIRMATION_WINDOW,
    FOPDT,
    HORIZONS_S,
    IPDT,
    MODEL_SELECTION_SCHEMA,
    PID_SP_CHECKPOINT_SCHEMA,
    SOPDT,
    ModelConfirmation,
    ModelFit,
    ModelForm,
    compare_model_fits,
    decode_model_confirmation,
    decode_pid_sp_checkpoint,
    encode_model_confirmation,
    encode_pid_sp_checkpoint,
    fit_pid_sp_models,
    fopdt_transformed_relative_standard_errors,
    fopdt_uncertainty_blockers,
    pid_sp_fit_corpus_digest,
    select_pid_sp_model,
    sopdt_transformed_relative_standard_errors,
    sopdt_uncertainty_blockers,
)
from controller.pid_sp_observation import PidSpDutySegment, PidSpInterval

_INSTALLATION_DIGEST = "a" * 64

_EPISODE_IDS = ("episode-a", "episode-b", "episode-c")
_COMMON_ROW_IDS = (
    ((6.0, 7.0),),
    ((6.0, 7.0),),
    ((6.0, 7.0),),
)


def _profile(
    form: ModelForm,
    *,
    blockers: tuple[DelayBlocker, ...] = (),
    episode_ids: tuple[str, ...] = _EPISODE_IDS,
    upper_s: int = 10,
) -> DelayProfile:
    confidence_upper_s = 100 if DelayBlocker.DELAY_BASIN_TOO_WIDE in blockers else upper_s
    basin = DelayBasin(
        lower_s=5,
        upper_s=upper_s,
        representative_s=5,
        confidence_lower_s=5,
        confidence_upper_s=confidence_upper_s,
        confidence_method="provided",
        confidence_resamples=0,
        episode_count=len(episode_ids),
        interior=True,
        blockers=blockers,
    )
    return DelayProfile(
        model_form=form.value,
        evaluated_bound_s=300,
        candidate_losses=((5, 1.0), (upper_s, 1.01)),
        episode_ids=episode_ids,
        basin=basin,
        next_evaluated_bound_s=None,
        blockers=blockers,
        authorized=not blockers,
    )


def _unavailable_profile(form: ModelForm) -> DelayProfile:
    return DelayProfile(
        model_form=form.value,
        evaluated_bound_s=300,
        candidate_losses=tuple((delay_s, 1e300) for delay_s in range(0, 301, 5)),
        episode_ids=_EPISODE_IDS,
        basin=None,
        next_evaluated_bound_s=None,
        blockers=(DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE,),
        authorized=False,
    )


def _parameters(form: ModelForm, *, gain_offset: float = 0.0):
    return {
        ModelForm.IPDT: IPDT(K_i=0.4 + gain_offset, c0=-0.02, theta=5.0),
        ModelForm.FOPDT: FOPDT(K=500.0 + gain_offset, tau=800.0, theta=5.0),
        ModelForm.SOPDT: SOPDT(
            K=500.0 + gain_offset,
            tau_1=500.0,
            tau_2=900.0,
            theta=5.0,
        ),
    }[form]


def _fit(
    form: ModelForm,
    losses: tuple[float, ...],
    *,
    one_step_loss: float = 1.0,
    parameters=None,
    profile: DelayProfile | None = None,
    physical: tuple[str, ...] = (),
    uncertainty: tuple[str, ...] = (),
    basin: tuple[DelayBlocker, ...] = (),
    stability: tuple[str, ...] = (),
    validation: tuple[str, ...] = (),
) -> ModelFit:
    delay_profile = profile or _profile(form, blockers=basin)
    return ModelFit(
        form=form,
        parameters=_parameters(form) if parameters is None else parameters,
        delay_profile=delay_profile,
        one_step_loss=one_step_loss,
        horizon_losses=tuple((horizon, sum(losses) / len(losses)) for horizon in HORIZONS_S),
        fold_losses=losses,
        episode_ids=_EPISODE_IDS,
        common_row_ids=_COMMON_ROW_IDS,
        physical_blockers=physical,
        uncertainty_blockers=uncertainty,
        stability_blockers=stability,
        validation_blockers=validation,
    )


def _comparison(
    *fits: ModelFit,
    confirmation: ModelConfirmation | None = None,
    corpus_digest: str = "1" * 64,
    configuration_digest: str = "2" * 64,
):
    return compare_model_fits(
        tuple(fits),
        fit_corpus_digest=corpus_digest,
        configuration_digest=configuration_digest,
        confirmation=confirmation,
    )


def test_nonphysical_fopdt_cannot_preempt_physical_ipdt():
    ipdt = _fit(ModelForm.IPDT, (1.0, 1.0))
    fopdt = _fit(
        ModelForm.FOPDT,
        (0.1, 0.1),
        physical=("tau-out-of-bounds",),
    )

    comparison = _comparison(fopdt, ipdt)

    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.IPDT
    assert comparison.authorized is False
    rejected = comparison.fit_for(ModelForm.FOPDT)
    assert rejected.physical_blockers == ("tau-out-of-bounds",)
    assert rejected.parameters == fopdt.parameters


def test_form_input_order_cannot_change_selection_or_comparison_order():
    fits = (
        _fit(ModelForm.IPDT, (1.0, 1.0)),
        _fit(ModelForm.FOPDT, (0.96, 1.04)),
        _fit(ModelForm.SOPDT, (0.8, 1.2)),
    )

    outcomes = tuple(_comparison(*order) for order in itertools.permutations(fits))

    assert {outcome.selected.form for outcome in outcomes if outcome.selected} == {ModelForm.IPDT}
    assert all(
        tuple(fit.form for fit in outcome.fits) == (ModelForm.IPDT, ModelForm.FOPDT, ModelForm.SOPDT)
        for outcome in outcomes
    )
    assert len({outcome.selected.model_digest for outcome in outcomes if outcome.selected}) == 1


def test_sopdt_one_step_win_loses_when_common_rolling_validation_is_worse():
    ipdt = _fit(ModelForm.IPDT, (1.0, 1.0), one_step_loss=0.8)
    sopdt = _fit(ModelForm.SOPDT, (1.5, 1.5), one_step_loss=0.1)

    comparison = _comparison(sopdt, ipdt)

    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.IPDT
    assert comparison.fit_for(ModelForm.SOPDT).one_step_loss < ipdt.one_step_loss
    assert comparison.fit_for(ModelForm.SOPDT).mean_validation_loss > ipdt.mean_validation_loss


def test_simplest_form_within_one_standard_error_wins():
    ipdt = _fit(ModelForm.IPDT, (1.0, 1.0))
    fopdt = _fit(ModelForm.FOPDT, (0.92, 0.98))
    sopdt = _fit(ModelForm.SOPDT, (0.75, 1.05))

    comparison = _comparison(sopdt, fopdt, ipdt)

    assert comparison.best_form is ModelForm.SOPDT
    assert comparison.best_mean_validation_loss == pytest.approx(0.9)
    assert comparison.best_standard_error == pytest.approx(0.15)
    assert comparison.comparison_threshold == pytest.approx(1.05)
    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.IPDT


def test_selected_report_uses_exact_gated_profile_margin_and_threshold():
    ipdt = _fit(ModelForm.IPDT, (1.0, 1.0))
    fopdt = _fit(ModelForm.FOPDT, (0.75, 1.05))

    comparison = _comparison(fopdt, ipdt)

    assert comparison.selected is not None
    assert comparison.comparison_threshold is not None
    assert comparison.selected.schema_version == MODEL_SELECTION_SCHEMA
    assert comparison.selected.delay_basin is ipdt.delay_profile.basin
    assert comparison.selected.common_row_digest == selection.pid_sp_common_row_digest(ipdt.common_row_ids)
    assert "delay_profile" not in {field.name for field in fields(comparison.selected)}
    assert "common_row_ids" not in {field.name for field in fields(comparison.selected)}
    assert "mean_validation_loss" not in {field.name for field in fields(comparison.selected)}
    assert comparison.selected.comparison_threshold == comparison.comparison_threshold
    assert comparison.selected.selection_margin == comparison.selection_margin
    assert comparison.selection_margin == pytest.approx(comparison.comparison_threshold - ipdt.mean_validation_loss)


def test_formless_selection_cannot_authorize_output():
    rejected = _fit(
        ModelForm.IPDT,
        (1.0, 1.0),
        uncertainty=("covariance-singular",),
    )

    comparison = _comparison(rejected, confirmation=ModelConfirmation())

    assert comparison.selected is None
    assert comparison.authorized is False


def test_rejected_form_retains_every_exact_gate_blocker():
    fit = _fit(
        ModelForm.SOPDT,
        (math.inf, math.inf),
        physical=("gain-out-of-bounds",),
        uncertainty=("covariance-singular",),
        basin=(DelayBlocker.DELAY_BASIN_TOO_WIDE,),
        stability=("complex-discrete-poles",),
        validation=("nonfinite-common-validation-loss",),
    )

    assert fit.eligible is False
    assert fit.physical_blockers == ("gain-out-of-bounds",)
    assert fit.uncertainty_blockers == ("covariance-singular",)
    assert fit.basin_blockers == (DelayBlocker.DELAY_BASIN_TOO_WIDE,)
    assert fit.stability_blockers == ("complex-discrete-poles",)
    assert fit.validation_blockers == ("nonfinite-common-validation-loss",)
    assert fit.all_blockers == (
        "gain-out-of-bounds",
        "covariance-singular",
        DelayBlocker.DELAY_BASIN_TOO_WIDE,
        "complex-discrete-poles",
        "nonfinite-common-validation-loss",
    )


def test_sopdt_with_nonphysical_time_constant_is_rejected_without_clamping():
    fit = _fit(
        ModelForm.SOPDT,
        (0.1, 0.1),
        parameters=SOPDT(K=500.0, tau_1=-25.0, tau_2=900.0, theta=5.0),
    )

    assert fit.eligible is False
    assert isinstance(fit.parameters, SOPDT)
    assert fit.parameters.tau_1 == -25.0
    assert fit.physical_blockers == ("tau_1-out-of-bounds",)


def test_twentieth_complete_model_confirmation_authorizes_not_nineteenth():
    confirmation = ModelConfirmation()
    fit = _fit(ModelForm.IPDT, (1.0, 1.0))

    for expected in range(1, CONFIRMATION_WINDOW):
        comparison = _comparison(fit, confirmation=confirmation)
        assert comparison.selected is not None
        assert comparison.selected.confirmation_observed == expected
        assert comparison.selected.authorized is False
        assert comparison.authorized is False

    comparison = _comparison(fit, confirmation=confirmation)
    assert comparison.selected is not None
    assert comparison.selected.confirmation_observed == CONFIRMATION_WINDOW
    assert comparison.selected.confirmation_required == CONFIRMATION_WINDOW
    assert comparison.selected.authorized is True
    assert comparison.authorized is True


def test_confirmation_state_round_trips_across_cold_restart() -> None:
    fit = _fit(ModelForm.IPDT, (1.0, 1.0))
    first = ModelConfirmation()
    for _ in range(7):
        _comparison(fit, confirmation=first)

    encoded = encode_model_confirmation(first)
    restored = decode_model_confirmation(encoded)
    comparison = _comparison(fit, confirmation=restored)

    assert comparison.selected is not None
    assert comparison.selected.confirmation_observed == 8


@pytest.mark.parametrize(
    "encoded",
    (
        {"schema": "pid-sp-confirmation/v1", "candidate_key": None, "observed": 1},
        {"schema": "pid-sp-confirmation/v1", "candidate_key": "1" * 64, "observed": 0},
        {"schema": "pid-sp-confirmation/v1", "candidate_key": "1" * 64, "observed": 21},
        {"schema": "pid-sp-confirmation/v1", "candidate_key": "not-a-digest", "observed": 1},
    ),
)
def test_confirmation_codec_rejects_malformed_or_over_window_state(encoded) -> None:
    with pytest.raises((TypeError, ValueError)):
        decode_model_confirmation(encoded)


@pytest.mark.parametrize(
    "dimension",
    ["parameters", "form", "basin", "corpus", "configuration"],
)
def test_confirmation_resets_on_every_complete_model_dimension(dimension):
    confirmation = ModelConfirmation()
    base = _fit(ModelForm.IPDT, (1.0, 1.0))
    for _ in range(7):
        _comparison(base, confirmation=confirmation)

    changed = base
    corpus_digest = "1" * 64
    configuration_digest = "2" * 64
    if dimension == "parameters":
        changed = replace(base, parameters=_parameters(ModelForm.IPDT, gain_offset=0.01))
    elif dimension == "form":
        changed = _fit(ModelForm.FOPDT, (1.0, 1.0))
    elif dimension == "basin":
        changed = replace(base, delay_profile=_profile(ModelForm.IPDT, upper_s=15))
    elif dimension == "corpus":
        corpus_digest = "3" * 64
    else:
        configuration_digest = "4" * 64

    comparison = _comparison(
        changed,
        confirmation=confirmation,
        corpus_digest=corpus_digest,
        configuration_digest=configuration_digest,
    )

    assert comparison.selected is not None
    assert comparison.selected.confirmation_observed == 1
    assert comparison.selected.authorized is False


def test_model_fit_requires_exact_rolling_origin_fold_count():
    with pytest.raises(ValueError, match="fold_losses"):
        _fit(ModelForm.IPDT, (1.0,))


def test_two_episode_fit_has_exact_uncertainty_blocker_and_cannot_authorize():
    profile = _profile(
        ModelForm.IPDT,
        episode_ids=("episode-a", "episode-b"),
    )
    fit = ModelFit(
        form=ModelForm.IPDT,
        parameters=IPDT(K_i=0.4, c0=-0.02, theta=5.0),
        delay_profile=profile,
        one_step_loss=1.0,
        horizon_losses=tuple((horizon, 1.0) for horizon in HORIZONS_S),
        fold_losses=(1.0,),
        episode_ids=("episode-a", "episode-b"),
        common_row_ids=(((6.0, 7.0),), ((6.0, 7.0),)),
    )

    assert fit.standard_error == math.inf
    assert fit.uncertainty_blockers == ("insufficient-independent-validation-folds",)
    assert fit.eligible is False
    assert _comparison(fit, confirmation=ModelConfirmation()).authorized is False


def test_20_second_rows_score_exact_horizon_targets_not_endpoint_buckets():
    rows = tuple(
        selection._Row(
            start_s=index * 20.0,
            end_s=(index + 1) * 20.0,
            temperature_0=100.0 + index,
            temperature_1=101.0 + index,
            previous_rate=0.05,
            duties=((0.0, 0.5),),
        )
        for index in range(12)
    )

    _, losses = selection._predict_losses(
        ModelForm.IPDT,
        (0.0, 0.0),
        rows,
        0.0,
    )
    by_horizon = dict(losses)

    assert by_horizon[3] != by_horizon[15]
    assert by_horizon[3] < by_horizon[15] < by_horizon[45]


def _segmented_validation_rows(*, high_first: bool):
    segments = ((0.0, 10.0, 1.0), (10.0, 20.0, 0.0)) if high_first else ((0.0, 10.0, 0.0), (10.0, 20.0, 1.0))
    return tuple(
        selection._Row(
            start_s=index * 20.0,
            end_s=(index + 1) * 20.0,
            temperature_0=100.0,
            temperature_1=100.0,
            previous_rate=0.0,
            duties=((0.0, 0.5),),
            duty_segments=((0.0, segments),),
        )
        for index in range(12)
    )


@pytest.mark.parametrize(
    ("form", "coefficients"),
    [
        (ModelForm.IPDT, (0.0, 1.0)),
        (ModelForm.FOPDT, (0.02, -0.001, 0.2)),
        (
            ModelForm.SOPDT,
            (0.0, -(1 / 500 + 1 / 900), -1 / (500 * 900), 0.2),
        ),
    ],
)
def test_horizon_predictions_split_at_real_delayed_duty_boundaries(
    form,
    coefficients,
):
    early = _segmented_validation_rows(high_first=True)
    late = _segmented_validation_rows(high_first=False)
    assert all(row.duty(0.0) == 0.5 for row in (*early, *late))

    _, early_losses = selection._predict_losses(form, coefficients, early, 0.0)
    _, late_losses = selection._predict_losses(form, coefficients, late, 0.0)
    early_by_horizon = dict(early_losses)
    late_by_horizon = dict(late_losses)

    assert early_by_horizon[3] != pytest.approx(late_by_horizon[3])
    assert early_by_horizon[15] != pytest.approx(late_by_horizon[15])
    if form is ModelForm.IPDT:
        assert late_by_horizon[3] == pytest.approx(0.0)
        assert early_by_horizon[3] == pytest.approx(9.0)


def test_validation_gap_stops_continuous_propagation():
    first, second = _segmented_validation_rows(high_first=True)[:2]
    second = replace(second, start_s=40.0, end_s=60.0)

    _, losses = selection._predict_losses(
        ModelForm.IPDT,
        (0.0, 1.0),
        (first, second),
        0.0,
    )

    assert math.isfinite(dict(losses)[15])
    assert dict(losses)[45] == math.inf


def test_fopdt_delta_covariance_keeps_correlated_uncertainty():
    coefficients = np.asarray((0.0, -0.001, 0.5))
    covariance = np.zeros((3, 3))
    covariance[1, 1] = 2.25e-8
    covariance[2, 2] = 0.005625
    covariance[1, 2] = covariance[2, 1] = 1.125e-5

    rse_k, rse_tau = fopdt_transformed_relative_standard_errors(
        coefficients,
        covariance,
    )

    assert rse_k == pytest.approx(0.30)
    assert rse_tau == pytest.approx(0.15)
    assert fopdt_uncertainty_blockers(coefficients, covariance) == ("K-relative-standard-error",)


def test_sopdt_delta_covariance_keeps_correlated_uncertainty():
    tau_1, tau_2, gain = 500.0, 900.0, 500.0
    coefficients = np.asarray(
        (
            0.0,
            -(1.0 / tau_1 + 1.0 / tau_2),
            -1.0 / (tau_1 * tau_2),
            gain / (tau_1 * tau_2),
        )
    )
    perturbation = np.asarray((0.0, 0.0, 0.15 * abs(coefficients[2]), 0.15 * abs(coefficients[3])))
    covariance = np.outer(perturbation, perturbation)

    errors = sopdt_transformed_relative_standard_errors(coefficients, covariance)

    assert errors["K"] > 0.20
    assert sopdt_uncertainty_blockers(coefficients, covariance) == (
        "K-relative-standard-error",
        "tau_2-relative-standard-error",
    )


def _integrating_episode(episode_id: str, phase: int, *, row_s: float = 1.0) -> ExcitationEpisode:
    intervals: list[PidSpInterval] = []
    temperature = 100.0
    duties: list[float] = []
    count = int(360.0 / row_s)
    delay_rows = int(5.0 / row_s)
    for index in range(count):
        start_s = index * row_s
        duty = (0.18, 0.72, 0.35, 0.62)[int((start_s + phase) // 45) % 4]
        duties.append(duty)
        delayed_duty = duties[index - delay_rows] if index >= delay_rows else 0.18
        temperature += row_s * (0.4 * delayed_duty - 0.02)
        intervals.append(
            PidSpInterval(
                start_s=start_s,
                end_s=start_s + row_s,
                temperature_f=temperature,
                realized_duty=duty,
                continuous=True,
                observation_sequence=index,
                role_generation=1,
            )
        )
    return ExcitationEpisode(
        episode_id=episode_id,
        intervals=tuple(intervals),
        transition_at_s=45.0,
        duty_before=0.18,
        duty_after=0.72,
        terminal_reason="response-window-complete",
    )


def _real_episodes() -> tuple[ExcitationEpisode, ...]:
    return (
        _integrating_episode("episode-a", 0),
        _integrating_episode("episode-b", 7),
        _integrating_episode("episode-c", 13),
    )


def _real_profiles(episode_ids=_EPISODE_IDS):
    return {form: _profile(form, episode_ids=episode_ids) for form in (ModelForm.IPDT, ModelForm.FOPDT)}


def test_real_common_fitter_aligns_rows_before_every_form_and_fold():
    episodes = _real_episodes()
    profiles = _real_profiles()

    fits = fit_pid_sp_models(
        episodes,
        profiles,
        forms=(ModelForm.FOPDT, ModelForm.IPDT),
    )

    assert tuple(fit.form for fit in fits) == (ModelForm.IPDT, ModelForm.FOPDT)
    assert fits[0].common_row_ids == fits[1].common_row_ids
    assert tuple(len(rows) for rows in fits[0].common_row_ids) == (354, 354, 354)
    assert all(rows[0] == (6.0, 7.0) for rows in fits[0].common_row_ids)
    assert all(rows[-1] == (359.0, 360.0) for rows in fits[0].common_row_ids)
    assert all(len(fit.fold_losses) == 2 for fit in fits)


def test_real_common_fitter_keeps_nonphysical_fopdt_out_of_selection():
    episodes = _real_episodes()
    profiles = _real_profiles()

    comparison = select_pid_sp_model(
        episodes,
        profiles,
        forms=(ModelForm.FOPDT, ModelForm.IPDT),
    )

    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.IPDT
    assert comparison.fit_for(ModelForm.FOPDT).physical_blockers
    assert comparison.authorized is False


def test_unavailable_forms_are_physically_blocked_while_valid_ipdt_participates():
    episodes = _real_episodes()
    profiles = {
        ModelForm.IPDT: _profile(ModelForm.IPDT),
        ModelForm.FOPDT: _unavailable_profile(ModelForm.FOPDT),
        ModelForm.SOPDT: _unavailable_profile(ModelForm.SOPDT),
    }

    comparison = select_pid_sp_model(episodes, profiles)

    ipdt = comparison.fit_for(ModelForm.IPDT)
    assert ipdt.parameters is not None
    assert math.isfinite(ipdt.mean_validation_loss)
    for form in (ModelForm.FOPDT, ModelForm.SOPDT):
        blocked = comparison.fit_for(form)
        assert blocked.parameters is None
        assert blocked.delay_profile.basin is None
        assert DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE in blocked.basin_blockers
        assert DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE.value in blocked.physical_blockers
        assert blocked.common_row_ids == ipdt.common_row_ids
    assert comparison.best_form is ModelForm.IPDT
    assert comparison.selected is not None
    assert comparison.selected.form is ModelForm.IPDT


@pytest.mark.parametrize(
    ("constant_name", "replacement"),
    [
        ("RSE_K_MAX", 0.19),
        ("CONFIRMATION_WINDOW", CONFIRMATION_WINDOW + 1),
    ],
)
def test_every_gate_constant_changes_configuration_and_resets_confirmation(
    monkeypatch,
    constant_name,
    replacement,
):
    episodes = _real_episodes()
    profiles = _real_profiles()
    confirmation = ModelConfirmation()
    before = select_pid_sp_model(
        episodes,
        profiles,
        forms=(ModelForm.IPDT,),
        confirmation=confirmation,
    )
    assert before.selected is not None
    assert before.selected.confirmation_observed == 1

    monkeypatch.setattr(selection, constant_name, replacement)
    after = select_pid_sp_model(
        episodes,
        profiles,
        forms=(ModelForm.IPDT,),
        confirmation=confirmation,
    )

    assert after.configuration_digest != before.configuration_digest
    assert after.selected is not None
    assert after.selected.confirmation_observed == 1


def test_two_episode_real_rolling_origin_selection_remains_unauthorized():
    episodes = _real_episodes()[:2]
    profiles = _real_profiles(("episode-a", "episode-b"))

    comparison = select_pid_sp_model(
        episodes,
        profiles,
        forms=(ModelForm.IPDT,),
    )

    assert comparison.authorized is False
    assert comparison.fit_for(ModelForm.IPDT).uncertainty_blockers == ("insufficient-independent-validation-folds",)


def test_fit_corpus_digest_includes_exact_duty_segments():
    episodes = _real_episodes()
    first = episodes[0]
    interval = first.intervals[0]
    segmented = replace(
        interval,
        duty_segments=(
            PidSpDutySegment(interval.start_s, interval.start_s + 0.5, 0.17),
            PidSpDutySegment(interval.start_s + 0.5, interval.end_s, 0.19),
        ),
    )
    altered = (
        replace(first, intervals=(segmented, *first.intervals[1:])),
        *episodes[1:],
    )

    assert segmented.realized_duty == interval.realized_duty
    assert pid_sp_fit_corpus_digest(altered) != pid_sp_fit_corpus_digest(episodes)


def test_selected_model_rejects_a_forged_but_well_formed_digest():
    comparison = _comparison(_fit(ModelForm.IPDT, (1.0, 1.0)))
    assert comparison.selected is not None

    with pytest.raises(ValueError, match="model_digest"):
        replace(comparison.selected, model_digest="0" * 64)


def test_selected_model_rejects_a_forged_common_row_digest():
    comparison = _comparison(_fit(ModelForm.IPDT, (1.0, 1.0)))
    assert comparison.selected is not None

    with pytest.raises(ValueError, match="model_digest"):
        replace(comparison.selected, common_row_digest="0" * 64)


def _authorized_selected_model(form=ModelForm.SOPDT):
    confirmation = ModelConfirmation()
    comparison = None
    for _ in range(CONFIRMATION_WINDOW):
        comparison = _comparison(
            _fit(form, (1.0, 1.0)),
            confirmation=confirmation,
        )
    assert comparison is not None
    assert comparison.selected is not None
    assert comparison.selected.authorized
    return comparison.selected


def test_authorized_checkpoint_v3_round_trips_installation_bound_typed_evidence():
    selected = _authorized_selected_model()

    checkpoint = encode_pid_sp_checkpoint(
        selected,
        revision=7,
        provenance="online-common-validation",
        installation_identity_digest=_INSTALLATION_DIGEST,
    )
    restored = decode_pid_sp_checkpoint(checkpoint)

    assert checkpoint["schema_version"] == PID_SP_CHECKPOINT_SCHEMA == 3
    assert checkpoint["revision"] == 7
    assert checkpoint["provenance"] == "online-common-validation"
    assert checkpoint["installation_identity_digest"] == _INSTALLATION_DIGEST
    assert set(checkpoint) == {
        "schema_version",
        "revision",
        "provenance",
        "installation_identity_digest",
        "selected",
    }
    assert checkpoint["selected"]["form"] == "sopdt"
    assert set(checkpoint["selected"]["parameters"]) == {
        "K",
        "tau_1",
        "tau_2",
        "theta",
    }
    assert "delay_profile" not in checkpoint["selected"]
    assert "common_row_ids" not in checkpoint["selected"]
    assert "mean_validation_loss" not in checkpoint["selected"]
    assert restored.selected == selected
    assert restored.revision == 7
    assert restored.provenance == "online-common-validation"
    assert restored.installation_identity_digest == _INSTALLATION_DIGEST
    assert (
        encode_pid_sp_checkpoint(
            restored.selected,
            revision=restored.revision,
            provenance=restored.provenance,
            installation_identity_digest=restored.installation_identity_digest,
        )
        == checkpoint
    )


def test_legacy_v2_checkpoint_decodes_only_as_unbound_migration_input():
    checkpoint = encode_pid_sp_checkpoint(
        _authorized_selected_model(),
        revision=7,
        provenance="online-common-validation",
        installation_identity_digest=_INSTALLATION_DIGEST,
    )
    checkpoint["schema_version"] = 2
    checkpoint.pop("installation_identity_digest")

    restored = decode_pid_sp_checkpoint(checkpoint)

    assert restored.installation_identity_digest is None


@pytest.mark.parametrize(
    ("form", "parameters"),
    [
        pytest.param(
            ModelForm.IPDT,
            IPDT(K_i=-0.1, c0=-0.02, theta=5.0),
            id="ipdt-gain-rate",
        ),
        pytest.param(
            ModelForm.FOPDT,
            FOPDT(K=500.0, tau=1.0, theta=5.0),
            id="fopdt-time-constant",
        ),
        pytest.param(
            ModelForm.SOPDT,
            SOPDT(K=500.0, tau_1=900.0, tau_2=500.0, theta=5.0),
            id="sopdt-canonical-time-constant-order",
        ),
    ],
)
def test_selected_model_reapplies_inferred_physics(form, parameters):
    selected = _authorized_selected_model(form)

    with pytest.raises(ValueError, match="physical"):
        replace(selected, parameters=parameters)


@pytest.mark.parametrize(
    ("form", "changes"),
    [
        pytest.param(ModelForm.IPDT, {"K_i": -0.1}, id="ipdt-physical-bounds"),
        pytest.param(ModelForm.FOPDT, {"tau": 1.0}, id="fopdt-physical-bounds"),
        pytest.param(
            ModelForm.SOPDT,
            {"tau_1": 900.0, "tau_2": 500.0},
            id="sopdt-canonical-time-constant-order",
        ),
    ],
)
def test_checkpoint_rejects_recomputed_digest_with_nonphysical_parameters(
    form,
    changes,
):
    checkpoint = encode_pid_sp_checkpoint(
        _authorized_selected_model(form),
        revision=7,
        provenance="online-common-validation",
        installation_identity_digest=_INSTALLATION_DIGEST,
    )
    checkpoint["selected"]["parameters"].update(changes)
    digest_payload = {key: value for key, value in checkpoint["selected"].items() if key != "model_digest"}
    checkpoint["selected"]["model_digest"] = selection._json_digest(digest_payload)

    with pytest.raises(ValueError, match="physical"):
        decode_pid_sp_checkpoint(checkpoint)


def _mutated_checkpoint(mutation):
    checkpoint = encode_pid_sp_checkpoint(
        _authorized_selected_model(),
        revision=7,
        provenance="online-common-validation",
        installation_identity_digest=_INSTALLATION_DIGEST,
    )
    mutation(checkpoint)
    return checkpoint


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(
            lambda value: value["selected"].__setitem__(
                "horizon_losses",
                [[3.9, 1.0], *value["selected"]["horizon_losses"][1:]],
            ),
            id="fractional-horizon",
        ),
        pytest.param(
            lambda value: value["selected"].__setitem__(
                "horizon_losses",
                [["3", 1.0], *value["selected"]["horizon_losses"][1:]],
            ),
            id="string-horizon",
        ),
        pytest.param(
            lambda value: value["selected"]["parameters"].__setitem__("K", 500),
            id="integer-float-parameter",
        ),
        pytest.param(
            lambda value: value["selected"].__setitem__(
                "fold_losses",
                ["1.0", *value["selected"]["fold_losses"][1:]],
            ),
            id="numeric-string-loss",
        ),
        pytest.param(
            lambda value: value["selected"].__setitem__(
                "fold_losses",
                [True, *value["selected"]["fold_losses"][1:]],
            ),
            id="boolean-loss",
        ),
    ],
)
def test_checkpoint_decode_rejects_normalized_digest_smuggling(mutation):
    with pytest.raises((TypeError, ValueError)):
        decode_pid_sp_checkpoint(_mutated_checkpoint(mutation))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("schema_version"),
        lambda value: value.pop("installation_identity_digest"),
        lambda value: value.__setitem__("schema_version", 1),
        lambda value: value["selected"].pop("form"),
        lambda value: value["selected"].__setitem__("form", "unknown"),
        lambda value: value["selected"]["parameters"].pop("tau_2"),
        lambda value: value["selected"].__setitem__("model_digest", "0" * 64),
        lambda value: value["selected"].__setitem__("confirmation_observed", 19),
        lambda value: value["selected"].__setitem__("authorized", False),
    ],
)
def test_checkpoint_decode_rejects_old_malformed_inconsistent_or_unconfirmed(
    mutation,
):
    checkpoint = encode_pid_sp_checkpoint(
        _authorized_selected_model(),
        revision=7,
        provenance="online-common-validation",
        installation_identity_digest=_INSTALLATION_DIGEST,
    )
    malformed = copy.deepcopy(checkpoint)
    mutation(malformed)

    with pytest.raises((TypeError, ValueError, KeyError)):
        decode_pid_sp_checkpoint(malformed)


def test_model_selection_timeline_consumes_typed_pre_roll_duty_history() -> None:
    episode = ExcitationEpisode(
        episode_id="history-supported",
        intervals=(
            PidSpInterval(0.0, 20.0, 200.0, 0.7, True, 1, 4),
            PidSpInterval(20.0, 40.0, 201.0, 0.7, True, 2, 4),
        ),
        transition_at_s=0.0,
        duty_before=0.4,
        duty_after=0.7,
        terminal_reason="stop",
        input_history=EpisodeInputHistory(
            duty_segments=(
                PidSpDutySegment(-40.0, -20.0, 0.2),
                PidSpDutySegment(-20.0, 0.0, 0.4),
            )
        ),
    )

    timeline = selection._DutyTimeline(episode)

    assert timeline.parts(-40.0, -20.0) == ((0.0, 20.0, 0.2),)
    assert timeline.parts(-20.0, 0.0) == ((0.0, 20.0, 0.4),)


def test_fit_corpus_digest_binds_pre_roll_duty_history() -> None:
    episode = ExcitationEpisode(
        episode_id="history-digest",
        intervals=(
            PidSpInterval(0.0, 20.0, 200.0, 0.7, True, 1, 4),
            PidSpInterval(20.0, 40.0, 201.0, 0.7, True, 2, 4),
        ),
        transition_at_s=0.0,
        duty_before=0.4,
        duty_after=0.7,
        terminal_reason="stop",
        input_history=EpisodeInputHistory(duty_segments=(PidSpDutySegment(-20.0, 0.0, 0.2),)),
    )
    changed = replace(
        episode,
        input_history=EpisodeInputHistory(duty_segments=(PidSpDutySegment(-20.0, 0.0, 0.4),)),
    )

    assert pid_sp_fit_corpus_digest((episode,)) != pid_sp_fit_corpus_digest((changed,))
