"""Budget sizing, admission/queueing, pressure guard, rate limits, egress."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import GB, FakeProbe, make_settings, write_tree

from viddown_server.config import Settings
from viddown_server.resources import (
    AdmissionController,
    Budget,
    EgressMeter,
    KeyedConcurrency,
    Overloaded,
    PressureGuard,
    Psi,
    RateLimited,
    RateLimiter,
    SystemProbe,
    budget_from_probe,
    classify_pressure,
    compute_budget,
    month_key,
    parse_meminfo,
    parse_psi,
    seconds_until_next_month,
)

MEMINFO_12G = """MemTotal:       11937644 kB
MemFree:         9000000 kB
MemAvailable:   10485760 kB
Buffers:           12345 kB
"""

# ── /proc + cgroup parsing ──────────────────────────────────────────────────


def test_parse_meminfo() -> None:
    m = parse_meminfo(MEMINFO_12G)
    assert m["MemTotal"] == 11937644 * 1024
    assert m["MemAvailable"] == 10 * GB * 1024 * 1024


def test_parse_psi() -> None:
    text = "some avg10=12.50 avg60=3.00 avg300=1.00 total=123\nfull avg10=6.25 avg60=1.00 avg300=0.50 total=45\n"
    assert parse_psi(text) == Psi(12.5, 6.25)
    assert parse_psi("garbage") is None


def test_system_probe_reads_fake_proc_and_cgroup_v2(tmp_path: Path) -> None:
    cg = "sys/fs/cgroup/system.slice/viddown.service"
    write_tree(
        tmp_path,
        {
            "proc/meminfo": MEMINFO_12G,
            "proc/self/cgroup": "0::/system.slice/viddown.service\n",
            "proc/pressure/memory": "some avg10=1.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n",
            f"{cg}/memory.high": str(9 * GB * 1024 * 1024),
            f"{cg}/memory.max": str(10 * GB * 1024 * 1024),
            f"{cg}/memory.current": str(2 * GB * 1024 * 1024),
            f"{cg}/memory.pressure": "some avg10=2.00 avg60=0 avg300=0 total=0\nfull avg10=0.50 avg60=0 avg300=0 total=0\n",
            f"{cg}/cpu.max": "150000 100000",
            "sys/fs/cgroup/system.slice/memory.max": "max",
        },
    )
    p = SystemProbe(tmp_path, cpu_count=lambda: 4)
    assert p.mem_total_mb() == 11937644 // 1024
    assert p.cgroup_memory_limit_mb() == 9 * GB  # tightest of high/max
    # MemAvailable (10 GB) is tightened by cgroup headroom (9 GB - 2 GB).
    assert p.mem_available_mb() == 7 * GB
    assert p.cpu_count() == 2  # ceil(1.5) from cpu.max beats the 4 host CPUs
    assert p.psi_memory() == Psi(2.0, 0.5)  # our cgroup's PSI preferred


def test_system_probe_without_cgroup(tmp_path: Path) -> None:
    write_tree(tmp_path, {"proc/meminfo": MEMINFO_12G})
    p = SystemProbe(tmp_path, cpu_count=lambda: 4)
    assert p.cgroup_memory_limit_mb() is None
    assert p.cpu_count() == 4
    assert p.psi_memory() is None
    assert p.mem_available_mb() == 10 * GB


# ── budget ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("total_gb", "cpus", "expect"),
    [
        # 2 OCPU / 12 GB: CPU-capped streams (2 * 15), 1 transcode slot.
        (12, 2, (2, 30, 1)),
        # 4 OCPU / 24 GB: network-capped streams (40), 3 transcode slots.
        (24, 4, (4, 40, 3)),
    ],
)
def test_budget_oracle_shapes(settings: Settings, total_gb: int, cpus: int, expect: tuple[int, int, int]) -> None:
    # MemTotal on a VM is a bit under the nominal size; systemd MemoryHigh=80%.
    total = int(total_gb * GB * 0.97)
    b = compute_budget(total, int(total * 0.80), cpus, settings)
    assert (b.extract_workers, b.stream_slots, b.transcode_slots) == expect
    assert b.job_budget_mb == min(total - 1500, int(total * 0.8)) - 200


def test_budget_is_memory_capped_on_small_machine(settings: Settings) -> None:
    # 1 GB, 1 CPU (AMD micro): budget is tiny; still at least one stream slot.
    b = compute_budget(1 * GB, None, 1, dataclasses.replace(settings, reserved_mb=400))
    assert b.extract_workers == 1
    assert b.stream_slots >= 1
    assert b.transcode_slots == 0  # no memory for a transcode alongside a stream


def test_budget_respects_reserved_and_overrides(settings: Settings) -> None:
    s = dataclasses.replace(settings, reserved_mb=6000, stream_slots=7, extract_workers=1)
    b = compute_budget(8 * GB, None, 4, s)
    assert b.job_budget_mb == 8 * GB - 6000 - 200
    assert (b.extract_workers, b.stream_slots) == (1, 7)


def test_budget_from_probe(settings: Settings) -> None:
    b = budget_from_probe(FakeProbe(total_mb=24 * GB, cpus=4), settings)
    assert b.cpus == 4 and b.stream_slots == 40


# ── admission ───────────────────────────────────────────────────────────────


def ac_with(settings: Settings, stream: int = 2, waiters: int = 2, wait_s: float = 0.2) -> AdmissionController:
    s = dataclasses.replace(settings, max_waiters=waiters, queue_wait_s=wait_s)
    return AdmissionController(Budget(4096, None, 2, 2000, 1, stream, 1), s)


async def test_admits_up_to_capacity_then_queues_then_503(settings: Settings) -> None:
    ac = ac_with(settings, stream=2, waiters=1, wait_s=0.2)
    t1 = await ac.acquire("stream")
    t2 = await ac.acquire("stream")
    waiter = asyncio.create_task(ac.acquire("stream"))
    await asyncio.sleep(0)
    assert ac.classes["stream"].waiting == 1
    with pytest.raises(Overloaded) as e:  # queue (1 waiter) is full
        await ac.acquire("stream")
    assert "queue_full" in e.value.reason and e.value.retry_after == 30
    ac.release(t1)  # slot handed directly to the waiter
    t3 = await asyncio.wait_for(waiter, 1)
    assert ac.classes["stream"].snapshot()["active"] == 2
    ac.release(t2)
    ac.release(t3)
    assert ac.active_count() == 0


async def test_waiter_times_out_with_503(settings: Settings) -> None:
    ac = ac_with(settings, stream=1, waiters=4, wait_s=0.05)
    await ac.acquire("stream")
    with pytest.raises(Overloaded) as e:
        await ac.acquire("stream")
    assert "queue_timeout" in e.value.reason
    assert ac.classes["stream"].waiting == 0


async def test_waiters_are_fifo(settings: Settings) -> None:
    ac = ac_with(settings, stream=1, waiters=4, wait_s=2)
    t = await ac.acquire("stream")
    order: list[int] = []

    async def wait(i: int) -> None:
        tk = await ac.acquire("stream")
        order.append(i)
        ac.release(tk)

    tasks = [asyncio.create_task(wait(i)) for i in range(3)]
    await asyncio.sleep(0.01)
    ac.release(t)
    await asyncio.gather(*tasks)
    assert order == [0, 1, 2]


async def test_cancelled_waiter_does_not_leak_slot(settings: Settings) -> None:
    ac = ac_with(settings, stream=1, waiters=4, wait_s=2)
    t = await ac.acquire("stream")
    w = asyncio.create_task(ac.acquire("stream"))
    await asyncio.sleep(0.01)
    w.cancel()
    await asyncio.sleep(0)
    ac.release(t)
    t2 = await ac.acquire("stream")  # immediately available
    ac.release(t2)
    assert ac.active_count() == 0


async def test_drain_rejects_new_and_waiting(settings: Settings) -> None:
    ac = ac_with(settings, stream=1, waiters=4, wait_s=2)
    t = await ac.acquire("stream")
    w = asyncio.create_task(ac.acquire("stream"))
    await asyncio.sleep(0.01)
    ac.start_drain()
    with pytest.raises(Overloaded, match="draining"):
        await w
    with pytest.raises(Overloaded, match="draining"):
        await ac.acquire("extract")
    ac.release(t)


async def test_pressure_gate_blocks_then_dispatches_on_recovery(settings: Settings) -> None:
    ac = ac_with(settings, stream=1, waiters=4, wait_s=2)
    t = await ac.acquire("stream")
    w = asyncio.create_task(ac.acquire("stream"))
    await asyncio.sleep(0.01)
    ac.set_pressure("pressure")
    with pytest.raises(Overloaded, match="memory_pressure"):
        await ac.acquire("transcode")
    ac.release(t)  # gate closed: the waiter is NOT admitted yet
    await asyncio.sleep(0.01)
    assert not w.done()
    ac.set_pressure("ok")  # recovery hands the free slot to the waiter
    tk = await asyncio.wait_for(w, 1)
    ac.release(tk)


async def test_shed_newest_prefers_transcode(settings: Settings) -> None:
    ac = ac_with(settings, stream=3)
    s1 = await ac.acquire("stream")
    s2 = await ac.acquire("stream")
    tr = await ac.acquire("transcode")
    hits: list[str] = []
    tr.on_cancel(lambda r: hits.append(f"tr:{r}"))
    s2.on_cancel(lambda r: hits.append(f"s2:{r}"))
    assert ac.shed_newest() is tr
    assert ac.shed_newest() is s2  # no live transcode left -> newest stream
    assert hits == ["tr:memory_critical", "s2:memory_critical"]
    assert s1.cancelled is None


async def test_pressure_guard_tick(settings: Settings) -> None:
    probe = FakeProbe(available_mb=10 * GB, psi=Psi(0, 0))
    ac = ac_with(settings, stream=2)
    guard = PressureGuard(probe, ac, settings)
    assert guard.tick() == "ok" and ac.gate() is None

    probe.psi = Psi(some_avg10=25.0, full_avg10=0.0)
    assert guard.tick() == "pressure" and ac.gate() == "memory_pressure"

    ac.set_pressure("ok")
    t = await ac.acquire("transcode")
    probe.available_mb = 100  # below the critical floor
    assert guard.tick() == "critical"
    assert t.cancelled == "memory_critical"

    probe.available_mb, probe.psi = 10 * GB, Psi(0, 0)
    assert guard.tick() == "ok" and ac.gate() is None


def test_classify_pressure_thresholds(settings: Settings) -> None:
    assert classify_pressure(None, 5000, settings) == "ok"
    assert classify_pressure(None, 700, settings) == "pressure"
    assert classify_pressure(None, 200, settings) == "critical"
    assert classify_pressure(Psi(0, 6.0), 5000, settings) == "critical"
    assert classify_pressure(Psi(10.0, 0), 5000, settings) == "pressure"


# ── per-client limits ───────────────────────────────────────────────────────


def test_token_bucket_refills() -> None:
    now = [0.0]
    rl = RateLimiter.per_minute(20, burst=20, clock=lambda: now[0])
    for _ in range(20):
        assert rl.hit("1.2.3.4") == 0
    wait = rl.hit("1.2.3.4")
    assert wait == pytest.approx(3.0)  # one token per 3 s
    assert rl.hit("5.6.7.8") == 0  # other clients unaffected
    now[0] += 3.0
    assert rl.hit("1.2.3.4") == 0
    with pytest.raises(RateLimited) as e:
        rl.check("1.2.3.4")
    assert e.value.retry_after == 3


def test_token_bucket_bounded_memory() -> None:
    rl = RateLimiter(1, 1, max_keys=100)
    for i in range(1000):
        rl.hit(f"10.0.{i // 256}.{i % 256}")
    assert len(rl) == 100


def test_keyed_concurrency() -> None:
    kc = KeyedConcurrency(2)
    assert kc.try_acquire("a") and kc.try_acquire("a")
    assert not kc.try_acquire("a")
    assert kc.try_acquire("b")
    kc.release("a")
    assert kc.try_acquire("a")
    kc.release("a"), kc.release("a"), kc.release("b")
    assert kc.count("a") == 0 and kc.count("b") == 0


# ── egress ──────────────────────────────────────────────────────────────────


def test_egress_cutoff_and_month_rollover() -> None:
    db: dict[str, int] = {"2026-11": 7_999_999_999_000}
    clock = [datetime(2026, 11, 30, 23, 59, tzinfo=UTC)]
    m = EgressMeter(8 * 10**12, lambda k: db.get(k, 0), db.__setitem__, now=lambda: clock[0])
    assert not m.exceeded
    m.add(1_000)
    assert m.exceeded
    m.flush()
    assert db["2026-11"] == 8 * 10**12
    clock[0] = datetime(2026, 12, 1, 0, 0, 1, tzinfo=UTC)
    assert not m.exceeded and m.month == "2026-12" and m.total == 0


def test_month_helpers() -> None:
    assert month_key(datetime(2026, 1, 5, tzinfo=UTC)) == "2026-01"
    assert seconds_until_next_month(datetime(2026, 12, 31, 23, 59, 0, tzinfo=UTC)) == 60


def test_settings_from_env(tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "VIDDOWN_SECRET": "x" * 32,
            "VIDDOWN_RESERVED_MB": "2000",
            "VIDDOWN_EGRESS_CUTOFF_TB": "7.5",
            "VIDDOWN_ALLOWED_EXTRACTORS": "youtube, vimeo",
            "RUNTIME_DIRECTORY": "/run/viddown",
            "STATE_DIRECTORY": "/var/lib/viddown",
        }
    )
    assert s.reserved_mb == 2000
    assert s.egress_cutoff_bytes == 7_500_000_000_000
    assert s.allowed_extractors == ("youtube", "vimeo")
    assert s.ytdlp_extractor_patterns == ["youtube", "vimeo", "-generic"]
    assert s.runtime_dir == Path("/run/viddown") and s.state_dir == Path("/var/lib/viddown")
    assert make_settings(tmp_path).ytdlp_extractor_patterns[-1] == "-generic"
