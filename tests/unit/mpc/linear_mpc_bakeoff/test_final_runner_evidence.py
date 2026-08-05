"""Final runner evidence contracts that prevent scientific-evidence regressions."""

from __future__ import annotations

from docs.superpowers.experiments.linear_mpc_bakeoff.runner import (
    ExperimentConfig,
    _common_validation_horizon,
    _validation_origins,
)


def test_validation_origins_never_cross_the_validation_boundary() -> None:
    origins = _validation_origins(
        record_samples=600,
        validation_start=360,
        validation_end=480,
        horizon_steps=30,
        frame_steps=1,
    )

    assert origins
    assert all(origin + 30 <= 480 for origin in origins)
    assert all(origin >= 360 for origin in origins)


def test_common_horizon_is_one_validation_only_choice_for_every_arm() -> None:
    choice = _common_validation_horizon(
        {
            "scheduled-arx:GrillSim:correct:0": {600: (0.999,), 800: (0.995,), 1000: (0.99,)},
            "dmc:MAKGrillSim:wrong-gain:0": {600: (0.999,), 800: (0.995,), 1000: (0.99,)},
        }
    )

    assert choice["selected_horizon_s"] == 600
    assert choice["pooled_validation_scores"] == {"600": 0.999, "800": 0.995, "1000": 0.99}


def test_quick_matrix_contract_includes_calibrated_and_wrong_initializations() -> None:
    config = ExperimentConfig.quick()
    assert config.initializations == ("correct", "wrong-gain", "wrong-pole", "wrong-delay")


def test_quick_artifact_persists_one_horizon_and_real_mak_provenance() -> None:
    from docs.superpowers.experiments.linear_mpc_bakeoff.runner import run_experiment

    artifact = run_experiment(ExperimentConfig.quick())

    selected = artifact.config["horizon_selection"]["selected_horizon_s"]
    assert {row.mpc_horizon_s for row in artifact.scenarios} == {selected}
    for arm in artifact.arms:
        real = artifact.horizon_evidence[arm.name]["real"]
        assert real["provenance"] == "requested-input-reconstruction"
        assert set(real["diagnostics_c"]) == {"60", "300", "900", "1800", "3600"}
        assert real["diagnostics_c"]["1800"] is None
        assert real["diagnostics_c"]["3600"] is None
    assert all(
        key.count(":") == 2 and key.split(":", 1)[0] in {"online", "frozen"}
        for arm in artifact.arms
        for key in arm.domain_median_scores
    )

