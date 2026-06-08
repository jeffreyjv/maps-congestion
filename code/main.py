import time
import redis.asyncio as aioredis
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
redis = aioredis.from_url("redis://localhost:6379")

class Ping(BaseModel):
    device_id: str
    timestamp: float
    lat: float
    lon: float

@app.post("/ping")
async def receive_ping(ping: Ping):
    key = f"congestion:{ping.lat},{ping.lon}"
    now = time.time()
    await redis.zadd(key, {ping.device_id: now})
    await redis.zremrangebyscore(key, 0, now - 300)
    return {"status": "ok"}

@app.get("/congestion")
async def get_congestion(lat: float, lon: float):
    key = f"congestion:{lat},{lon}"
    now = time.time()
    
    count = await redis.zcount(key, now - 300, now)
    
    if count < 10:
        tier = "low"
    elif count < 50:
        tier = "moderate"
    else:
        tier = "high"
    
    return {"geohash": key, "count": count, "tier": tier}

def get_tier(count: int) -> str:
    if count < 10:
        return "low"
    elif count < 50:
        return "moderate"
    return "high"