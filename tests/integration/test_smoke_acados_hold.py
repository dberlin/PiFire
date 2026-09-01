from dataclasses import replace

from pytest import LogCaptureFixture, MonkeyPatch

from controller.mpc_model import EstimatorSeed
from tests.fakes.learning_trajectory import ExactEstimatorSeedSource
from tools.smoke_acados_hold import main


def test_real_mpc_hold_complete_learning_inputs_restore_learning_and_actuation_smoke() -> None:
    assert main() == 0


def test_real_mpc_hold_accepts_distinct_delay_state_and_pre_roll_dimensions(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    seed_for = ExactEstimatorSeedSource.seed_for

    def seed_with_distinct_dimensions(
        self: ExactEstimatorSeedSource,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed:
        seed = seed_for(self, theta, n_delay, at_ms, measured_temp_c)
        required_frame_count = max(1, seed.required_frame_count - 1)
        return replace(
            seed,
            pre_roll_frame_count=required_frame_count,
            required_frame_count=required_frame_count,
        )

    monkeypatch.setattr(ExactEstimatorSeedSource, "seed_for", seed_with_distinct_dimensions)

    assert main() == 0
    assert "Estimator seed trace failed" not in caplog.text
