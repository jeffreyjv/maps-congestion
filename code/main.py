import os
import time
import geohash2
import redis.asyncio as aioredis
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

WINDOW_SECONDS = 300
PRECISION = 7


class Ping(BaseModel):
    device_id: str
    timestamp: datetime
    lat: float
    lon: float


def _neighbors(h: str) -> list[str]:
    lat, lon, lat_err, lon_err = geohash2.decode_exactly(h)
    precision = len(h)
    offsets = [
        (lat_err * 2, 0), (-lat_err * 2, 0),
        (0, lon_err * 2), (0, -lon_err * 2),
        (lat_err * 2, lon_err * 2), (lat_err * 2, -lon_err * 2),
        (-lat_err * 2, lon_err * 2), (-lat_err * 2, -lon_err * 2),
    ]
    return [geohash2.encode(lat + dlat, lon + dlon, precision=precision) for dlat, dlon in offsets]


def get_area_keys(lat: float, lon: float) -> list[str]:
    center = geohash2.encode(lat, lon, precision=PRECISION)
    return [f"congestion:{cell}" for cell in [center] + _neighbors(center)]


def calculate_congestion(entries: list[tuple[str, float]], now: float) -> float:
    latest: dict[str, float] = {}
    for device_id, timestamp in entries:
        if device_id not in latest or timestamp > latest[device_id]:
            latest[device_id] = timestamp
    score = 0.0
    for timestamp in latest.values():
        age = now - timestamp
        score += max(0.0, 1.0 - (age / WINDOW_SECONDS))
    return round(score, 4)


def get_tier(score: float) -> str:
    if score < 5:
        return "low"
    elif score <= 20:
        return "moderate"
    return "high"


@app.post("/ping")
async def receive_ping(ping: Ping) -> dict:
    now = time.time()
    await redis.xadd("pings", {
        "device_id": ping.device_id,
        "lat": str(ping.lat),
        "lon": str(ping.lon),
        "timestamp": str(now)
    })
    return {"status": "ok"}


@app.get("/congestion")
async def get_congestion(lat: float, lon: float) -> dict:
    keys = get_area_keys(lat, lon)
    now = time.time()

    pipe = redis.pipeline(transaction=False)
    for key in keys:
        pipe.zrangebyscore(key, now - WINDOW_SECONDS, now, withscores=True)
    results = await pipe.execute()

    all_entries: list[tuple[str, float]] = []
    for result in results:
        for member, timestamp in result:
            device_id = member.decode() if isinstance(member, bytes) else member
            all_entries.append((device_id, timestamp))

    score = calculate_congestion(all_entries, now)
    unique_devices = len({d for d, _ in all_entries})

    return {
        "lat": lat,
        "lon": lon,
        "area": geohash2.encode(lat, lon, precision=PRECISION),
        "score": score,
        "tier": get_tier(score),
        "unique_devices": unique_devices,
        "window_seconds": WINDOW_SECONDS,
    }