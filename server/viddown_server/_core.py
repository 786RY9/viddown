"""Import the shared ``viddown_core`` package.

In a release bundle ``viddown_core`` is vendored next to ``viddown_server``
(see scripts/vendor_core.py). In a git checkout we fall back to ``../cli`` so
there is exactly one source of truth for the shared logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import viddown_core
except ImportError:  # dev checkout: server/ sits next to cli/
    _cli = Path(__file__).resolve().parents[2] / "cli"
    if (_cli / "viddown_core").is_dir():
        sys.path.insert(0, str(_cli))
    import viddown_core  # noqa: F401

from viddown_core import filter_video_formats, parse_time, to_seconds

__all__ = ["filter_video_formats", "parse_time", "to_seconds"]
