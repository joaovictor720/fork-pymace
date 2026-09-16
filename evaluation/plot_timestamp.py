"""Shared timestamp helpers for generated plot filenames."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


PLOT_TIMESTAMP = os.environ.get("MACE_PLOT_TIMESTAMP") or datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)


def timestamped_path(path: Path, timestamp: str = PLOT_TIMESTAMP) -> Path:
    """Append the plot generation timestamp before the file suffix."""
    return path.with_name(f"{path.stem}__{timestamp}{path.suffix}")
