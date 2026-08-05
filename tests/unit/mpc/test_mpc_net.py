import os
import numpy as np

import pytest

from controller.mpc import _DEFAULTS
from controller.mpc_allocator import ALLOCATOR_REVISION
from controller.mpc_model import MODEL_SCHEMA, steady_combustion_load
from controller.mpc_net import NetPolicy, _CALIB_FLOATS, _CALIB_INTS, net_path_for

ART = os.path.join(os.path.dirname(__file__), "..", "..", "..", "controller", "mpc_policy_net.npz")


def _normalized_policy():
    calib = {key: _DEFAULTS[key] for key in _CALIB_FLOATS}
    calib.update({key: _DEFAULTS[key] for key in _CALIB_INTS})
    calib["model_schema"] = MODEL_SCHEMA
    input_dim = int(_DEFAULTS["n_delay"]) + 4
    return NetPolicy(
        [(np.zeros((input_dim, 1)), np.zeros(1))],
        np.zeros(input_dim),
        np.ones(input_dim),
        0.0,
        1.0,
        calib,
        110.0,
        285.0,
    )


@pytest.mark.parametrize(
    ("path", "enable_fan"),
    [(ART, False), (net_path_for(ART, True), True)],
)
def test_regenerated_policy_artifacts_embed_schema3_normalized_calibration_and_reference_pairs(path, enable_fan):
    """Both committed policies must be current evidence, not merely loadable archives."""
    assert MODEL_SCHEMA == 3
    policy = NetPolicy.load(path)
    active = dict(_DEFAULTS, enable_fan_input=enable_fan)

    assert policy.model_schema == 3
    assert policy.matches_config(active)
    assert policy.sp_lo < policy.sp_hi
    with np.load(path, allow_pickle=False) as artifact:
        assert int(artifact["allocator_revision"]) == ALLOCATOR_REVISION
        source_fields = {
            "source_dataset_schema",
            "source_sample_mode",
            "source_model_schema",
            "source_allocator_revision",
            "source_episode_count",
            "source_sampled_state_count",
            "source_seed",
            "source_generation_version",
            "source_generation_command",
            "source_sample_minutes",
            "source_sample_dither",
            "source_sp_lo",
            "source_sp_hi",
            "source_enable_fan_input",
        }
        assert source_fields <= set(artifact.files)
        assert int(artifact["source_dataset_schema"]) == 1
        assert str(artifact["source_sample_mode"]) == "span"
        assert int(artifact["source_model_schema"]) == MODEL_SCHEMA
        assert int(artifact["source_allocator_revision"]) == ALLOCATOR_REVISION
        assert int(artifact["source_episode_count"]) == 500
        assert int(artifact["source_sampled_state_count"]) == 142000
        assert int(artifact["source_seed"]) == 0
        assert int(artifact["source_generation_version"]) == 1
        assert int(artifact["source_enable_fan_input"]) == int(enable_fan)
        assert "sample_mpc.py --mode span -e 500" in str(artifact["source_generation_command"])
        assert float(artifact["source_sample_minutes"]) == 120.0
        assert float(artifact["source_sample_dither"]) == pytest.approx(0.08)
        assert float(artifact["source_sp_lo"]) == 100.0
        assert float(artifact["source_sp_hi"]) == 290.0
        for key in _CALIB_FLOATS:
            assert float(artifact[f"source_{key}"]) == pytest.approx(float(_DEFAULTS[key]))
        for key in _CALIB_INTS:
            expected = int(enable_fan) if key == "enable_fan_input" else int(_DEFAULTS[key])
            assert int(artifact[f"source_{key}"]) == expected
        assert {"ref_state", "ref_uprev", "ref_set", "ref_combustion_load"} <= set(artifact.files)
        assert len(artifact["ref_state"]) > 0
        assert len(artifact["ref_state"]) == len(artifact["ref_uprev"]) == len(artifact["ref_set"])
        assert len(artifact["ref_state"]) == len(artifact["ref_combustion_load"])
        assert np.all(np.isfinite(artifact["ref_state"]))
        assert np.all(np.isfinite(artifact["ref_combustion_load"]))

        observed = np.asarray(
            [
                policy.firing_rate(state, previous, setpoint)
                for state, previous, setpoint in zip(artifact["ref_state"], artifact["ref_uprev"], artifact["ref_set"])
            ]
        )
        np.testing.assert_allclose(observed, artifact["ref_combustion_load"], rtol=2e-5, atol=2e-5)

        state, previous, setpoint = artifact["ref_state"][0], artifact["ref_uprev"][0], artifact["ref_set"][0]
        baseline = steady_combustion_load(policy.calib, float(setpoint), float(state[policy.n_delay + 1]))
        assert policy.firing_rate_raw(state, previous, setpoint) == pytest.approx(
            baseline + policy.residual(state, previous, setpoint)
        )


@pytest.mark.parametrize(
    ("path", "enable_fan"),
    [(ART, False), (net_path_for(ART, True), True)],
)
def test_loaded_policy_raw_command_includes_its_analytic_equilibrium(path, enable_fan):
    """The artifact's raw interface is the complete command, not just its learned move."""
    policy = NetPolicy.load(path)
    with np.load(path, allow_pickle=False) as artifact:
        state, previous, setpoint = artifact["ref_state"][0], artifact["ref_uprev"][0], artifact["ref_set"][0]

    equilibrium = steady_combustion_load(policy.calib, float(setpoint), float(state[policy.n_delay + 1]))
    residual = policy.residual(state, previous, setpoint)

    assert policy.firing_rate_raw(state, previous, setpoint) == pytest.approx(equilibrium + residual)


def test_artifact_with_a_stale_model_schema_is_rejected():
    policy = _normalized_policy()
    policy.model_schema = MODEL_SCHEMA - 1

    assert not policy.matches_config(_DEFAULTS)


def test_firing_rate_is_bounded_to_the_normalized_command_domain():
    policy = _normalized_policy()
    state = np.array([0.0] * policy.n_delay + [150.0, 0.0])

    for setpoint in (110.0, 170.0, 230.0, 285.0):
        assert 0.0 <= policy.firing_rate(state, 0.5, setpoint) <= 1.0


def test_normalized_artifact_rejects_a_changed_model_calibration():
    policy = _normalized_policy()
    altered = dict(_DEFAULTS, K_Q=_DEFAULTS["K_Q"] * 1.5)

    assert not policy.matches_config(altered)


def test_net_path_for_fan_off_returns_base():
    assert net_path_for("./controller/mpc_policy_net.npz", False) == "./controller/mpc_policy_net.npz"


def test_net_path_for_fan_on_inserts_suffix():
    assert net_path_for("./controller/mpc_policy_net.npz", True) == "./controller/mpc_policy_net_fan.npz"
