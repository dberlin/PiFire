"""Characterize the minimum effective evidence for short-cook MPC learning."""

from __future__ import annotations

import pytest

from controller.grill_sim import GrillSim, MAKGrillSim
from tests.e2e._short_cook_mpc_admission_helpers import (
    HORIZONS,
    run_short_cook_campaign,
)


@pytest.mark.slow
@pytest.mark.parametrize(
    ("plant_type", "family"),
    ((GrillSim, "grill"), (MAKGrillSim, "mak")),
)
def test_three_59_frame_cooks_build_a_shadow_candidate_at_600_seconds(
    ds,
    plant_type: type[GrillSim],
    family: str,
) -> None:
    result = run_short_cook_campaign(plant_type, family)

    assert len(result.dwell) == 3
    assert all(cook.entry_frame <= 59 for cook in result.dwell)
    assert all(cook.frames_after_entry >= 30 for cook in result.dwell)

    for score in (result.first_600s, result.full_177):
        assert score.effective_duration_s >= 600.0
        assert tuple(horizon for horizon, _ in score.horizon_ratios) == HORIZONS
        assert score.evaluation_blockers == ()
        assert all(ratio < 1.0 for _, ratio in score.horizon_ratios)
        assert all(wins == 5 for _, wins in score.horizon_wins)
        assert score.whole_cook_ratio < 1.0
        assert score.closed_loop_iae_ratio < 1.0
        assert score.candidate_overshoot_c < score.incumbent_overshoot_c
        assert score.whole_cook_wins == 5
        assert score.closed_loop_iae_wins == 5
        assert score.overshoot_wins == 5
