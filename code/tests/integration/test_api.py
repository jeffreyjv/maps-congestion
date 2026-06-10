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


def _wait_for_devices(count: int, lat: float, lon: float, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = _congestion(lat, lon)
        if data.get("unique_devices", 0) >= count:
            break
        time.sleep(0.2)
    return data


def test_ping_returns_ok():
    r = _ping("integ-health", lat=0.0, lon=0.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_congestion_response_shape():
    data = _congestion(lat=-89.0, lon=0.0)
    assert set(data.keys()) >= {"lat", "lon", "area", "score", "tier", "unique_devices", "window_seconds"}
    assert data["tier"] in ("low", "moderate", "high")
    assert data["score"] >= 0.0


def test_single_device_registers():
    lat, lon = 10.0, 20.0
    _ping("integ-single", lat=lat, lon=lon)
    data = _wait_for_devices(1, lat=lat, lon=lon)
    assert data["unique_devices"] >= 1
    assert data["score"] > 0


def test_multiple_devices_raise_tier():
    lat, lon = 10.0, 21.0
    for i in range(6):
        _ping(f"integ-multi-{i}", lat=lat, lon=lon)
    data = _wait_for_devices(6, lat=lat, lon=lon)
    assert data["unique_devices"] >= 6
    assert data["score"] > 5.0
    assert data["tier"] in ("moderate", "high")


def test_deduplication_same_device():
    lat, lon = 10.0, 22.0
    for _ in range(5):
        _ping("integ-dedup", lat=lat, lon=lon)
    time.sleep(1.0)
    data = _congestion(lat=lat, lon=lon)
    assert data["unique_devices"] == 1


def test_full_ping_to_congestion_pipeline():
    lat, lon = 10.0, 23.0
    device_count = 5
    for i in range(device_count):
        _ping(f"integ-pipeline-{i}", lat=lat, lon=lon)
    data = _wait_for_devices(device_count, lat=lat, lon=lon)
    assert data["unique_devices"] == device_count
    assert data["score"] > 0
    assert data["tier"] in ("low", "moderate", "high")
