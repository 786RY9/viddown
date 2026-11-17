"""Self-sizing admission control.

At boot the service reads the machine (RAM, cgroup limits, CPUs) and derives a
*budget*: how many extract workers, stream slots and transcode slots it can run
without starving the OS. Everything that costs real resources goes through an
:class:`AdmissionController`, which

* admits up to ``capacity`` jobs per class,
* parks a bounded number of waiters (FIFO) for a bounded time,
* answers everything else with :class:`Overloaded` (HTTP 503 + Retry-After),
* refuses new work while draining (SIGTERM) or under memory pressure, and
* can shed the newest jobs when memory becomes critical.

Per-IP fairness (token buckets, concurrent-stream caps) and the monthly egress
budget live here too, so every "should we do this work?" decision is in one file.

All file reads go through :class:`SystemProbe`, which takes a root directory so
the tests can fake ``/proc`` and ``/sys/fs/cgroup`` on any OS.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import math
import os
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal

from .config import Settings

log = logging.getLogger(__name__)

MB = 1024 * 1024
UNLIMITED = 1 << 62  # cgroup "max"

# ═════════════════════════════════════════════════════════════════════════════
# Reading the machine
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Psi:
    """Pressure-stall information (percent of wall time stalled, 10 s average)."""

    some_avg10: float
    full_avg10: float


def parse_psi(text: str) -> Psi | None:
    some = full = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kv = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
        try:
            if parts[0] == "some":
                some = float(kv["avg10"])
            elif parts[0] == "full":
                full = float(kv["avg10"])
        except (KeyError, ValueError):
            return None
    if some is None:
        return None
    return Psi(some, full if full is not None else 0.0)


def parse_meminfo(text: str) -> dict[str, int]:
    """/proc/meminfo -> {key: bytes}."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        if not fields:
            continue
        try:
            val = int(fields[0])
        except ValueError:
            continue
        unit = fields[1].lower() if len(fields) > 1 else ""
        out[key.strip()] = val * 1024 if unit == "kb" else val
    return out


class SystemProbe:
    """Reads /proc and cgroup v2 (with a v1 fallback). ``root`` is "/" in production."""

    def __init__(self, root: Path | str = "/", cpu_count: Callable[[], int | None] | None = None) -> None:
        self.root = Path(root)
        self._cpu_count = cpu_count

    # -- helpers ------------------------------------------------------------
    def _read(self, rel: str) -> str | None:
        try:
            return (self.root / rel.lstrip("/")).read_text()
        except OSError:
            return None

    def _cgroup_dirs(self) -> list[Path]:
        """This process's cgroup-v2 directory and all its ancestors (leaf first)."""
        text = self._read("/proc/self/cgroup") or ""
        rel = None
        for line in text.splitlines():
            # cgroup v2 unified hierarchy: "0::/system.slice/viddown.service"
            if line.startswith("0::"):
                rel = line[3:].strip()
                break
        base = self.root / "sys/fs/cgroup"
        if rel is None:
            return []
        dirs: list[Path] = []
        cur = Path(rel.lstrip("/"))
        while True:
            dirs.append(base / cur)
            if cur == Path(".") or str(cur) == "":
                break
            cur = cur.parent
        return dirs

    @staticmethod
    def _read_limit(path: Path) -> int | None:
        try:
            raw = path.read_text().strip()
        except OSError:
            return None
        if raw == "max" or not raw:
            return UNLIMITED
        try:
            return int(raw)
        except ValueError:
            return None

    # -- memory -------------------------------------------------------------
    def meminfo(self) -> dict[str, int]:
        return parse_meminfo(self._read("/proc/meminfo") or "")

    def mem_total_mb(self) -> int:
        return self.meminfo().get("MemTotal", 0) // MB

    def cgroup_memory_limit_mb(self) -> int | None:
        """Tightest of memory.high / memory.max along the cgroup path, or None."""
        limits: list[int] = []
        for d in self._cgroup_dirs():
            for name in ("memory.high", "memory.max"):
                v = self._read_limit(d / name)
                if v is not None and v < UNLIMITED:
                    limits.append(v)
        # cgroup v1 fallback.
        v1 = self._read_limit(self.root / "sys/fs/cgroup/memory/memory.limit_in_bytes")
        if v1 is not None and v1 < (1 << 50):  # v1 reports ~2^63 for "unlimited"
            limits.append(v1)
        return min(limits) // MB if limits else None

    def cgroup_memory_current_mb(self) -> int | None:
        dirs = self._cgroup_dirs()
        if not dirs:
            return None
        v = self._read_limit(dirs[0] / "memory.current")
        return v // MB if v is not None and v < UNLIMITED else None

    def mem_available_mb(self) -> int:
        """Host MemAvailable, tightened by our cgroup headroom when limited."""
        avail = self.meminfo().get("MemAvailable", 0) // MB
        limit = self.cgroup_memory_limit_mb()
        current = self.cgroup_memory_current_mb()
        if limit is not None and current is not None:
            avail = min(avail, max(0, limit - current))
        return avail

    def psi_memory(self) -> Psi | None:
        """Prefer our own cgroup's PSI (what we cause), else the system-wide one."""
        dirs = self._cgroup_dirs()
        if dirs:
            try:
                text: str | None = (dirs[0] / "memory.pressure").read_text()
            except OSError:
                text = None
            if text:
                psi = parse_psi(text)
                if psi:
                    return psi
        text = self._read("/proc/pressure/memory")
        return parse_psi(text) if text else None

    # -- cpu ----------------------------------------------------------------
    def cpu_count(self) -> int:
        if self._cpu_count is not None:
            n = self._cpu_count() or 1
        elif hasattr(os, "sched_getaffinity"):
            n = len(os.sched_getaffinity(0))
        else:
            n = os.cpu_count() or 1
        # cgroup v2 cpu.max: "<quota> <period>" or "max <period>".
        for d in self._cgroup_dirs():
            try:
                quota, period = (d / "cpu.max").read_text().split()
            except (OSError, ValueError):
                continue
            if quota != "max":
                n = min(n, max(1, math.ceil(int(quota) / int(period))))
        return max(1, n)


