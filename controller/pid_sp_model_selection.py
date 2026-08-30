from __future__ import annotations

import bisect
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from enum import StrEnum

import numpy as np

from common.model_evidence import EvidenceKind, ModelEvidenceRecord, PidSpFitDecisionEvidence

from .fopdt_identifier import (
    GAIN_MAX,
    GAIN_MIN,
    GAIN_RATE_MAX,
    GAIN_RATE_MIN,
    RSE_GAIN_RATE_MAX,
    RSE_K_MAX,
    RSE_TAU_MAX,
    TAU_MAX,
    TAU_MIN,
)
from .pid_sp_delay_evidence import DelayBasin, DelayBlocker, DelayProfile, ExcitationEpisode
from .pid_sp_observation import canonical_pid_sp_observation_model_digest

MODEL_SELECTION_SCHEMA = "pid-sp-model-selection/v1"
MODEL_CONFIRMATION_SCHEMA = "pid-sp-confirmation/v1"
PID_SP_LEARNING_CHECKPOINT_SCHEMA = "pid-sp-learning-checkpoint/v2"
PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA = "pid-sp-learning-checkpoint/v1"
PID_SP_LEARNING_PREPARE_SCHEMA = "pid-sp-learning-prepare/v1"
PID_SP_CHECKPOINT_SCHEMA = 3
_PID_SP_LEGACY_CHECKPOINT_SCHEMA = 2
CONFIRMATION_WINDOW = 20
HORIZONS_S = (3, 15, 45, 90, 180)
ONE_STEP_WEIGHT = 1.0
HORIZON_WEIGHTS = tuple((horizon, 1.0) for horizon in HORIZONS_S)
MIN_INDEPENDENT_EPISODES = 2
MIN_INDEPENDENT_VALIDATION_FOLDS = 2
EPISODE_STABILITY_RELATIVE_SPAN_MAX = 0.50
SOPDT_POLE_IMAGINARY_TOLERANCE = 1e-9


class ModelForm(StrEnum):
    IPDT = "ipdt"
    FOPDT = "fopdt"
    SOPDT = "sopdt"


_COMPLEXITY = {
    ModelForm.IPDT: 0,
    ModelForm.FOPDT: 1,
    ModelForm.SOPDT: 2,
}
DEFAULT_FORMS = (ModelForm.IPDT, ModelForm.FOPDT, ModelForm.SOPDT)
_MIN_FIT_ROWS = {
    ModelForm.IPDT: 2,
    ModelForm.FOPDT: 3,
    ModelForm.SOPDT: 4,
}


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


@dataclass(frozen=True, slots=True)
class IPDT:
    K_i: float
    c0: float
    theta: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "K_i", _finite(self.K_i, "K_i"))
        object.__setattr__(self, "c0", _finite(self.c0, "c0"))
        object.__setattr__(self, "theta", _finite(self.theta, "theta"))


@dataclass(frozen=True, slots=True)
class FOPDT:
    K: float
    tau: float
    theta: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "K", _finite(self.K, "K"))
        object.__setattr__(self, "tau", _finite(self.tau, "tau"))
        object.__setattr__(self, "theta", _finite(self.theta, "theta"))


@dataclass(frozen=True, slots=True)
class SOPDT:
    K: float
    tau_1: float
    tau_2: float
    theta: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "K", _finite(self.K, "K"))
        object.__setattr__(self, "tau_1", _finite(self.tau_1, "tau_1"))
        object.__setattr__(self, "tau_2", _finite(self.tau_2, "tau_2"))
        object.__setattr__(self, "theta", _finite(self.theta, "theta"))


class ModelConfirmation:
    """Stateful confirmation of one complete immutable candidate identity."""

    __slots__ = ("_candidate_key", "_observed")

    def __init__(self) -> None:
        self._candidate_key: str | None = None
        self._observed = 0

    @property
    def observed(self) -> int:
        return self._observed

    def observe(self, candidate_key: str) -> int:
        _digest(candidate_key, "candidate_key")
        if candidate_key != self._candidate_key:
            self._candidate_key = candidate_key
            self._observed = 1
        else:
            self._observed = min(self._observed + 1, CONFIRMATION_WINDOW)
        return self._observed

    def snapshot(self) -> tuple[str | None, int]:
        """Return the complete immutable confirmation state for durability."""
        return self._candidate_key, self._observed

    def restore(self, candidate_key: str | None, observed: int) -> None:
        """Restore only a structurally valid, internally consistent state."""
        if isinstance(observed, bool) or not isinstance(observed, int):
            raise TypeError("confirmation observed must be an integer")
        if not 0 <= observed <= CONFIRMATION_WINDOW:
            raise ValueError("confirmation observed is outside its fixed window")
        if candidate_key is None:
            if observed != 0:
                raise ValueError("empty confirmation identity requires zero observations")
        else:
            _digest(candidate_key, "candidate_key")
            if observed == 0:
                raise ValueError("identified confirmation state requires observations")
        self._candidate_key = candidate_key
        self._observed = observed

    def reset(self) -> None:
        self._candidate_key = None
        self._observed = 0


def encode_model_confirmation(confirmation: ModelConfirmation) -> dict[str, object]:
    """Encode confirmation separately from an authorized model checkpoint."""
    if not isinstance(confirmation, ModelConfirmation):
        raise TypeError("confirmation must be a ModelConfirmation")
    candidate_key, observed = confirmation.snapshot()
    return {
        "schema": MODEL_CONFIRMATION_SCHEMA,
        "candidate_key": candidate_key,
        "observed": observed,
    }


def decode_model_confirmation(value: object) -> ModelConfirmation:
    """Decode confirmation state without authorizing or incrementing it."""
    if not isinstance(value, Mapping):
        raise TypeError("confirmation checkpoint must be a mapping")
    if set(value) != {"schema", "candidate_key", "observed"}:
        raise ValueError("confirmation checkpoint fields are invalid")
    if value["schema"] != MODEL_CONFIRMATION_SCHEMA:
        raise ValueError("confirmation checkpoint schema is unsupported")
    confirmation = ModelConfirmation()
    confirmation.restore(value["candidate_key"], value["observed"])
    return confirmation


type ModelParameters = IPDT | FOPDT | SOPDT


def _string_blockers(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{name} must contain nonempty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} must be unique")
    return values


