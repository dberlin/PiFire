"""Configuration normalization and model identity helpers for grey-box MPC."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Literal, overload


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


DEFAULT_MPC_CONFIG = MpcConfig({
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
})

MODEL_PARAMETER_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")
PHYSICAL_PARAMETER_KEYS = ("C_c", "h_amb", "theta", "n_delay", "K_Q", "sigma")
RETIRED_PARAMETER_KEYS = ("C_f", "h_fc")


def normalize_config(config: Mapping[str, JsonValue] | None) -> MpcConfig:
    """Return an owned complete MPC configuration with obsolete input removed."""

    normalized = MpcConfig(DEFAULT_MPC_CONFIG)
    normalized.update(config or {})
    normalized.pop("feed_forward", None)
    return normalized


def to_celsius(value: float, units: str) -> float:
    """Normalize a public controller temperature into the numerical Celsius domain."""

    return (value - 32.0) * 5.0 / 9.0 if units == "F" else value


def finite_float(value: bool | int | float | str) -> float | None:
    """Cast to float, returning ``None`` when the result is not finite."""

    normalized = float(value)
    return normalized if math.isfinite(normalized) else None


def optional_float(value: bool | int | float | str | None) -> float | None:
    """Cast to a finite float, or return ``None`` when no number is reportable."""

    if value is None:
        return None
    try:
        normalized = float(value)
    except ValueError:
        return None
    return normalized if math.isfinite(normalized) else None


def sanitized_copy(mapping: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Return a caller-owned JSON-safe copy of a settings mapping."""

    return {
        key: finite_float(value) if isinstance(value, float) else value
        for key, value in mapping.items()
    }


def model_is_identified(
    config: Mapping[str, JsonValue],
    model_metadata: ModelMetadata | None = None,
) -> bool:
    """Whether thermal parameters came from configuration or calibration evidence."""

    return model_metadata is not None or any(
        config.get(key) != DEFAULT_MPC_CONFIG[key] for key in PHYSICAL_PARAMETER_KEYS
    )


def warn_about_model(config: Mapping[str, JsonValue]) -> None:
    """Report obsolete or uncalibrated model settings without refusing control."""

    retired = [key for key in RETIRED_PARAMETER_KEYS if key in config]
    if retired:
        print(
            f"[mpc] ignoring {', '.join(retired)}: the model is a single chamber lump and no "
            "longer has a firepot state for them to describe. Remove them from "
            "Settings > Controller."
        )
    if all(config.get(key) == DEFAULT_MPC_CONFIG[key] for key in PHYSICAL_PARAMETER_KEYS):
        print(
            "[mpc] model is uncalibrated (every thermal parameter is still the shipped default). "
            "Expect large overshoot until you fit this grill with controller/update_mpc.py."
        )
