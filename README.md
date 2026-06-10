# maps-congestion

Real-time congestion tracking API. Devices send location pings which are processed by a background worker and used to compute a weighted congestion score per geographic area.

## Architecture

- **FastAPI** — receives pings and serves congestion queries
- **Redis Streams** — decouples ping ingestion from processing
- **Worker** — consumes the stream and writes to Redis sorted sets
- **Geohash** — encodes lat/lon into ~150m cells (precision 7); queries aggregate the target cell plus 8 neighbors to avoid boundary gaps

## Running locally

### Prerequisites

- [Docker](https://www.docker.com/)
- [uv](https://docs.astral.sh/uv/)

### Start everything

```bash
cd code
docker compose up --build
```

This starts Redis, the API, and the worker together.

API: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

## API

### POST `/ping`

Report a device location.

```bash
curl -X POST http://localhost:8000/ping \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "abc123",
    "timestamp": "2025-01-01T12:34:56Z",
    "lat": 40.743,
    "lon": -73.989
  }'
```

Response:
```json
{"status": "ok"}
```

### GET `/congestion`

Get congestion for a location.

```bash
curl "http://localhost:8000/congestion?lat=40.743&lon=-73.989"
```

Response:
```json
{
  "lat": 40.743,
  "lon": -73.989,
  "area": "dr5ru6n",
  "score": 3.42,
  "tier": "low",
  "unique_devices": 4,
  "window_seconds": 300
}
```

Tiers: `low` (score < 5) · `moderate` (5–20) · `high` (> 20)

## Tests

```bash
cd code
uv run pytest -v
```

Unit, integration, and load tests all run without live Redis (fakeredis):

```bash
uv run pytest tests/integration/ -v
uv run pytest tests/load/ -v -s
```