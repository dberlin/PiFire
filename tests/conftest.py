import os
import sys
import faulthandler
import signal

faulthandler.enable(all_threads=True)
faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True, chain=False)
# Ensure the repository root is importable so `grillplat`, `common`, etc. resolve.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest


@pytest.fixture(autouse=True)
def _neutralize_process_monitor(monkeypatch):
	"""Never run the real Process_Monitor's heartbeat loop in tests.

	Process_Monitor.__init__ starts a non-daemon thread running
	_heartbeat_check(), which loops forever (and shells out to `supervisorctl`
	on timeout). stop_monitor() only clears a flag -- it does NOT end the
	thread -- so those threads linger and block interpreter shutdown, hanging
	pytest. No-op'ing _heartbeat_check on the shared class makes the spawned
	thread return immediately, so every module that does
	`from common.process_mon import Process_Monitor` is covered at once
	(control.py's legacy path, the migrated ControlMode handlers, the
	ControlMode structural test, and any future user) -- no per-module name
	patching needed.
	"""
	from common.process_mon import Process_Monitor

	monkeypatch.setattr(Process_Monitor, '_heartbeat_check', lambda self: None)
