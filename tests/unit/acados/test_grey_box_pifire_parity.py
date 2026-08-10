import hashlib
import json
from pathlib import Path

import pytest

from controller.acados import AcadosGreyBoxMPC, GreyBoxMPCConfig


_FIXTURE = Path(__file__).with_name("fixtures") / "do_mpc_decision_parity.json"


def test_frozen_do_mpc_corpus_has_auditable_base_capture_provenance():
    corpus = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    provenance = corpus["provenance"]
    assert provenance["source_revision"] == "cd329fe72c7cec9b166f691aa7c4bc2078909d88"
    assert provenance["source_change_id"] == "kxkqznmtxuoqmxrnzzoopoywlppuksxo"
    assert provenance["backend"] == {"do_mpc": "5.1.1", "casadi": "3.7.2", "solver": "IPOPT"}
    raw_outputs = [
        {"name": case["name"], "do_mpc_first_q": case["do_mpc_first_q"]}
        for case in corpus["cases"]
    ]
    encoded = json.dumps(raw_outputs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert hashlib.sha256(encoded).hexdigest() == provenance["raw_outputs_sha256"]
    assert provenance["review"] == "recaptured-from-immutable-base-and-reviewed-for-task-8"


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
