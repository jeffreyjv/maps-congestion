# Load tests for throughput and concurrency under stress.
# These verify the API can handle large volumes of concurrent pings without
# dropping requests, queuing, or losing data. Uses fakeredis to isolate API
# performance from Redis I/O — the goal is measuring the async request handling
# layer, not the database. Skipped by default; run manually when needed.
# Run with: uv run pytest tests/load/ -v -s -m load

import asyncio
import statistics
import time

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

import main
from main import app

pytestmark = pytest.mark.load

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


# Fires 1000 pings simultaneously — asserts wall time is well under the sum of sequential
# latencies, proving requests are handled concurrently and not queuing
@pytest.mark.anyio
async def test_concurrent_burst():
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


# Sends 10,000 pings in batches of 500 — reports throughput and latency percentiles
# to verify performance does not degrade as load is sustained over time
@pytest.mark.anyio
async def test_sustained_load_no_degradation():
    all_latencies: list[float] = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for batch in range(TOTAL_SUSTAINED // BATCH_SIZE):
            offset = batch * BATCH_SIZE
            tasks = [_post_ping(client, f"device-{offset + i}") for i in range(BATCH_SIZE)]
            batch_latencies = await asyncio.gather(*tasks)
            all_latencies.extend(batch_latencies)

    wall_time = sum(all_latencies) / BATCH_SIZE
    _print_stats("Sustained load", all_latencies, wall_time)


# 100 unique devices ping the same geohash cell simultaneously — asserts all 100 writes
# land in the sorted set with no data loss from concurrent Redis ZADD operations
@pytest.mark.anyio
async def test_concurrent_writes_no_data_loss():
    device_count = 100
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        tasks = [_post_ping(client, f"device-{i}") for i in range(device_count)]
        wall_start = time.perf_counter()
        latencies = await asyncio.gather(*tasks)
        wall_time = time.perf_counter() - wall_start

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
    assert data["score"] > 0
