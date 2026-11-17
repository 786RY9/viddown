"""The server must run the same shared logic as the CLI (one source of truth)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1]


def _load_vendor_script():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("vendor_core", SERVER / "scripts" / "vendor_core.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vendored_copy_matches_cli() -> None:
    vc = _load_vendor_script()
    if not vc.SRC.is_dir():
        pytest.skip("not in a monorepo checkout (deploy bundle)")
    if not vc.DST.is_dir():
        pytest.skip("not vendored yet (python scripts/vendor_core.py)")
    assert vc.tree_hash(vc.SRC) == vc.tree_hash(vc.DST), "run: python scripts/vendor_core.py"


def test_shared_helpers_are_importable() -> None:
    from viddown_server._core import filter_video_formats, parse_time, to_seconds

    assert parse_time("5m23s") == "00:05:23" and to_seconds("00:05:23") == 323
    assert filter_video_formats([{"vcodec": "avc1", "height": 720, "format_id": "1"}])[0]["format_id"] == "1"
    assert "viddown_core" in sys.modules
