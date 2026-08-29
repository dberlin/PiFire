"""Validation and migration for persisted grey-box learning checkpoints."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

from controller.model_learning.activation import GreyControlPairDescriptor
from controller.mpc_model import MODEL_SCHEMA

GREY_BOX_KIND = "grey-box"
MODEL_PARAM_KEYS = ("C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma")

_GREY_V4_SCHEMA = "pifire-grey-learning/v4"
_GREY_V4_KEYS = frozenset(
    (
        "version",
        "revision",
        "schema",
        "structure",
        "active",
        "challenger",
        "active_pair",
        "window",
        "candidate_pair",
        "evidence",
        "origin",
        "policy",
        "identification",
        "cook_refit",
        "identities",
        "activation",
        "failure",
    )
)
_GREY_V5_SCHEMA = "pifire-grey-learning/v5"
_GREY_V5_KEYS = frozenset(
    (
        "version",
        "revision",
        "schema",
        "structure",
        "active",
        "active_pair",
        "evidence",
        "origin",
        "policy",
        "identification",
        "cook_refit",
        "identities",
        "activation",
        "failure",
        "challenger_authority",
    )
)
_GREY_V6_SCHEMA = "pifire-grey-learning/v6"
_GREY_V6_KEYS = _GREY_V5_KEYS - {"cook_refit"}


class GreySnapshotInvalid(ValueError):
    """A persisted learning record that cannot authorize the fixed grey model."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _grey_snapshot_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GreySnapshotInvalid(f"invalid-parameter:{name}")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise GreySnapshotInvalid(f"invalid-parameter:{name}")
    return normalized


def normalize_grey_parameters(value):
    from controller.model_promotion import PROMOTION_BOUNDS, n_delay_is_whole

    if not isinstance(value, Mapping):
        raise GreySnapshotInvalid("invalid-parameters")
    parameters = {}
    for name in MODEL_PARAM_KEYS:
        normalized = _grey_snapshot_number(value.get(name), name)
        lower, upper = PROMOTION_BOUNDS[name]
        if not lower <= normalized <= upper:
            raise GreySnapshotInvalid(f"out-of-bounds:{name}")
        parameters[name] = normalized
    if not n_delay_is_whole(parameters["n_delay"]) or int(parameters["n_delay"]) != 8:
        raise GreySnapshotInvalid("incompatible-delay")
    parameters["n_delay"] = 8
    return parameters


def _grey_snapshot_metadata(value):
    if value is None:
        return {"rmse": None, "samples": 0, "band_c": [0.0, 0.0], "nfev": None}
    if not isinstance(value, Mapping):
        raise GreySnapshotInvalid("invalid-metadata")
    rmse = value.get("rmse")
    if rmse is not None:
        rmse = _grey_snapshot_number(rmse, "rmse")
        if rmse < 0.0:
            raise GreySnapshotInvalid("invalid-metadata:rmse")
    samples = value.get("samples", 0)
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 0:
        raise GreySnapshotInvalid("invalid-metadata:samples")
    band = value.get("band_c", (0.0, 0.0))
    if isinstance(band, (str, bytes)) or not isinstance(band, (list, tuple)) or len(band) != 2:
        raise GreySnapshotInvalid("invalid-metadata:band")
    lower = _grey_snapshot_number(band[0], "band_c")
    upper = _grey_snapshot_number(band[1], "band_c")
    if lower > upper:
        raise GreySnapshotInvalid("invalid-metadata:band")
    nfev = value.get("nfev")
    if nfev is not None and (isinstance(nfev, bool) or not isinstance(nfev, int) or nfev < 0):
        raise GreySnapshotInvalid("invalid-metadata:nfev")
    return {"rmse": rmse, "samples": samples, "band_c": [lower, upper], "nfev": nfev}


def _grey_v4_mapping(value, keys, reason):
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise GreySnapshotInvalid(reason)
    return value


def _grey_v4_nonnegative_int(value, reason):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GreySnapshotInvalid(reason)
    return value


def _grey_v4_optional_text(value, reason):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise GreySnapshotInvalid(reason)
    return value


