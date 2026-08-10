import pytest

from tools.experiments import residual_mpc_compare as experiment


def _comparison_rows(*, candidate_auger=998.0, candidate_rmse=9.0, candidate_within=91.0):
    rows = []
    for arm in experiment.ARMS:
        for plant in experiment.PLANTS:
            for seed in experiment.SEEDS:
                for cook in range(1, experiment.COOKS + 1):
                    row = {
                        "arm": arm,
                        "plant": plant,
                        "seed": seed,
                        "cook": cook,
                        "pct_within_5f": 90.0,
                        "overshoot_f": 10.0,
                        "settle_s": 100.0,
                        "rmse_f": 10.0,
                        "steady_peak_to_peak_f": 8.0,
                        "auger_on_time_s": 1_000.0,
                        "requested_realized_load_error": 0.0,
                        "transitions_per_hour": 100.0,
                        "deadline_misses": 0,
                        "stale_result_episodes": 0,
                        "solver_p99_ms": 10.0,
                    }
                    if arm == "learned_residual" and cook == 3:
                        row.update(
                            pct_within_5f=candidate_within,
                            overshoot_f=9.0,
                            rmse_f=candidate_rmse,
                            steady_peak_to_peak_f=7.0,
                            auger_on_time_s=candidate_auger,
                        )
                    rows.append(row)
    return rows


def test_pellet_gate_bounds_increases_without_rejecting_reductions():
    within_limit = experiment._summary(_comparison_rows(candidate_auger=998.0))
    larger_reduction = experiment._summary(_comparison_rows(candidate_auger=989.0))
    excessive_increase = experiment._summary(_comparison_rows(candidate_auger=1_003.0))

    for summary in (within_limit, larger_reduction):
        assert summary["decision"]["third_cook_pellet_increase_within_0_2_percent"] is True
    assert excessive_increase["decision"]["third_cook_pellet_increase_within_0_2_percent"] is False


@pytest.mark.parametrize(("rmse", "within"), [(10.0, 91.0), (9.0, 90.0)])
def test_quality_gate_requires_strict_improvement_for_every_metric(rmse, within):
    summary = experiment._summary(_comparison_rows(candidate_rmse=rmse, candidate_within=within))

    assert summary["decision"]["third_cook_quality_improved"] is False
