#!/usr/bin/env python3

"""
*****************************************
 PiFire Mutation Control for the Horizon Requirement
*****************************************

 Every assertion added or changed for the braking-distance horizon requirement
 has to be shown capable of failing. This applies each mutation to a source
 file in Python, compile-checks the result, runs the named test nodes against
 it, and restores the original -- so a test that passes under a broken
 implementation is reported rather than assumed absent.

 A mutation the surrounding code overrides proves nothing, so the report
 carries the pass/fail split per mutation and a mutation that kills nothing is
 as much a finding as one that kills everything.

 Usage: python -m docs.superpowers.experiments.mutation_score
*****************************************
"""

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMOTION = ROOT / "controller" / "model_promotion.py"
UPDATE = ROOT / "controller" / "update_mpc.py"
GREY_RUNTIME = ROOT / "controller" / "model_learning" / "grey_runtime.py"
MPC_CONFIG = ROOT / "controller" / "mpc_config.py"
MPC_CORE = ROOT / "controller" / "mpc_core.py"
MODEL = ROOT / "controller" / "mpc_model.py"

NODES = [
    "tests/unit/mpc/test_model_promotion.py",
    "tests/unit/mpc/test_mpc_calibration.py",
    "tests/unit/mpc/test_mpc_controller.py",
    "tests/unit/mpc/test_mpc_model.py",
    "tests/unit/mpc/test_mpc_model_snapshot.py",
    "tests/unit/mpc/test_mpc_ekf.py",
]

