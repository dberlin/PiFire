"""Pure safety decisions extracted from _work_cycle. No I/O."""

from enum import Enum


class SafetyVerdict(Enum):
	OK = 'ok'
	REIGNITE = 'reignite'
	ERROR = 'error'


def startup_temp_bounds(ptemp, safety_settings):
	bound = int(max(ptemp * 0.9, safety_settings['minstartuptemp']))
	return int(min(bound, safety_settings['maxstartuptemp']))


def evaluate_flameout(ptemp, startup_temp, reignite_retries):
	if ptemp >= startup_temp:
		return SafetyVerdict.OK
	return SafetyVerdict.ERROR if reignite_retries == 0 else SafetyVerdict.REIGNITE


def over_max_temp(ptemp, safety_settings):
	return ptemp > safety_settings['maxtemp']
