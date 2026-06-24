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


def simulate_chamber(t, Q, *, C_f, C_c, h_fc, h_amb, T_amb, T0):
	'''Forward-simulate chamber temperature for the grey-box model (Euler).'''
	t = np.asarray(t, dtype=float)
	Q = np.asarray(Q, dtype=float)
	Tf = T0
	Tc = T0
	out = np.empty_like(t)
	for i in range(len(t)):
		dt = (t[i] - t[i - 1]) if i > 0 else (t[1] - t[0] if len(t) > 1 else 1.0)
		dTf = (Q[i] - h_fc * (Tf - Tc)) / C_f
		dTc = (h_fc * (Tf - Tc) - h_amb * (Tc - T_amb)) / C_c
		Tf += dTf * dt
		Tc += dTc * dt
		out[i] = Tc
	return out


def fit_params(t, temp, Q, *, T_amb, init):
	temp = np.asarray(temp, dtype=float)
	keys = ['C_f', 'C_c', 'h_fc', 'h_amb']
	x0 = np.array([init[k] for k in keys], dtype=float)

	def residual(x):
		params = dict(zip(keys, np.abs(x)))     # keep params positive
		sim = simulate_chamber(t, Q, T_amb=T_amb, T0=float(temp[0]), **params)
		return sim - temp

	res = least_squares(residual, x0, method='trf', max_nfev=2000)
	return dict(zip(keys, np.abs(res.x)))


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