def _grey_v4_optional_digest(value, reason):
    if value is not None and (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GreySnapshotInvalid(reason)
    return value


def _grey_v4_window(value):
    window = _grey_v4_mapping(
        value,
        (
            "session_id",
            "cook_id",
            "first_observation_sequence",
            "last_observation_sequence",
            "configuration_digest",
            "incumbent_digest",
            "role_generation",
        ),
        "invalid-window",
    )
    session_id = _grey_v4_optional_text(window["session_id"], "invalid-window")
    if session_id is None:
        raise GreySnapshotInvalid("invalid-window")
    _grey_v4_optional_text(window["cook_id"], "invalid-window")
    first_sequence = _grey_v4_nonnegative_int(
        window["first_observation_sequence"],
        "invalid-window",
    )
    last_sequence = _grey_v4_nonnegative_int(
        window["last_observation_sequence"],
        "invalid-window",
    )
    if last_sequence < first_sequence:
        raise GreySnapshotInvalid("invalid-window")
    if _grey_v4_optional_digest(window["configuration_digest"], "invalid-window") is None:
        raise GreySnapshotInvalid("invalid-window")
    if _grey_v4_optional_digest(window["incumbent_digest"], "invalid-window") is None:
        raise GreySnapshotInvalid("invalid-window")
    _grey_v4_nonnegative_int(window["role_generation"], "invalid-window")


def _grey_origin_policy(origin, policy, *, legacy):
    if origin is None or policy is None:
        if origin is None and policy is None:
            return None, None, False
        raise GreySnapshotInvalid("invalid-origin" if origin is None else "invalid-policy")
    admitted_origins = {"passive-online", "operator-calibration"}
    if legacy:
        admitted_origins.add("cook-refit")
    if not isinstance(origin, str) or origin not in admitted_origins:
        raise GreySnapshotInvalid("invalid-origin")
    admitted_policies = {
        "passive-online": {"causal-auto"},
        "operator-calibration": {"causal-auto"},
        "cook-refit": {"cook-refit"},
    }
    if legacy:
        admitted_policies["passive-online"].add("passive-auto")
        admitted_policies["operator-calibration"].add("operator-reviewed")
    if not isinstance(policy, str) or policy not in admitted_policies[origin]:
        raise GreySnapshotInvalid("invalid-policy")
    if origin == "cook-refit":
        return None, None, True
    return origin, "causal-auto", False


def _grey_v4_cook_refit(value):
    cook_refit = _grey_v4_mapping(
        value,
        ("status", "latest"),
        "invalid-cook-refit",
    )
    if cook_refit["status"] not in {
        "idle",
        "queued",
        "running",
        "succeeded",
        "failed",
        "stale",
    }:
        raise GreySnapshotInvalid("invalid-cook-refit")
    _grey_v4_optional_text(cook_refit["latest"], "invalid-cook-refit")


def _grey_v4_model(value, reason):
    model = _grey_v4_mapping(value, ("parameters", "metadata"), reason)
    metadata = _grey_v4_mapping(
        model["metadata"],
        ("rmse", "samples", "band_c", "nfev"),
        f"{reason}:metadata",
    )
    return {
        "parameters": normalize_grey_parameters(model["parameters"]),
        "metadata": _grey_snapshot_metadata(metadata),
    }


def _grey_v4_pair_descriptor(value, reason):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GreySnapshotInvalid(reason)
    try:
        descriptor = GreyControlPairDescriptor.from_dict(dict(value))
        from controller.mpc_factory import MpcPairFactory

        return MpcPairFactory.migrate_legacy_descriptor(descriptor).to_dict()
    except (KeyError, TypeError, ValueError) as error:
        raise GreySnapshotInvalid(reason) from error


def _grey_v4_snapshot(snapshot):
    revision = _grey_v4_nonnegative_int(snapshot.get("revision"), "invalid-revision")
    active = _grey_v4_model(snapshot.get("active"), "invalid-active")
    if (challenger_value := snapshot.get("challenger")) is not None:
        _grey_v4_model(challenger_value, "invalid-challenger")
    window_value = snapshot.get("window")
    if window_value is not None:
        _grey_v4_window(window_value)
    active_pair = _grey_v4_pair_descriptor(
        snapshot.get("active_pair"),
        "invalid-active-pair",
    )
    _grey_v4_pair_descriptor(
        snapshot.get("candidate_pair"),
        "invalid-candidate-pair",
    )
    evidence_value = _grey_v4_mapping(
        snapshot.get("evidence"),
        ("eligible", "rejected", "confidence_decision_id"),
        "invalid-evidence",
    )
    evidence = {
        "eligible": _grey_v4_nonnegative_int(evidence_value["eligible"], "invalid-evidence"),
        "rejected": _grey_v4_nonnegative_int(evidence_value["rejected"], "invalid-evidence"),
        "confidence_decision_id": _grey_v4_optional_text(
            evidence_value["confidence_decision_id"],
            "invalid-evidence",
        ),
    }
    origin, policy, retired_origin = _grey_origin_policy(
        snapshot.get("origin"),
        snapshot.get("policy"),
        legacy=True,
    )
    identification_value = _grey_v4_mapping(
        snapshot.get("identification"),
        ("status",),
        "invalid-identification",
    )
    identification_status = identification_value["status"]
    if identification_status not in ("identified", "unidentified"):
        raise GreySnapshotInvalid("invalid-identification")
    _grey_v4_cook_refit(snapshot.get("cook_refit"))
    identities_value = _grey_v4_mapping(
        snapshot.get("identities"),
        (
            "active_digest",
            "active_generation",
            "candidate_digest",
            "candidate_generation",
            "rollback_digest",
            "rollback_generation",
        ),
        "invalid-identities",
    )
    identities = {}
    for role in ("active", "candidate", "rollback"):
        digest = _grey_v4_optional_digest(
            identities_value[f"{role}_digest"],
            "invalid-identities",
        )
        generation_value = identities_value[f"{role}_generation"]
        generation = (
            None if generation_value is None else _grey_v4_nonnegative_int(generation_value, "invalid-identities")
        )
        if (digest is None) != (generation is None):
            raise GreySnapshotInvalid("invalid-identities")
        identities[f"{role}_digest"] = digest
        identities[f"{role}_generation"] = generation
    if identities["active_digest"] is None:
        raise GreySnapshotInvalid("invalid-identities")
    activation_value = _grey_v4_mapping(
        snapshot.get("activation"),
        ("phase", "pending_persistence", "pending_swap"),
        "invalid-activation",
    )
    if activation_value["phase"] not in ("prepared", "active", "aborted"):
        raise GreySnapshotInvalid("invalid-activation")
    if not isinstance(activation_value["pending_persistence"], bool) or not isinstance(
        activation_value["pending_swap"], bool
    ):
        raise GreySnapshotInvalid("invalid-activation")
    activation = dict(activation_value)
    if retired_origin:
        activation = {
            "phase": "aborted",
            "pending_persistence": False,
            "pending_swap": False,
        }
    failure_value = snapshot.get("failure")
    if failure_value is None:
        failure = None
    else:
        failure_mapping = _grey_v4_mapping(
            failure_value,
            ("code", "detail"),
            "invalid-failure",
        )
        failure = {
            "code": _grey_v4_optional_text(failure_mapping["code"], "invalid-failure"),
            "detail": _grey_v4_optional_text(failure_mapping["detail"], "invalid-failure"),
        }
        if failure["code"] is None or failure["detail"] is None:
            raise GreySnapshotInvalid("invalid-failure")
    return {
        "version": MODEL_SCHEMA,
        "revision": revision,
        "schema": _GREY_V6_SCHEMA,
        "structure": {"kind": GREY_BOX_KIND, "n_delay": 8, "state_count": 10},
        "active": active,
        "active_pair": active_pair,
        "evidence": evidence,
        "origin": origin,
        "policy": policy,
        "identification": {"status": identification_status},
        "identities": {
            "active_digest": identities["active_digest"],
            "active_generation": identities["active_generation"],
            "rollback_digest": identities["rollback_digest"],
            "rollback_generation": identities["rollback_generation"],
        },
        "activation": activation,
        "failure": failure,
        "challenger_authority": None,
    }


def _grey_v5_snapshot(snapshot):
    authority_value = snapshot.get("challenger_authority")
    if authority_value is None:
        authority = None
    else:
        authority_mapping = _grey_v4_mapping(
            authority_value,
            ("challenger_id", "revision"),
            "invalid-challenger-authority",
        )
        challenger_id = _grey_v4_optional_text(
            authority_mapping["challenger_id"],
            "invalid-challenger-authority",
        )
        if challenger_id is None:
            raise GreySnapshotInvalid("invalid-challenger-authority")
        authority = {
            "challenger_id": challenger_id,
            "revision": _grey_v4_nonnegative_int(
                authority_mapping["revision"],
                "invalid-challenger-authority",
            ),
        }
    identities_value = _grey_v4_mapping(
        snapshot.get("identities"),
        (
            "active_digest",
            "active_generation",
            "rollback_digest",
            "rollback_generation",
        ),
        "invalid-identities",
    )
    legacy = {
        **dict(snapshot),
        "version": 4,
        "schema": _GREY_V4_SCHEMA,
        "challenger": None,
        "window": None,
        "candidate_pair": None,
        "identities": {
            **dict(identities_value),
            "candidate_digest": None,
            "candidate_generation": None,
        },
    }
    legacy.pop("challenger_authority", None)
    normalized = _grey_v4_snapshot(legacy)
    normalized["challenger_authority"] = None if snapshot.get("origin") == "cook-refit" else authority
    return normalized


def _grey_v6_snapshot(snapshot):
    origin, policy, _ = _grey_origin_policy(
        snapshot.get("origin"),
        snapshot.get("policy"),
        legacy=False,
    )
    legacy = {
        **dict(snapshot),
        "version": 5,
        "schema": _GREY_V5_SCHEMA,
        "cook_refit": {"status": "idle", "latest": None},
    }
    normalized = _grey_v5_snapshot(legacy)
    normalized["origin"] = origin
    normalized["policy"] = policy
    return normalized


def _grey_parameters_digest(parameters):
    return hashlib.sha256(
        json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def new_grey_learning_snapshot(*, revision, parameters, metadata):
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise GreySnapshotInvalid("invalid-revision")
    owned_parameters = normalize_grey_parameters(parameters)
    owned_metadata = _grey_snapshot_metadata(metadata)
    digest = _grey_parameters_digest(owned_parameters)
    return {
        "version": MODEL_SCHEMA,
        "revision": revision,
        "schema": _GREY_V6_SCHEMA,
        "structure": {"kind": GREY_BOX_KIND, "n_delay": 8, "state_count": 10},
        "active": {"parameters": owned_parameters, "metadata": owned_metadata},
        "active_pair": None,
        "evidence": {"eligible": 0, "rejected": 0, "confidence_decision_id": None},
        "origin": None,
        "policy": None,
        "identification": {
            "status": "identified" if owned_metadata["samples"] else "unidentified",
        },
        "identities": {
            "active_digest": digest,
            "active_generation": 0,
            "rollback_digest": None,
            "rollback_generation": None,
        },
        "activation": {
            "phase": "aborted",
            "pending_persistence": False,
            "pending_swap": False,
        },
        "failure": None,
        "challenger_authority": None,
    }


def migrate_grey_learning_snapshot(snapshot):
    """Own one compatible checkpoint as v6, accepting v3/v4/v5 only for migration."""

    if not isinstance(snapshot, Mapping):
        raise GreySnapshotInvalid("malformed-snapshot")
    version = snapshot.get("version")
    if version == 3:
        metadata = {name: snapshot.get(name) for name in ("rmse", "samples", "band_c", "nfev")}
        return new_grey_learning_snapshot(
            revision=snapshot.get("revision"),
            parameters=snapshot.get("params"),
            metadata=metadata,
        )
    structure = snapshot.get("structure")
    if structure != {"kind": GREY_BOX_KIND, "n_delay": 8, "state_count": 10}:
        raise GreySnapshotInvalid("incompatible-structure")
    if version == 4:
        if set(snapshot) != _GREY_V4_KEYS or snapshot.get("schema") != _GREY_V4_SCHEMA:
            raise GreySnapshotInvalid("malformed-v4")
        return _grey_v4_snapshot(snapshot)
    if version == 5:
        if set(snapshot) != _GREY_V5_KEYS or snapshot.get("schema") != _GREY_V5_SCHEMA:
            raise GreySnapshotInvalid("malformed-v5")
        return _grey_v5_snapshot(snapshot)
    if version != MODEL_SCHEMA:
        raise GreySnapshotInvalid("incompatible-schema")
    if set(snapshot) != _GREY_V6_KEYS or snapshot.get("schema") != _GREY_V6_SCHEMA:
        raise GreySnapshotInvalid("malformed-v6")
    return _grey_v6_snapshot(snapshot)
