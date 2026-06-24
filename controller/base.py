#!/usr/bin/env python3

'''
*****************************************
 PiFire PID Controller Base Class
*****************************************

 Description: Base class for the controller.  Inherited by all controller
 modules in this package.  

*****************************************
'''

'''
Imported Libraries
'''
import time

'''
Class Definition
'''

class ControllerBase:
	def __init__(self, config, units, cycle_data):
		self.config = config
		self.units = units
		self.cycle_data = cycle_data
		self.function_list = [
			'update', 
	        'set_target', 
	        'get_config',
			'set_config',
			'set_cycle_data', 
			'set_units'	
        ]

	def update(self, current):
		'''
		Input:
	        current :: Current temperature
	    Output:
            cycle_ratio(u) :: Raw Cycle Ratio
	    '''
		return 0.0

	def set_target(self, set_point):
		'''
		Input:
	        set_point :: Temperature Target
	    '''
		self.set_point = set_point
		self.last_update = time.time()
	
	def get_config(self):
		return self.config
	
	def set_config(self, config):
		'''
		Input:
	        config :: Configuration Dictionary
	    '''
		self.config = config

	def set_cycle_data(self, cycle_data):
		'''
		Input:
	        cycle_data :: Cycle Data Dictionary
	    '''
		self.cycle_data = cycle_data

	def set_units(self, units):
		'''
		Input:
	        units :: Units Dictionary
	    '''
		self.units = units

	def get_control_period(self):
		'''
		Desired re-solve / actuation period in seconds. Return None to use the
		mode's CycleTime (legacy behavior). Controllers that run faster than the
		auger cycle (e.g. MPC) return a fixed period such as 1.0.
		'''
		return None

	def supported_functions(self):
		return self.function_list


def normalize_controller_output(output):
	'''
	Normalize a controller's update() return into (cycle_ratio, fan).

	Legacy controllers return a float cycle ratio; the MPC controller returns
	{'cycle_ratio': float, 'fan': {'duty': pct or None}}. fan is returned only
	when a duty is present.
	'''
	if isinstance(output, dict):
		ratio = float(output.get('cycle_ratio', 0.0))
		fan = output.get('fan')
		if isinstance(fan, dict) and fan.get('duty') is not None:
			return ratio, fan
		return ratio, None
	return float(output), None