# Docker guide

RunnerX v0.1 is delivered as a local Docker Compose application: MySQL 8.4, database migrations, FastAPI and offline Excel/CSV imports. No GCP account, cloud credentials or host MySQL installation is required.

Run all commands from the repository root containing `compose.yaml`. The examples below use API port **8002** to leave port 8000 available for native development.

## 1. Start the application

Install Docker with its Linux container engine and the Docker Compose plugin. `docker version` must show both Client and Server. Host Python 3.11+ is only needed for the backup/recovery helper below; the acceptance tool runs inside Docker.

Prepare the environment in **PowerShell**:

~~~powershell
Set-Location 'E:\files\RunnerX_public' # Replace with your checkout path.
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
}
$env:API_PORT = '8002'
$env:MYSQL_HOST_PORT = '3308'
~~~

Or in **Bash on Linux/macOS**:

~~~sh
cd /path/to/runnerx-public
test -f .env || cp .env.example .env
export API_PORT=8002
export MYSQL_HOST_PORT=3308
~~~

The shell overrides apply only to that terminal. For a persistent choice, edit the existing `API_PORT` and `MYSQL_HOST_PORT` lines in `.env`. Shell values take precedence over `.env`; use the same environment for later Compose commands.

The following commands work in either shell. Run them in order and stop if a command fails:

~~~sh
docker compose config --quiet
docker compose build api
docker compose up -d --no-build --wait --wait-timeout 180
docker compose --profile tools run --rm seed
docker compose ps -a
~~~

The first build downloads Python, MySQL and Python packages. An initial download can take several minutes. The `db` and `api` services should become healthy. `migrate` exiting with code 0 is expected: migrations are a one-time startup task.

The named MySQL volume is initialized only on its first use. Updating database passwords in `.env` afterwards does not change passwords in that existing database.

Both published ports bind to `127.0.0.1`. Compose containers connect to MySQL using `db:3306`; the host port 3308 is for tools on your computer. The `DB_PORT` value in `.env` is relevant to host Python development, while Compose sets its internal database port itself.

## 2. Check the API

