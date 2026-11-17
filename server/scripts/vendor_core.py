#!/usr/bin/env python3
"""Copy ../cli/viddown_core into server/viddown_core so server/ is a
self-contained deploy unit. Run before building a bundle, wheel or image.

    python scripts/vendor_core.py          # copy
    python scripts/vendor_core.py --check  # exit 1 if the vendored copy is stale
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
SRC = SERVER.parent / "cli" / "viddown_core"
DST = SERVER / "viddown_core"


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if not SRC.is_dir():
        if DST.is_dir():
            print(f"{SRC} not found; keeping existing vendored copy")
            return 0
        print(f"error: {SRC} not found", file=sys.stderr)
        return 1
    if args.check:
        ok = DST.is_dir() and tree_hash(SRC) == tree_hash(DST)
        print("vendored viddown_core is " + ("up to date" if ok else "STALE or missing"))
        return 0 if ok else 1
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (DST / "VENDORED").write_bytes(
        f"Copied from cli/viddown_core by scripts/vendor_core.py. Do not edit here.\nsha256={tree_hash(SRC)}\n".encode()
    )
    print(f"vendored {SRC} -> {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
