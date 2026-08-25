"""Contracts for the normalized PID-SP live-learning projection."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from pydantic import TypeAdapter, ValidationError

from common import controller_model_state
from common.persistence import runtime as runtime_persistence
from common.web_contracts.learning import (
    FopdtPidSpCheckpoint,
    IpdtPidSpCheckpoint,
    PidSpCheckpointModel,
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
from controller.pid_sp_learning import build_pid_sp_live_learning


def _identifier_status(**overrides):
    status = {
        "accepted": MIN_ACCEPTED,
        "accepted_seconds": MIN_ACCEPTED_SECONDS,
        "duty_std": MIN_DUTY_STD,
        "temp_span": MIN_TEMP_SPAN_F,
        "transition_seen": True,
        "duty_segments": 3,
        "best_residual": 0.5,
        "runner_up_residual": 1.0,
        "candidates_passing": 1,
        "confirming": CONFIRM_WINDOW,
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


def test_projection_publishes_backend_owned_confirmation_progress():
    projection = build_pid_sp_live_learning(
        _identifier_status(confirming=CONFIRM_WINDOW - 3),
        _predictor_status(),
    )

    assert projection["confirmation"] == {
        "observed": CONFIRM_WINDOW - 3,
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
            "active",
            id="trusted-model-and-active-predictor",
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
    assert build_pid_sp_live_learning(identifier, predictor)["status"] == expected


def test_fallback_wins_over_an_active_looking_trusted_model():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(active=True, disabled=True)

    assert build_pid_sp_live_learning(identifier, predictor)["status"] == "fallback"


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
    )

    assert projection["status"] == "evaluating"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_gate_observations_are_rejected(value):
    with pytest.raises(ValueError, match="accepted_seconds"):
        build_pid_sp_live_learning(
            _identifier_status(accepted_seconds=value),
            _predictor_status(),
        )


@pytest.mark.parametrize("field", ["accepted", "accepted_seconds", "duty_std", "temp_span"])
def test_booleans_are_not_accepted_as_numeric_gate_observations(field):
    with pytest.raises(TypeError, match=field):
        build_pid_sp_live_learning(
            _identifier_status(**{field: True}),
            _predictor_status(),
        )


def test_arbitrarily_large_integer_observations_remain_valid_numbers():
    accepted = 10**1000

    projection = build_pid_sp_live_learning(
        _identifier_status(accepted=accepted),
        _predictor_status(),
    )

    assert projection["gates"][0]["observed"] == accepted


def test_projection_defensively_copies_nested_identifier_and_predictor_mappings():
    identifier = _identifier_status(trusted={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    predictor = _predictor_status(model={"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0})
    original_identifier = deepcopy(identifier)
    original_predictor = deepcopy(predictor)

    projection = build_pid_sp_live_learning(identifier, predictor)
    projection["identifier"]["trusted"]["K"] = 999.0
    projection["predictor"]["model"]["theta"] = 999.0

    assert identifier == original_identifier
    assert predictor == original_predictor


_FOPDT_CHECKPOINT = {
    "form": "fopdt",
    "K": 800.0,
    "tau": 600.0,
    "theta": 40.0,
    "revision": 3,
    "identified_at_f": 225.0,
}
_IPDT_CHECKPOINT = {
    "form": "ipdt",
    "K_i": 0.8,
    "c0": -0.2,
    "theta": 20.0,
    "revision": 4,
    "setpoint_f": 230.0,
}


def _marked_live(**identifier_overrides):
    return build_pid_sp_live_learning(
        _identifier_status(**identifier_overrides),
        _predictor_status(),
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
        "checkpoint": None,
        "failure": None,
    }
    assert len(report.revision) == 64
    int(report.revision, 16)


@pytest.mark.parametrize(
    ("checkpoint", "expected"),
    [
        pytest.param(_FOPDT_CHECKPOINT, _FOPDT_CHECKPOINT, id="fopdt"),
        pytest.param(
            _IPDT_CHECKPOINT,
            {
                "form": "ipdt",
                "K_i": 0.8,
                "c0": -0.2,
                "theta": 20.0,
                "revision": 4,
                "identified_at_f": 230.0,
            },
            id="ipdt",
        ),
    ],
)
def test_idle_report_normalizes_each_durable_checkpoint_form(checkpoint, expected):
    report = _report(checkpoint=checkpoint).as_dict()

    assert report["status"] == "idle"
    assert report["live"] is False
    assert report["confirmation"] is None
    assert report["checkpoint"] == expected
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

    validated = WirePidSpLearningReport.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", exclude_unset=True) == payload


def test_pid_sp_report_contract_preserves_idle_and_structured_failure_projections():
    idle = _report().as_dict()
    malformed_live = _marked_live()
    malformed_live["identifier"]["accepted"] = True
    failed = _report(status={"learning": malformed_live}).as_dict()

    assert (
        WirePidSpLearningReport.model_validate(idle, strict=True).model_dump(
            mode="json",
            exclude_unset=True,
        )
        == idle
    )
    assert (
        WirePidSpLearningReport.model_validate(failed, strict=True).model_dump(
            mode="json",
            exclude_unset=True,
        )
        == failed
    )


def test_checkpoint_and_live_predictor_models_remain_distinct_discriminated_unions():
    checkpoint = TypeAdapter(PidSpCheckpointModel).validate_python(_FOPDT_CHECKPOINT, strict=True)
    predictor = TypeAdapter(PidSpPredictorModel).validate_python(
        {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
        strict=True,
    )

    assert isinstance(checkpoint, FopdtPidSpCheckpoint)
    assert predictor.form == "fopdt"
    with pytest.raises(ValidationError):
        TypeAdapter(PidSpPredictorModel).validate_python(_FOPDT_CHECKPOINT, strict=True)
    with pytest.raises(ValidationError):
        TypeAdapter(PidSpCheckpointModel).validate_python(
            {"form": "fopdt", "K": 800.0, "tau": 600.0, "theta": 40.0},
            strict=True,
        )


def test_legacy_checkpoint_without_form_is_normalized_to_fopdt():
    checkpoint = {key: value for key, value in _FOPDT_CHECKPOINT.items() if key != "form"}

    normalized = _report(checkpoint=checkpoint).as_dict()["checkpoint"]

    assert normalized == _FOPDT_CHECKPOINT
    assert normalized is not checkpoint


def test_marked_pid_sp_live_projection_is_preserved():
    live = _marked_live()

    payload = _report(status={"learning": live}, checkpoint=_FOPDT_CHECKPOINT).as_dict()

    assert payload["status"] == "evaluating"
    assert payload["live"] is True
    assert payload["gates"] == live["gates"]
    assert payload["identifier"] == live["identifier"]
    assert payload["predictor"] == live["predictor"]
    assert payload["confirmation"] == live["confirmation"]
    assert payload["checkpoint"] == _FOPDT_CHECKPOINT
    assert payload["failure"] is None


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
    assert payload["failure"] is None


def test_malformed_marked_live_state_is_structured_error_without_hiding_checkpoint():
    malformed = {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "collecting",
        "identifier": {},
        "predictor": {},
        "confirmation": {},
        "gates": [],
    }

    payload = _report(status={"learning": malformed}, checkpoint=_FOPDT_CHECKPOINT).as_dict()

    assert payload["status"] == "error"
    assert payload["live"] is False
    assert payload["gates"] == []
    assert payload["identifier"] is None
    assert payload["predictor"] is None
    assert payload["confirmation"] is None
    assert payload["checkpoint"] == _FOPDT_CHECKPOINT
    assert payload["failure"]["code"] == "live-status-invalid"
    assert payload["failure"]["terminal"] is False
    assert payload["failure"]["detail"]


@pytest.mark.parametrize(
    "checkpoint",
    [
        pytest.param({**_FOPDT_CHECKPOINT, "K": float("nan")}, id="nan"),
        pytest.param({**_FOPDT_CHECKPOINT, "tau": float("inf")}, id="infinity"),
        pytest.param({**_FOPDT_CHECKPOINT, "form": "unknown"}, id="unknown-form"),
        pytest.param({key: value for key, value in _FOPDT_CHECKPOINT.items() if key != "theta"}, id="missing"),
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
            "identifier",
            "status",
            "controller",
            "schema_version",
        )
    }
    checkpoint = dict(reversed(tuple(_FOPDT_CHECKPOINT.items())))

    first = _report(status={"learning": live}, checkpoint=_FOPDT_CHECKPOINT)
    reordered = _report(status={"learning": reordered_live}, checkpoint=checkpoint)
    changed = _report(
        status={"learning": live},
        checkpoint={**_FOPDT_CHECKPOINT, "K": 801.0},
    )
    confirmation_changed_live = deepcopy(live)
    confirmation_changed_live["confirmation"]["observed"] -= 1
    confirmation_changed = _report(
        status={"learning": confirmation_changed_live},
        checkpoint=_FOPDT_CHECKPOINT,
    )

    assert first.revision == reordered.revision
    assert first.revision != changed.revision
    assert first.revision != confirmation_changed.revision
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
            "revision": 3,
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
    returned["checkpoint"]["K"] = 999.0

    assert status == original_status
    assert checkpoint == original_checkpoint
    assert report.as_dict()["identifier"]["trusted"]["K"] == 800.0
    assert report.as_dict()["confirmation"] == original_status["controller"]["learning"]["confirmation"]
    assert report.as_dict()["checkpoint"]["K"] == 800.0
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


def test_checkpoint_contracts_are_immutable_and_discriminated():
    fopdt = FopdtPidSpCheckpoint(**_FOPDT_CHECKPOINT)
    ipdt = IpdtPidSpCheckpoint(
        form="ipdt",
        K_i=0.8,
        c0=-0.2,
        theta=20.0,
        revision=4,
        identified_at_f=230.0,
    )

    assert fopdt.form == "fopdt"
    assert ipdt.form == "ipdt"
    with pytest.raises(ValidationError):
        fopdt.K = 900.0


@pytest.mark.parametrize(
    ("checkpoint", "field"),
    [
        pytest.param({**_FOPDT_CHECKPOINT, "K": True}, "K", id="fopdt-K"),
        pytest.param({**_FOPDT_CHECKPOINT, "tau": True}, "tau", id="fopdt-tau"),
        pytest.param({**_FOPDT_CHECKPOINT, "theta": True}, "theta", id="fopdt-theta"),
        pytest.param(
            {**_FOPDT_CHECKPOINT, "identified_at_f": True},
            "identified_at_f",
            id="fopdt-identified-at",
        ),
        pytest.param(
            {
                **{key: value for key, value in _FOPDT_CHECKPOINT.items() if key != "identified_at_f"},
                "setpoint_f": True,
            },
            "identified_at_f",
            id="fopdt-legacy-setpoint",
        ),
        pytest.param({**_IPDT_CHECKPOINT, "K_i": True}, "K_i", id="ipdt-K-i"),
        pytest.param({**_IPDT_CHECKPOINT, "c0": True}, "c0", id="ipdt-c0"),
        pytest.param({**_IPDT_CHECKPOINT, "theta": True}, "theta", id="ipdt-theta"),
        pytest.param(
            {**_IPDT_CHECKPOINT, "identified_at_f": True},
            "identified_at_f",
            id="ipdt-identified-at",
        ),
        pytest.param(
            {**_IPDT_CHECKPOINT, "setpoint_f": True},
            "identified_at_f",
            id="ipdt-legacy-setpoint",
        ),
    ],
)
def test_checkpoint_numeric_fields_reject_booleans_before_conversion(checkpoint, field):
    with pytest.raises(ValueError, match=rf"checkpoint {field} must be a number"):
        _report(checkpoint=checkpoint)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_bool_or_float_schema_markers_do_not_claim_pid_sp_live_state(schema_version):
    live = _marked_live()
    live["schema_version"] = schema_version

    payload = _report(status={"learning": live}).as_dict()

    assert payload["status"] == "idle"
    assert payload["live"] is False
    assert payload["failure"] is None
