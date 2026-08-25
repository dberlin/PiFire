#!/usr/bin/env python3
"""Stable regeneration entry point for promotion-signal evidence."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.experiments.promotion_signal import main  # noqa: E402

if __name__ == "__main__":
    main()
