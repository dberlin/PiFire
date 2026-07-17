"""Record current Valkey-backed accessor behavior as golden fixtures.

Run ONCE against the unmodified codebase with a live valkey-server:
    python -m tests.oracle.capture_oracle
Commit the resulting tests/oracle/fixtures/*.json. The SQLite rewrite is
asserted byte-for-byte against these (see tests/unit/datastore/test_datastore.py::test_oracle_*).
"""

import json
import os

from common import common as c
from common import datastore_accessors, defaults

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _dump(name, value):
    os.makedirs(FIX, exist_ok=True)
    with open(os.path.join(FIX, f"{name}.json"), "w") as fh:
        json.dump(value, fh, indent=2, sort_keys=True)


def scenario_control_merge():
    c.cmdsts.delete("control:general")
    c.cmdsts.delete("control:write")
    datastore_accessors.write_control(
        {"mode": "Stop", "nested": {"a": 1, "b": 2}}, c.WriteKind.OVERWRITE, origin="test"
    )
    datastore_accessors.write_control({"nested": {"b": 9, "c": 3}}, c.WriteKind.MERGE, origin="webapp")
    before = datastore_accessors.read_control()
    datastore_accessors.execute_control_writes()
    after = datastore_accessors.read_control()
    return {"before_execute": before, "after_execute": after}


def scenario_history_cap():
    c.cmdsts.delete("control:history")
    sample = {
        "probe_history": {"primary": {"Grill": 225}, "food": {"P1": 145}, "aux": {}},
        "primary_setpoint": 225,
        "notify_targets": {"Grill": 0},
    }
    for _ in range(5):
        datastore_accessors.write_history(sample, maxsizelines=3)
    return {"len": c.cmdsts.llen("control:history"), "items": datastore_accessors.read_history()}


def scenario_metrics_replace_last():
    c.cmdsts.delete("metrics:general")
    m = defaults.default_metrics()
    m["mode"] = "Startup"
    datastore_accessors.write_metrics(m, new_metric=True)
    m2 = defaults.default_metrics()
    m2["mode"] = "Hold"
    datastore_accessors.write_metrics(m2, new_metric=False)
    return {"last": datastore_accessors.read_metrics(), "all_len": len(datastore_accessors.read_metrics(all=True))}


def scenario_warnings():
    c.cmdsts.delete("warnings")
    datastore_accessors.write_warning("first")
    datastore_accessors.write_warning("second")
    return {"read1": datastore_accessors.read_warnings(), "read2_after_clear": datastore_accessors.read_warnings()}


def main():
    _dump("control_merge", scenario_control_merge())
    _dump("history_cap", scenario_history_cap())
    _dump("metrics_replace_last", scenario_metrics_replace_last())
    _dump("warnings", scenario_warnings())
    print("wrote fixtures to", FIX)


if __name__ == "__main__":
    main()
