# Release 0.1 validation

The native checks below were run on 2026-09-10 using Python 3.11, MySQL Community Server 8.0.44 and the versions pinned in pyproject.toml. Docker Compose was separately verified on the same date with Docker Desktop 4.80.0 / Engine 29.6.1, Python 3.11.16 and MySQL 8.4.11. The native suite was not rerun against the Compose database. A separate GitHub-hosted CI run also passed all 93 tests against its dedicated MySQL 8.4 service.

| Check | Observed result |
| --- | --- |
| Full Python suite against a dedicated native MySQL database | 93 passed |
| Full Python suite in GitHub Actions with a dedicated MySQL 8.4 service | 93 passed |
| GitHub Actions migration, Docker build and infrastructure checks | Passed; 4 simulated deployment tests also passed |
| Deployment-script flow tests with fake cloud commands | 4 passed |
| Alembic migrations applied to an empty database | Passed |
| Alembic metadata drift check | No new upgrade operations detected |
| Native Windows startup from a fresh private MySQL directory | Passed |
| Sample import | 30 runners, 30 registrations, 360 sessions |
| Live HTTP readiness | 200, status ok |
| Live HTTP without a tenant key | 401 |
| Live authenticated runner list and camp statistics | 30 runners, 360 sessions, 4176.01 km of generated data |
| Swagger documentation endpoint | 200 |
| Docker Compose configuration and application image build | Passed |
| Compose startup from an empty named database volume | MySQL and API healthy; migration exited 0 |
| Container Alembic version and metadata drift | 0002 (head); no new upgrade operations detected |
| Container runtime user | Non-root, UID 10001 |
| Compose sample import and authenticated HTTP | 30 runners, 30 registrations, 360 sessions, 2 import audit records |
| Compose health, readiness, Swagger and OpenAPI endpoints | All returned HTTP 200 |
| Compose API without a key or with an invalid key | HTTP 401 in both cases |
| Compose camp statistics | 30 registered/active runners, 360 sessions, 4176.01 km |
| Repeated Compose seed | Both files skipped as duplicates; original batch IDs and all record IDs retained |
| Compose down/up without removing volumes or reseeding | Counts, record IDs and camp statistics unchanged; API and MySQL healthy |
| Terraform format, provider initialization and validate | Passed; no resources provisioned |

Database tests include tenant isolation at both API and SQL layers, foreign-key restrictions, partial-update handling, duplicate import replay, exact external identifiers, decimal precision rejection, row-level error reporting, full Excel-to-API flow, and an import transaction racing a camp date update.

The four release tests replace cloud commands with fakes. They verify control flow for migration failure, failed candidate readiness, a successful revision release and the first deployment. They do not verify the availability of a real GCP deployment.

## Docker Compose verification

The local Compose check used API port 8002 and MySQL host port 3308, both bound to loopback. The sample files embedded in the built image were imported into a fresh MySQL volume, then queried through the host's HTTP connection to the API container. Every runner, registration, session, import and bootcamp ID was compared before and after seed replay and container recreation.

The commands used were:

```powershell
# After copying .env.example to .env:
$env:API_PORT = '8002'
$env:MYSQL_HOST_PORT = '3308'
docker compose config --quiet
docker compose build api
docker compose up -d --no-build --wait --wait-timeout 180
docker compose --profile tools run --rm seed
docker compose exec -T api alembic current
docker compose exec -T api alembic check
# Verify the API data, then replay the same files:
docker compose --profile tools run --rm seed
# Recreate containers while retaining the named database volume:
docker compose down
docker compose up -d --no-build --wait --wait-timeout 120
# Verify the API data again before running any seed command.
```

Use the DEMO_API_KEY in .env for authenticated requests. Set API_PORT in .env if the port choice should persist across PowerShell sessions. The default port in .env.example remains 8000.

## GitHub-hosted CI verification

The first hosted [CI run](https://github.com/Clouddelta/runnerx-public/actions/runs/34499311823) completed successfully on 2026-09-10 for commit `b1b24c7fe50ba82e9e2a0f10ecb1651085f5c2f0`. Both `test` and `infrastructure` passed. The run applied migrations to an empty MySQL 8.4 test database, passed all 93 Python tests, built the Docker image, validated Terraform and Bash syntax, and passed all 4 deployment flow tests with fake cloud commands. No GCP resources were provisioned or deployed.

## Not executed

- GCP provisioning, Cloud SQL connections and Cloud Run deployment: no cloud project was modified.
- Load testing, zero-downtime measurement, or a measured reduction in manual cleaning work.

These limitations apply to release evidence, not to future verification by a contributor running CI or deploying their own environment.