# ═════════════════════════════════════════════════════════════════════════════
# Budget
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Budget:
    mem_total_mb: int
    cgroup_limit_mb: int | None
    cpus: int
    job_budget_mb: int
    extract_workers: int
    stream_slots: int
    transcode_slots: int

    def describe(self) -> str:
        cg = f"{self.cgroup_limit_mb} MB" if self.cgroup_limit_mb else "none"
        return (
            f"RAM {self.mem_total_mb} MB, cgroup limit {cg}, {self.cpus} CPU -> "
            f"job budget {self.job_budget_mb} MB: extract={self.extract_workers} "
            f"stream={self.stream_slots} transcode={self.transcode_slots}"
        )


def compute_budget(mem_total_mb: int, cgroup_limit_mb: int | None, cpus: int, s: Settings) -> Budget:
    """
    budget = min(RAM - RESERVED_MB, cgroup limit) - BASE_MB

    RESERVED_MB protects the OS, Caddy and SSH, which live *outside* our cgroup,
    so it is subtracted from host RAM; the cgroup limit (systemd MemoryHigh /
    MemoryMax) already excludes them, so it caps the result as-is.

    extract   = cpus (pre-warmed yt-dlp workers; extraction is CPU-bound Python)
    transcode = cpus - 1 (one core always stays free for remuxing and the event loop)
    stream    = min(memory left // STREAM_MB, cpus * STREAMS_PER_CPU, NET_STREAM_CAP)
    """
    cpus = max(1, cpus)
    host_budget = mem_total_mb - s.reserved_mb
    budget = min(host_budget, cgroup_limit_mb) if cgroup_limit_mb else host_budget
    budget = max(0, budget - s.base_mb)

    extract = s.extract_workers or cpus
    transcode = s.transcode_slots if s.transcode_slots is not None else max(1, cpus - 1)
    remaining = budget - extract * s.extract_mb
    # Don't let transcodes eat the memory that streams need on tiny machines.
    while transcode > 0 and remaining - transcode * s.transcode_mb < s.stream_mb:
        transcode -= 1
    remaining -= transcode * s.transcode_mb

    if s.stream_slots is not None:
        stream = s.stream_slots
    else:
        stream = max(1, min(remaining // s.stream_mb, cpus * s.streams_per_cpu, s.net_stream_cap))

    return Budget(
        mem_total_mb=mem_total_mb,
        cgroup_limit_mb=cgroup_limit_mb,
        cpus=cpus,
        job_budget_mb=budget,
        extract_workers=max(1, extract),
        stream_slots=stream,
        transcode_slots=transcode,
    )


def budget_from_probe(probe: SystemProbe, s: Settings) -> Budget:
    total = probe.mem_total_mb()
    if total <= 0:  # non-Linux dev box: assume a small VM rather than crash
        total = 4096
    return compute_budget(total, probe.cgroup_memory_limit_mb(), probe.cpu_count(), s)


# ═════════════════════════════════════════════════════════════════════════════
# Errors surfaced as HTTP responses
# ═════════════════════════════════════════════════════════════════════════════


class Overloaded(Exception):
    """Server is busy / draining / under pressure -> 503 + Retry-After."""

    def __init__(self, reason: str, retry_after: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(1, int(retry_after))


class RateLimited(Exception):
    """This client is over its own limit -> 429 + Retry-After."""

    def __init__(self, reason: str, retry_after: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = max(1, math.ceil(retry_after))


# ═════════════════════════════════════════════════════════════════════════════
# Admission control
# ═════════════════════════════════════════════════════════════════════════════

JobClassName = Literal["extract", "stream", "transcode"]
_ticket_ids = itertools.count(1)


@dataclass(eq=False)
class Ticket:
    """One admitted job. Holders register cancel callbacks so the pressure guard
    (or a drain timeout) can stop the job from outside."""

    job_class: str
    started: float = field(default_factory=time.monotonic)
    id: int = field(default_factory=lambda: next(_ticket_ids))
    cancelled: str | None = None
    _callbacks: list[Callable[[str], None]] = field(default_factory=list)

    def on_cancel(self, cb: Callable[[str], None]) -> None:
        if self.cancelled:
            cb(self.cancelled)
        else:
            self._callbacks.append(cb)

    def cancel(self, reason: str) -> None:
        if self.cancelled:
            return
        self.cancelled = reason
        for cb in self._callbacks:
            try:
                cb(reason)
            except Exception:  # pragma: no cover - defensive
                log.exception("ticket cancel callback failed")


class JobClass:
    def __init__(self, name: str, capacity: int, max_waiters: int, wait_s: float, retry_after_s: int) -> None:
        self.name = name
        self.capacity = capacity
        self.max_waiters = max_waiters
        self.wait_s = wait_s
        self.retry_after_s = retry_after_s
        self.active: list[Ticket] = []  # admission order, oldest first
        self._waiters: deque[asyncio.Future[Ticket]] = deque()
        self.rejected = 0
        self.admitted = 0

    @property
    def waiting(self) -> int:
        return sum(1 for f in self._waiters if not f.done())

    def _admit(self) -> Ticket:
        t = Ticket(self.name)
        self.active.append(t)
        self.admitted += 1
        return t

    def _reject(self, reason: str) -> Overloaded:
        self.rejected += 1
        return Overloaded(f"{self.name}:{reason}", self.retry_after_s)

    async def acquire(self, gate: Callable[[], str | None]) -> Ticket:
        if (reason := gate()) is not None:
            raise self._reject(reason)
        if len(self.active) < self.capacity and not self.waiting:
            return self._admit()
        if self.waiting >= self.max_waiters:
            raise self._reject("queue_full")

        fut: asyncio.Future[Ticket] = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            async with asyncio.timeout(self.wait_s):
                return await fut
        except (TimeoutError, asyncio.CancelledError) as exc:
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                # The slot was handed to us just as we gave up: pass it on.
                self.release(fut.result(), gate)
            else:
                fut.cancel()
            if isinstance(exc, TimeoutError):
                raise self._reject("queue_timeout") from None
            raise
        finally:
            try:
                self._waiters.remove(fut)
            except ValueError:
                pass

    def release(self, ticket: Ticket, gate: Callable[[], str | None] | None = None) -> None:
        try:
            self.active.remove(ticket)
        except ValueError:
            return  # double release is harmless
        self.dispatch(gate)

    def dispatch(self, gate: Callable[[], str | None] | None = None) -> None:
        """Hand free slots to waiters (FIFO) unless the gate is closed."""
        while len(self.active) < self.capacity and self._waiters:
            if gate is not None and gate() is not None:
                return
            fut = self._waiters.popleft()
            if fut.done():
                continue
            fut.set_result(self._admit())

    def fail_waiters(self, reason: str) -> None:
        while self._waiters:
            fut = self._waiters.popleft()
            if not fut.done():
                fut.set_exception(self._reject(reason))

    def snapshot(self) -> dict[str, int]:
        return {
            "active": len(self.active),
            "capacity": self.capacity,
            "waiting": self.waiting,
            "admitted": self.admitted,
            "rejected": self.rejected,
        }


PressureState = Literal["ok", "pressure", "critical"]


class AdmissionController:
    RETRY_AFTER: ClassVar[dict[str, int]] = {"extract": 5, "stream": 30, "transcode": 60}

    def __init__(self, budget: Budget, s: Settings) -> None:
        self.budget = budget
        self.classes: dict[str, JobClass] = {
            name: JobClass(name, cap, s.max_waiters, s.queue_wait_s, self.RETRY_AFTER[name])
            for name, cap in (
                ("extract", budget.extract_workers),
                ("stream", budget.stream_slots),
                ("transcode", budget.transcode_slots),
            )
        }
        self.draining = False
        self.pressure: PressureState = "ok"

    def gate(self) -> str | None:
        """Reason new work must not start right now, or None."""
        if self.draining:
            return "draining"
        if self.pressure != "ok":
            return f"memory_{self.pressure}"
        return None

    async def acquire(self, job_class: JobClassName) -> Ticket:
        return await self.classes[job_class].acquire(self.gate)

    def release(self, ticket: Ticket) -> None:
        self.classes[ticket.job_class].release(ticket, self.gate)

    def slot(self, job_class: JobClassName) -> _Slot:
        return _Slot(self, job_class)

    def set_pressure(self, state: PressureState) -> None:
        prev, self.pressure = self.pressure, state
        if prev != state:
            log.warning("memory pressure: %s -> %s", prev, state)
        if state == "ok" and prev != "ok":
            for c in self.classes.values():
                c.dispatch(self.gate)

    def start_drain(self) -> None:
        if self.draining:
            return
        self.draining = True
        log.info("draining: refusing new jobs, %d active", self.active_count())
        for c in self.classes.values():
            c.fail_waiters("draining")

    def active_count(self) -> int:
        return sum(len(c.active) for c in self.classes.values())

    def shed_newest(self, order: Iterable[str] = ("transcode", "stream")) -> Ticket | None:
        """Cancel the newest job of the first class (in ``order``) that has one."""
        for name in order:
            live = [t for t in self.classes[name].active if not t.cancelled]
            if live:
                victim = max(live, key=lambda t: (t.started, t.id))
                log.warning("memory critical: cancelling newest %s job #%d", name, victim.id)
                victim.cancel("memory_critical")
                return victim
        return None

    def snapshot(self) -> dict[str, object]:
        return {
            "draining": self.draining,
            "pressure": self.pressure,
            "classes": {n: c.snapshot() for n, c in self.classes.items()},
        }


class _Slot:
    """``async with admission.slot("stream") as ticket:``"""

    def __init__(self, ac: AdmissionController, job_class: JobClassName) -> None:
        self.ac = ac
        self.job_class = job_class
        self.ticket: Ticket | None = None

    async def __aenter__(self) -> Ticket:
        self.ticket = await self.ac.acquire(self.job_class)
        return self.ticket

    async def __aexit__(self, *exc: object) -> None:
        if self.ticket is not None:
            self.ac.release(self.ticket)


# ═════════════════════════════════════════════════════════════════════════════
# Per-client fairness
# ═════════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """Token bucket per key. Keys live only in memory and are evicted LRU, so
    client IPs are never persisted."""

    def __init__(
        self,
        rate_per_s: float,
        burst: float,
        max_keys: int = 50_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate = rate_per_s
        self.burst = float(burst)
        self.max_keys = max_keys
        self.clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    @classmethod
    def per_minute(cls, n: float, burst: float | None = None, **kw: object) -> RateLimiter:
        return cls(n / 60.0, burst if burst is not None else max(1.0, n), **kw)  # type: ignore[arg-type]

    @classmethod
    def per_hour(cls, n: float, burst: float | None = None, **kw: object) -> RateLimiter:
        return cls(n / 3600.0, burst if burst is not None else max(1.0, n), **kw)  # type: ignore[arg-type]

    def hit(self, key: str, cost: float = 1.0) -> float:
        """Spend ``cost`` tokens. Returns 0.0 if allowed, else seconds until it would be."""
        now = self.clock()
        tokens, last = self._buckets.pop(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens >= cost:
            tokens -= cost
            wait = 0.0
        else:
            wait = (cost - tokens) / self.rate if self.rate > 0 else float("inf")
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self.max_keys:
            self._buckets.popitem(last=False)
        return wait

    def check(self, key: str, reason: str = "rate_limited") -> None:
        wait = self.hit(key)
        if wait > 0:
            raise RateLimited(reason, wait)

    def __len__(self) -> int:
        return len(self._buckets)


class KeyedConcurrency:
    """At most ``limit`` concurrent holders per key (e.g. 2 streams per IP)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._counts: dict[str, int] = {}

    def try_acquire(self, key: str) -> bool:
        n = self._counts.get(key, 0)
        if n >= self.limit:
            return False
        self._counts[key] = n + 1
        return True

    def release(self, key: str) -> None:
        n = self._counts.get(key, 0) - 1
        if n <= 0:
            self._counts.pop(key, None)
        else:
            self._counts[key] = n

    def count(self, key: str) -> int:
        return self._counts.get(key, 0)


# ═════════════════════════════════════════════════════════════════════════════
# Monthly egress budget
# ═════════════════════════════════════════════════════════════════════════════


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def seconds_until_next_month(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=UTC)
    return max(1, int((nxt - now).total_seconds()))


class EgressMeter:
    """Counts streamed bytes per UTC month and trips a cutoff before the free
    10 TB/month allowance is reached. Persisted through ``load``/``save``."""

    def __init__(
        self,
        cutoff_bytes: int,
        load: Callable[[str], int],
        save: Callable[[str, int], None],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.cutoff = cutoff_bytes
        self._load = load
        self._save = save
        self._now = now
        self.month = month_key(now())
        self.total = load(self.month)
        self._dirty = False

    def _roll(self) -> None:
        m = month_key(self._now())
        if m != self.month:
            self.flush()
            self.month, self.total = m, self._load(m)

    def add(self, n: int) -> None:
        self._roll()
        self.total += n
        self._dirty = True

    @property
    def exceeded(self) -> bool:
        self._roll()
        return self.total >= self.cutoff

    def flush(self) -> None:
        if self._dirty:
            self._save(self.month, self.total)
            self._dirty = False

    def snapshot(self) -> dict[str, object]:
        return {"month": self.month, "bytes": self.total, "cutoffBytes": self.cutoff, "exceeded": self.exceeded}


# ═════════════════════════════════════════════════════════════════════════════
# Memory-pressure guard
# ═════════════════════════════════════════════════════════════════════════════


def classify_pressure(psi: Psi | None, mem_available_mb: int, s: Settings) -> PressureState:
    if mem_available_mb and mem_available_mb < s.mem_available_critical_mb:
        return "critical"
    if psi is not None and psi.full_avg10 >= s.psi_full_avg10_critical:
        return "critical"
    if mem_available_mb and mem_available_mb < s.mem_available_low_mb:
        return "pressure"
    if psi is not None and psi.some_avg10 >= s.psi_some_avg10_pressure:
        return "pressure"
    return "ok"


class PressureGuard:
    """Every ``pressure_interval_s``: read PSI + MemAvailable, open/close the
    admission gate, and at "critical" cancel the newest transcode (then stream)
    job — one per tick, so the next reading reflects the relief."""

    def __init__(self, probe: SystemProbe, admission: AdmissionController, s: Settings) -> None:
        self.probe = probe
        self.admission = admission
        self.s = s
        self.enabled = probe.mem_total_mb() > 0  # no /proc (Windows dev) -> no-op
        self.last: dict[str, object] = {}

    def tick(self) -> PressureState:
        if not self.enabled:
            return "ok"
        psi = self.probe.psi_memory()
        avail = self.probe.mem_available_mb()
        state = classify_pressure(psi, avail, self.s)
        self.last = {"memAvailableMb": avail, "psi": psi.__dict__ if psi else None, "state": state}
        self.admission.set_pressure(state)
        if state == "critical":
            self.admission.shed_newest()
        return state

    async def run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # never let the guard die
                log.exception("pressure guard tick failed")
            await asyncio.sleep(self.s.pressure_interval_s)


async def periodic(interval: float, fn: Callable[[], Awaitable[None] | None]) -> None:
    """Run ``fn`` every ``interval`` seconds forever (cancel to stop)."""
    while True:
        await asyncio.sleep(interval)
        try:
            res = fn()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            log.exception("periodic task failed")
