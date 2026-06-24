import os
import numpy as np
from controller.mpc import Controller, _DEFAULTS

CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def test_logging_writes_calibration_rows(tmp_path):
    log = tmp_path / "cal.csv"
    cfg = dict(_DEFAULTS); cfg.update(log_data=True, log_path=str(log))
    c = Controller(cfg, 'C', dict(CYCLE))
    c.set_target(110.0)
    for _ in range(5):
        c.update(100.0)
    lines = log.read_text().strip().splitlines()
    assert lines[0] == 'time_s,temp_c,Q'        # header for update_mpc.py
    assert len(lines) == 6                       # header + 5 control steps
    for row in lines[1:]:
        t, temp, Q = row.split(',')
        float(t)
        assert abs(float(temp) - 100.0) < 1e-6   # logs the internal Celsius temp
        assert 5.0 <= float(Q) <= 100.0          # firing-rate demand within bounds


def test_logging_disabled_by_default(tmp_path):
    log = tmp_path / "cal.csv"
    cfg = dict(_DEFAULTS); cfg.update(log_path=str(log))   # log_data defaults False
    c = Controller(cfg, 'C', dict(CYCLE))
    c.set_target(110.0)
    c.update(100.0)
    assert not os.path.exists(log)
