import builtins
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
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


def _dispatcher():
    from controller.learning_report import controller_learning_report_revision

    return controller_learning_report_revision


@pytest.fixture
def isolated_providers(monkeypatch):
    calls: list[str] = []
    mpc_provider = ModuleType(_PROVIDER_MODULES["mpc"])
    pid_sp_provider = ModuleType(_PROVIDER_MODULES["pid_sp"])

    def mpc_revision():
        calls.append("mpc")
        return "mpc-revision"

    def pid_sp_report():
        calls.append("pid_sp")
        return SimpleNamespace(revision="pid-sp-revision")

    mpc_provider.learning_report_revision = mpc_revision
    pid_sp_provider.backend_pid_sp_learning_report = pid_sp_report
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["mpc"], mpc_provider)
    monkeypatch.setitem(sys.modules, _PROVIDER_MODULES["pid_sp"], pid_sp_provider)
    return calls, mpc_provider, pid_sp_provider


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
    ("controller_name", "expected_revision", "expected_calls"),
    [
        ("mpc", "mpc-revision", ["mpc"]),
        ("pid_sp", "pid-sp-revision", ["pid_sp"]),
    ],
)
def test_supported_controller_delegates_only_to_its_provider(
    isolated_providers, controller_name, expected_revision, expected_calls
):
    calls, _mpc_provider, _pid_sp_provider = isolated_providers

    assert _dispatcher()(controller_name) == expected_revision
    assert calls == expected_calls


@pytest.mark.parametrize("controller_name", ["pid", "unknown", "", None])
def test_unsupported_controller_returns_none_without_touching_providers(isolated_providers, controller_name):
    calls, _mpc_provider, _pid_sp_provider = isolated_providers

    assert _dispatcher()(controller_name) is None
    assert calls == []


@pytest.mark.parametrize("controller_name", ["mpc", "pid_sp"])
def test_provider_failure_does_not_fall_through_to_the_other_provider(monkeypatch, isolated_providers, controller_name):
    calls, mpc_provider, pid_sp_provider = isolated_providers

    def fail_mpc():
        calls.append("mpc")
        raise RuntimeError("mpc report unavailable")

    def fail_pid_sp():
        calls.append("pid_sp")
        raise RuntimeError("pid-sp report unavailable")

    mpc_provider.learning_report_revision = fail_mpc
    pid_sp_provider.backend_pid_sp_learning_report = fail_pid_sp

    with pytest.raises(RuntimeError, match="report unavailable"):
        _dispatcher()(controller_name)

    assert calls == [controller_name]


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
