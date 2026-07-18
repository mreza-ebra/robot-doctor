#!/usr/bin/env python3
"""Backward-compatible launcher for :mod:`robot_doctor.overviews`."""

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from robot_doctor.overviews import main


if __name__ == "__main__":
    raise SystemExit(main())
