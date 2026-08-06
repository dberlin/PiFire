"""Command line entry point for the deterministic linear-MPC bake-off."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .artifact import render_table
from .runner import ExperimentConfig, _checkpoint_path, default_output_path, run_experiment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the linear-MPC model bake-off.")
    parser.add_argument("--quick", action="store_true", help="run deterministic tiny smoke scenarios")
    parser.add_argument("--output", type=Path, help="bounded artifact manifest path (*.manifest.json)")
    parser.add_argument("--resume", action="store_true", help="resume from an existing checkpoint")
    parser.add_argument("--workers", type=int, help="spawned process workers (default: bounded auto)")
    parser.add_argument("--blas-threads", type=int, help="native numerical threads per worker (default: 1)")
    parser.add_argument(
        "--online-arx-compare",
        action="store_true",
        help="run the production-path online scheduled-ARX comparison artifact",
    )
    args = parser.parse_args(argv)
    if args.online_arx_compare:
        if args.resume or args.workers is not None or args.blas_threads is not None:
            parser.error("--online-arx-compare does not use bake-off resume or worker settings")
        from ..online_arx_compare import main as online_arx_compare_main

        command = ["--output", str(args.output)] if args.output is not None else []
        if args.quick:
            command.append("--tiny")
        return online_arx_compare_main(command)
    output = args.output or (
        default_output_path().with_name("_linear_mpc_bakeoff_quick.manifest.json")
        if args.quick
        else default_output_path()
    )
    if not output.name.endswith(".manifest.json"):
        parser.error("--output must end with .manifest.json; legacy .gz is load-only")
    checkpoint = _checkpoint_path(output)
    if args.resume and not checkpoint.exists():
        parser.error(f"checkpoint does not exist: {checkpoint}")
    config = ExperimentConfig.quick() if args.quick else ExperimentConfig()
    config = replace(
        config,
        output=output,
        workers=args.workers,
        blas_threads=args.blas_threads,
    )
    artifact = run_experiment(config, resume=args.resume)
    print(render_table(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
