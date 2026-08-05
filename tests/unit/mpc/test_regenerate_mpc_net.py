import os
import sys

import numpy as np
import pytest

# tools/ isn't a package (no __init__.py); resolve it relative to this file
# rather than the fragile cwd-relative 'tools' insert this replaced.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "superpowers", "experiments"))
import approxmpc_span
import export_span_net
import regenerate_mpc_net as rg

from controller.mpc import _DEFAULTS
from controller.mpc_allocator import ALLOCATOR_REVISION
from controller.mpc_model import MODEL_SCHEMA


def test_export_cmd_fan_on_uses_fan_paths_and_flag():
    cmd = rg.export_cmd("py", True)
    assert "--enable-fan" in cmd
    assert any(a.endswith("pifire_span_fan.npz") for a in cmd)
    assert any(a.endswith("mpc_policy_net_fan.npz") for a in cmd)


def test_export_cmd_fan_off_uses_base_paths_no_flag():
    cmd = rg.export_cmd("py", False)
    assert "--enable-fan" not in cmd
    assert any(a.endswith("pifire_span.npz") and not a.endswith("_fan.npz") for a in cmd)
    assert any(a.endswith("mpc_policy_net.npz") and not a.endswith("_fan.npz") for a in cmd)


def test_sample_cmd_carries_episodes_and_fan_flag():
    on = rg.sample_cmd("py", True, 500, None)
    assert "--enable-fan" in on and "500" in on and "--mode" in on and "span" in on
    off = rg.sample_cmd("py", False, 300, 8)
    assert "--enable-fan" not in off and "300" in off and "8" in off


def test_plan_commands_both_orders_sample_before_export_per_mode():
    cmds = rg.plan_commands([False, True], episodes=500, workers=None, skip_sample=False)
    # 4 commands: sample-off, export-off, sample-on, export-on
    assert len(cmds) == 4
    assert "sample_mpc.py" in " ".join(cmds[0]) and "export_span_net.py" in " ".join(cmds[1])


def test_plan_commands_skip_sample_omits_sampling():
    cmds = rg.plan_commands([True], episodes=500, workers=None, skip_sample=True)
    assert len(cmds) == 1 and "export_span_net.py" in " ".join(cmds[0])


def _span_dataset():
    """A complete, hand-authored schema-1 fan-off span archive fixture."""
    sample_count = 4
    state_width = int(_DEFAULTS["n_delay"]) + 2
    dataset = {
        "X0": np.zeros((sample_count, state_width), dtype=np.float64),
        "u_prev": np.zeros(sample_count, dtype=np.float64),
        "t_set": np.full(sample_count, 110.0, dtype=np.float64),
        "u0": np.full(sample_count, 0.5, dtype=np.float64),
        "sp_lo": np.float64(100.0),
        "sp_hi": np.float64(290.0),
        "dataset_schema": np.int64(1),
        "sample_mode": np.array("span"),
        "model_schema": np.int64(MODEL_SCHEMA),
        "allocator_revision": np.int64(ALLOCATOR_REVISION),
        "episode_count": np.int64(500),
        "sampled_state_count": np.int64(sample_count),
        "seed": np.int64(0),
        "generation_version": np.int64(1),
        "sample_minutes": np.float64(120.0),
        "sample_dither": np.float64(0.08),
        "generation_command": np.array(
            "sample_mpc.py --mode span -e 500 --minutes 120.0 --dither 0.08 "
            "--sp-lo 100.0 --sp-hi 290.0 --seed 0"
        ),
    }
    for key, value in _DEFAULTS.items():
        if key in {
            "C_c",
            "h_amb",
            "T_amb",
            "theta",
            "K_Q",
            "sigma",
            "Q_w",
            "R_dQ",
            "t_step",
            "n_delay",
            "n_horizon",
        }:
            dataset[key] = np.asarray(value)
    dataset["enable_fan_input"] = np.int64(0)
    return dataset


def test_span_dataset_validator_accepts_complete_current_provenance():
    provenance = approxmpc_span.validate_span_dataset(
        _span_dataset(), expected_enable_fan=False, expected_episodes=500, expected_seed=0
    )

    assert provenance["episode_count"] == 500
    assert provenance["sampled_state_count"] == 4
    assert str(provenance["generation_command"]).endswith("--seed 0")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model_schema", np.int64(2)),
        ("allocator_revision", np.int64(ALLOCATOR_REVISION + 1)),
        ("dataset_schema", np.int64(0)),
        ("K_Q", np.asarray(float(_DEFAULTS["K_Q"]) * 1.01)),
        ("enable_fan_input", np.int64(1)),
        ("episode_count", np.int64(499)),
        ("sampled_state_count", np.int64(3)),
        ("generation_command", np.array("fabricated command")),
    ],
)
def test_span_dataset_validator_rejects_stale_or_fabricated_provenance(key, value):
    dataset = _span_dataset()
    dataset[key] = value

    with pytest.raises(ValueError, match=key):
        approxmpc_span.validate_span_dataset(
            dataset, expected_enable_fan=False, expected_episodes=500, expected_seed=0
        )


def test_span_dataset_validator_rejects_metadata_free_input():
    dataset = _span_dataset()
    del dataset["generation_version"]

    with pytest.raises(ValueError, match="generation_version"):
        approxmpc_span.validate_span_dataset(
            dataset, expected_enable_fan=False, expected_episodes=500, expected_seed=0
        )


def test_export_rejects_mismatched_provenance_before_stamping_an_artifact(tmp_path):
    dataset = _span_dataset()
    dataset["episode_count"] = np.int64(499)
    data_path = tmp_path / "stale-span.npz"
    artifact_path = tmp_path / "policy.npz"
    np.savez_compressed(data_path, **dataset)

    with pytest.raises(ValueError, match="episode_count"):
        export_span_net.main(
            str(data_path),
            str(artifact_path),
            False,
            expected_episodes=500,
            expected_seed=0,
        )

    assert not artifact_path.exists()


def test_export_command_carries_expected_dataset_provenance():
    cmd = rg.export_cmd("py", True, episodes=500, seed=7)

    assert "--expected-episodes" in cmd and cmd[cmd.index("--expected-episodes") + 1] == "500"
    assert "--expected-seed" in cmd and cmd[cmd.index("--expected-seed") + 1] == "7"


def test_plan_commands_interpreter_is_injectable():
    # py is threaded through to every command so the builder is fully injectable
    cmds = rg.plan_commands([False, True], episodes=500, workers=None, skip_sample=False, py="DUMMYPY")
    assert all(cmd[0] == "DUMMYPY" for cmd in cmds)
