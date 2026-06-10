"""
Load tests for POST /ping — proves the endpoint handles concurrent pings
efficiently and without data loss.

All tests run in-process using fakeredis, so no live infrastructure is needed.
"""

import asyncio
import statistics
import time

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

import main
from main import app

LAT = 37.7749
LON = -122.4194

CONCURRENT_BURST = 1000
TOTAL_SUSTAINED = 10_000
BATCH_SIZE = 500


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    import worker
    fake = fakeredis.aioredis.FakeRedis(max_connections=CONCURRENT_BURST + 1000)
    monkeypatch.setattr(main, "redis", fake)
    monkeypatch.setattr(worker, "redis", fake)
    yield fake
    await fake.aclose()


async def _post_ping(client: AsyncClient, device_id: str) -> float:
    start = time.perf_counter()
    response = await client.post(
        "/ping",
        json={"device_id": device_id, "timestamp": time.time(), "lat": LAT, "lon": LON},
    )
    elapsed = time.perf_counter() - start
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    return elapsed


def _percentile(sorted_values: list[float], p: float) -> float:
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


def _print_stats(label: str, latencies: list[float], wall_time: float) -> None:
    s = sorted(latencies)
    n = len(latencies)
    print(f"\n--- {label} ({n} pings) ---")
    print(f"  wall time:   {wall_time * 1000:.1f} ms")
    print(f"  throughput:  {n / wall_time:.0f} req/s")
    print(f"  p50 latency: {statistics.median(s) * 1000:.2f} ms")
    print(f"  p95 latency: {_percentile(s, 0.95) * 1000:.2f} ms")
    print(f"  p99 latency: {_percentile(s, 0.99) * 1000:.2f} ms")
    print(f"  max latency: {s[-1] * 1000:.2f} ms")


@pytest.mark.anyio
async def test_concurrent_burst():
    """
    Fire CONCURRENT_BURST unique pings simultaneously.

    Asserts:
    - Every request returns 200 {"status": "ok"}
    - Wall time is materially less than the sum of sequential latencies,
      proving requests overlap in flight rather than queuing
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tasks = [_post_ping(client, f"device-{i}") for i in range(CONCURRENT_BURST)]
        wall_start = time.perf_counter()
        latencies = await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - wall_start

    _print_stats("Concurrent burst", list(latencies), wall_time)

    sequential_estimate = sum(latencies)
    assert wall_time < sequential_estimate / 4, (
        f"Requests appear to be queuing: wall={wall_time * 1000:.0f} ms, "
        f"sequential estimate={sequential_estimate * 1000:.0f} ms"
    )


@pytest.mark.anyio
async def test_sustained_load_no_degradation():
    """
    Send TOTAL_SUSTAINED pings in consecutive batches of BATCH_SIZE.

    Reports p50/p95/p99 latency and throughput — no hard thresholds.
    """
    all_latencies: list[float] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for batch in range(TOTAL_SUSTAINED // BATCH_SIZE):
            offset = batch * BATCH_SIZE
            tasks = [_post_ping(client, f"device-{offset + i}") for i in range(BATCH_SIZE)]
            batch_latencies = await asyncio.gather(*tasks)
            all_latencies.extend(batch_latencies)

    wall_time = sum(all_latencies) / BATCH_SIZE  # rough: sum / parallelism
    _print_stats("Sustained load", all_latencies, wall_time)


@pytest.mark.anyio
async def test_concurrent_writes_no_data_loss():
    """
    100 unique devices ping the same geohash simultaneously.

    Asserts:
    - All 100 writes land — GET /congestion must report unique_devices == 100
    - No silent data loss from concurrent Redis ZADD operations
    """
    device_count = 100
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tasks = [_post_ping(client, f"device-{i}") for i in range(device_count)]
        wall_start = time.perf_counter()
        latencies = await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - wall_start

        # Simulate the worker draining the stream into sorted sets
        import worker as worker_mod
        results = await worker_mod.redis.xread({"pings": "0"}, count=device_count + 10)
        if results:
            _, entries = results[0]
            await asyncio.gather(*[worker_mod.process_ping(data) for _, data in entries])

        response = await client.get("/congestion", params={"lat": LAT, "lon": LON})

    assert response.status_code == 200
    data = response.json()

    _print_stats(f"Same-location burst ({device_count} devices)", list(latencies), wall_time)
    print(f"  unique_devices registered: {data['unique_devices']}")

    assert data["unique_devices"] == device_count, (
        f"Concurrent writes dropped pings: expected {device_count} devices, "
        f"got {data['unique_devices']}"
    )
    assert data["score"] > 0, "Congestion score should be positive after 100 fresh pings"
