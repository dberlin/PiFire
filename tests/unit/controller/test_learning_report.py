import builtins
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from common.cook_diagnostics import ControllerLearningReport
from common.web_contracts.learning import (
    ModelActionRejected,
    ModelActivationAccepted,
    ModelActivationRequest,
    ModelEvidenceReport,
    ModelRollbackAccepted,
    ModelRollbackRequest,
    MpcCalibrationCommand,
)
from controller.model_learning.report import build_learning_report

_PROVIDER_MODULES = {
    "mpc": "controller.model_learning.report",
    "pid_sp": "controller.pid_sp_learning",
}


def _dispatchers():
    from controller.learning_report import (
        controller_learning_report,
        controller_learning_report_revision,
    )

    return controller_learning_report, controller_learning_report_revision


@pytest.fixture
def isolated_providers(monkeypatch):
    calls: list[str] = []
    sources = {
        "mpc": {"status": "collecting", "candidate": {"digest": "a" * 64}},
        "pid_sp": {"status": "idle", "checkpoint": {"revision": 1}},
    }
    revisions = {"mpc": "a" * 64, "pid_sp": "b" * 64}
    mpc_provider = ModuleType(_PROVIDER_MODULES["mpc"])
    pid_sp_provider = ModuleType(_PROVIDER_MODULES["pid_sp"])

    def provider(controller_name):
        def diagnostic_learning_report():
            calls.append(controller_name)
            return ControllerLearningReport(
                controller=controller_name,
                schema_version=1,
                revision=revisions[controller_name],
                report=sources[controller_name],
            )

        return diagnostic_learning_report

    mpc_provider.diagnostic_learning_report = provider("mpc")
    pid_sp_provider.diagnostic_learning_report = provider("pid_sp")
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["mpc"], mpc_provider)
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["pid_sp"], pid_sp_provider)
    return calls, mpc_provider, pid_sp_provider, sources


def test_importing_dispatcher_does_not_import_either_provider(monkeypatch):
    monkeypatch.delitem(sys.modules, "controller.learning_report", raising=False)
    imported_providers: list[str] = []
    real_import = builtins.__import__

    def tracked_import(name, *args, **kwargs):
        if name in _PROVIDER_MODULES.values():
            imported_providers.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracked_import)

    importlib.import_module("controller.learning_report")

    assert imported_providers == []


@pytest.mark.parametrize(
    ("controller_name", "expected_status", "expected_calls"),
    [
        ("mpc", "collecting", ["mpc"]),
        ("pid_sp", "idle", ["pid_sp"]),
    ],
)
def test_supported_controller_delegates_only_to_its_provider(
    isolated_providers, controller_name, expected_status, expected_calls
):
    calls, _mpc_provider, _pid_sp_provider, _sources = isolated_providers
    controller_learning_report, _revision = _dispatchers()

    report = controller_learning_report(controller_name)

    assert report is not None
    assert report.controller == controller_name
    assert report.report["status"] == expected_status
    assert calls == expected_calls


@pytest.mark.parametrize("controller_name", ["pid", "unknown", "", None])
def test_unsupported_controller_returns_none_without_touching_providers(
    monkeypatch, isolated_providers, controller_name
):
    calls, _mpc_provider, _pid_sp_provider, _sources = isolated_providers
    import controller.learning_report as dispatcher_module

    imported_providers = []
    monkeypatch.setattr(
        dispatcher_module,
        "import_module",
        lambda module_name: imported_providers.append(module_name),
    )
    controller_learning_report, controller_learning_report_revision = _dispatchers()

    assert controller_learning_report(controller_name) is None
    assert controller_learning_report_revision(controller_name) is None
    assert imported_providers == []
    assert calls == []


@pytest.mark.parametrize("controller_name", ["mpc", "pid_sp"])
def test_provider_failure_does_not_fall_through_to_the_other_provider(monkeypatch, isolated_providers, controller_name):
    calls, mpc_provider, pid_sp_provider, _sources = isolated_providers

    def fail_mpc():
        calls.append("mpc")
        raise RuntimeError("mpc report unavailable")

    def fail_pid_sp():
        calls.append("pid_sp")
        raise RuntimeError("pid-sp report unavailable")

    mpc_provider.diagnostic_learning_report = fail_mpc
    pid_sp_provider.diagnostic_learning_report = fail_pid_sp
    controller_learning_report, _revision = _dispatchers()

    with pytest.raises(RuntimeError, match="report unavailable"):
        controller_learning_report(controller_name)

    assert calls == [controller_name]


@pytest.mark.parametrize(
    ("controller_name", "expected_revision"),
    [("mpc", "a" * 64), ("pid_sp", "b" * 64)],
)
def test_revision_dispatch_delegates_to_the_generic_report(isolated_providers, controller_name, expected_revision):
    calls, _mpc_provider, _pid_sp_provider, _sources = isolated_providers
    _report, controller_learning_report_revision = _dispatchers()

    assert controller_learning_report_revision(controller_name) == expected_revision
    assert calls == [controller_name]


