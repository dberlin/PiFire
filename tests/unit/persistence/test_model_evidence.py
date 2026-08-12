"""Direct contracts for the model-evidence persistence boundary."""

from __future__ import annotations

import json
import subprocess
import sys


def test_model_evidence_persistence_imports_no_controller_policy_modules() -> None:
    script = """
import importlib
import json
import sys

before = set(sys.modules)
module = importlib.import_module("common.persistence.model_evidence")
forbidden = {
    "controller.mpc",
    "controller.mpc_snapshot",
    "controller.model_learning.activation",
}
loaded = sorted((set(sys.modules) - before) & forbidden)
print(json.dumps({"loaded": loaded, "module": module.__name__}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result == {
        "loaded": [],
        "module": "common.persistence.model_evidence",
    }
