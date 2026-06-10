import asyncio

import fakeredis.aioredis
import pytest
from httpx import AsyncClient, ASGITransport

import main
import worker as worker_mod
from main import app

LAT = 37.7749
LON = -122.4194


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    fake = fakeredis.aioredis.FakeRedis()
    monkeypatch.setattr(main, "redis", fake)
    monkeypatch.setattr(worker_mod, "redis", fake)
    yield fake
    await fake.aclose()


async def _drain(fake) -> None:
    results = await fake.xread({"pings": "0"}, count=1000)
    if results:
        _, entries = results[0]
        await asyncio.gather(*[worker_mod.process_ping(data) for _, data in entries])


@pytest.fixture()
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_ping_returns_ok(client):
    async with client:
        response = await client.post("/ping", json={
            "device_id": "d1",
            "timestamp": 0.0,
            "lat": LAT,
            "lon": LON,
        })
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_congestion_no_pings(client):
    async with client:
        response = await client.get("/congestion", params={"lat": LAT, "lon": LON})
    assert response.status_code == 200
    data = response.json()
    assert data["lat"] == LAT
    assert data["lon"] == LON
    assert data["score"] == 0.0
    assert data["tier"] == "low"
    assert data["unique_devices"] == 0


@pytest.mark.anyio
async def test_congestion_single_device(client, fake_redis):
    async with client:
        await client.post("/ping", json={"device_id": "d1", "timestamp": 0.0, "lat": LAT, "lon": LON})
        await _drain(fake_redis)
        response = await client.get("/congestion", params={"lat": LAT, "lon": LON})
    data = response.json()
    assert data["unique_devices"] == 1
    assert 0.0 < data["score"] <= 1.0
    assert data["tier"] == "low"


@pytest.mark.anyio
async def test_congestion_multiple_devices(client, fake_redis):
    async with client:
        for i in range(6):
            await client.post("/ping", json={"device_id": f"d{i}", "timestamp": 0.0, "lat": LAT, "lon": LON})
        await _drain(fake_redis)
        response = await client.get("/congestion", params={"lat": LAT, "lon": LON})
    data = response.json()
    assert data["unique_devices"] == 6
    assert data["score"] > 5.0
    assert data["tier"] == "moderate"


@pytest.mark.anyio
async def test_congestion_deduplicates_same_device(client, fake_redis):
    async with client:
        for _ in range(3):
            await client.post("/ping", json={"device_id": "d1", "timestamp": 0.0, "lat": LAT, "lon": LON})
        await _drain(fake_redis)
        response = await client.get("/congestion", params={"lat": LAT, "lon": LON})
    data = response.json()
    assert data["unique_devices"] == 1
    assert data["score"] <= 1.0
