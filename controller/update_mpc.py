#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Offline Calibration Utility
*****************************************

 Fits the grey-box thermal parameters (C_f, C_c, h_fc, h_amb) to a logged
 history CSV so the MPC model can be refined for a specific grill. The
 controller ships with working defaults and does not require calibration.

 CSV columns: time_s, temp_c, Q  (Q is the firing-rate demand; if you logged
 auger duty instead, map it back through the allocator first).

 Usage: python -m controller.update_mpc history.csv
*****************************************
'''

import argparse
import numpy as np
from scipy.optimize import least_squares


def simulate_chamber(t, Q, *, C_f, C_c, h_fc, h_amb, T_amb, T0, K_Q=1.0):
	'''Forward-simulate chamber temperature for the grey-box model (Euler).

	out[i] is the chamber temperature AT time t[i] (so out[0] == T0); each step
	advances the state from t[i] to t[i+1] using the input Q[i]. This alignment
	matters when fitting real logs, where the measured series starts at T0.
	'''
	t = np.asarray(t, dtype=float)
	Q = np.asarray(Q, dtype=float)
	Tf = T0
	Tc = T0
	out = np.empty_like(t)
	for i in range(len(t)):
		out[i] = Tc                      # record state at t[i] (out[0] == T0)
		if i < len(t) - 1:
			dt = t[i + 1] - t[i]
			dTf = (K_Q * Q[i] - h_fc * (Tf - Tc)) / C_f
			dTc = (h_fc * (Tf - Tc) - h_amb * (Tc - T_amb)) / C_c
			Tf += dTf * dt
			Tc += dTc * dt
	return out


def fit_params(t, temp, Q, *, T_amb, init):
	# Fit the firing-rate heat gain K_Q (steady gain) along with C_c, h_fc, h_amb.
	# C_f (the firepot time constant) is held fixed at its init value: it is
	# redundant with K_Q for the steady gain, so fitting both is ill-posed.
	temp = np.asarray(temp, dtype=float)
	keys = ['K_Q', 'C_c', 'h_fc', 'h_amb']
	C_f = init['C_f']
	x0 = np.array([init[k] for k in keys], dtype=float)

	def residual(x):
		params = dict(zip(keys, x))
		sim = simulate_chamber(t, Q, T_amb=T_amb, T0=float(temp[0]), C_f=C_f, **params)
		return sim - temp

	# Keep parameters physically positive via solver bounds (cleaner than abs(),
	# which introduces a non-smooth gradient at zero).
	res = least_squares(residual, x0, method='trf', bounds=(0.0, np.inf), max_nfev=2000)
	out = dict(zip(keys, res.x))
	out['C_f'] = C_f
	return out


def main():
	ap = argparse.ArgumentParser()
	ap.add_argument('csv')
	ap.add_argument('--t-amb', type=float, default=20.0)
	args = ap.parse_args()
	import pandas as pd
	df = pd.read_csv(args.csv)
	fitted = fit_params(df['time_s'].values, df['temp_c'].values, df['Q'].values,
	                    T_amb=args.t_amb,
	                    init=dict(C_f=60.0, C_c=306.0, h_fc=2.0, h_amb=0.55))
	print('Fitted grey-box params:')
	for k, v in fitted.items():
		print(f'  {k}: {v:.4f}')


if __name__ == '__main__':
	main()
