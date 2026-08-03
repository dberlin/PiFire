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

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
PROMOTION = ROOT / "controller" / "model_promotion.py"
UPDATE = ROOT / "controller" / "update_mpc.py"
MPC = ROOT / "controller" / "mpc.py"
MODEL = ROOT / "controller" / "mpc_model.py"
NET = ROOT / "controller" / "mpc_net.py"

NODES = [
    "tests/unit/mpc/test_model_promotion.py",
    "tests/unit/mpc/test_mpc_calibration.py",
    "tests/unit/mpc/test_mpc_controller.py",
    "tests/unit/mpc/test_mpc_model.py",
    "tests/unit/mpc/test_mpc_model_snapshot.py",
    "tests/unit/mpc/test_mpc_net.py",
    "tests/unit/mpc/test_mpc_ekf.py",
]

#: (label, file, old, new). Each `old` must appear exactly once.
MUTATIONS = [
    (
        "M1 horizon sized from the time constant again",
        PROMOTION,
        "    brake = longest_braking_distance(candidate)",
        '    brake = float(candidate["C_c"]) / float(candidate["h_amb"])',
    ),
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
        "M6 range read at the hot end only",
        PROMOTION,
        "    refs = [t for t in (T_FLOOR_C, T_HAZARD_C) if t > t_amb]",
        "    refs = [t for t in (T_HAZARD_C,) if t > t_amb]",
    ),
    (
        "M7 range takes the shortest brake",
        PROMOTION,
        "    return max(braking_distance(params, t, q_full=q_full) for t in refs)",
        "    return min(braking_distance(params, t, q_full=q_full) for t in refs)",
    ),
    (
        "M8 a reference below ambient is kept",
        PROMOTION,
        "    refs = [t for t in (T_FLOOR_C, T_HAZARD_C) if t > t_amb]",
        "    refs = [T_FLOOR_C, T_HAZARD_C]",
    ),
    (
        "M9 full fire read as one unit of demand",
        PROMOTION,
        "Q_FULL_FIRE = 100.0",
        "Q_FULL_FIRE = 1.0",
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
        PROMOTION,
        "        return h_amb * (t_c - t_amb) + sigma * ((t_c + _KELVIN) ** 4 - (t_amb + _KELVIN) ** 4)",
        "        return h_amb * (t_c - t_amb)",
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
        "M20 running warning back on the time constant",
        MPC,
        "    brake = longest_braking_distance(cfg)",
        '    brake = float(cfg["C_c"]) / float(cfg["h_amb"])',
    ),
    (
        "M21 running warning never fires",
        MPC,
        "    if math.isfinite(brake) and horizon < brake:",
        "    if False:",
    ),
    (
        "M22 running warning always fires",
        MPC,
        "    if math.isfinite(brake) and horizon < brake:",
        "    if True:",
    ),
    (
        "M23 an endless brake passes with no demand attached",
        PROMOTION,
        '        return Verdict(False, "the model does not predict the chamber ever stops rising after a fuel cut")',
        "        return Verdict(True, 'unbounded brake', None)",
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
        "            dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
        "            dT_c = (heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
    ),
    (
        "M26 the simulator drops the radiative loss",
        MODEL,
        "            dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb) - _rad_loss(T_c, T_amb, sigma)) / C_c",
        "            dT_c = (K_Q * heat_in - h_amb * (T_c - T_amb)) / C_c",
    ),
    (
        "M27 the simulator ignores the transport chain",
        MODEL,
        "                heat_in = lags[-1]",
        "                heat_in = u",
    ),
    (
        "M28 the simulator stops sub-stepping",
        MODEL,
        "        steps = max(1, int(np.ceil(span / max_dt)))",
        "        steps = 1",
    ),
    (
        "M29 the do-mpc model keeps a firepot state the estimators do not",
        MODEL,
        '    T_c = model.set_variable("_x", "T_c")\n    d = model.set_variable("_x", "d")\n    Q = model.set_variable("_u", "Q")',
        '    model.set_variable("_x", "T_f")\n'
        '    T_c = model.set_variable("_x", "T_c")\n'
        '    d = model.set_variable("_x", "d")\n'
        '    Q = model.set_variable("_u", "Q")',
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
        "M33 the snapshot schema never moved off the two-lump version",
        MPC,
        "    _MODEL_SCHEMA = 2",
        "    _MODEL_SCHEMA = 1",
    ),
    (
        "M34 restore_model takes a snapshot of any version",
        MPC,
        "        if version != self._MODEL_SCHEMA:",
        "        if False:",
    ),
    (
        "M35 restore_model refuses an old snapshot without saying so",
        MPC,
        '            print(\n'
        '                f"[mpc] discarding a version {version!r} model snapshot: this controller "\n'
        '                f"stores version {self._MODEL_SCHEMA}, the single-lump model. The next "\n'
        '                "cook refits from scratch."\n'
        "            )\n"
        "            return False",
        "            return False",
    ),
    # ---- the net artifact guard -------------------------------------------
    (
        "M36 the net width check is gone, so a stale artifact loads",
        NET,
        "        if self.input_dim != self.expected_input_dim(cfg):\n            return False",
        "        if False:\n            return False",
    ),
    (
        "M37 the net reads the disturbance from the two-lump slot",
        NET,
        "        d = x[self.n_delay + 1]",
        "        d = x[self.n_delay + 2]",
    ),
    (
        "M38 the expected width still counts a firepot slot",
        NET,
        '        return int(cfg.get("n_delay", self.n_delay)) + 4',
        '        return int(cfg.get("n_delay", self.n_delay)) + 5',
    ),
    # ---- retired settings keys --------------------------------------------
    (
        "M39 a settings record's retired keys are ignored in silence",
        MPC,
        "    retired = [k for k in _RETIRED_PARAMS if k in cfg]",
        "    retired = []",
    ),
    (
        "M40 the retired-key message fires for every record",
        MPC,
        "    retired = [k for k in _RETIRED_PARAMS if k in cfg]",
        "    retired = list(_RETIRED_PARAMS)",
    ),
]


def _score():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *NODES, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
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
