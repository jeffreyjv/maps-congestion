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

## Running locally

### Prerequisites

- [Docker](https://www.docker.com/) — for running the full stack
- [uv](https://docs.astral.sh/uv/) — for running tests without Docker (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

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

Tiers: `low` (score < 5) · `moderate` (5–20) · `high` (> 20)

## Production Infrastructure

No infra is included in this repo — the service runs fully via Docker Compose locally. In a production deployment on AWS, the natural mapping would be:

| Component | AWS Service |
|---|---|
| FastAPI app | **ECS Fargate** — containerized, auto-scales with load |
| Background worker | **ECS Fargate** (separate task definition) — scales independently from the API |
| Redis Streams + sorted sets | **ElastiCache for Redis** — managed, multi-AZ with replication |
| Container images | **ECR** — private registry for Docker images |
| Load balancing | **ALB** — routes traffic to the API task; health checks on `/ping` |
| Secrets (Redis URL, etc.) | **Secrets Manager** or **Parameter Store** — injected as env vars at runtime |
| CI/CD | **CodePipeline + CodeBuild** — build image, push to ECR, deploy to ECS |
| Observability | **CloudWatch** — container logs, custom metrics for congestion scores and stream lag |

The stateless nature of both the API and worker makes Fargate a clean fit — no servers to manage, and tasks can scale horizontally without coordination. ElastiCache handles the Redis layer with persistence and failover that would be impractical to self-manage.

## Tests

### Unit tests — no Docker required

Pure function tests using fakeredis. Run these first; they're fast and have no dependencies.

```bash
cd code
uv sync --dev
uv run pytest tests/unit/ -v
```

### Integration tests — Docker stack required

Tests the full running system: real Redis, real worker, real HTTP. Start the stack first, then run:

```bash
cd code
docker compose up --build -d
uv run pytest tests/integration/ -v
```

These also run automatically in CI on every pull request to `main`.

### Load tests — manual only

Measures throughput and concurrency using fakeredis (isolates API performance from Redis I/O). Skipped by default; trigger explicitly:

```bash
cd code
uv run pytest tests/load/ -v -s -m load
```

In CI, trigger via **Actions → Load Test → Run workflow** (uses `workflow_dispatch`).