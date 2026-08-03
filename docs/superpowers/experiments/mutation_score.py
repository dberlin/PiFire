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

NODES = [
    "tests/unit/mpc/test_model_promotion.py",
    "tests/unit/mpc/test_mpc_calibration.py",
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
        "M2 braking distance ignores the transport delay",
        PROMOTION,
        '    transport = float(params["theta"]) / n_delay if n_delay > 0 else 0.0',
        "    transport = 0.0",
    ),
    (
        "M3 braking distance ignores the firepot",
        PROMOTION,
        '    firepot = float(params["C_f"]) / h_fc',
        "    firepot = 0.0",
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
        "    stages = n_delay + 1",
        "    stages = 1",
    ),
    (
        "M11 chain stages read at the fastest instead of the slowest",
        PROMOTION,
        "    mean = stages * max(firepot, transport)",
        "    mean = stages * min(firepot, transport)",
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
        "M16 h_fc back in the free set",
        UPDATE,
        '_FREE = ("K_Q", "C_c", "theta")',
        '_FREE = ("K_Q", "C_c", "h_fc", "theta")',
    ),
    (
        "M17 h_amb freed, restoring the scale escape",
        UPDATE,
        '_FREE = ("K_Q", "C_c", "theta")',
        '_FREE = ("K_Q", "C_c", "h_fc", "h_amb", "theta")',
    ),
    (
        "M18 held parameters dropped instead of held",
        UPDATE,
        "    held = {k: float(init[k]) for k in _FIT_KEYS if k not in _FREE}",
        '    held = {"C_f": float(init["C_f"])}',
    ),
    (
        "M19 solve scale flattened to ones",
        UPDATE,
        "        scale.append(magnitude if magnitude > 0.0 and np.isfinite(magnitude) else 1.0)",
        "        scale.append(1.0)",
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
