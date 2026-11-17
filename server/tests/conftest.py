from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from viddown_server.config import Settings
from viddown_server.resources import Psi, SystemProbe

GB = 1024


def make_settings(tmp_path: Path, **kw: Any) -> Settings:
    base = Settings(
        secret=b"test-secret-test-secret-32-bytes",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
    )
    return dataclasses.replace(base, **kw)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


class FakeProbe(SystemProbe):
    """A SystemProbe whose readings are plain attributes."""

    def __init__(
        self,
        total_mb: int = 12 * GB,
        cgroup_mb: int | None = None,
        cpus: int = 2,
        available_mb: int = 10 * GB,
        psi: Psi | None = Psi(0.0, 0.0),
    ) -> None:
        super().__init__("/nonexistent")
        self.total_mb, self.cgroup_mb, self.cpus = total_mb, cgroup_mb, cpus
        self.available_mb, self.psi = available_mb, psi

    def mem_total_mb(self) -> int:
        return self.total_mb

    def cgroup_memory_limit_mb(self) -> int | None:
        return self.cgroup_mb

    def cpu_count(self) -> int:
        return self.cpus

    def mem_available_mb(self) -> int:
        return self.available_mb

    def psi_memory(self) -> Psi | None:
        return self.psi


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
