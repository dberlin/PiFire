"""Configuration normalization and model identity helpers for grey-box MPC."""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Literal, overload

from controller.runtime.context import EVENT_LOG_NAME

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type FloatConfigKey = Literal[
    "control_period",
    "Q_w",
    "R_dQ",
    "C_c",
    "h_amb",
    "T_amb",
    "theta",
    "K_Q",
    "sigma",
    "fan_min_pct",
    "fan_max_pct",
    "est_q_temp",
    "est_q_dist",
    "est_r_meas",
]
type IntConfigKey = Literal["n_horizon", "n_delay"]
type BoolConfigKey = Literal["enable_fan_input", "enable_online_adaptation"]


class MpcConfig(dict[str, JsonValue]):
    """Owned extensible settings with precise types for the runtime-owned keys."""

    @overload
    def __getitem__(self, key: FloatConfigKey) -> float: ...

    @overload
    def __getitem__(self, key: IntConfigKey) -> int: ...

    @overload
    def __getitem__(self, key: BoolConfigKey) -> bool: ...

    @overload
    def __getitem__(self, key: Literal["estimator"]) -> str: ...

    @overload
    def __getitem__(self, key: str) -> JsonValue: ...

    def __getitem__(self, key: str) -> JsonValue:
        return super().__getitem__(key)


type ModelMetadata = Mapping[str, JsonValue]


DEFAULT_MPC_CONFIG = MpcConfig(
    {
        # R_dQ (firing-move penalty) kept low: 1.0 was over-damped -> sluggish rise
        # and a looser steady band. 0.1 is materially faster without a large
        # step-overshoot penalty.
        "n_horizon": 24,
        # The generated prediction map is fixed at 25 seconds. The shorter period
        # is estimator/runner cadence.
        "control_period": 5.0,
        "Q_w": 1.0,
        "R_dQ": 0.1,
        # Nominal grey-box thermal parameters. They are a safe first-cook starting
        # point, not an identified description of a particular grill.
        "C_c": 320.0,
        "h_amb": 0.50,
        "T_amb": 20.0,
        "theta": 50.0,
        "n_delay": 8,
        "K_Q": 350.0,
        "sigma": 1.4e-9,
        "estimator": "ekf",
        "fan_min_pct": 40.0,
        "fan_max_pct": 100.0,
        "enable_fan_input": False,
        "est_q_temp": 1e-2,
        "est_q_dist": 0.05,
        "est_r_meas": 0.04,
        "enable_online_adaptation": False,
    }
)

MODEL_PARAMETER_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")
#: The parameters a fit actually solves for -- update_mpc's free set. Everything
#: else in MODEL_PARAMETER_KEYS is held at its shipped value by the solve, so a
#: pasted fit leaves those at the default and only these three move together.
FITTED_PARAMETER_KEYS = ("K_Q", "C_c", "theta")
RETIRED_PARAMETER_KEYS = ("C_f", "h_fc")


def _validated_float(value: JsonValue, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{key} must be a finite number")
    return normalized


def _validated_int(value: JsonValue, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _validated_bool(value: JsonValue, key: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _validated_fan_bool(value: JsonValue) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError("enable_fan_input must be a boolean or legacy 0/1")


def _validated_estimator(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("estimator must be a string")
    return value


def normalize_config(config: Mapping[str, JsonValue] | None) -> MpcConfig:
    """Return an owned complete MPC configuration with obsolete input removed."""

    supplied = {} if config is None else config
    normalized = MpcConfig(DEFAULT_MPC_CONFIG)
    normalized.update(supplied)
    normalized.pop("feed_forward", None)
    for key in (
        "control_period",
        "Q_w",
        "R_dQ",
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "fan_min_pct",
        "fan_max_pct",
        "est_q_temp",
        "est_q_dist",
        "est_r_meas",
    ):
        normalized[key] = _validated_float(normalized[key], key)
    for key in ("n_horizon", "n_delay"):
        normalized[key] = _validated_int(normalized[key], key)
    normalized["enable_fan_input"] = _validated_fan_bool(normalized["enable_fan_input"])
    normalized["enable_online_adaptation"] = _validated_bool(
        normalized["enable_online_adaptation"],
        "enable_online_adaptation",
    )
    normalized["estimator"] = _validated_estimator(normalized["estimator"])
    return normalized


def to_celsius(value: float, units: str) -> float:
    """Normalize a public controller temperature into the numerical Celsius domain."""

    return (value - 32.0) * 5.0 / 9.0 if units == "F" else value


def finite_float(value: bool | float | str) -> float | None:
    """Cast to float, returning ``None`` when the result is not finite."""

    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def optional_float(value: JsonValue) -> float | None:
    """Cast to a finite float, or return ``None`` when no number is reportable."""

    if value is None:
        return None
    try:
        normalized = float(value)
    except TypeError, ValueError:
        return None
    return normalized if math.isfinite(normalized) else None


def sanitized_copy(mapping: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a caller-owned JSON-safe copy of a settings mapping."""

    return {key: finite_float(value) if isinstance(value, float) else value for key, value in mapping.items()}


def model_is_identified(
    config: Mapping[str, JsonValue],
    model_metadata: ModelMetadata | None = None,
) -> bool:
    """Whether thermal parameters came from a fit rather than from a default.

    Two things count as a fit. A stored model carries `model_metadata`, the
    record of its own fit. An offline fit has no store to write to --
    update_mpc.py prints its result for the operator to paste into
    Settings > Controller -- so the configuration itself is the delivery path,
    and a config that differs from the shipped defaults is real evidence.

    Reading such a paste means reading FITTED_PARAMETER_KEYS, and reading them
    together: the solve moves all three, so a genuine paste differs in all
    three. One parameter alone is a stale or hand-edited value, not a fit, and
    must not buy the trust a fit buys.
    """

    return model_metadata is not None or all(
        config.get(key, DEFAULT_MPC_CONFIG[key]) != DEFAULT_MPC_CONFIG[key] for key in FITTED_PARAMETER_KEYS
    )


def warn_about_model(
    config: Mapping[str, JsonValue],
    model_metadata: ModelMetadata | None = None,
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Report obsolete or uncalibrated model settings without refusing control.

    The uncalibrated warning asks `model_is_identified` rather than restating
    its rule, so the model the controller plans against as uncalibrated is
    exactly the model the operator is told is uncalibrated. Restating it is how
    one stale parameter came to both buy a learned residual weight and silence
    this warning at the same time.

    Both warnings name something the operator has to act on, so they go to the
    event log the controller context carries. No context reaches this far down,
    which is what `logger` is for; its default is the same named logger the
    context defaults to, so an un-injected call still lands in the file.
    """

    log = logging.getLogger(EVENT_LOG_NAME) if logger is None else logger
    retired = [key for key in RETIRED_PARAMETER_KEYS if key in config]
    if retired:
        log.warning(
            f"[mpc] ignoring {', '.join(retired)}: the model is a single chamber lump and no "
            "longer has a firepot state for them to describe. Remove them from "
            "Settings > Controller."
        )
    if not model_is_identified(config, model_metadata):
        log.warning(
            "[mpc] model is uncalibrated (the thermal parameters are not a completed fit). "
            "Expect large overshoot until you fit this grill with controller/update_mpc.py."
        )
