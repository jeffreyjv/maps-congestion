"""
Smoke tests for the live Docker stack.

Requires the full stack to be running:
    docker compose up --build -d
"""

import time

import httpx

BASE_URL = "http://localhost:8000"
LAT = 37.7749
LON = -122.4194


def test_ping_returns_ok():
    r = httpx.post(f"{BASE_URL}/ping", json={
        "device_id": "smoke-d1",
        "timestamp": time.time(),
        "lat": LAT,
        "lon": LON,
    })
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_congestion_pipeline():
    """Post pings, poll until the worker drains them, then verify congestion."""
    device_count = 5
    for i in range(device_count):
        httpx.post(f"{BASE_URL}/ping", json={
            "device_id": f"smoke-device-{i}",
            "timestamp": time.time(),
            "lat": LAT,
            "lon": LON,
        })

    deadline = time.time() + 5
    data: dict = {}
    while time.time() < deadline:
        r = httpx.get(f"{BASE_URL}/congestion", params={"lat": LAT, "lon": LON})
        data = r.json()
        if data.get("unique_devices", 0) >= device_count:
            break
        time.sleep(0.2)

    assert data["unique_devices"] == device_count
    assert data["score"] > 0
    assert data["tier"] in ("low", "moderate", "high")
