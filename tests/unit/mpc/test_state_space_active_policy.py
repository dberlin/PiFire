from __future__ import annotations

import numpy as np
import pytest

from controller.linear_mpc.activation import GREY_BOX_KIND, STATE_SPACE_KIND
from tests.unit.mpc.test_model_activation import _fixture, _state_space_snapshot


def test_successful_activation_publishes_the_reconstructed_affine_prediction_owner(state_space_snapshot):
    manager, request, *_ = _fixture(state_space_snapshot)
    assert manager.commit(manager.prepare(request)).accepted
    assert manager.active_model is not None

    prediction = manager.active_model.affine_prediction(3, 0.35, np.full(3, 20.0))

    assert manager.active_kind == STATE_SPACE_KIND
    assert prediction.free_output_c.shape == (3,)
    assert prediction.input_response_c.shape == (3, 3)
    assert np.isfinite(prediction.free_output_c).all()
    assert np.isfinite(prediction.input_response_c).all()


@pytest.mark.parametrize(
    "reason",
    [
        "invalid-active-state",
        "non-finite-forecast",
        "active-solve-failed",
        "repeated-policy-exception",
        "stale-result-threshold",
        "deadline-threshold",
        "restore-failed",
        "residual-degradation",
    ],
)
def test_every_named_runtime_failure_falls_back_immediately_and_fences_generation(
    state_space_snapshot,
    reason,
):
    manager, request, *_ = _fixture(state_space_snapshot)
    prepared = manager.prepare(request)
    assert manager.commit(prepared).accepted

    state = manager.fallback(reason, last_safe_command=0.36)

    assert state.active_kind == GREY_BOX_KIND
    assert state.failed_digest == request.candidate_digest
    assert state.failed_generation == 8
    assert state.fallback_reason == reason
    assert state.last_safe_command == pytest.approx(0.36)
    assert manager.commit(prepared).reason == "failed-generation-cannot-be-reenabled"


def test_explicit_rollback_is_an_immediate_fallback_with_exact_reason(state_space_snapshot):
    manager, request, *_ = _fixture(state_space_snapshot)
    assert manager.commit(manager.prepare(request)).accepted

    state = manager.rollback("operator requested rollback after smoke excursion")

    assert state.active_kind == GREY_BOX_KIND
    assert state.fallback_kind == GREY_BOX_KIND
    assert state.fallback_reason == "operator requested rollback after smoke excursion"
