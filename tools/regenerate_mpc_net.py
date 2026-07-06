#!/usr/bin/env python3
"""One-command wrapper: regenerate the MPC net-policy artifact(s).

Chains the two committed pipeline stages:
    sample_mpc.py --mode span [--enable-fan] -e N [-w W]   -> dataset .npz
    export_span_net.py --data <dataset> --out <artifact> [--enable-fan]

for a chosen fan mode (fan-off, fan-on, or both). See tools/README.md.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controller.mpc_net import net_path_for

BASE_ARTIFACT = './controller/mpc_policy_net.npz'
SPAN_DATA = './docs/superpowers/experiments/_ampc_data/pifire_span.npz'
SAMPLER = 'docs/superpowers/experiments/sample_mpc.py'
EXPORTER = 'docs/superpowers/experiments/export_span_net.py'

ACCEPTANCE_GATE = (
    'Acceptance gate: run scratchpad fan ablation; fan-on net should hit '
    '|bias|<=0.10C, RMS<=0.72C (5s control period, 110-288C).'
)


def sample_cmd(py, enable_fan, episodes, workers):
    cmd = [py, SAMPLER, '--mode', 'span', '-e', str(episodes)]
    if workers is not None:
        cmd += ['-w', str(workers)]
    if enable_fan:
        cmd.append('--enable-fan')
    return cmd


def export_cmd(py, enable_fan):
    data = net_path_for(SPAN_DATA, enable_fan)
    out = net_path_for(BASE_ARTIFACT, enable_fan)
    cmd = [py, EXPORTER, '--data', data, '--out', out]
    if enable_fan:
        cmd.append('--enable-fan')
    return cmd


def plan_commands(modes, episodes, workers, skip_sample, py=sys.executable):
    cmds = []
    for enable_fan in modes:
        if not skip_sample:
            cmds.append(sample_cmd(py, enable_fan, episodes, workers))
        cmds.append(export_cmd(py, enable_fan))
    return cmds


_MODE_MAP = {
    'fan-off': [False],
    'fan-on': [True],
    'both': [False, True],
}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Regenerate the MPC net-policy artifact(s).')
    ap.add_argument('--mode', choices=sorted(_MODE_MAP), default='both')
    ap.add_argument('--episodes', type=int, default=500)
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--skip-sample', action='store_true',
                     help='skip sampling; retrain+export from an existing dataset')
    ap.add_argument('--dry-run', action='store_true',
                     help='print the commands without executing them')
    args = ap.parse_args(argv)

    modes = _MODE_MAP[args.mode]
    cmds = plan_commands(modes, args.episodes, args.workers, args.skip_sample)

    if args.dry_run:
        for cmd in cmds:
            print(' '.join(cmd))
        return

    for cmd in cmds:
        subprocess.run(cmd, check=True)

    print(ACCEPTANCE_GATE)


if __name__ == '__main__':
    main()