def _loss(value: object, name: str, *, allow_infinite: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be numeric")
    converted = float(value)
    if math.isnan(converted) or converted < 0.0:
        raise ValueError(f"{name} must be nonnegative")
    if not allow_infinite and not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _inferred_physical_blockers(
    parameters: ModelParameters | None,
) -> tuple[str, ...]:
    if parameters is None:
        return ()
    blockers: list[str] = []
    if isinstance(parameters, IPDT):
        if not GAIN_RATE_MIN <= parameters.K_i <= GAIN_RATE_MAX:
            blockers.append("K_i-out-of-bounds")
        if parameters.c0 > 0.0:
            blockers.append("c0-positive")
    else:
        if not GAIN_MIN <= parameters.K <= GAIN_MAX:
            blockers.append("K-out-of-bounds")
        if isinstance(parameters, FOPDT):
            if not TAU_MIN <= parameters.tau <= TAU_MAX:
                blockers.append("tau-out-of-bounds")
        else:
            if not TAU_MIN <= parameters.tau_1 <= TAU_MAX:
                blockers.append("tau_1-out-of-bounds")
            if not TAU_MIN <= parameters.tau_2 <= TAU_MAX:
                blockers.append("tau_2-out-of-bounds")
            if parameters.tau_1 > parameters.tau_2:
                blockers.append("time-constants-not-canonical")
    return tuple(blockers)


@dataclass(frozen=True, slots=True)
class ModelFit:
    form: ModelForm
    parameters: ModelParameters | None
    delay_profile: DelayProfile
    one_step_loss: float
    horizon_losses: tuple[tuple[int, float], ...]
    fold_losses: tuple[float, ...]
    episode_ids: tuple[str, ...]
    common_row_ids: tuple[tuple[tuple[float, float], ...], ...]
    physical_blockers: tuple[str, ...] = ()
    uncertainty_blockers: tuple[str, ...] = ()
    stability_blockers: tuple[str, ...] = ()
    validation_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.form, ModelForm):
            raise TypeError("form must be a ModelForm")
        expected_type = {
            ModelForm.IPDT: IPDT,
            ModelForm.FOPDT: FOPDT,
            ModelForm.SOPDT: SOPDT,
        }[self.form]
        if self.parameters is not None and not isinstance(self.parameters, expected_type):
            raise TypeError("parameters must match model form")
        if not isinstance(self.delay_profile, DelayProfile):
            raise TypeError("delay_profile must be a DelayProfile")
        if self.delay_profile.model_form != self.form.value:
            raise ValueError("delay profile form must match model form")
        if self.parameters is not None:
            if self.delay_profile.basin is None:
                raise ValueError("parameterized fits require an available delay basin")
            if self.parameters.theta != self.delay_profile.basin.representative_s:
                raise ValueError("parameter delay must equal the gated basin representative")
        object.__setattr__(
            self,
            "one_step_loss",
            _loss(self.one_step_loss, "one_step_loss", allow_infinite=True),
        )
        if not isinstance(self.horizon_losses, tuple):
            raise TypeError("horizon_losses must be a tuple")
        if tuple(horizon for horizon, _ in self.horizon_losses) != HORIZONS_S:
            raise ValueError("horizon losses must use the canonical horizon order")
        object.__setattr__(
            self,
            "horizon_losses",
            tuple(
                (horizon, _loss(loss, f"{horizon}-second loss", allow_infinite=True))
                for horizon, loss in self.horizon_losses
            ),
        )
        if not isinstance(self.fold_losses, tuple):
            raise TypeError("fold_losses must be a tuple")
        object.__setattr__(
            self,
            "fold_losses",
            tuple(_loss(value, "fold loss", allow_infinite=True) for value in self.fold_losses),
        )
        if not isinstance(self.episode_ids, tuple) or not self.episode_ids:
            raise ValueError("episode_ids must be a nonempty tuple")
        if any(not isinstance(value, str) or not value for value in self.episode_ids):
            raise ValueError("episode identities must be nonempty strings")
        if len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError("episode identities must be unique")
        expected_fold_count = max(len(self.episode_ids) - 1, 0)
        if len(self.fold_losses) != expected_fold_count:
            raise ValueError("fold_losses count must equal the rolling-origin held-out fold count")
        if not isinstance(self.common_row_ids, tuple) or len(self.common_row_ids) != len(self.episode_ids):
            raise ValueError("common_row_ids must contain one audit tuple per episode")
        for rows in self.common_row_ids:
            if not isinstance(rows, tuple):
                raise TypeError("common row identities must be tuples")
            previous_end = -math.inf
            for row_id in rows:
                if not isinstance(row_id, tuple) or len(row_id) != 2:
                    raise TypeError("common row identity must be a (start_s, end_s) tuple")
                start_s = _finite(row_id[0], "common row start")
                end_s = _finite(row_id[1], "common row end")
                if end_s <= start_s or start_s < previous_end:
                    raise ValueError("common row identities must be ordered and nonoverlapping")
                previous_end = end_s
        for field_name in (
            "physical_blockers",
            "uncertainty_blockers",
            "stability_blockers",
            "validation_blockers",
        ):
            object.__setattr__(
                self,
                field_name,
                _string_blockers(getattr(self, field_name), field_name),
            )
        if len(self.fold_losses) < MIN_INDEPENDENT_VALIDATION_FOLDS:
            object.__setattr__(
                self,
                "uncertainty_blockers",
                tuple(
                    dict.fromkeys(
                        (
                            *self.uncertainty_blockers,
                            "insufficient-independent-validation-folds",
                        )
                    )
                ),
            )
        inferred_physical = _inferred_physical_blockers(self.parameters)
        object.__setattr__(
            self,
            "physical_blockers",
            tuple(dict.fromkeys((*self.physical_blockers, *inferred_physical))),
        )
        if self.parameters is None and not self.all_blockers:
            raise ValueError("a parameterless fit must retain its rejection blocker")

    @property
    def basin_blockers(self) -> tuple[DelayBlocker, ...]:
        return self.delay_profile.blockers

    @property
    def all_blockers(self) -> tuple[str | DelayBlocker, ...]:
        return tuple(
            dict.fromkeys(
                (
                    *self.physical_blockers,
                    *self.uncertainty_blockers,
                    *self.basin_blockers,
                    *self.stability_blockers,
                    *self.validation_blockers,
                )
            )
        )

    @property
    def eligible(self) -> bool:
        return self.parameters is not None and not self.all_blockers

    @property
    def mean_validation_loss(self) -> float:
        if not self.fold_losses:
            return math.inf
        return float(np.mean(np.asarray(self.fold_losses, dtype=float)))

    @property
    def standard_error(self) -> float:
        losses = np.asarray(self.fold_losses, dtype=float)
        if losses.size < 2 or not np.isfinite(losses).all():
            return math.inf
        return float(np.std(losses, ddof=1) / math.sqrt(losses.size))


@dataclass(frozen=True, slots=True)
class SelectedPidSpModel:
    schema_version: str
    form: ModelForm
    parameters: ModelParameters
    delay_basin: DelayBasin
    one_step_loss: float
    horizon_losses: tuple[tuple[int, float], ...]
    fold_losses: tuple[float, ...]
    standard_error: float
    episode_ids: tuple[str, ...]
    common_row_digest: str
    fit_corpus_digest: str
    configuration_digest: str
    comparison_threshold: float
    selection_margin: float
    confirmation_observed: int
    confirmation_required: int
    model_digest: str
    authorized: bool

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_SELECTION_SCHEMA:
            raise ValueError("unsupported model-selection schema")
        expected_type = {
            ModelForm.IPDT: IPDT,
            ModelForm.FOPDT: FOPDT,
            ModelForm.SOPDT: SOPDT,
        }[self.form]
        if not isinstance(self.parameters, expected_type):
            raise TypeError("selected parameters must match selected form")
        physical_blockers = _inferred_physical_blockers(self.parameters)
        if physical_blockers:
            raise ValueError("selected parameters fail physical bounds: " + ", ".join(physical_blockers))
        if self.parameters.theta != self.delay_basin.representative_s:
            raise ValueError("selected parameter delay must equal the basin representative")
        if self.delay_basin.blockers:
            raise ValueError("selected delay basin must be blocker-free")
        if self.delay_basin.episode_count != len(self.episode_ids):
            raise ValueError("selected delay basin must describe the selected episodes")
        object.__setattr__(
            self,
            "one_step_loss",
            _loss(self.one_step_loss, "one_step_loss"),
        )
        if tuple(horizon for horizon, _ in self.horizon_losses) != HORIZONS_S:
            raise ValueError("horizon losses must use the canonical horizon order")
        object.__setattr__(
            self,
            "horizon_losses",
            tuple((horizon, _loss(loss, f"{horizon}-second loss")) for horizon, loss in self.horizon_losses),
        )
        object.__setattr__(
            self,
            "fold_losses",
            tuple(_loss(loss, "fold loss") for loss in self.fold_losses),
        )
        if len(self.fold_losses) != len(self.episode_ids) - 1:
            raise ValueError("fold losses must match rolling-origin episode support")
        if len(self.fold_losses) < MIN_INDEPENDENT_VALIDATION_FOLDS:
            raise ValueError("selected model requires independent validation folds")
        if len(set(self.episode_ids)) != len(self.episode_ids) or any(
            not isinstance(episode_id, str) or not episode_id for episode_id in self.episode_ids
        ):
            raise ValueError("episode identities must be unique nonempty strings")
        expected_standard_error = float(
            np.std(np.asarray(self.fold_losses, dtype=float), ddof=1) / math.sqrt(len(self.fold_losses))
        )
        if _loss(self.standard_error, "standard_error") != expected_standard_error:
            raise ValueError("standard_error must be derived from fold losses")
        mean_loss = self.mean_validation_loss
        threshold = _loss(self.comparison_threshold, "comparison_threshold")
        margin = _loss(self.selection_margin, "selection_margin")
        if margin != threshold - mean_loss:
            raise ValueError("selection_margin must match threshold and fold losses")
        if self.confirmation_required != CONFIRMATION_WINDOW:
            raise ValueError("selected model must use the fixed confirmation window")
        if not 0 <= self.confirmation_observed <= self.confirmation_required:
            raise ValueError("confirmation progress must lie within its required window")
        if self.authorized != (self.confirmation_observed >= self.confirmation_required):
            raise ValueError("authorization requires the complete confirmation window")
        for value, name in (
            (self.common_row_digest, "common_row_digest"),
            (self.fit_corpus_digest, "fit_corpus_digest"),
            (self.configuration_digest, "configuration_digest"),
            (self.model_digest, "model_digest"),
        ):
            _digest(value, name)
        if self.model_digest != _selected_model_digest(self):
            raise ValueError("model_digest does not match the complete selected payload")

    @property
    def mean_validation_loss(self) -> float:
        return float(np.mean(np.asarray(self.fold_losses, dtype=float)))


@dataclass(frozen=True, slots=True)
class ModelComparison:
    schema_version: str
    fits: tuple[ModelFit, ...]
    best_form: ModelForm | None
    best_mean_validation_loss: float | None
    best_standard_error: float | None
    comparison_threshold: float | None
    selection_margin: float | None
    selected: SelectedPidSpModel | None
    fit_corpus_digest: str
    configuration_digest: str
    authorized: bool

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_SELECTION_SCHEMA:
            raise ValueError("unsupported model-selection schema")
        expected = tuple(sorted(self.fits, key=lambda fit: _COMPLEXITY[fit.form]))
        if self.fits != expected or len({fit.form for fit in self.fits}) != len(self.fits):
            raise ValueError("comparison fits must be unique and canonically ordered")
        _digest(self.fit_corpus_digest, "fit_corpus_digest")
        _digest(self.configuration_digest, "configuration_digest")
        if self.authorized != (self.selected is not None and self.selected.authorized):
            raise ValueError("authorization requires a selected typed model")
        if self.selected is None:
            if any(
                value is not None
                for value in (
                    self.best_form,
                    self.best_mean_validation_loss,
                    self.best_standard_error,
                    self.comparison_threshold,
                    self.selection_margin,
                )
            ):
                raise ValueError("form-less comparison cannot report selection authority")
        elif self.selected.form not in {fit.form for fit in self.fits}:
            raise ValueError("selected form must be present in comparison fits")

    def fit_for(self, form: ModelForm) -> ModelFit:
        for fit in self.fits:
            if fit.form is form:
                return fit
        raise KeyError(form)