Open [Swagger at localhost:8002](http://127.0.0.1:8002/docs). Click **Authorize** and paste the `DEMO_API_KEY` used by the seed command. Enter the key value alone, without the `Bearer ` prefix.

For an unchanged `.env.example`, the demo key is:

~~~text
runnerx-demo-token-for-local-use-only-2026
~~~

This is an application API key for the `demo` tenant, separate from `DB_PASSWORD`. The native Windows launcher generates a different key and uses a different database. Do not use the native key with this Docker instance.

Use **Try it out → Execute** on:

- `GET /api/v1/runners`: `total` should be 30.
- `GET /api/v1/registrations`: `total` should be 30.
- `GET /api/v1/sessions`: `total` should be 360.
- `GET /api/v1/bootcamps`: returns the `demo-2026` camp; use its `id` for statistics.

The public demo credentials are for localhost examples. For separate application data, provision a separate tenant and keep its generated key. Editing `DEMO_API_KEY` and reseeding adds that key; it does not automatically revoke an existing key.

PowerShell HTTP example:

~~~powershell
$headers = @{ Authorization = 'Bearer runnerx-demo-token-for-local-use-only-2026' }
Invoke-RestMethod 'http://127.0.0.1:8002/ready'
Invoke-RestMethod 'http://127.0.0.1:8002/api/v1/runners' -Headers $headers
~~~

Bash HTTP example:

~~~sh
curl --fail http://127.0.0.1:8002/ready
curl --fail http://127.0.0.1:8002/api/v1/runners -H 'Authorization: Bearer runnerx-demo-token-for-local-use-only-2026'
~~~

`/health` checks the process, while `/ready` also checks the database. Both are unauthenticated local health endpoints. Business APIs require a tenant key.

## 3. Import your own Excel or CSV

Imports run through the administrative CLI inside the API container. Swagger does not have a file-upload endpoint. An import identifies its destination by tenant **slug** and camp **code**, not their UUIDs.

### Prepare a tenant and camp

To keep your files separate from the generated demo, create a tenant:

~~~sh
docker compose exec -T api python -m runnerx.cli create-tenant --slug my-club --name "My Club"
~~~

Save the returned `api_key`; it is printed once and only its hash is stored in MySQL. In Swagger, log out of the previous authorization and authorize with this new key. Use `POST /api/v1/bootcamps` with a body such as:

~~~json
{
  "code": "my-camp-2026",
  "name": "My Training Camp",
  "city": "Example City",
  "start_date": "2026-01-01",
  "end_date": "2026-12-31"
}
~~~

Choose dates that include your training records. The bundled `demo-2026` camp covers only 2026-01-05 through 2026-02-01.

### Prepare the files

Use the bundled `data/sample/registrations.xlsx` with canonical English headers as a format reference. Save your own workbook at `data/private/registrations.xlsx`, which is ignored by Git. Keep generated release samples unchanged.

Registration data needs `external_id` and `full_name`. Training data needs `external_id`, `session_date`, `distance_km` and `pace_sec_per_km`. Import registrations before sessions. XLSX uses the first worksheet, with headers in row 1; CSV must be UTF-8. The maximum source file size is 32 MiB.

External IDs must remain stable between imports. Format Excel ID cells as text if leading zeros matter. Training dates must be explicit and inside the camp. See [data contracts](data-contracts.md) for accepted headers, units, precision and update rules.

### Copy, validate and import

Create a writable container directory, then copy the host workbook into it:

~~~sh
docker compose exec -T api mkdir -p /tmp/runnerx-imports
docker compose cp "./data/private/registrations.xlsx" api:/tmp/runnerx-imports/registrations.xlsx
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp-2026 --kind registrations --file /tmp/runnerx-imports/registrations.xlsx --dry-run
~~~

A valid dry run reports `status: validated` and writes no business records or audit entries. If validation succeeds, apply the file:

~~~sh
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp-2026 --kind registrations --file /tmp/runnerx-imports/registrations.xlsx --report /tmp/runnerx-imports/registrations-report.json
docker compose cp api:/tmp/runnerx-imports/registrations-report.json "./data/private/registrations-report.json"
~~~

The file and report paths passed to the CLI are **inside the container**. Files under `/tmp` disappear when that container is replaced, so retain your source file and copy any report you need back to the host.

To import training records saved at `data/private/sessions.csv`:

~~~sh
docker compose cp "./data/private/sessions.csv" api:/tmp/runnerx-imports/sessions.csv
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp-2026 --kind sessions --file /tmp/runnerx-imports/sessions.csv --dry-run
docker compose exec -T api python -m runnerx.cli import --tenant my-club --bootcamp my-camp-2026 --kind sessions --file /tmp/runnerx-imports/sessions.csv --report /tmp/runnerx-imports/sessions-report.json
docker compose cp api:/tmp/runnerx-imports/sessions-report.json "./data/private/sessions-report.json"
~~~

Run the final import only after reviewing its dry run. Query the API using the `my-club` key to see the imported records and `GET /api/v1/imports` for audit details.

### Understand the outcome

| CLI exit code | Meaning | Database effect |
| --- | --- | --- |
| 0, `validated` | Valid dry run | No writes |
| 0, `committed` | File applied | Business changes and an import audit committed |
| 0, `duplicate` | Exact successful file bytes already imported for this tenant/camp/kind | No changes; original audit reused |
| 2, `rejected` | Input validation failed | No business changes; an audit is retained unless this was a dry run |
| 1 | Runtime, database or file error | Investigate the error; database operation failures roll back |

Immediately after an import, PowerShell exposes its code in `$LASTEXITCODE`; Bash exposes it in `$?`. Inspect it before running another command. A failure while writing a report may occur after the database transaction committed; check the audit before assuming an exit-1 import made no changes.

A rejected batch rejects every business row in that file, including valid rows. Fix the fields named in `errors`, then validate again. A changed source file updates existing business keys; missing optional fields preserve existing values. `updated_count` counts existing registration rows processed, not individual edited cells.

Replaying an old successful file is a no-op even if its records were later edited through the API. Submit changed file bytes or use the API for a deliberate correction.

## 4. Repeat the business acceptance checks

With the API running, execute:

~~~sh
docker compose --profile tools run --rm verify
~~~

The tool creates fresh synthetic QA tenants, performs real HTTP requests to the API and imports Excel/CSV through the application CLI. It requires no host Python, demo key or pre-existing demo data. It checks Excel corrections, stable identities, dry runs, duplicate replay, whole-batch rejection and tenant isolation.

The command prints a JSON report and exits 0 for success or 1 for failure. By default it cleans up only tenants created by that invocation, including after a failed check. Existing application tenants and demo data are retained. Run the check against a local development instance while other writers are idle so comparisons are meaningful.

The existing [business acceptance report](business-acceptance.md) documents Excel corrections, whole-batch rejection and isolation between two tenants. The [validation record](validation.md) distinguishes live checks from pytest and simulated cloud tests.

## 5. Back up and recover the database

The helper needs **host Python 3.11+** and Docker Compose, with no extra Python packages or host MySQL installation. On systems where the executable is named `python3`, substitute that name below.

Create a backup in a new output directory:

~~~sh
python scripts/backup_restore.py backup --output .local/backups/v0.1.0
~~~

The output directory must not already exist; omit `--output` to use a unique timestamped directory. The directory contains `database.sql` and `manifest.json` with its SHA-256, byte count, source database, MySQL version, charset/collation, dump options and creation time. The InnoDB dump uses a single transaction, so regular data writes can continue; do not run migrations or other schema changes during backup.

Restore and verify into a **new, separate** database:

~~~sh
python scripts/backup_restore.py restore --backup .local/backups/v0.1.0 --database runnerx_v010_restore_test
~~~

The helper requires a fresh database name ending in `_restore_test`, refuses an existing destination or the source application database, verifies the backup checksum and matching MySQL version, and re-exports the restored database to confirm its content hash matches the original dump. It also reports restored table counts. It removes its temporary restore user and retains the restored database for inspection.

This is a recovery rehearsal. It does not replace the running application database or switch FastAPI to the restored data. Promoting a restored copy requires a separate maintenance operation: grant an application user access to the restored schema, update the application's database configuration, recreate the application services and verify the API before allowing writes. Keep the original database and backup available until that verification is complete.

The helper uses this checkout's `compose.yaml` and `db` service with the same inherited shell and `.env` configuration as other Compose commands. See [validation](validation.md) for the recorded recovery rehearsal.

A Docker volume surviving a container restart is not a backup. Store the backup directory outside the container and keep the matching application version. Database exports can contain tenant data and API-key hashes, so keep them outside Git. Restore only backups from a trusted source; an unkeyed checksum detects accidental changes but does not authenticate an archive.

## 6. Stop, restart and upgrade

Stop services while retaining containers and data:

~~~sh
docker compose stop
docker compose start
~~~

Remove containers and the Compose network while retaining the named database volume:

~~~sh
docker compose down
docker compose up -d --no-build --wait --wait-timeout 180
~~~

Do not add `-v` or `--volumes` to a normal `down`: those options delete the named database volume. Do not delete the volume to repair an authentication error.

For an upgrade, first create and verify a backup using the procedure above. Read the target version's migration notes and keep the earlier image/version available. Stop the API and avoid running any import or other database writer during the change:

~~~sh
docker compose stop api
git pull --ff-only
docker compose build api
docker compose run --rm migrate
docker compose up -d --no-build --wait --wait-timeout 180
docker compose exec -T api alembic current
docker compose exec -T api alembic check
~~~

Run each command only after the previous one succeeds. This checkout-based upgrade uses the current branch; release testing may instead check out a specific published tag before building. It introduces a maintenance window. Code or image changes do not take effect in the current container until it is recreated.

If migration fails, inspect `docker compose logs db migrate` and the migration command output before restarting the API. MySQL DDL may have already taken effect. Do not assume that switching to old code reverses the schema; restore and verify a separate copy with the matching application version, then deliberately configure the application to use that copy if a database recovery is needed.

## 7. Common problems

| Symptom | Check or action |
| --- | --- |
| Docker pipe/socket error or no Server in `docker version` | Start Docker Desktop/Engine with Linux containers; the application cannot build or run until the engine is available |
| Port already allocated | Set a free `API_PORT` or `MYSQL_HOST_PORT` in the current shell or `.env`, then run Compose again |
| Required variable missing | Create `.env` from `.env.example`; run `docker compose config --quiet` |
| Database unhealthy / access denied | Read `docker compose logs --tail 100 db`; confirm credentials match the existing volume, including shell overrides |
| `migrate` exited 0 | Normal successful one-time migration |
| `migrate` exited nonzero / API unavailable | Read the migration logs and resolve the database or schema failure before starting the API |
| API returns 401 | Seed the demo, use the key for this instance and tenant, and enter only the token in Swagger |
| API returns 404 | Check the UUID and tenant key; another tenant's objects are deliberately invisible |
| API returns 409 | Inspect the conflict response: uniqueness or referenced child rows can block the operation |
| API returns 422 | Check required fields, types, limits and unknown/ownership fields against Swagger |
| Import cannot find file | Copy the file into the container and pass its container path to `--file` |
| Import says tenant/camp not found | Use the existing tenant slug and camp code; create the camp with that tenant's key |
| Imported data does not appear in demo | Query with the imported tenant's key and check import status; datasets are tenant-scoped |
| Latest code has no effect | Rebuild the application image, apply migrations and recreate the API as described above |

General diagnostics:

~~~sh
docker compose ps -a
docker compose logs --tail 100 db migrate api
docker compose exec -T api alembic current
~~~

Inspect logs before sharing them because they can contain request paths and operational details. Prefer `config --quiet` for validation; unfiltered `docker compose config` expands environment values, including database passwords.

## Reference

- [Docker Compose startup and health waiting](https://docs.docker.com/reference/cli/docker/compose/up/)
- [Copy files between a service container and the host](https://docs.docker.com/reference/cli/docker/compose/cp/)
- [Compose environment interpolation and precedence](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)
- [RunnerX data contracts](data-contracts.md), [architecture](architecture.md), [v0.1.1 release notes](releases/v0.1.1.md)
