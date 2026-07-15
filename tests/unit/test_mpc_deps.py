def test_do_mpc_and_casadi_import():
	import do_mpc  # noqa: F401
	import casadi  # noqa: F401


def test_do_mpc_solves_trivial_mpc():
	import numpy as np
	import do_mpc

	m = do_mpc.model.Model('continuous')
	x = m.set_variable('_x', 'x')
	u = m.set_variable('_u', 'u')
	m.set_rhs('x', -x + u)
	m.setup()
	mpc = do_mpc.controller.MPC(m)
	mpc.set_param(
		n_horizon=10, t_step=1.0, store_full_solution=False, nlpsol_opts={'ipopt.print_level': 0, 'print_time': 0}
	)
	mpc.set_objective(mterm=x**2, lterm=x**2)
	mpc.set_rterm(u=1e-2)
	mpc.bounds['lower', '_u', 'u'] = -1
	mpc.bounds['upper', '_u', 'u'] = 1
	mpc.setup()
	mpc.x0 = np.array([[1.0]])
	mpc.set_initial_guess()
	u0 = np.asarray(mpc.make_step(np.array([[1.0]]))).flatten()
	assert abs(float(u0[0])) <= 1.0
