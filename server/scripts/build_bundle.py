#!/usr/bin/env python3
"""Build the server release tarball that deploy/update.sh installs.

    python scripts/build_bundle.py [--out dist]

Produces ``viddown-server-<version>.tar.gz`` and ``.sha256`` (sha256sum format).
The archive contains a single top-level ``app/`` directory with the server
package, the vendored ``viddown_core``, requirements and the deploy kit.
CI uploads both files to a GitHub Release tagged ``server-v<version>``.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
INCLUDE = [
    "viddown_server",
    "viddown_core",
    "deploy",
    "requirements.txt",
    "pyproject.toml",
    "README.md",
    ".env.example",
]
EXEC_SUFFIXES = (".sh",)


def version() -> str:
    text = (SERVER / "viddown_server" / "__init__.py").read_text()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("cannot find __version__")
    return m.group(1)


def files() -> list[Path]:
    out: list[Path] = []
    for name in INCLUDE:
        p = SERVER / name
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*")) if f.is_file() and "__pycache__" not in f.parts]
        elif p.is_file():
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SERVER / "dist"))
    args = ap.parse_args()

    subprocess.run([sys.executable, str(SERVER / "scripts" / "vendor_core.py")], check=True)
    ver = version()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = f"viddown-server-{ver}.tar.gz"

    # Deterministic archive: fixed mtimes/owners, sorted entries, LF line endings.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for f in files():
            rel = f.relative_to(SERVER).as_posix()
            data = f.read_bytes()
            if f.suffix in (".py", ".sh", ".service", ".timer", ".yaml", ".md", ".txt", ".toml", "") or rel.endswith(
                ("Caddyfile", ".env.example")
            ):
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(f"app/{rel}")
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o755 if f.suffix in EXEC_SUFFIXES else 0o644
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
        vinfo = tarfile.TarInfo("app/VERSION")
        vbytes = f"{ver}\n".encode()
        vinfo.size, vinfo.mtime, vinfo.mode = len(vbytes), 0, 0o644
        tar.addfile(vinfo, io.BytesIO(vbytes))

    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, mtime=0) as g:
        g.write(buf.getvalue())
    (out / name).write_bytes(gz.getvalue())
    digest = hashlib.sha256(gz.getvalue()).hexdigest()
    (out / f"{name}.sha256").write_bytes(f"{digest}  {name}\n".encode())  # LF even on Windows
    print(f"{out / name}\n{digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
