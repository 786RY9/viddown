"""SQLite persistence: contact messages, anonymous counters, egress totals.

One connection guarded by a lock; callers use the ``a*`` coroutine wrappers so
SQLite never blocks the event loop. No IP addresses are ever written.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS contact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  INTEGER NOT NULL,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    message     TEXT NOT NULL,
    source      TEXT
);
CREATE TABLE IF NOT EXISTS counters (
    day         TEXT NOT NULL,
    event       TEXT NOT NULL,
    platform    TEXT NOT NULL,
    app_version TEXT NOT NULL,
    n           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, event, platform, app_version)
);
CREATE TABLE IF NOT EXISTS egress (
    month       TEXT PRIMARY KEY,
    bytes       INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- contact -------------------------------------------------------------
    def add_contact(self, name: str, email: str, message: str, source: str | None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO contact (created_at, name, email, message, source) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), name, email, message, source),
            )
            return int(cur.lastrowid or 0)

    # -- counters ------------------------------------------------------------
    def bump(self, event: str, platform: str, app_version: str, day: str | None = None) -> None:
        day = day or datetime.now(UTC).strftime("%Y-%m-%d")
        with self._lock:
            self._db.execute(
                "INSERT INTO counters (day, event, platform, app_version, n) VALUES (?, ?, ?, ?, 1) "
                "ON CONFLICT (day, event, platform, app_version) DO UPDATE SET n = n + 1",
                (day, event, platform, app_version),
            )

    def stats(self, days: int = 30) -> dict[str, Any]:
        since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._lock:
            totals = dict(self._db.execute("SELECT event, SUM(n) FROM counters GROUP BY event").fetchall())
            recent = dict(
                self._db.execute(
                    "SELECT event, SUM(n) FROM counters WHERE day >= ? GROUP BY event", (since,)
                ).fetchall()
            )
            platforms = dict(
                self._db.execute(
                    "SELECT platform, SUM(n) FROM counters WHERE event = 'install' GROUP BY platform"
                ).fetchall()
            )
        return {"totals": totals, f"last{days}d": recent, "installsByPlatform": platforms}

    # -- egress --------------------------------------------------------------
    def load_egress(self, month: str) -> int:
        with self._lock:
            row = self._db.execute("SELECT bytes FROM egress WHERE month = ?", (month,)).fetchone()
        return int(row[0]) if row else 0

    def save_egress(self, month: str, total: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO egress (month, bytes) VALUES (?, ?) "
                "ON CONFLICT (month) DO UPDATE SET bytes = excluded.bytes",
                (month, total),
            )

    # -- async wrappers ------------------------------------------------------
    async def aadd_contact(self, *a: Any) -> int:
        return await asyncio.to_thread(self.add_contact, *a)

    async def abump(self, *a: Any) -> None:
        await asyncio.to_thread(self.bump, *a)

    async def astats(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.stats)