def test_dispatcher_returns_a_deeply_owned_report(isolated_providers):
    calls, _mpc_provider, _pid_sp_provider, sources = isolated_providers
    controller_learning_report, _revision = _dispatchers()

    report = controller_learning_report("pid_sp")
    sources["pid_sp"]["checkpoint"]["revision"] = 99
    sources["pid_sp"]["added"] = True

    assert report is not None
    assert report.report == {"status": "idle", "checkpoint": {"revision": 1}}
    assert calls == ["pid_sp"]


def test_dispatcher_rejects_a_non_report_provider_result(isolated_providers):
    _calls, mpc_provider, _pid_sp_provider, _sources = isolated_providers
    mpc_provider.diagnostic_learning_report = lambda: SimpleNamespace(
        controller="mpc",
        revision="a" * 64,
        report={},
    )
    controller_learning_report, _revision = _dispatchers()

    with pytest.raises(TypeError, match="ControllerLearningReport"):
        controller_learning_report("mpc")


def test_dispatcher_rejects_a_provider_for_the_wrong_controller(isolated_providers):
    _calls, mpc_provider, _pid_sp_provider, _sources = isolated_providers
    mpc_provider.diagnostic_learning_report = lambda: ControllerLearningReport(
        controller="pid_sp",
        schema_version=1,
        revision="a" * 64,
        report={},
    )
    controller_learning_report, _revision = _dispatchers()

    with pytest.raises(ValueError, match="mpc"):
        controller_learning_report("mpc")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"controller": ""}, "controller"),
        ({"controller": "  "}, "controller"),
        ({"schema_version": 0}, "schema_version"),
        ({"schema_version": True}, "schema_version"),
        ({"revision": ""}, "revision"),
        ({"revision": "  "}, "revision"),
    ],
)
def test_learning_report_rejects_invalid_envelope_metadata(overrides, message):
    fields = {
        "controller": "pid_sp",
        "schema_version": 1,
        "revision": "b" * 64,
        "report": {},
    }
    fields.update(overrides)

    with pytest.raises(ValueError, match=message):
        ControllerLearningReport(**fields)


@pytest.mark.parametrize(
    ("report", "error_type"),
    [
        ({"bad": float("nan")}, ValueError),
        ({"bad": float("inf")}, ValueError),
        ({"bad": {1: "non-string key"}}, TypeError),
        ({"bad": object()}, TypeError),
    ],
)
def test_learning_report_rejects_non_json_report_values(report, error_type):
    with pytest.raises(error_type):
        ControllerLearningReport(
            controller="pid_sp",
            schema_version=1,
            revision="b" * 64,
            report=report,
        )


def test_model_evidence_report_contract_preserves_the_real_canonical_projection():
    report = build_learning_report(
        (),
        activation_state={},
        live_status={},
        checkpoint_required=True,
        calibration_command_high_water=0,
    )
    payload = report.as_dict()

    validated = ModelEvidenceReport.model_validate(payload, strict=True)

    assert validated.model_dump(mode="json", exclude_unset=True) == payload


def test_model_evidence_action_contracts_preserve_exact_request_and_response_members():
    digest = "a" * 64
    request = ModelActivationRequest(candidate_digest=digest, decision_id="decision-1")
    accepted = ModelActivationAccepted(
        accepted=True,
        phase="prepared",
        transaction_id="b" * 64,
        decision_id="decision-1",
        candidate_digest=digest,
        role_generation=3,
    )
    rejected = ModelActionRejected(
        accepted=False,
        active_kind="grey-box",
        error="model-activation-rejected",
        detail="stale-confidence-decision",
    )
    rollback_request = ModelRollbackRequest(reason="operator observed instability")
    rollback = ModelRollbackAccepted(
        accepted=True,
        active_kind="grey-box",
        decision_id="decision-1",
        reason="operator observed instability",
        role_generation=4,
        rollback_digest="c" * 64,
    )
    calibration = MpcCalibrationCommand(
        action="start",
        revision=7,
        ambient_c=20.0,
        ambient_source="measured",
        empty_grill_confirmed=True,
        pellets_confirmed=True,
    )

    assert request.model_dump(mode="json") == {
        "candidate_digest": digest,
        "decision_id": "decision-1",
    }
    assert accepted.model_dump(mode="json") == {
        "accepted": True,
        "phase": "prepared",
        "transaction_id": "b" * 64,
        "decision_id": "decision-1",
        "candidate_digest": digest,
        "role_generation": 3,
    }
    assert rejected.model_dump(mode="json") == {
        "accepted": False,
        "active_kind": "grey-box",
        "error": "model-activation-rejected",
        "detail": "stale-confidence-decision",
    }
    assert rollback_request.model_dump(mode="json") == {
        "reason": "operator observed instability",
    }
    assert rollback.model_dump(mode="json") == {
        "accepted": True,
        "active_kind": "grey-box",
        "decision_id": "decision-1",
        "reason": "operator observed instability",
        "role_generation": 4,
        "rollback_digest": "c" * 64,
    }
    assert calibration.model_dump(mode="json") == {
        "action": "start",
        "revision": 7,
        "ambient_c": 20.0,
        "ambient_source": "measured",
        "empty_grill_confirmed": True,
        "pellets_confirmed": True,
    }
