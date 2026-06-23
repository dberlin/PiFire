#!/usr/bin/env python3

'''
*****************************************
 PiFire MPC Combustion Allocator
*****************************************

 Maps the MPC's scalar firing-rate demand Q to physical actuators (auger duty
 and, on PWM/DC-fan builds, fan duty) along a sensible air-fuel curve. Air
 tracks fuel so the air-fuel ratio stays near its target across the firing
 range, which keeps combustion sensible by construction.

*****************************************
'''


def allocate(Q, *, Q_min, Q_max, u_min, u_max, fan_min_pct, fan_max_pct, enable_fan):
	'''
	:param Q: firing-rate / heat-release demand
	:returns: (auger_duty, fan_duty_pct or None)
	'''
	span = (Q_max - Q_min) if Q_max > Q_min else 1.0
	frac = (Q - Q_min) / span
	frac = max(0.0, min(1.0, frac))                 # clamp to [0, 1]
	auger = u_min + frac * (u_max - u_min)
	fan = fan_min_pct + frac * (fan_max_pct - fan_min_pct) if enable_fan else None
	return auger, fan
