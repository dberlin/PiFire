import json
from pathlib import Path

import pytest

from controller.acados import AcadosGreyBoxMPC, GreyBoxMPCConfig


_FIXTURE = Path(__file__).with_name("fixtures") / "do_mpc_decision_parity.json"


def test_acados_first_decision_matches_the_frozen_reviewed_do_mpc_corpus():
    corpus = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert corpus["schema"] == 1
    assert "do-mpc/IPOPT" in corpus["source"]

    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(**corpus["config"]))
    try:
        for case in corpus["cases"]:
            result = solver.solve(
                case["state"],
                setpoint_c=case["setpoint_c"],
                q_previous=case["q_previous"],
                equilibrium_q=case["equilibrium_q"],
            )
            assert result.sequence_q[0] == pytest.approx(
                case["do_mpc_first_q"], abs=2e-4
            ), case["name"]
    finally:
        solver.close()
