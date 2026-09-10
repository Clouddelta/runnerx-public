# RunnerX

A multi-tenant backend and offline ETL pipeline for running clubs and training camps. Import Excel/CSV registrations and training logs into MySQL, then query and manage them through a REST API.

**Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, MySQL 8.4, Alembic, pandas, openpyxl, Docker Compose.

## Features

- Tenant-scoped API keys, UUID identities and composite foreign keys enforce data ownership.
- Atomic imports validate the entire file, report row-level errors and support dry runs.
- SHA-256 replay detection prevents duplicate imports; corrected files update records by stable business keys.
- CRUD APIs cover runners, camps, groups, registrations and sessions, with pagination, filters and camp statistics.
- Import audits, schema migrations, database-backed tests and backup/restore tools support the data lifecycle.

## Quickstart

Requires Docker with Compose. From the repository root:

```sh
cp .env.example .env
docker compose build api
docker compose up -d --no-build --wait --wait-timeout 180
docker compose --profile tools run --rm seed
```

API docs: [localhost:8000/docs](http://localhost:8000/docs). Authorize with `DEMO_API_KEY` from `.env`; Swagger expects the token without the `Bearer ` prefix.

The seed creates 30 synthetic runners, 30 registrations and 360 sessions in tenant `demo`, camp `demo-2026`. API and MySQL bind to localhost on ports 8000 and 3308; override `API_PORT` and `MYSQL_HOST_PORT` in `.env`. Example credentials are for local use.

Run end-to-end import and tenant-isolation checks:

```sh
docker compose --profile tools run --rm verify
```

## API

Business endpoints use `Authorization: Bearer <tenant-api-key>` under `/api/v1`.

| Resources | Operations |
| --- | --- |
| `runners`, `bootcamps`, `groups`, `registrations`, `sessions` | List, create, retrieve, update, delete |
| `bootcamps/{id}/stats` | Runner/session counts and total distance |
| `imports` | List and retrieve import audits |

Lists return `items`, `total`, `page` and `page_size` (maximum 100). Tenant ownership comes from authentication. Imports run through the administrative CLI. `/health` checks the process; `/ready` checks the database.

## Documentation

- [Data contracts](docs/data-contracts.md): columns, units, validation and update rules.
- [Architecture](docs/architecture.md): schema, ownership and transaction boundaries.
- [Docker operations](docs/docker-guide.md): tenant setup, imports, backups and maintenance.
- [Development](CONTRIBUTING.md): setup, tests and contribution conventions.

[MIT License](LICENSE).
