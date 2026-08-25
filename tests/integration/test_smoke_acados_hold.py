from tools.smoke_acados_hold import main


def test_real_mpc_hold_restore_learning_and_actuation_smoke() -> None:
    assert main() == 0
