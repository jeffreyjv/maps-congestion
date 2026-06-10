import asyncio
import os
import time
import geohash2
import redis.asyncio as aioredis

PRECISION = 7
WINDOW_SECONDS = 300

redis = aioredis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


async def process_ping(data: dict) -> None:
    device_id: str = data[b"device_id"].decode()
    lat: float = float(data[b"lat"])
    lon: float = float(data[b"lon"])
    now: float = time.time()

    key = f"congestion:{geohash2.encode(lat, lon, precision=PRECISION)}"
    await redis.zadd(key, {device_id: now})
    await redis.zremrangebyscore(key, 0, now - WINDOW_SECONDS)
    print(f"processed ping from {device_id} → {key}", flush=True)


async def main() -> None:
    print("worker started, listening for pings...", flush=True)
    last_id = "0"

    while True:
        results = await redis.xread({"pings": last_id}, block=1000, count=10)
        if results:
            stream, entries = results[0]
            for entry_id, data in entries:
                await process_ping(data)
                last_id = entry_id


if __name__ == "__main__":
    asyncio.run(main())