#: (label, file, old, new). Each `old` must appear exactly once.
MUTATIONS = [
    (
        "M2 braking distance ignores the transport delay's length",
        PROMOTION,
        '    mean = float(params["theta"]) if stages > 0 else 0.0',
        "    mean = 1.0 if stages > 0 else 0.0",
    ),
    (
        "M3 braking distance ignores the chain outright",
        PROMOTION,
        '    mean = float(params["theta"]) if stages > 0 else 0.0',
        "    mean = 0.0",
    ),
    (
        "M4 braking distance ignores the chamber's loss",
        PROMOTION,
        "    ratio = loss / flux",
        "    ratio = 0.5",
    ),
    (
        "M5 braking distance ignores the radiative loss",
        PROMOTION,
        '    loss = float(params["h_amb"]) * (t_ref_c - t_amb) + float(params["sigma"]) * (\n'
        "        (t_ref_c + _KELVIN) ** 4 - (t_amb + _KELVIN) ** 4\n"
        "    )",
        '    loss = float(params["h_amb"]) * (t_ref_c - t_amb)',
    ),
    (
        "M9 full fire reduced below normalized maximum demand",
        PROMOTION,
        "NORMALIZED_FULL_LOAD = 1.0",
        "NORMALIZED_FULL_LOAD = 0.1",
    ),
    (
        "M10 chain read as a single stage",
        PROMOTION,
        '    stages = max(int(params["n_delay"]), 0)',
        "    stages = 1",
    ),
    (
        "M11 chain padded by the stage the firepot used to add",
        PROMOTION,
        '    stages = max(int(params["n_delay"]), 0)',
        '    stages = max(int(params["n_delay"]), 0) + 1',
    ),
    (
        "M12 survival drops its polynomial tail",
        PROMOTION,
        "    return math.exp(-x) * total",
        "    return math.exp(-x)",
    ),
    (
        "M13 a candidate that cannot hold temperature still demands a horizon",
        PROMOTION,
        "    if ratio >= 1.0:\n"
        "        # Full fire cannot even hold this temperature, so the chamber is not\n"
        "        # rising and there is nothing to brake.\n"
        "        return 0.0",
        "    if ratio >= 1.0:\n        return 1e6",
    ),
    (
        "M14 steady state ignores the radiative loss",
        MODEL,
        "    return (h_amb * (t_set - t_amb) + _rad_loss(t_set, t_amb, sigma) - d) / k_q",
        "    return (h_amb * (t_set - t_amb) - d) / k_q",
    ),
    (
        "M15 steady state balanced against a tenth of full fire",
        PROMOTION,
        '    target = float(params["K_Q"]) * float(q_full)',
        '    target = float(params["K_Q"]) * float(q_full) * 0.1',
    ),
    (
        "M16 the dead time dropped from the free set",
        UPDATE,
        '_FREE = ("K_Q", "C_c", "theta")',
        '_FREE = ("K_Q", "C_c")',
    ),
    (
        "M17 h_amb freed, restoring the scale escape",
        UPDATE,
        '_FREE = ("K_Q", "C_c", "theta")',
        '_FREE = ("K_Q", "C_c", "h_amb", "theta")',
    ),
    (
        "M18 held parameters dropped instead of held",
        UPDATE,
        "    held = {k: float(init[k]) for k in _FIT_KEYS if k not in _FREE}",
        '    held = {"h_amb": float(init["h_amb"]), "sigma": 0.0}',
    ),
    (
        "M19 the solve back in raw parameters instead of their logarithms",
        UPDATE,
        "        params.update(zip(_FREE, (math.exp(v) for v in z)))",
        "        params.update(zip(_FREE, (float(v) for v in z)))",
    ),
    (
        "M19b the log floor returns the raw value for a degenerate start",
        UPDATE,
        "    return math.log(value) if value > 0.0 and math.isfinite(value) else floor",
        "    return math.log(value) if value > 0.0 and math.isfinite(value) else value",
    ),
    (
        "M24 steady-state search ceiling collapsed to nothing",
        PROMOTION,
        "_STEADY_STATE_CEILING_C = 100000.0",
        "_STEADY_STATE_CEILING_C = 1.0",
    ),
    # ---- the single-lump model and the state vector it produces ------------
    (
        "M25 the simulator drops the firing-rate gain",
        MODEL,
        "            dT_c = (K_Q * heat[k] - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
        "            dT_c = (heat[k] - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
    ),
    (
        "M26 the simulator drops the radiative loss",
        MODEL,
        "            dT_c = (K_Q * heat[k] - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
        "            dT_c = (K_Q * heat[k] - h_amb * (T_c - T_amb)) / C_c",
    ),
    (
        "M27 the simulator ignores the transport chain",
        MODEL,
        "            heat = (load + coef @ dev[::-1]).tolist()",
        "            heat = [load] * steps",
    ),
    (
        "M28 the simulator stops sub-stepping",
        MODEL,
        "        steps = max(1, int(np.ceil(span / max_dt)))",
        "        steps = 1",
    ),
    (
        "M30 the Kalman state vector keeps the slot the firepot used to hold",
        MODEL,
        "        n = n_delay + 2\n        iTc, iD = n_delay, n_delay + 1\n\n        A = np.zeros((n, n))\n"
        "        if n_delay > 0:\n            tau_d = theta / n_delay\n            for i in range(n_delay):\n"
        "                A[i, i] = -1.0 / tau_d\n                if i > 0:\n                    A[i, i - 1] = 1.0 / tau_d\n"
        "            A[iTc, n_delay - 1] = K_Q / C_c  # last lag feeds the chamber (scaled by K_Q)",
        "        n = n_delay + 3\n        iTc, iD = n_delay + 1, n_delay + 2\n\n        A = np.zeros((n, n))\n"
        "        if n_delay > 0:\n            tau_d = theta / n_delay\n            for i in range(n_delay):\n"
        "                A[i, i] = -1.0 / tau_d\n                if i > 0:\n                    A[i, i - 1] = 1.0 / tau_d\n"
        "            A[iTc, n_delay - 1] = K_Q / C_c  # last lag feeds the chamber (scaled by K_Q)",
    ),
    (
        "M31 the Kalman default state seeds the wrong slot with ambient",
        MODEL,
        "            x0 = [0.0] * n_delay + [T_amb, 0.0]\n        self.x = np.array(x0, dtype=float)\n"
        "        self.P = np.eye(n) * 5.0\n        self.n = n",
        "            x0 = [0.0] * n_delay + [0.0, T_amb]\n        self.x = np.array(x0, dtype=float)\n"
        "        self.P = np.eye(n) * 5.0\n        self.n = n",
    ),
    (
        "M32 the EKF measures a lag state instead of the chamber",
        MODEL,
        "        self.A_lin, self.Baug = A, Baug\n        self.n, self.iTc = n, iTc",
        "        self.A_lin, self.Baug = A, Baug\n        self.n, self.iTc = n, 0",
    ),
    # ---- the persisted snapshot -------------------------------------------
    (
        "M33 the snapshot schema never moves with the model",
        MODEL,
        "MODEL_SCHEMA = 4",
        "MODEL_SCHEMA = 3",
    ),
    (
        "M34 restore_model takes a snapshot of any version",
        GREY_RUNTIME,
        '        if not isinstance(snapshot, dict) or snapshot.get("version") != self.MODEL_SCHEMA:',
        "        if False:",
    ),
    (
        "M35 restore_model refuses an old snapshot without saying so",
        GREY_RUNTIME,
        "            self._logger.warning(\n"
        '                f"[mpc] discarding a version {version!r} model snapshot: runtime restore "\n'
        '                f"accepts only grey schema {self.MODEL_SCHEMA}; version 3 is migration input only."\n'
        "            )\n"
        "            return False",
        "            return False",
    ),
    # ---- retired settings keys --------------------------------------------
    (
        "M39 a settings record's retired keys are ignored in silence",
        MPC_CONFIG,
        "    retired = [key for key in RETIRED_PARAMETER_KEYS if key in config]",
        "    retired = []",
    ),
    (
        "M40 the retired-key message fires for every record",
        MPC_CONFIG,
        "    retired = [key for key in RETIRED_PARAMETER_KEYS if key in config]",
        "    retired = list(RETIRED_PARAMETER_KEYS)",
    ),
    (
        "M43 the snapshot counts its own schema instead of sharing the model's",
        GREY_RUNTIME,
        "    MODEL_SCHEMA = MODEL_SCHEMA",
        "    MODEL_SCHEMA = 1",
    ),
    # ---- the frozen output ------------------------------------------------
    (
        "M44 a failing policy freezes the output in silence again",
        MPC_CORE,
        "            if failure_count == 1 or failure_count in (10, 60) or failure_count % 300 == 0:",
        "            if False:",
    ),
    (
        "M45 the failure report fires on every step, burying the first",
        MPC_CORE,
        "            if failure_count == 1 or failure_count in (10, 60) or failure_count % 300 == 0:",
        "            if True:",
    ),
    (
        "M46 the frozen-output counter never advances",
        MPC_CORE,
        "            self._consecutive_policy_failures += 1",
        "            self._consecutive_policy_failures += 0",
    ),
    (
        "M47 the counter never clears, so a healthy policy reads as frozen",
        MPC_CORE,
        "            if self._consecutive_policy_failures:\n"
        "                self._logger.info(\n"
        '                    f"[mpc] native solver recovered after {self._consecutive_policy_failures} failed step(s)"\n'
        "                )\n"
        "            self._consecutive_policy_failures = 0",
        "            if self._consecutive_policy_failures:\n"
        "                self._logger.info(\n"
        '                    f"[mpc] native solver recovered after {self._consecutive_policy_failures} failed step(s)"\n'
        "                )",
    ),
    # ---- the deadtime chain length ----------------------------------------
    (
        "M48 the deadtime chain back to the smeared n_delay=4",
        MPC_CONFIG,
        '    "n_delay": 8,',
        '    "n_delay": 4,',
    ),
]


def _score():
    # PYTHONDONTWRITEBYTECODE stops the child caching bytecode for the sources
    # being mutated. CPython validates a .pyc on the source's (mtime, size) at
    # one-second granularity, so a mutation and its restore that are the same
    # length and land in the same second leave the MUTATED bytecode looking
    # current -- the file reads as restored while the import is not.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *NODES, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    tail = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line]
    return tail[-1] if tail else f"NO SUMMARY ({proc.returncode})"


def main():
    originals = {p: p.read_text() for p in {m[1] for m in MUTATIONS}}
    print(f"baseline: {_score()}\n")
    try:
        for label, path, old, new in MUTATIONS:
            text = originals[path]
            if text.count(old) != 1:
                print(f"{label:60s} SKIPPED -- anchor appears {text.count(old)} times")
                continue
            mutated = text.replace(old, new)
            try:
                compile(mutated, str(path), "exec")
            except SyntaxError as exc:
                print(f"{label:60s} SKIPPED -- mutation does not compile: {exc}")
                continue
            path.write_text(mutated)
            try:
                print(f"{label:60s} {_score()}")
            finally:
                path.write_text(text)
    finally:
        for p, text in originals.items():
            p.write_text(text)


if __name__ == "__main__":
    main()
