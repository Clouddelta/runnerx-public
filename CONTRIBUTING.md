# Development

## Setup

Use an activated Python 3.11+ environment and configure `.env` for MySQL (`DB_*` or `RUNNERX_DATABASE_URL`).

```sh
python -m pip install -e ".[dev]"
python -m runnerx.cli init-db
uvicorn runnerx.api:app --host 127.0.0.1 --port 8001
```

Windows helper: `.\scripts\run-local.ps1 -ApiPort 8001` starts a dedicated MySQL instance and seeds the API. It requires MySQL binaries; use `-MySqlBin` to specify their directory. Credentials are stored in `.local/native/state.json`.

## Tests

```sh
python -m pytest -m "not integration"
```

For the full suite, set `RUNNERX_TEST_DATABASE_URL` to a disposable MySQL database whose name ends in `_test`, then run `python -m pytest`. Integration tests apply migrations and clear that database's application tables. Use a separate database for each concurrent suite.

With Compose running, `docker compose --profile tools run --rm verify` checks imports and API isolation. CI runs the MySQL suite, Docker build, business acceptance and backup/restore checks.

## Conventions

- Write concise English for readers with a CS background. Explain functionality, contracts and operational constraints in documentation, comments, messages and fixtures.
- Use synthetic fixtures. Keep credentials, database exports and private input files out of Git.
- Use explicit units (`_sec`, `_km`, `_cm`, `_kg`), preserve row numbers in validation errors, and distinguish missing values from zero.
- Identify runners by tenant and external ID. Derive tenant ownership from authentication.
- Cover parser changes with valid and rejected inputs; cover ownership changes through both API and database constraints.
- Include migrations for schema changes. Keep pull requests focused and report the checks run.