def _digest(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _json_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pid_sp_common_row_digest(
    common_row_ids: tuple[tuple[tuple[float, float], ...], ...],
) -> str:
    """Canonical identity of the full common support retained by ModelFit."""
    return _json_digest(common_row_ids)


def _selected_payload(
    *,
    form: ModelForm,
    parameters: ModelParameters,
    delay_basin: DelayBasin,
    one_step_loss: float,
    horizon_losses: tuple[tuple[int, float], ...],
    fold_losses: tuple[float, ...],
    standard_error: float,
    episode_ids: tuple[str, ...],
    common_row_digest: str,
    fit_corpus_digest: str,
    configuration_digest: str,
    comparison_threshold: float,
    selection_margin: float,
    confirmation_observed: int,
    confirmation_required: int,
    authorized: bool,
) -> dict[str, object]:
    return {
        "schema_version": MODEL_SELECTION_SCHEMA,
        "form": form.value,
        "parameters": asdict(parameters),
        "delay_basin": {
            **asdict(delay_basin),
            "blockers": [blocker.value for blocker in delay_basin.blockers],
        },
        "one_step_loss": one_step_loss,
        "horizon_losses": [[horizon, loss] for horizon, loss in horizon_losses],
        "fold_losses": list(fold_losses),
        "standard_error": standard_error,
        "episode_ids": list(episode_ids),
        "common_row_digest": common_row_digest,
        "fit_corpus_digest": fit_corpus_digest,
        "configuration_digest": configuration_digest,
        "comparison_threshold": comparison_threshold,
        "selection_margin": selection_margin,
        "confirmation_observed": confirmation_observed,
        "confirmation_required": confirmation_required,
        "authorized": authorized,
    }


def _selected_digest(
    fit: ModelFit,
    fit_corpus_digest: str,
    configuration_digest: str,
    threshold: float,
    margin: float,
    confirmation_observed: int,
    authorized: bool,
) -> str:
    assert fit.parameters is not None
    assert fit.delay_profile.basin is not None
    return _json_digest(
        _selected_payload(
            form=fit.form,
            parameters=fit.parameters,
            delay_basin=fit.delay_profile.basin,
            one_step_loss=fit.one_step_loss,
            horizon_losses=fit.horizon_losses,
            fold_losses=fit.fold_losses,
            standard_error=fit.standard_error,
            episode_ids=fit.episode_ids,
            common_row_digest=pid_sp_common_row_digest(fit.common_row_ids),
            fit_corpus_digest=fit_corpus_digest,
            configuration_digest=configuration_digest,
            comparison_threshold=threshold,
            selection_margin=margin,
            confirmation_observed=confirmation_observed,
            confirmation_required=CONFIRMATION_WINDOW,
            authorized=authorized,
        )
    )


def _selected_model_digest(model: SelectedPidSpModel) -> str:
    return _json_digest(
        _selected_payload(
            form=model.form,
            parameters=model.parameters,
            delay_basin=model.delay_basin,
            one_step_loss=model.one_step_loss,
            horizon_losses=model.horizon_losses,
            fold_losses=model.fold_losses,
            standard_error=model.standard_error,
            episode_ids=model.episode_ids,
            common_row_digest=model.common_row_digest,
            fit_corpus_digest=model.fit_corpus_digest,
            configuration_digest=model.configuration_digest,
            comparison_threshold=model.comparison_threshold,
            selection_margin=model.selection_margin,
            confirmation_observed=model.confirmation_observed,
            confirmation_required=model.confirmation_required,
            authorized=model.authorized,
        )
    )


@dataclass(frozen=True, slots=True)
class PidSpCheckpoint:
    revision: int
    provenance: str
    selected: SelectedPidSpModel
    installation_identity_digest: str | None

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("checkpoint revision must be a nonnegative integer")
        if not isinstance(self.provenance, str) or not self.provenance:
            raise ValueError("checkpoint provenance must be a nonempty string")
        if self.installation_identity_digest is not None:
            _digest(self.installation_identity_digest, "installation_identity_digest")
        if not self.selected.authorized or (
            self.selected.confirmation_observed,
            self.selected.confirmation_required,
        ) != (CONFIRMATION_WINDOW, CONFIRMATION_WINDOW):
            raise ValueError("checkpoint selected model must be fully confirmed")


def encode_pid_sp_checkpoint(
    selected: SelectedPidSpModel,
    *,
    revision: int,
    provenance: str,
    installation_identity_digest: str | None,
) -> dict[str, object]:
    checkpoint = PidSpCheckpoint(revision, provenance, selected, installation_identity_digest)
    payload = _selected_payload(
        form=checkpoint.selected.form,
        parameters=checkpoint.selected.parameters,
        delay_basin=checkpoint.selected.delay_basin,
        one_step_loss=checkpoint.selected.one_step_loss,
        horizon_losses=checkpoint.selected.horizon_losses,
        fold_losses=checkpoint.selected.fold_losses,
        standard_error=checkpoint.selected.standard_error,
        episode_ids=checkpoint.selected.episode_ids,
        common_row_digest=checkpoint.selected.common_row_digest,
        fit_corpus_digest=checkpoint.selected.fit_corpus_digest,
        configuration_digest=checkpoint.selected.configuration_digest,
        comparison_threshold=checkpoint.selected.comparison_threshold,
        selection_margin=checkpoint.selected.selection_margin,
        confirmation_observed=checkpoint.selected.confirmation_observed,
        confirmation_required=checkpoint.selected.confirmation_required,
        authorized=checkpoint.selected.authorized,
    )
    payload["model_digest"] = checkpoint.selected.model_digest
    return {
        "schema_version": PID_SP_CHECKPOINT_SCHEMA,
        "revision": checkpoint.revision,
        "provenance": checkpoint.provenance,
        "installation_identity_digest": checkpoint.installation_identity_digest,
        "selected": payload,
    }


def _checkpoint_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{name} must be an object")
    return value


def _checkpoint_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields are malformed")


def _checkpoint_int(value: object, name: str, *, nonnegative: bool = False) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _checkpoint_float(value: object, name: str) -> float:
    if type(value) is not float:
        raise TypeError(f"{name} must be a JSON float")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _checkpoint_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a nonempty string")
    return value


def decode_pid_sp_checkpoint(value: object) -> PidSpCheckpoint:
    checkpoint = _checkpoint_mapping(value, "checkpoint")
    schema_version = _checkpoint_int(
        checkpoint.get("schema_version"),
        "checkpoint schema",
    )
    if schema_version == PID_SP_CHECKPOINT_SCHEMA:
        _checkpoint_exact_keys(
            checkpoint,
            {
                "schema_version",
                "revision",
                "provenance",
                "installation_identity_digest",
                "selected",
            },
            "checkpoint",
        )
        installation_digest = checkpoint["installation_identity_digest"]
        if installation_digest is not None:
            installation_digest = _digest(
                installation_digest,
                "installation_identity_digest",
            )
    elif schema_version == _PID_SP_LEGACY_CHECKPOINT_SCHEMA:
        _checkpoint_exact_keys(
            checkpoint,
            {"schema_version", "revision", "provenance", "selected"},
            "checkpoint",
        )
        installation_digest = None
    else:
        raise ValueError("unsupported PID-SP checkpoint schema")
    revision = _checkpoint_int(
        checkpoint["revision"],
        "checkpoint revision",
        nonnegative=True,
    )
    provenance = _checkpoint_string(checkpoint["provenance"], "checkpoint provenance")

    selected_value = _checkpoint_mapping(checkpoint["selected"], "selected model")
    _checkpoint_exact_keys(
        selected_value,
        {
            "schema_version",
            "form",
            "parameters",
            "delay_basin",
            "one_step_loss",
            "horizon_losses",
            "fold_losses",
            "standard_error",
            "episode_ids",
            "common_row_digest",
            "fit_corpus_digest",
            "configuration_digest",
            "comparison_threshold",
            "selection_margin",
            "confirmation_observed",
            "confirmation_required",
            "authorized",
            "model_digest",
        },
        "selected model",
    )
    selected_schema = _checkpoint_string(
        selected_value["schema_version"],
        "selected-model schema",
    )
    if selected_schema != MODEL_SELECTION_SCHEMA:
        raise ValueError("unsupported selected-model schema")
    form_value = _checkpoint_string(selected_value["form"], "selected form")
    form = ModelForm(form_value)

    parameter_value = _checkpoint_mapping(
        selected_value["parameters"],
        "selected parameters",
    )
    parameter_type, parameter_fields = {
        ModelForm.IPDT: (IPDT, ("K_i", "c0", "theta")),
        ModelForm.FOPDT: (FOPDT, ("K", "tau", "theta")),
        ModelForm.SOPDT: (SOPDT, ("K", "tau_1", "tau_2", "theta")),
    }[form]
    _checkpoint_exact_keys(
        parameter_value,
        set(parameter_fields),
        "selected parameters",
    )
    parameters = parameter_type(
        **{
            name: _checkpoint_float(
                parameter_value[name],
                f"selected parameter {name}",
            )
            for name in parameter_fields
        }
    )

    basin_value = _checkpoint_mapping(selected_value["delay_basin"], "delay basin")
    basin_fields = {
        "lower_s",
        "upper_s",
        "representative_s",
        "confidence_lower_s",
        "confidence_upper_s",
        "confidence_method",
        "confidence_resamples",
        "episode_count",
        "interior",
        "blockers",
    }
    _checkpoint_exact_keys(basin_value, basin_fields, "delay basin")
    blockers_value = basin_value["blockers"]
    if not isinstance(blockers_value, list):
        raise TypeError("delay basin blockers must be a JSON array")
    blockers = tuple(DelayBlocker(_checkpoint_string(blocker, "delay basin blocker")) for blocker in blockers_value)
    interior = basin_value["interior"]
    if type(interior) is not bool:
        raise TypeError("delay basin interior must be a boolean")
    delay_basin = DelayBasin(
        lower_s=_checkpoint_int(
            basin_value["lower_s"],
            "delay basin lower_s",
            nonnegative=True,
        ),
        upper_s=_checkpoint_int(
            basin_value["upper_s"],
            "delay basin upper_s",
            nonnegative=True,
        ),
        representative_s=_checkpoint_int(
            basin_value["representative_s"],
            "delay basin representative_s",
            nonnegative=True,
        ),
        confidence_lower_s=_checkpoint_int(
            basin_value["confidence_lower_s"],
            "delay basin confidence_lower_s",
            nonnegative=True,
        ),
        confidence_upper_s=_checkpoint_int(
            basin_value["confidence_upper_s"],
            "delay basin confidence_upper_s",
            nonnegative=True,
        ),
        confidence_method=_checkpoint_string(
            basin_value["confidence_method"],
            "delay basin confidence_method",
        ),
        confidence_resamples=_checkpoint_int(
            basin_value["confidence_resamples"],
            "delay basin confidence_resamples",
            nonnegative=True,
        ),
        episode_count=_checkpoint_int(
            basin_value["episode_count"],
            "delay basin episode_count",
            nonnegative=True,
        ),
        interior=interior,
        blockers=blockers,
    )

    horizon_value = selected_value["horizon_losses"]
    fold_value = selected_value["fold_losses"]
    episode_value = selected_value["episode_ids"]
    if not isinstance(horizon_value, list):
        raise TypeError("horizon losses must be a JSON array")
    if not isinstance(fold_value, list):
        raise TypeError("fold losses must be a JSON array")
    if not isinstance(episode_value, list):
        raise TypeError("episode identities must be a JSON array")
    horizon_losses_list: list[tuple[int, float]] = []
    for item in horizon_value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("horizon losses are malformed")
        horizon_losses_list.append(
            (
                _checkpoint_int(item[0], "horizon"),
                _checkpoint_float(item[1], "horizon loss"),
            )
        )
    horizon_losses = tuple(horizon_losses_list)
    fold_losses = tuple(_checkpoint_float(loss, "fold loss") for loss in fold_value)
    episode_ids = tuple(_checkpoint_string(episode_id, "episode identity") for episode_id in episode_value)
    authorized = selected_value["authorized"]
    if type(authorized) is not bool:
        raise TypeError("selected authorization must be a boolean")

    selected = SelectedPidSpModel(
        schema_version=selected_schema,
        form=form,
        parameters=parameters,
        delay_basin=delay_basin,
        one_step_loss=_checkpoint_float(
            selected_value["one_step_loss"],
            "one-step loss",
        ),
        horizon_losses=horizon_losses,
        fold_losses=fold_losses,
        standard_error=_checkpoint_float(
            selected_value["standard_error"],
            "standard error",
        ),
        episode_ids=episode_ids,
        common_row_digest=_checkpoint_string(
            selected_value["common_row_digest"],
            "common-row digest",
        ),
        fit_corpus_digest=_checkpoint_string(
            selected_value["fit_corpus_digest"],
            "fit-corpus digest",
        ),
        configuration_digest=_checkpoint_string(
            selected_value["configuration_digest"],
            "configuration digest",
        ),
        comparison_threshold=_checkpoint_float(
            selected_value["comparison_threshold"],
            "comparison threshold",
        ),
        selection_margin=_checkpoint_float(
            selected_value["selection_margin"],
            "selection margin",
        ),
        confirmation_observed=_checkpoint_int(
            selected_value["confirmation_observed"],
            "confirmation observed",
            nonnegative=True,
        ),
        confirmation_required=_checkpoint_int(
            selected_value["confirmation_required"],
            "confirmation required",
            nonnegative=True,
        ),
        model_digest=_checkpoint_string(
            selected_value["model_digest"],
            "model digest",
        ),
        authorized=authorized,
    )
    return PidSpCheckpoint(
        revision=revision,
        provenance=provenance,
        selected=selected,
        installation_identity_digest=installation_digest,
    )


def _decode_pending_pid_sp_checkpoint(
    value: object,
) -> tuple[
    PidSpCheckpoint | None,
    tuple[str, str, str],
    ModelConfirmation,
    str | None,
]:
    checkpoint = _checkpoint_mapping(value, "PID-SP learning checkpoint")
    schema = checkpoint.get("schema")
    if schema == PID_SP_LEARNING_CHECKPOINT_SCHEMA:
        _checkpoint_exact_keys(
            checkpoint,
            {
                "schema",
                "revision",
                "confirmation",
                "identity",
                "incumbent",
                "installation_identity_digest",
            },
            "PID-SP learning checkpoint",
        )
        installation_digest = checkpoint["installation_identity_digest"]
        if installation_digest is not None:
            installation_digest = _digest(
                installation_digest,
                "installation_identity_digest",
            )
    elif schema == PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA:
        _checkpoint_exact_keys(
            checkpoint,
            {"schema", "revision", "confirmation", "identity", "incumbent"},
            "PID-SP learning checkpoint",
        )
        installation_digest = None
    else:
        raise ValueError("unsupported PID-SP learning checkpoint schema")
    _checkpoint_int(
        checkpoint["revision"],
        "PID-SP learning revision",
        nonnegative=True,
    )
    identity_value = _checkpoint_mapping(
        checkpoint["identity"],
        "PID-SP learning identity",
    )
    _checkpoint_exact_keys(
        identity_value,
        {"fit_corpus_digest", "configuration_digest", "incumbent_digest"},
        "PID-SP learning identity",
    )
    identity = tuple(
        _digest(identity_value[name], f"PID-SP learning {name}")
        for name in (
            "fit_corpus_digest",
            "configuration_digest",
            "incumbent_digest",
        )
    )
    confirmation = decode_model_confirmation(checkpoint["confirmation"])
    incumbent_value = checkpoint["incumbent"]
    incumbent = None if incumbent_value is None else decode_pid_sp_checkpoint(incumbent_value)
    expected_incumbent = (
        canonical_pid_sp_observation_model_digest(None) if incumbent is None else incumbent.selected.model_digest
    )
    if identity[2] != expected_incumbent:
        raise ValueError("PID-SP learning incumbent identity is stale")
    return incumbent, identity, confirmation, installation_digest


def _validate_prepared_pid_sp_proposal(
    proposed_value: object,
    terminal: ModelEvidenceRecord,
) -> None:
    proposed = _checkpoint_mapping(
        proposed_value,
        "PID-SP prepared proposal",
    )
    _checkpoint_exact_keys(
        proposed,
        {"checkpoint", "lineage"},
        "PID-SP prepared proposal",
    )
    lineage = _checkpoint_mapping(
        proposed["lineage"],
        "PID-SP prepared lineage",
    )
    payload = terminal.payload
    if (
        not isinstance(payload, PidSpFitDecisionEvidence)
        or not payload.request_bound
        or payload.candidate_digest is None
        or payload.confirmation_candidate_digest is None
        or payload.fit_corpus_digest is None
        or payload.parent_incumbent_generation is None
        or payload.candidate_generation is None
    ):
        raise ValueError("PID-SP prepared terminal lineage is incomplete")
    expected_lineage = {
        "request_id": payload.request_id,
        "candidate_digest": payload.candidate_digest,
        "confirmation_candidate_digest": payload.confirmation_candidate_digest,
        "fit_corpus_digest": payload.fit_corpus_digest,
        "configuration_digest": payload.configuration_digest,
        "parent_incumbent_digest": payload.parent_incumbent_digest,
        "parent_incumbent_generation": payload.parent_incumbent_generation,
        "candidate_generation": payload.candidate_generation,
    }
    if dict(lineage) != expected_lineage or terminal.model_digest != payload.candidate_digest:
        raise ValueError("PID-SP prepared lineage is inconsistent")
    proposed_checkpoint = proposed["checkpoint"]
    if isinstance(proposed_checkpoint, Mapping) and proposed_checkpoint.get("schema") in {
        PID_SP_LEARNING_CHECKPOINT_SCHEMA,
        PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA,
    }:
        (
            _incumbent,
            identity,
            confirmation,
            _installation_digest,
        ) = _decode_pending_pid_sp_checkpoint(proposed_checkpoint)
        candidate_key, observed = confirmation.snapshot()
        if (
            payload.outcome != "rejected"
            or identity
            != (
                payload.fit_corpus_digest,
                payload.configuration_digest,
                payload.parent_incumbent_digest,
            )
            or candidate_key != payload.confirmation_candidate_digest
            or observed != payload.confirmation_observed
        ):
            raise ValueError("PID-SP prepared pending proposal is inconsistent")
        return
    decoded = decode_pid_sp_checkpoint(proposed_checkpoint)
    selected = decoded.selected
    if (
        payload.outcome != "accepted-next-cook"
        or selected.model_digest != payload.candidate_digest
        or selected.fit_corpus_digest != payload.fit_corpus_digest
        or selected.configuration_digest != payload.configuration_digest
        or selected.form.value != payload.selected_form
        or selected.confirmation_observed != payload.confirmation_observed
    ):
        raise ValueError("PID-SP prepared accepted proposal is inconsistent")


def project_pid_sp_persisted_checkpoint(
    value: object,
) -> PidSpCheckpoint | None:
    """Validate any controller-owned PID-SP state and return active authority."""

    checkpoint = _checkpoint_mapping(value, "PID-SP persisted checkpoint")
    schema = checkpoint.get("schema")
    if schema in {
        PID_SP_LEARNING_CHECKPOINT_SCHEMA,
        PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA,
    }:
        (
            incumbent,
            _identity,
            _confirmation,
            _installation_digest,
        ) = _decode_pending_pid_sp_checkpoint(checkpoint)
        return incumbent
    if schema == PID_SP_LEARNING_PREPARE_SCHEMA:
        _checkpoint_exact_keys(
            checkpoint,
            {
                "schema",
                "revision",
                "terminal_evidence_json",
                "proposed",
                "incumbent",
            },
            "PID-SP prepared checkpoint",
        )
        _checkpoint_int(
            checkpoint["revision"],
            "PID-SP prepared revision",
            nonnegative=True,
        )
        terminal_json = checkpoint["terminal_evidence_json"]
        if not isinstance(terminal_json, str):
            raise TypeError("PID-SP prepared terminal evidence must be JSON text")
        terminal = ModelEvidenceRecord.model_validate_json(terminal_json)
        if terminal.model_dump_json() != terminal_json or terminal.kind is not EvidenceKind.PID_SP_FIT_DECISION:
            raise ValueError("PID-SP prepared terminal evidence is inconsistent")
        _validate_prepared_pid_sp_proposal(checkpoint["proposed"], terminal)
        incumbent_value = checkpoint["incumbent"]
        if isinstance(incumbent_value, Mapping) and incumbent_value.get("schema") in {
            PID_SP_LEARNING_CHECKPOINT_SCHEMA,
            PID_SP_LEGACY_LEARNING_CHECKPOINT_SCHEMA,
        }:
            (
                incumbent,
                _identity,
                _confirmation,
                _installation_digest,
            ) = _decode_pending_pid_sp_checkpoint(incumbent_value)
        else:
            incumbent = None if incumbent_value is None else decode_pid_sp_checkpoint(incumbent_value)
        payload = terminal.payload
        assert isinstance(payload, PidSpFitDecisionEvidence)
        expected_parent = (
            canonical_pid_sp_observation_model_digest(None) if incumbent is None else incumbent.selected.model_digest
        )
        if payload.parent_incumbent_digest != expected_parent:
            raise ValueError("PID-SP prepared incumbent is inconsistent")
        return incumbent
    return decode_pid_sp_checkpoint(checkpoint)


def _confirmation_key(
    fit: ModelFit,
    fit_corpus_digest: str,
    configuration_digest: str,
) -> str:
    assert fit.parameters is not None
    assert fit.delay_profile.basin is not None
    return _json_digest(
        {
            "form": fit.form.value,
            "parameters": asdict(fit.parameters),
            "delay_basin": asdict(fit.delay_profile.basin),
            "fit_corpus_digest": fit_corpus_digest,
            "configuration_digest": configuration_digest,
        }
    )


def compare_model_fits(
    fits: tuple[ModelFit, ...],
    *,
    fit_corpus_digest: str,
    configuration_digest: str,
    confirmation: ModelConfirmation | None = None,
) -> ModelComparison:
    if not isinstance(fits, tuple) or not fits:
        raise ValueError("fits must be a nonempty tuple")
    _digest(fit_corpus_digest, "fit_corpus_digest")
    _digest(configuration_digest, "configuration_digest")
    canonical = tuple(sorted(fits, key=lambda fit: _COMPLEXITY[fit.form]))
    if len({fit.form for fit in canonical}) != len(canonical):
        raise ValueError("fits must contain each form at most once")
    evidence_identity = (
        canonical[0].episode_ids,
        canonical[0].common_row_ids,
    )
    if any((fit.episode_ids, fit.common_row_ids) != evidence_identity for fit in canonical[1:]):
        raise ValueError("all compared forms must use identical common row support")
    eligible = tuple(fit for fit in canonical if fit.eligible and math.isfinite(fit.mean_validation_loss))
    if not eligible:
        if confirmation is not None:
            confirmation.reset()
        return ModelComparison(
            schema_version=MODEL_SELECTION_SCHEMA,
            fits=canonical,
            best_form=None,
            best_mean_validation_loss=None,
            best_standard_error=None,
            comparison_threshold=None,
            selection_margin=None,
            selected=None,
            fit_corpus_digest=fit_corpus_digest,
            configuration_digest=configuration_digest,
            authorized=False,
        )

    best = min(
        eligible,
        key=lambda fit: (fit.mean_validation_loss, _COMPLEXITY[fit.form]),
    )
    threshold = best.mean_validation_loss + best.standard_error
    chosen = next(fit for fit in eligible if fit.mean_validation_loss <= threshold)
    margin = threshold - chosen.mean_validation_loss
    assert chosen.parameters is not None
    confirmation_observed = (
        confirmation.observe(
            _confirmation_key(
                chosen,
                fit_corpus_digest,
                configuration_digest,
            )
        )
        if confirmation is not None
        else 0
    )
    authorized = confirmation_observed >= CONFIRMATION_WINDOW
    model_digest = _selected_digest(
        chosen,
        fit_corpus_digest,
        configuration_digest,
        threshold,
        margin,
        confirmation_observed,
        authorized,
    )
    assert chosen.delay_profile.basin is not None
    selected = SelectedPidSpModel(
        schema_version=MODEL_SELECTION_SCHEMA,
        form=chosen.form,
        parameters=chosen.parameters,
        delay_basin=chosen.delay_profile.basin,
        one_step_loss=chosen.one_step_loss,
        horizon_losses=chosen.horizon_losses,
        fold_losses=chosen.fold_losses,
        standard_error=chosen.standard_error,
        episode_ids=chosen.episode_ids,
        common_row_digest=pid_sp_common_row_digest(chosen.common_row_ids),
        fit_corpus_digest=fit_corpus_digest,
        configuration_digest=configuration_digest,
        comparison_threshold=threshold,
        selection_margin=margin,
        confirmation_observed=confirmation_observed,
        confirmation_required=CONFIRMATION_WINDOW,
        model_digest=model_digest,
        authorized=authorized,
    )
    return ModelComparison(
        schema_version=MODEL_SELECTION_SCHEMA,
        fits=canonical,
        best_form=best.form,
        best_mean_validation_loss=best.mean_validation_loss,
        best_standard_error=best.standard_error,
        comparison_threshold=threshold,
        selection_margin=margin,
        selected=selected,
        fit_corpus_digest=fit_corpus_digest,
        configuration_digest=configuration_digest,
        authorized=authorized,
    )


@dataclass(frozen=True, slots=True)
class _Row:
    start_s: float
    end_s: float
    temperature_0: float
    temperature_1: float
    previous_rate: float
    duties: tuple[tuple[float, float], ...]
    duty_segments: tuple[
        tuple[float, tuple[tuple[float, float, float], ...]],
        ...,
    ] = ()

    @property
    def dt(self) -> float:
        return self.end_s - self.start_s

    @property
    def rate(self) -> float:
        return (self.temperature_1 - self.temperature_0) / self.dt

    def duty(self, theta: float) -> float:
        for candidate, duty in self.duties:
            if candidate == theta:
                return duty
        raise KeyError(theta)

    def duty_parts(self, theta: float) -> tuple[tuple[float, float, float], ...]:
        for candidate, parts in self.duty_segments:
            if candidate == theta:
                return parts
        return ((0.0, self.dt, self.duty(theta)),)


class _DutyTimeline:
    def __init__(self, episode: ExcitationEpisode) -> None:
        self._starts: list[float] = []
        self._ends: list[float] = []
        self._duties: list[float] = []
        if episode.input_history is not None:
            for segment in episode.input_history.duty_segments:
                self._starts.append(segment.start_s)
                self._ends.append(segment.end_s)
                self._duties.append(segment.realized_duty)
        for interval in episode.intervals:
            assert interval.duty_segments is not None
            for segment in interval.duty_segments:
                self._starts.append(segment.start_s)
                self._ends.append(segment.end_s)
                self._duties.append(segment.realized_duty)

    def parts(
        self,
        start_s: float,
        end_s: float,
    ) -> tuple[tuple[float, float, float], ...] | None:
        if end_s <= start_s or not self._starts:
            return None
        first = bisect.bisect_right(self._starts, start_s) - 1
        last = bisect.bisect_left(self._ends, end_s)
        if (
            first < 0
            or last >= len(self._ends)
            or not self._starts[first] <= start_s < self._ends[first]
            or not self._starts[last] < end_s <= self._ends[last]
        ):
            return None
        if any(self._ends[index] != self._starts[index + 1] for index in range(first, last)):
            return None
        parts: list[tuple[float, float, float]] = []
        for index in range(first, last + 1):
            overlap_start = max(start_s, self._starts[index])
            overlap_end = min(end_s, self._ends[index])
            if overlap_end > overlap_start:
                parts.append(
                    (
                        overlap_start - start_s,
                        overlap_end - start_s,
                        self._duties[index],
                    )
                )
        return tuple(parts)


def _episode_rows(
    episode: ExcitationEpisode,
    delays: tuple[float, ...],
) -> tuple[_Row, ...]:
    timeline = _DutyTimeline(episode)
    rows: list[_Row] = []
    for previous, current in zip(episode.intervals, episode.intervals[1:]):
        if not previous.continuous or not current.continuous or previous.end_s != current.start_s:
            continue
        duties: list[tuple[float, float]] = []
        duty_segments: list[tuple[float, tuple[tuple[float, float, float], ...]]] = []
        for delay in delays:
            delayed_start = previous.end_s - delay
            delayed_end = current.end_s - delay
            parts = timeline.parts(delayed_start, delayed_end)
            if parts is None:
                break
            duty = sum((part_end - part_start) * value for part_start, part_end, value in parts) / (
                delayed_end - delayed_start
            )
            duties.append((delay, duty))
            duty_segments.append((delay, parts))
        else:
            rows.append(
                _Row(
                    start_s=previous.end_s,
                    end_s=current.end_s,
                    temperature_0=previous.temperature_f,
                    temperature_1=current.temperature_f,
                    previous_rate=math.nan,
                    duties=tuple(duties),
                    duty_segments=tuple(duty_segments),
                )
            )
    return tuple(
        replace(row, previous_rate=rows[index - 1].rate)
        for index, row in enumerate(rows[1:], start=1)
        if rows[index - 1].end_s == row.start_s
    )


@dataclass(frozen=True, slots=True)
class _RawFit:
    parameters: ModelParameters | None
    coefficients: tuple[float, ...] | None
    physical_blockers: tuple[str, ...]
    uncertainty_blockers: tuple[str, ...]
    stability_blockers: tuple[str, ...]


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    coefficients, _, rank, _ = np.linalg.lstsq(x, y, rcond=None)
    if rank < x.shape[1] or x.shape[0] <= x.shape[1]:
        return coefficients, None
    residual = y - x @ coefficients
    variance = float(residual @ residual) / (x.shape[0] - x.shape[1])
    try:
        covariance = variance * np.linalg.inv(x.T @ x)
    except np.linalg.LinAlgError:
        return coefficients, None
    if not np.isfinite(covariance).all():
        return coefficients, None
    return coefficients, covariance


def _relative_error(value: float, variance: float) -> float:
    if value == 0.0 or variance < 0.0 or not math.isfinite(variance):
        return math.inf
    return math.sqrt(variance) / abs(value)


def _validated_covariance(
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    coefficients = np.asarray(coefficients, dtype=float)
    covariance = np.asarray(covariance, dtype=float)
    if (
        coefficients.ndim != 1
        or covariance.shape != (coefficients.size, coefficients.size)
        or not np.isfinite(coefficients).all()
        or not np.isfinite(covariance).all()
    ):
        raise ValueError("coefficients and covariance must be finite matching arrays")
    return coefficients, covariance


def fopdt_transformed_relative_standard_errors(
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> tuple[float, float]:
    coefficients, covariance = _validated_covariance(coefficients, covariance)
    if coefficients.size != 3:
        raise ValueError("FOPDT covariance requires three regression coefficients")
    temperature_coefficient = float(coefficients[1])
    duty_coefficient = float(coefficients[2])
    if temperature_coefficient == 0.0:
        return math.inf, math.inf
    gain = -duty_coefficient / temperature_coefficient
    tau = -1.0 / temperature_coefficient
    jacobian = np.zeros((2, 3), dtype=float)
    jacobian[0, 1] = duty_coefficient / temperature_coefficient**2
    jacobian[0, 2] = -1.0 / temperature_coefficient
    jacobian[1, 1] = 1.0 / temperature_coefficient**2
    transformed = jacobian @ covariance @ jacobian.T
    variances = np.diag(transformed)
    if np.any(variances < 0.0) or not np.isfinite(variances).all():
        return math.inf, math.inf
    return (
        math.sqrt(float(variances[0])) / abs(gain) if gain else math.inf,
        math.sqrt(float(variances[1])) / abs(tau) if tau else math.inf,
    )


def fopdt_uncertainty_blockers(
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> tuple[str, ...]:
    gain_rse, tau_rse = fopdt_transformed_relative_standard_errors(
        coefficients,
        covariance,
    )
    blockers: list[str] = []
    if gain_rse > RSE_K_MAX:
        blockers.append("K-relative-standard-error")
    if tau_rse > RSE_TAU_MAX:
        blockers.append("tau-relative-standard-error")
    return tuple(blockers)


def _sopdt_values(coefficients: np.ndarray) -> np.ndarray | None:
    coefficients = np.asarray(coefficients, dtype=float)
    if coefficients.shape != (4,) or not np.isfinite(coefficients).all():
        return None
    _, rate_coefficient, temperature_coefficient, duty_coefficient = coefficients
    roots = np.roots(np.asarray((1.0, -rate_coefficient, -temperature_coefficient)))
    if np.max(np.abs(np.imag(roots))) > SOPDT_POLE_IMAGINARY_TOLERANCE or not np.isfinite(roots).all():
        return None
    real_roots = np.real(roots)
    if np.any(real_roots >= 0.0) or np.any(real_roots == 0.0):
        return None
    taus = sorted(float(-1.0 / root) for root in real_roots)
    if temperature_coefficient == 0.0 or not all(math.isfinite(tau) and tau > 0.0 for tau in taus):
        return None
    gain = float(-duty_coefficient / temperature_coefficient)
    if not math.isfinite(gain):
        return None
    return np.asarray((gain, taus[0], taus[1]), dtype=float)


def sopdt_transformed_relative_standard_errors(
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> dict[str, float]:
    coefficients, covariance = _validated_covariance(coefficients, covariance)
    if coefficients.size != 4:
        raise ValueError("SOPDT covariance requires four regression coefficients")
    values = _sopdt_values(coefficients)
    if values is None:
        return {"K": math.inf, "tau_1": math.inf, "tau_2": math.inf}
    jacobian = np.zeros((3, 4), dtype=float)
    for index in range(1, 4):
        step = max(abs(float(coefficients[index])) * 1e-6, 1e-12)
        upper = coefficients.copy()
        lower = coefficients.copy()
        upper[index] += step
        lower[index] -= step
        upper_values = _sopdt_values(upper)
        lower_values = _sopdt_values(lower)
        if upper_values is None or lower_values is None:
            return {"K": math.inf, "tau_1": math.inf, "tau_2": math.inf}
        jacobian[:, index] = (upper_values - lower_values) / (2.0 * step)
    transformed = jacobian @ covariance @ jacobian.T
    variances = np.diag(transformed)
    if np.any(variances < 0.0) or not np.isfinite(variances).all():
        return {"K": math.inf, "tau_1": math.inf, "tau_2": math.inf}
    return {
        name: math.sqrt(float(variances[index])) / abs(float(values[index]))
        for index, name in enumerate(("K", "tau_1", "tau_2"))
    }


def sopdt_uncertainty_blockers(
    coefficients: np.ndarray,
    covariance: np.ndarray,
) -> tuple[str, ...]:
    errors = sopdt_transformed_relative_standard_errors(coefficients, covariance)
    blockers: list[str] = []
    if errors["K"] > RSE_K_MAX:
        blockers.append("K-relative-standard-error")
    if errors["tau_1"] > RSE_TAU_MAX:
        blockers.append("tau_1-relative-standard-error")
    if errors["tau_2"] > RSE_TAU_MAX:
        blockers.append("tau_2-relative-standard-error")
    return tuple(blockers)


def _fit_ipdt(rows: Sequence[_Row], theta: float) -> _RawFit:
    if len(rows) < _MIN_FIT_ROWS[ModelForm.IPDT]:
        return _RawFit(None, None, (), ("covariance-singular",), ())
    x = np.asarray([(1.0, row.duty(theta)) for row in rows], dtype=float)
    y = np.asarray([row.rate for row in rows], dtype=float)
    coefficients, covariance = _linear_fit(x, y)
    c0, gain = (float(value) for value in coefficients)
    parameters = IPDT(K_i=gain, c0=c0, theta=theta)
    physical: list[str] = []
    if not GAIN_RATE_MIN <= gain <= GAIN_RATE_MAX:
        physical.append("K_i-out-of-bounds")
    if c0 > 0.0:
        physical.append("c0-positive")
    uncertainty: list[str] = []
    if covariance is None:
        uncertainty.append("covariance-singular")
    elif _relative_error(gain, float(covariance[1, 1])) > RSE_GAIN_RATE_MAX:
        uncertainty.append("K_i-relative-standard-error")
    return _RawFit(parameters, tuple(coefficients), tuple(physical), tuple(uncertainty), ())


def _fit_fopdt(rows: Sequence[_Row], theta: float) -> _RawFit:
    if len(rows) < _MIN_FIT_ROWS[ModelForm.FOPDT]:
        return _RawFit(None, None, (), ("covariance-singular",), ())
    x = np.asarray(
        [(1.0, row.temperature_0, row.duty(theta)) for row in rows],
        dtype=float,
    )
    y = np.asarray([row.rate for row in rows], dtype=float)
    coefficients, covariance = _linear_fit(x, y)
    _, temperature_coefficient, duty_coefficient = (float(value) for value in coefficients)
    with np.errstate(divide="ignore", invalid="ignore"):
        tau = float(-1.0 / temperature_coefficient)
        gain = float(-duty_coefficient / temperature_coefficient)
    physical: list[str] = []
    parameters: FOPDT | None = None
    if not math.isfinite(gain) or not math.isfinite(tau):
        physical.append("nonfinite-parameters")
    else:
        parameters = FOPDT(K=gain, tau=tau, theta=theta)
        if not GAIN_MIN <= gain <= GAIN_MAX:
            physical.append("K-out-of-bounds")
        if not TAU_MIN <= tau <= TAU_MAX:
            physical.append("tau-out-of-bounds")
    uncertainty: list[str] = []
    if covariance is None:
        uncertainty.append("covariance-singular")
    else:
        uncertainty.extend(fopdt_uncertainty_blockers(coefficients, covariance))
    return _RawFit(parameters, tuple(coefficients), tuple(physical), tuple(uncertainty), ())


def _sopdt_design(
    rows_by_episode: Sequence[Sequence[_Row]],
    theta: float,
) -> tuple[np.ndarray, np.ndarray]:
    design: list[tuple[float, float, float, float]] = []
    target: list[float] = []
    for rows in rows_by_episode:
        for row in rows:
            design.append(
                (
                    1.0,
                    row.previous_rate,
                    row.temperature_0,
                    row.duty(theta),
                )
            )
            target.append((row.rate - row.previous_rate) / row.dt)
    return np.asarray(design, dtype=float), np.asarray(target, dtype=float)


def _fit_sopdt(
    rows_by_episode: Sequence[Sequence[_Row]],
    theta: float,
) -> _RawFit:
    x, y = _sopdt_design(rows_by_episode, theta)
    if x.ndim != 2 or x.shape[0] < _MIN_FIT_ROWS[ModelForm.SOPDT]:
        return _RawFit(None, None, (), ("covariance-singular",), ())
    coefficients, covariance = _linear_fit(x, y)
    values = _sopdt_values(coefficients)
    stability: list[str] = []
    parameters: SOPDT | None = None
    physical: list[str] = []
    if values is None:
        _, rate_coefficient, temperature_coefficient, _ = coefficients
        roots = np.roots(np.asarray((1.0, -rate_coefficient, -temperature_coefficient)))
        if np.max(np.abs(np.imag(roots))) > SOPDT_POLE_IMAGINARY_TOLERANCE:
            stability.append("complex-continuous-poles")
        elif np.any(np.real(roots) >= 0.0):
            stability.append("unstable-continuous-poles")
        else:
            stability.append("repeated-degenerate-poles")
    else:
        gain, tau_1, tau_2 = (float(value) for value in values)
        parameters = SOPDT(
            K=gain,
            tau_1=tau_1,
            tau_2=tau_2,
            theta=theta,
        )
        if not GAIN_MIN <= gain <= GAIN_MAX:
            physical.append("K-out-of-bounds")
        if not TAU_MIN <= tau_1 <= TAU_MAX:
            physical.append("tau_1-out-of-bounds")
        if not TAU_MIN <= tau_2 <= TAU_MAX:
            physical.append("tau_2-out-of-bounds")
    uncertainty: list[str] = []
    if covariance is None:
        uncertainty.append("covariance-singular")
    else:
        uncertainty.extend(sopdt_uncertainty_blockers(coefficients, covariance))
    return _RawFit(
        parameters,
        tuple(coefficients),
        tuple(physical),
        tuple(uncertainty),
        tuple(stability),
    )


def _fit_form(
    form: ModelForm,
    rows_by_episode: Sequence[Sequence[_Row]],
    theta: float,
) -> _RawFit:
    rows = tuple(row for episode_rows in rows_by_episode for row in episode_rows)
    if form is ModelForm.IPDT:
        return _fit_ipdt(rows, theta)
    if form is ModelForm.FOPDT:
        return _fit_fopdt(rows, theta)
    return _fit_sopdt(rows_by_episode, theta)


def _advance_prediction(
    form: ModelForm,
    coefficients: tuple[float, ...],
    temperature: float,
    rate: float,
    duty: float,
    duration_s: float,
) -> tuple[float, float]:
    if duration_s <= 0.0:
        return temperature, rate
    if form is ModelForm.IPDT:
        intercept, gain = coefficients
        next_rate = intercept + gain * duty
        return temperature + duration_s * next_rate, next_rate
    if form is ModelForm.FOPDT:
        intercept, temperature_coefficient, duty_coefficient = coefficients
        forcing = intercept + duty_coefficient * duty
        if abs(temperature_coefficient) < 1e-15:
            next_temperature = temperature + duration_s * forcing
        else:
            equilibrium = -forcing / temperature_coefficient
            next_temperature = equilibrium + (temperature - equilibrium) * math.exp(
                temperature_coefficient * duration_s
            )
        next_rate = intercept + temperature_coefficient * next_temperature + duty_coefficient * duty
        return next_temperature, next_rate

    intercept, rate_coefficient, temperature_coefficient, duty_coefficient = coefficients

    def derivative(state_temperature: float, state_rate: float) -> tuple[float, float]:
        return (
            state_rate,
            intercept
            + rate_coefficient * state_rate
            + temperature_coefficient * state_temperature
            + duty_coefficient * duty,
        )

    k1_t, k1_r = derivative(temperature, rate)
    k2_t, k2_r = derivative(
        temperature + 0.5 * duration_s * k1_t,
        rate + 0.5 * duration_s * k1_r,
    )
    k3_t, k3_r = derivative(
        temperature + 0.5 * duration_s * k2_t,
        rate + 0.5 * duration_s * k2_r,
    )
    k4_t, k4_r = derivative(
        temperature + duration_s * k3_t,
        rate + duration_s * k3_r,
    )
    return (
        temperature + duration_s * (k1_t + 2.0 * k2_t + 2.0 * k3_t + k4_t) / 6.0,
        rate + duration_s * (k1_r + 2.0 * k2_r + 2.0 * k3_r + k4_r) / 6.0,
    )


def _observed_at(row: _Row, offset_s: float) -> float:
    fraction = offset_s / row.dt
    return row.temperature_0 + fraction * (row.temperature_1 - row.temperature_0)


def _predict_losses(
    form: ModelForm,
    coefficients: tuple[float, ...] | None,
    rows: Sequence[_Row],
    theta: float,
) -> tuple[float, tuple[tuple[int, float], ...]]:
    if coefficients is None or not rows:
        return math.inf, tuple((horizon, math.inf) for horizon in HORIZONS_S)
    squared: dict[int, list[float]] = {horizon: [] for horizon in HORIZONS_S}
    one_step: list[float] = []
    for start in range(len(rows)):
        temperature = rows[start].temperature_0
        rate = rows[start].previous_rate
        elapsed = 0.0
        pending: list[int] = list(HORIZONS_S)
        previous_end = rows[start].start_s
        for row_index in range(start, len(rows)):
            row = rows[row_index]
            if row.start_s != previous_end:
                break
            row_elapsed = 0.0
            for part_start, part_end, duty in row.duty_parts(theta):
                for horizon in tuple(value for value in pending if elapsed + part_start < value <= elapsed + part_end):
                    step = horizon - elapsed - row_elapsed
                    temperature, rate = _advance_prediction(
                        form,
                        coefficients,
                        temperature,
                        rate,
                        duty,
                        step,
                    )
                    row_elapsed += step
                    observed = _observed_at(row, horizon - elapsed)
                    squared[horizon].append((temperature - observed) ** 2)
                    pending.remove(horizon)
                temperature, rate = _advance_prediction(
                    form,
                    coefficients,
                    temperature,
                    rate,
                    duty,
                    part_end - row_elapsed,
                )
                row_elapsed = part_end
            if row_index == start:
                one_step.append((temperature - row.temperature_1) ** 2)
            elapsed += row.dt
            previous_end = row.end_s
            if not pending:
                break
    one_step_loss = float(np.mean(one_step)) if one_step else math.inf
    horizon_losses = tuple(
        (
            horizon,
            float(np.mean(squared[horizon])) if squared[horizon] else math.inf,
        )
        for horizon in HORIZONS_S
    )
    return one_step_loss, horizon_losses


def _weighted_loss(one_step: float, horizons: tuple[tuple[int, float], ...]) -> float:
    values = np.asarray((one_step, *(loss for _, loss in horizons)), dtype=float)
    weights = np.asarray((ONE_STEP_WEIGHT, *(weight for _, weight in HORIZON_WEIGHTS)), dtype=float)
    if not np.isfinite(values).all():
        return math.inf
    return float(np.average(values, weights=weights))


def _merge_blockers(groups: Sequence[Sequence[str]]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for group in groups for value in group))


def _parameter_vector(parameters: ModelParameters | None) -> np.ndarray | None:
    if parameters is None:
        return None
    values = asdict(parameters)
    values.pop("theta")
    return np.asarray(tuple(values.values()), dtype=float)


def _episode_stability(
    form: ModelForm,
    rows_by_episode: Sequence[Sequence[_Row]],
    theta: float,
) -> tuple[str, ...]:
    vectors: list[np.ndarray] = []
    for rows in rows_by_episode:
        fit = _fit_form(form, (rows,), theta)
        vector = _parameter_vector(fit.parameters)
        if vector is None or fit.physical_blockers or fit.stability_blockers:
            return ("episode-fit-unavailable",)
        vectors.append(vector)
    if len(vectors) < MIN_INDEPENDENT_EPISODES:
        return ("insufficient-independent-episodes",)
    stacked = np.vstack(vectors)
    scale = np.maximum(np.abs(np.median(stacked, axis=0)), 1e-9)
    relative_span = np.ptp(stacked, axis=0) / scale
    return ("parameters-vary-across-episodes",) if np.any(relative_span > EPISODE_STABILITY_RELATIVE_SPAN_MAX) else ()


def _fit_one(
    form: ModelForm,
    profile: DelayProfile,
    episode_ids: tuple[str, ...],
    rows_by_episode: tuple[tuple[_Row, ...], ...],
) -> ModelFit:
    if profile.basin is None:
        blocker = DelayBlocker.NO_PHYSICALLY_VALID_CANDIDATE
        return ModelFit(
            form=form,
            parameters=None,
            delay_profile=profile,
            one_step_loss=math.inf,
            horizon_losses=tuple((horizon, math.inf) for horizon in HORIZONS_S),
            fold_losses=tuple(math.inf for _ in range(max(len(episode_ids) - 1, 0))),
            episode_ids=episode_ids,
            common_row_ids=tuple(tuple((row.start_s, row.end_s) for row in rows) for rows in rows_by_episode),
            physical_blockers=(blocker.value,),
            stability_blockers=(() if profile.episode_ids == episode_ids else ("profile-episode-identities-mismatch",)),
        )
    theta = float(profile.basin.representative_s)
    full = _fit_form(form, rows_by_episode, theta)
    physical_groups: list[Sequence[str]] = [full.physical_blockers]
    uncertainty_groups: list[Sequence[str]] = [full.uncertainty_blockers]
    stability_groups: list[Sequence[str]] = [full.stability_blockers]
    validation: list[str] = []
    if full.coefficients is None:
        validation.append("insufficient-common-support")
    fold_losses: list[float] = []
    one_step_losses: list[float] = []
    horizon_by_fold: list[tuple[tuple[int, float], ...]] = []
    if profile.episode_ids != episode_ids:
        stability_groups.append(("profile-episode-identities-mismatch",))
    if len(rows_by_episode) < 2:
        validation.append("no-common-validation-folds")
    for held_out in range(1, len(rows_by_episode)):
        fold_fit = _fit_form(form, rows_by_episode[:held_out], theta)
        physical_groups.append(fold_fit.physical_blockers)
        uncertainty_groups.append(fold_fit.uncertainty_blockers)
        stability_groups.append(fold_fit.stability_blockers)
        one_step, horizons = _predict_losses(
            form,
            fold_fit.coefficients,
            rows_by_episode[held_out],
            theta,
        )
        one_step_losses.append(one_step)
        horizon_by_fold.append(horizons)
        fold_losses.append(_weighted_loss(one_step, horizons))
    if not np.isfinite(np.asarray(fold_losses)).all():
        validation.append("nonfinite-common-validation-loss")
    stability_groups.append(_episode_stability(form, rows_by_episode, theta))
    one_step_loss = (
        float(np.mean(one_step_losses)) if one_step_losses and np.isfinite(one_step_losses).all() else math.inf
    )
    horizon_losses = tuple(
        (
            horizon,
            float(np.mean([fold[index][1] for fold in horizon_by_fold]))
            if horizon_by_fold and np.isfinite([fold[index][1] for fold in horizon_by_fold]).all()
            else math.inf,
        )
        for index, horizon in enumerate(HORIZONS_S)
    )
    return ModelFit(
        form=form,
        parameters=full.parameters,
        delay_profile=profile,
        one_step_loss=one_step_loss,
        horizon_losses=horizon_losses,
        fold_losses=tuple(fold_losses),
        episode_ids=episode_ids,
        common_row_ids=tuple(tuple((row.start_s, row.end_s) for row in rows) for rows in rows_by_episode),
        physical_blockers=_merge_blockers(physical_groups),
        uncertainty_blockers=_merge_blockers(uncertainty_groups),
        stability_blockers=_merge_blockers(stability_groups),
        validation_blockers=tuple(dict.fromkeys(validation)),
    )


def _canonical_forms(
    forms: Sequence[ModelForm | str],
) -> tuple[ModelForm, ...]:
    converted = tuple(ModelForm(form) for form in forms)
    if not converted or len(set(converted)) != len(converted):
        raise ValueError("forms must contain unique supported model forms")
    return tuple(sorted(converted, key=_COMPLEXITY.__getitem__))


def _profile_for(
    profiles: Mapping[ModelForm, DelayProfile] | Mapping[str, DelayProfile],
    form: ModelForm,
) -> DelayProfile:
    profile = next(
        (candidate for key, candidate in profiles.items() if key == form or key == form.value),
        None,
    )
    if profile is None:
        raise ValueError(f"missing delay profile for {form.value}")
    if profile.model_form != form.value:
        raise ValueError("delay profile form does not match mapping key")
    return profile


def pid_sp_fit_corpus_digest(
    episodes: tuple[ExcitationEpisode, ...],
) -> str:
    return _json_digest(
        tuple(
            {
                "episode_id": episode.episode_id,
                "transition_at_s": episode.transition_at_s,
                "duty_before": episode.duty_before,
                "duty_after": episode.duty_after,
                "terminal_reason": episode.terminal_reason,
                "input_history": tuple(
                    (
                        segment.start_s,
                        segment.end_s,
                        segment.realized_duty,
                    )
                    for segment in (() if episode.input_history is None else episode.input_history.duty_segments)
                ),
                "intervals": tuple(
                    {
                        "start_s": interval.start_s,
                        "end_s": interval.end_s,
                        "temperature_f": interval.temperature_f,
                        "realized_duty": interval.realized_duty,
                        "continuous": interval.continuous,
                        "observation_sequence": interval.observation_sequence,
                        "role_generation": interval.role_generation,
                        "duty_segments": tuple(
                            (
                                segment.start_s,
                                segment.end_s,
                                segment.realized_duty,
                            )
                            for segment in interval.duty_segments or ()
                        ),
                    }
                    for interval in episode.intervals
                ),
            }
            for episode in episodes
        )
    )


def _configuration_digest(
    forms: tuple[ModelForm, ...],
    profiles: Mapping[ModelForm, DelayProfile],
) -> str:
    return _json_digest(
        {
            "schema": MODEL_SELECTION_SCHEMA,
            "forms": tuple(form.value for form in forms),
            "horizon_weights": HORIZON_WEIGHTS,
            "one_step_weight": ONE_STEP_WEIGHT,
            "selection_gates": {
                "confirmation_window": CONFIRMATION_WINDOW,
                "complexity": tuple((form.value, _COMPLEXITY[form]) for form in DEFAULT_FORMS),
                "minimum_fit_rows": tuple((form.value, _MIN_FIT_ROWS[form]) for form in DEFAULT_FORMS),
                "minimum_independent_episodes": MIN_INDEPENDENT_EPISODES,
                "minimum_independent_validation_folds": (MIN_INDEPENDENT_VALIDATION_FOLDS),
                "episode_stability_relative_span_max": (EPISODE_STABILITY_RELATIVE_SPAN_MAX),
                "sopdt_pole_imaginary_tolerance": (SOPDT_POLE_IMAGINARY_TOLERANCE),
            },
            "physical_bounds": {
                "gain": (GAIN_MIN, GAIN_MAX),
                "gain_rate": (GAIN_RATE_MIN, GAIN_RATE_MAX),
                "tau": (TAU_MIN, TAU_MAX),
                "rse_gain": RSE_K_MAX,
                "rse_gain_rate": RSE_GAIN_RATE_MAX,
                "rse_tau": RSE_TAU_MAX,
            },
            "profiles": {form.value: asdict(profiles[form]) for form in forms},
        }
    )


def fit_pid_sp_models(
    episodes: tuple[ExcitationEpisode, ...],
    delay_profiles: Mapping[ModelForm, DelayProfile] | Mapping[str, DelayProfile],
    *,
    forms: Sequence[ModelForm | str] = DEFAULT_FORMS,
) -> tuple[ModelFit, ...]:
    if not isinstance(episodes, tuple) or not episodes:
        raise ValueError("episodes must be a nonempty tuple")
    if any(not isinstance(episode, ExcitationEpisode) for episode in episodes):
        raise TypeError("episodes must contain ExcitationEpisode values")
    episode_ids = tuple(episode.episode_id for episode in episodes)
    if len(set(episode_ids)) != len(episode_ids):
        raise ValueError("episode identities must be unique")
    canonical_forms = _canonical_forms(forms)
    profiles = {form: _profile_for(delay_profiles, form) for form in canonical_forms}
    delays = tuple(
        sorted({float(profile.basin.representative_s) for profile in profiles.values() if profile.basin is not None})
    )
    rows_by_episode = tuple(_episode_rows(episode, delays) for episode in episodes)
    return tuple(_fit_one(form, profiles[form], episode_ids, rows_by_episode) for form in canonical_forms)


def select_pid_sp_model(
    episodes: tuple[ExcitationEpisode, ...],
    delay_profiles: Mapping[ModelForm, DelayProfile] | Mapping[str, DelayProfile],
    *,
    forms: Sequence[ModelForm | str] = DEFAULT_FORMS,
    confirmation: ModelConfirmation | None = None,
) -> ModelComparison:
    canonical_forms = _canonical_forms(forms)
    profiles = {form: _profile_for(delay_profiles, form) for form in canonical_forms}
    fits = fit_pid_sp_models(episodes, delay_profiles, forms=canonical_forms)
    return compare_model_fits(
        fits,
        fit_corpus_digest=pid_sp_fit_corpus_digest(episodes),
        configuration_digest=_configuration_digest(canonical_forms, profiles),
        confirmation=confirmation,
    )
