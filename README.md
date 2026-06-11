# maps-congestion

Real-time congestion tracking API. Devices send location pings which are processed by a background worker and used to compute a weighted congestion score per geographic area.

## Repository Layout

```
maps-congestion/
├── code/
│   ├── main.py                         # FastAPI app — /ping and /congestion endpoints
│   ├── worker.py                       # Redis Stream consumer — processes pings and writes scores
│   ├── Dockerfile                      # Single image used for both api and worker services
│   ├── docker-compose.yml              # Local orchestration: Redis + API + worker
│   ├── pyproject.toml                  # Dependencies and tool config (ruff, pytest)
│   └── tests/
│       ├── unit/
│       │   └── test_congestion_logic.py  # Pure function tests — no Redis, no Docker
│       ├── integration/
│       │   └── test_api.py               # Full-stack tests — requires Docker stack running
│       └── load/
│           └── test_throughput.py        # Throughput/concurrency tests — manual only
└── .github/
    └── workflows/
        ├── ci.yml            # Main pipeline: lint → unit → integration
        └── load-test.yml     # Load tests — triggered manually via workflow_dispatch
```

## CI Pipeline

The GitHub Actions pipeline (`.github/workflows/ci.yml`) runs on every push and every pull request to `main`. Jobs are:

1. **sast** — runs [Bandit](https://bandit.readthedocs.io/) to catch common Python security issues
2. **dependency-scan** — runs [pip-audit](https://pypi.org/project/pip-audit/) against production dependencies to flag known CVEs
3. **lint** — runs [Ruff](https://docs.astral.sh/ruff/) for style and correctness
4. **test** — runs unit tests (`tests/unit/`) via fakeredis, no Docker needed; gates on all three checks above
5. **integration** — spins up the full Docker Compose stack, waits for the API to be healthy, then runs `tests/integration/` against it; runs on PRs to `main` and pushes to `main`

A separate workflow (`.github/workflows/load-test.yml`) runs load tests on demand via `workflow_dispatch`.

```
sast ──┐
       ├──► test ──► integration  (PRs to main + main)
lint ──┘
dependency-scan ──┘
```

## Architecture

- **FastAPI** — receives pings and serves congestion queries
- **Redis Streams** — decouples ping ingestion from processing
- **Worker** — consumes the stream and writes to Redis sorted sets
- **Geohash** — encodes lat/lon into ~150m cells (precision 7); queries aggregate the target cell plus 8 neighbors to avoid boundary gaps

### What "area" means

An area is a fixed geographic grid cell defined by a **geohash at precision 7**, which corresponds to roughly **150m × 150m**. Every lat/lon coordinate maps deterministically to one cell — no polygons, no dynamic regions, no radius math. When a device pings from a location, it lands in exactly one cell. When a client queries a location, the system reads that cell plus its 8 neighboring cells (the cells directly north, south, east, west, and the four diagonals) and aggregates them into a single score. This means every query effectively covers a **~450m × 450m** area (a 3×3 grid of 150m cells centered on the queried location). This eliminates the edge case where two devices physically meters apart appear to be in different areas just because a grid boundary falls between them.

### How congestion is calculated

Each area has a sorted set in Redis keyed by `congestion:<geohash>`, storing `{ device_id: last_seen_timestamp }`. When a client queries a location, the system reads all entries in that cell and its 8 neighbors that fall within the last 5 minutes, then computes a weighted score.

Each unique device gets a weight based on how recently it pinged:

```
weight = 1 - (age_in_seconds / 300)
```

| Age | Weight |
|---|---|
| Just pinged | 1.0 |
| 150s ago | 0.5 |
| 299s ago | ~0.01 |
| 300s ago | 0.0 (expired) |

The score is the **sum of all device weights**. 10 devices that just pinged = score of 10.0. 10 devices that pinged 150s ago = score of 5.0. The same device pinging multiple times is deduplicated — only its most recent ping counts.

This design means congestion decays naturally over time. If cars leave an area and stop pinging, their weights drop toward zero and the score falls on its own — no cleanup job needed.

That score is then bucketed into a tier: `low` (< 5), `moderate` (5–20), `high` (> 20).

### How pings are received, stored, and processed

1. A device sends `POST /ping` with its `device_id`, `lat`, `lon`, and `timestamp`
2. The API appends the ping to a **Redis Stream** (`pings`) and immediately returns `{"status": "ok"}` — no blocking, no processing in the request path
3. The **worker** runs as a separate process, continuously reading from the stream; for each entry it encodes the location to a geohash and writes `{ device_id: now }` into the corresponding sorted set, pruning entries older than 5 minutes in the same operation

### How clients retrieve results

A client sends `GET /congestion?lat=<lat>&lon=<lon>`. The API encodes the coordinates to a geohash, reads the sorted sets for that cell and its 8 neighbors, computes the score, and returns:

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

## Running locally

### Prerequisites

- [Docker](https://www.docker.com/) — for running the full stack
- [uv](https://docs.astral.sh/uv/) — for running tests without Docker

### Start the full stack

```bash
git clone <repo-url>
cd maps-congestion/code
docker compose up --build
```

This starts three services together:
- **Redis** on port `6379`
- **API** on port `8000` (waits for Redis to be healthy before starting)
- **Worker** — consumes the Redis Stream and computes congestion scores

API: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

To stop: `docker compose down`

### Run tests (no Docker needed)

The unit, integration, and load tests all use fakeredis so they run without any running services:

```bash
cd maps-congestion/code
uv sync --dev      # install dependencies into .venv
uv run pytest -v   # run all tests
```

Run a specific suite:

```bash
uv run pytest tests/integration/ -v
uv run pytest tests/load/ -v -s
```

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

Tiers:

| Tier | Score | Meaning |
|---|---|---|
| `low` | < 5 | Few active devices nearby — normal traffic conditions |
| `moderate` | 5 – 20 | Elevated device density — expect slowdowns |
| `high` | > 20 | High device concentration — significant congestion |

Score is a time-weighted count of unique devices in the area. Each device contributes between 0.0 (pinged 5 minutes ago) and 1.0 (just pinged), so the score reflects how many devices are actively present right now, not just how many have ever been seen. Pings older than 5 minutes expire automatically.

## Design Decisions & Trade-offs

### Why these technologies

**FastAPI + async** — hundreds of devices can be pinging at the same time. FastAPI handles requests asynchronously, meaning it doesn't wait for one to finish before starting the next. The `/ping` endpoint does one thing (drop a message in a queue) and immediately responds, so it stays fast no matter how many devices are hitting it at once.

**Redis Streams** — the API and the processing logic are completely separate. The API puts each ping into a queue (the stream) and moves on. The worker reads from that queue in the background at its own pace. This means if processing slows down or spikes, it never slows down the API or causes pings to be dropped. If the worker crashes and restarts, it picks up right where it left off.

**Redis sorted sets** — a sorted set stores each device with its timestamp as the score. This makes three things free: deduplication (writing the same device again just updates its timestamp), expiry (old entries are removed in the same operation as writing), and reading (fetching all active devices for an area is a single fast query). No background cleanup job, no separate database.

**Geohash** — instead of doing radius math on every query, a geohash encodes any lat/lon into a short string that represents a fixed grid cell. Two nearby locations produce similar strings. This makes area lookups fast and simple — just look up the right cell and its 8 neighbors.

### How the system scales

**API** is stateless and horizontally scalable — run more replicas behind a load balancer and throughput scales linearly. Each replica only talks to Redis.

**Worker** can also scale horizontally using Redis Stream consumer groups. Each worker instance claims a partition of the stream, so processing throughput scales with worker count without any coordination logic in application code.

**Redis** is the single bottleneck. At very high ping volume, a Redis cluster (sharded by geohash key) distributes write load. Read volume is naturally lower since congestion queries are less frequent than pings.

### What I'd improve given more time

- **Auth** — API keys or JWT on `/ping` to prevent arbitrary devices from injecting data
- **Metrics** — expose a `/metrics` endpoint for stream lag, score distribution, and request latency so operations has visibility without digging into logs

## Production Infrastructure

No infra is included in this repo — the service runs fully via Docker Compose locally. In a production deployment on AWS, the natural mapping would be:

| Component | AWS Service |
|---|---|
| FastAPI app | **ECS Fargate** — containerized, auto-scales with load |
| Background worker | **ECS Fargate** (separate task definition) — scales independently from the API |
| Redis Streams + sorted sets | **ElastiCache for Redis** — managed, multi-AZ with replication |
| Container images | **ECR** — private registry for Docker images |
| Load balancing | **ALB** — routes traffic to the API task; health checks on `/ping` |
| Secrets (Redis URL, etc.) | **Secrets Manager** — injected as env vars at runtime |
| Observability | **CloudWatch** — container logs, custom metrics for congestion scores and stream lag |

ECS Fargate is a natural fit here because both the API and worker are stateless containers — Fargate removes the need to manage EC2 instances, handles scaling automatically, and lets the API and worker scale independently based on their own load. ElastiCache handles the Redis layer with persistence and failover that would be impractical to self-manage.

## Tests

### Unit tests
**Runs locally and automatically in CI on every push.**

Pure function tests using fakeredis — no Docker, no running services needed.

```bash
cd code
uv sync --dev
uv run pytest tests/unit/ -v
```

### Integration tests
**Runs locally and automatically in CI on every pull request to `main`.**

Tests the full running system: real Redis, real worker, real HTTP. Requires the Docker stack to be up first.

```bash
cd code
docker compose up --build -d
uv run pytest tests/integration/ -v
```

### Load tests
**Manual only — never runs automatically.**

Measures throughput and concurrency using fakeredis. Skipped by default in both local and CI runs; must be triggered explicitly.

Locally:
```bash
cd code
uv run pytest tests/load/ -v -s -m load
```

In CI: **Actions → Load Test → Run workflow**