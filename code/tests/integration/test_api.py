# Integration tests for the full running system.
# These test the entire pipeline end-to-end: a real HTTP request hits the API,
# the ping flows through the Redis Stream, the worker processes it, and the
# congestion endpoint reflects the result. Requires the Docker stack to be running.
# Run with: docker compose up --build -d && uv run pytest tests/integration/ -v

import os
import time

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")


def _ping(device_id: str, lat: float, lon: float) -> httpx.Response:
    return httpx.post(f"{BASE_URL}/ping", json={
        "device_id": device_id,
        "timestamp": time.time(),
        "lat": lat,
        "lon": lon,
    })


def _congestion(lat: float, lon: float) -> dict:
    return httpx.get(f"{BASE_URL}/congestion", params={"lat": lat, "lon": lon}).json()


# Polls until unique_devices >= count or timeout — gives the worker time to drain the stream
def _wait_for_devices(count: int, lat: float, lon: float, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = _congestion(lat, lon)
        if data.get("unique_devices", 0) >= count:
            break
        time.sleep(0.2)
    return data


# POST /ping should return 200 {"status": "ok"} for a valid payload
def test_ping_returns_ok():
    r = _ping("integ-health", lat=0.0, lon=0.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# GET /congestion should return all expected fields with valid types
def test_congestion_response_shape():
    data = _congestion(lat=-89.0, lon=0.0)
    assert set(data.keys()) >= {"lat", "lon", "area", "score", "tier", "unique_devices", "window_seconds"}
    assert data["tier"] in ("low", "moderate", "high")
    assert data["score"] >= 0.0


# A single device ping should show up in the congestion score after the worker processes it
def test_single_device_registers():
    lat, lon = 10.0, 20.0
    _ping("integ-single", lat=lat, lon=lon)
    data = _wait_for_devices(1, lat=lat, lon=lon)
    assert data["unique_devices"] >= 1
    assert data["score"] > 0


# 6 devices in the same area should push the score above 5.0 and into the moderate tier
def test_multiple_devices_raise_tier():
    lat, lon = 10.0, 21.0
    for i in range(6):
        _ping(f"integ-multi-{i}", lat=lat, lon=lon)
    data = _wait_for_devices(6, lat=lat, lon=lon)
    assert data["unique_devices"] >= 6
    assert data["score"] > 5.0
    assert data["tier"] in ("moderate", "high")


# The same device pinging 5 times should still count as 1 unique device
def test_deduplication_same_device():
    lat, lon = 10.0, 22.0
    for _ in range(5):
        _ping("integ-dedup", lat=lat, lon=lon)
    time.sleep(1.0)
    data = _congestion(lat=lat, lon=lon)
    assert data["unique_devices"] == 1


# End-to-end: pings flow through the API → Redis Stream → worker → sorted set → congestion read
def test_full_ping_to_congestion_pipeline():
    lat, lon = 10.0, 23.0
    device_count = 5
    for i in range(device_count):
        _ping(f"integ-pipeline-{i}", lat=lat, lon=lon)
    data = _wait_for_devices(device_count, lat=lat, lon=lon)
    assert data["unique_devices"] == device_count
    assert data["score"] > 0
    assert data["tier"] in ("low", "moderate", "high")
