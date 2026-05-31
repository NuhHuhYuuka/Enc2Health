#!/usr/bin/env python3
"""Compatibility wrapper for running the T10 exporter from benchmark/."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from monitoring.exporter import main  # noqa: E402


if __name__ == "__main__":
    main()
