# RunnerX — Backend & Data Engineering Platform

A Python backend and offline ETL platform for runner and training camp data. This first release connects synthetic Excel/CSV inputs, validation, MySQL, authenticated APIs, and lifecycle tests. It includes local deployment and optional Google Cloud delivery configuration.

RunnerX 是一个以跑者与训练营为场景的后端和数据工程项目。第一版覆盖数据清洗、关系存储、租户隔离、API、测试与部署配置。仓库中的样例全部由程序生成。

## What works

- Python 3.11+, FastAPI, Pydantic 2, pandas, openpyxl, MySQL, SQLAlchemy 2, PyMySQL and Alembic.
- UUID primary keys and composite foreign keys enforce tenant and bootcamp ownership in MySQL itself.
- Bearer API keys identify tenants; only key hashes are stored. Five resources support CRUD, filtering and pagination.
- CSV/XLSX imports recognize English and Chinese headers, validate dates/units/precision and reject invalid batches atomically.
- Runner identity uses a case-sensitive external ID within each tenant. Re-importing a committed file's bytes is a no-op; corrected files update existing business keys.
- Dry runs write nothing. Imports retain a SHA-256 fingerprint, row-level errors and inserted/updated counts.
- Versioned migrations, dedicated MySQL test fixtures, end-to-end tests, Docker Compose and CI.
- Optional Cloud SQL TCP/Unix socket connectivity, Terraform and a gated Cloud Run release workflow.

The API is the product interface. Interactive documentation is available at `/docs` in local mode. No frontend, ML prediction or third-party fitness account is required.

## Quick start with Docker

Prerequisites: Docker Engine/Desktop with Compose running. The default ports are API 8000 and MySQL 3308, bound to localhost.

```sh
cp .env.example .env
docker compose up --build -d
docker compose --profile tools run --rm seed
```

PowerShell users can replace the first command with `Copy-Item .env.example .env`.

If port 8000 is already in use, set `API_PORT=8002` in `.env` before starting Compose, then open `http://127.0.0.1:8002/docs`. This is useful when the native Windows API is also running.

Open <http://127.0.0.1:8000/docs>, click **Authorize**, and enter the local `DEMO_API_KEY` from `.env`. The provided `.env.example` credentials are public demo values, intended only for localhost. Replace them for any shared deployment.

```sh
curl http://127.0.0.1:8000/api/v1/runners \
  -H "Authorization: Bearer runnerx-demo-token-for-local-use-only-2026"
```

The seed command creates the `demo` tenant, the `demo-2026` training camp, 30 synthetic runners and 360 sessions. Run it again to verify that row counts stay unchanged.

```sh
docker compose logs api
docker compose down
```

Stopping Compose preserves the database volume. Only remove its volume when you intend to erase the demo database.

## Native Windows start

For a machine with Python 3.11+ and MySQL Server 8.0/8.4 binaries installed, this helper starts a separate MySQL process and data directory; it does not connect to or modify the installed MySQL service.

```powershell
.\scripts\run-local.ps1
# If the MySQL binaries are elsewhere:
.\scripts\run-local.ps1 -MySqlBin 'C:\Program Files\MySQL\MySQL Server 8.4\bin'
```

The helper installs dependencies in `.venv`, initializes `.local/native`, applies migrations, imports the samples and serves the API on localhost:8000. Native MySQL uses port 3318. Subsequent starts can use `-SkipInstall`. Generated private demo credentials are in `.local/native/state.json`; use its `api_key` in Swagger Authorize. Ctrl+C stops the API and the helper shuts down its private database. If interrupted during setup, `.\.venv\Scripts\python.exe scripts/local_mysql.py stop` stops that private database while retaining its files.

## Python development with an existing dedicated database

Create an empty MySQL database and a user authorized for that database, then configure `.env`. Do not point demo or test commands at a production database.

```sh
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install '.[dev]'
python -m runnerx.cli init-db
python -m runnerx.cli seed-demo
uvicorn runnerx.api:app --host 127.0.0.1 --port 8000
```

`DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` configure TCP. Set `DB_UNIX_SOCKET` to use Cloud SQL's mounted Unix socket. `RUNNERX_DATABASE_URL` is an optional explicit `mysql+pymysql://` override. The server does not create tables at startup; migrations are an explicit operation.

## Import and validation example

```sh
python -m runnerx.cli import --tenant demo --bootcamp demo-2026 \
  --kind registrations --file data/sample/registrations.xlsx --dry-run

python -m runnerx.cli import --tenant demo --bootcamp demo-2026 \
  --kind registrations --file data/sample/invalid_registrations.xlsx \
  --report data/reports/rejected.json
```

The invalid example exits with code **2**, reports both invalid rows and leaves existing runners/registrations unchanged. Non-dry rejected imports create an audit entry, visible through `/api/v1/imports`. Runtime/database failures exit **1** and roll back the transaction. Success, duplicate skips and valid dry runs exit **0**.

Generate a different synthetic dataset with:

```sh
python -m runnerx.cli generate-sample --output data/generated --runners 50 --seed 42
```

See [data contracts](docs/data-contracts.md) for required columns, identity, update behavior, units and limits.

## API resources

All `/api/v1` endpoints require `Authorization: Bearer <tenant-api-key>`.

| Resource | Operations |
| --- | --- |
| `/api/v1/runners` | List, create, retrieve, patch, delete |
| `/api/v1/bootcamps` | List, create, retrieve, patch, delete |
| `/api/v1/groups` | List, create, retrieve, patch, delete |
| `/api/v1/registrations` | List, create, retrieve, patch, delete |
| `/api/v1/sessions` | List, create, retrieve, patch, delete |
| `/api/v1/bootcamps/{id}/stats` | Runner/session counts and total distance |
| `/api/v1/imports` | List and retrieve import audit records |
| `/health` | Process liveness, no database dependency |
| `/ready` | Database connectivity check |

Lists return `items`, `total`, `page`, and `page_size` (maximum 100). The API rejects client-supplied ownership fields and unknown input fields. Foreign-tenant objects are not visible. Delete dependent rows explicitly before deleting their parents; references are not silently discarded.

Provision another tenant from a trusted database administration environment:

```sh
python -m runnerx.cli create-tenant --slug example-club --name 'Example Club'
python -m runnerx.cli list-keys --tenant example-club
python -m runnerx.cli revoke-key --tenant example-club --key-id <key-uuid>
```

The generated key is displayed once. In v0.1 a tenant key grants access to that tenant's complete API; per-user roles and interactive login are outside this release.

## Tests

Unit tests require no database:

```sh
python -m pytest -m 'not integration'
```

For the complete suite, create a **dedicated, disposable** MySQL database whose name ends in `_test`. Set `RUNNERX_TEST_DATABASE_URL` explicitly:

```powershell
$env:RUNNERX_TEST_DATABASE_URL='mysql+pymysql://test_user:test_password@127.0.0.1:3306/runnerx_test'
python -m pytest
```

Tests apply migrations and clear application rows only in that explicitly configured test database. Without the variable, integration tests are skipped; skipped tests are not evidence of a working database lifecycle. Run only one suite at a time against a given test database.

CI starts an ephemeral MySQL 8.4 service, applies migrations, runs the full suite and builds the Docker image. Deployment-script tests use fake cloud commands to check that failures prevent traffic changes. See [validation notes](docs/validation.md) for the checks actually run for this release.

## Architecture and cloud delivery

See [architecture](docs/architecture.md) and [deployment](docs/deployment.md). Cloud provisioning/deployment is opt-in and requires your own GCP project. Local operation needs no cloud credentials.

The repository makes no measured claim of 90% labor savings, high-throughput performance, or zero-downtime releases. Those require separate measurements under stated workloads.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Useful contributions include new header mappings with fixtures, parser edge cases, database/tenant regression tests, and query benchmarks. All submitted sample data must be synthetic or clearly authorized for redistribution.

Code and generated fixtures are distributed under the [MIT license](LICENSE). Dependencies retain their respective licenses.
