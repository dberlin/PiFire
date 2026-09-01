"""Shared PID-SP model-selection fixtures."""

from __future__ import annotations

from controller.pid_sp_delay_evidence import DelayBasin, DelayBlocker, DelayProfile
from controller.pid_sp_model_selection import (
    FOPDT,
    HORIZONS_S,
    IPDT,
    SOPDT,
    ModelConfirmation,
    ModelFit,
    ModelForm,
    compare_model_fits,
)

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
