# Docker operations

See the [README](../README.md#quickstart) for startup and API authentication. Run commands from the repository root. Defaults: API `http://127.0.0.1:8000`, host MySQL `127.0.0.1:3308`; containers use `db:3306`.

## Import data

Provision a tenant and save its returned API key; the raw key is printed once:

```sh
docker compose exec -T api python -m runnerx.cli create-tenant --slug my-club --name "My Club"
```

Authorize with that key in `/docs`, then call `POST /api/v1/bootcamps`:

```json
{
  "code": "my-camp",
  "name": "My Training Camp",
  "city": "Example City",
  "start_date": "2026-01-01",
  "end_date": "2026-12-31"
}
```

Choose dates covering the training records. The import CLI takes a tenant **slug** and camp **code**, while API paths use UUIDs. Imports run through the CLI; there is no upload endpoint.

Use `data/sample/` for formats and `data/private/` for Git-ignored inputs. XLSX reads the first worksheet with row-1 headers; CSV uses UTF-8. Files are limited to 32 MiB. See [data contracts](data-contracts.md) for required columns, types and update rules.

Import registrations before sessions. Copy, validate, then apply:

```sh
docker compose exec -T api mkdir -p /tmp/runnerx-imports
docker compose cp ./data/private/registrations.xlsx api:/tmp/runnerx-imports/registrations.xlsx
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp --kind registrations --file /tmp/runnerx-imports/registrations.xlsx --dry-run
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp --kind registrations --file /tmp/runnerx-imports/registrations.xlsx --report /tmp/runnerx-imports/report.json
docker compose cp api:/tmp/runnerx-imports/report.json ./data/private/registrations-report.json
```

Run the import only after a successful dry run. For training data, repeat with `sessions.csv` and `--kind sessions`. CLI paths are inside the container; copy reports to the host before replacing it. Query `/api/v1/imports` with the tenant key for audit details.

| Exit | Status | Effect |
| --- | --- | --- |
| 0 | `validated` | Dry run; no writes |
| 0 | `committed` | Business data and audit committed |
| 0 | `duplicate` | Successful file bytes already imported for this tenant/camp/kind; no changes |
| 2 | `rejected` | Entire batch rejected; audit retained unless a dry run |
| 1 | Error | Runtime, database or file failure; inspect the error and audit |

Report writing happens after the transaction: an exit-1 report error can follow a successful commit. Replaying a successful file remains a no-op after API edits; use changed file bytes or the API for corrections.

## Business verification

```sh
docker compose --profile tools run --rm verify
```

Runs CLI imports and HTTP checks for corrections, stable identities, dry runs, replay, batch rejection and tenant isolation. No demo seed or key is required. JSON goes to stdout; progress goes to stderr. Exit 0 means success, 1 means failure.

Use a local development instance with other writers idle. The command deletes only tenants it creates and checks that existing data is unchanged. Cleanup also runs after failed checks; forced termination can leave test tenants.

## Backup and restore

Requires host Python 3.11+ and Docker Compose. Back up to a directory that does not exist:

```sh
python scripts/backup_restore.py backup --output .local/backups/manual
python scripts/backup_restore.py restore --backup .local/backups/manual --database runnerx_manual_restore_test
```

The backup contains `database.sql` and a manifest with its SHA-256 and database metadata. Omit `--output` for a unique directory. The transactional dump permits data writes; avoid schema changes during backup.

Restore requires the same MySQL version and a fresh database ending in `_restore_test`. It verifies the checksum, imports through a restricted temporary account, and compares a new dump against the original. The restored database is retained for inspection; the temporary account is removed.

Restore verifies recovery in a separate database. It does not replace the application database or change its configuration. Keep backups outside Git and use trusted backup sources.

## Maintenance

Stop/start services or remove containers while retaining the database volume:

```sh
docker compose stop
docker compose start
docker compose down
docker compose up -d --no-build --wait --wait-timeout 180
```

`docker compose down -v` deletes the database volume. Changing passwords in `.env` does not update credentials in an existing volume.

For upgrades, verify a backup, stop all writers, and select the target Git revision. Run each step only after the previous one succeeds:

```sh
docker compose stop api
docker compose build api
docker compose run --rm migrate
docker compose up -d --no-build --wait --wait-timeout 180
docker compose exec -T api alembic current
docker compose exec -T api alembic check
```

Upgrades require a maintenance window. If migration fails, inspect its output before restarting. MySQL DDL may already have taken effect; switching application code does not roll back the schema.

## Troubleshooting

```sh
docker compose config --quiet
docker compose ps -a
docker compose logs --tail 100 db migrate api
```

| Symptom | Check |
| --- | --- |
| Port conflict | Set `API_PORT` or `MYSQL_HOST_PORT` in `.env`; shell overrides take precedence |
| Database unhealthy / access denied | Match credentials to the existing volume; inspect `db` logs |
| API unavailable | Inspect `migrate` and `api` logs; `migrate` exiting 0 is expected |
| HTTP 401 / 404 | Use the correct instance's tenant key; other tenants' objects are invisible |
| Import path or destination missing | Copy the file into the container; supply an existing tenant slug and camp code |
| Code changes missing | Rebuild the image, apply migrations and recreate the API |

Use `config --quiet` for validation; plain `config` expands environment values, including secrets.
