#!/usr/bin/env python3
"""Stable entry point for the MPC mutation-score driver."""

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.mutation_score import main  # noqa: E402

if __name__ == "__main__":
    main()
