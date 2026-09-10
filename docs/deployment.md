# Local and Google Cloud deployment

Local development is the v0.1 starting point. Cloud configuration is an optional,
reviewable deployment path; adding these files does not provision resources or
publish a service. A successful local test run is not evidence of Cloud Run
availability, throughput, or zero-downtime rollouts.

## Local Docker Compose

Install Docker with its Linux container engine running. From the repository root,
copy `.env.example` to `.env` and set `DB_PASSWORD`, `MYSQL_ROOT_PASSWORD`, and a
`DEMO_API_KEY` of at least 32 characters. These are local credentials. Use separate
credentials and a separate database for every other environment.

```sh
docker compose up --build -d
docker compose --profile tools run --rm seed
docker compose ps
```

The first command starts MySQL 8.4, waits for a successful database query, executes
`alembic upgrade head` once, and starts FastAPI only after the migration succeeds.
The seed command imports the bundled synthetic data. API readiness is available
at <http://127.0.0.1:8000/ready>; interactive API documentation is at
<http://127.0.0.1:8000/docs>.

Both published ports bind to loopback: API `8000`, MySQL `3308`. Set `API_PORT` or
`MYSQL_HOST_PORT` in `.env` if a port is occupied. Inside Compose the database is
always `db:3306`; the host port is for tools running on your computer.

```sh
docker compose logs migrate api
docker compose stop
```

`stop` keeps the named MySQL volume. `docker compose down` removes the containers
and network while retaining data. **Adding `--volumes` deletes the local database**;
use it only when intentionally resetting a disposable demo. Updating a password
in `.env` does not change credentials in an already initialized MySQL volume.

For a code update, rebuild and rerun migrations explicitly:

```sh
docker compose build
docker compose run --rm migrate
docker compose up -d api
```

Keep the API stopped while applying a migration that is incompatible with the
currently running code. MySQL schema changes are not generally rollback-safe
transactions.

## CI and the release gate

`.github/workflows/ci.yml` runs on pushes and pull requests. It starts a disposable
MySQL 8.4 service, migrates an empty `runnerx_test` database, installs the package
with its development dependencies, runs pytest including the MySQL lifecycle
tests, and builds the runtime image. A separate job validates Terraform and Bash
syntax and exercises release failure gates with fake cloud commands, without
provisioning anything. The passwords in this workflow are public,
short-lived test credentials and must never be used for a deployed database.

`.github/workflows/deploy.yml` is manual (`workflow_dispatch`) and reuses **both** CI
jobs before its deployment job can start. It targets `main` and the GitHub
`production` Environment. Configure that Environment to allow `main` deployments
and, if desired, require a maintainer review. The workflow does not run for a pull
request and contains no long-lived GCP service account key.

The workflow is ready to run after the repository and cloud environment have been
configured. Cloud execution and GitHub-hosted execution must be verified in those
environments; local syntax checks do not replace them.

## Provision the optional GCP environment

Use a **new, dedicated GCP project** with billing enabled and an authenticated
operator allowed to create the listed resources. Terraform 1.11 or newer and the
Google Cloud CLI are required. The reference instance is single-zone and incurs
charges while running. It is intended as a small deployment baseline, not a
high-availability database design.

`infra/gcp` provisions:

- Required APIs, an Artifact Registry Docker repository, and a MySQL 8.4 Cloud SQL
  instance with backups and deletion protection.
- The application database and a Secret Manager secret container.
- Separate runtime and deployment service accounts.
- GitHub Workload Identity Federation restricted to the configured immutable
  repository/owner IDs, the named repository, `main`, the deployment workflow,
  and the `production` Environment subject.

Terraform does not create password values or a database user. This keeps database
credentials out of Terraform state. Cloud Run revisions and migration jobs are
managed by the deployment script rather than by Terraform.

Copy `infra/gcp/terraform.tfvars.example` to `infra/gcp/terraform.tfvars`. Replace
the project and repository placeholders. Obtain the numeric IDs using GitHub's
repository API or `gh api repos/OWNER/REPOSITORY --jq '{repository_id: .id,
owner_id: .owner.id}'`.

After authenticating with `gcloud auth application-default login`, run:

```sh
terraform -chdir=infra/gcp init
terraform -chdir=infra/gcp fmt -check
terraform -chdir=infra/gcp validate
terraform -chdir=infra/gcp plan -out=deployment.tfplan
```

Review the plan and resource costs before applying it:

```sh
terraform -chdir=infra/gcp apply deployment.tfplan
terraform -chdir=infra/gcp output github_environment_variables
```

The configuration initially uses local state. Store state and plans securely;
neither belongs in Git. Use a protected remote backend with locking before
multiple operators manage an environment. Commit the generated
`.terraform.lock.hcl` when intentionally updating the provider so subsequent
installations use the reviewed provider version. Use separate projects and state for staging
and production. Bootstrap permissions belong to the operator, not the GitHub
deployment account.

## Supply the database credential

In the Cloud SQL console, create the built-in MySQL user named by `database_user`
and set a newly generated password. Create a version of the provisioned Secret
Manager secret containing the **same password**, with no trailing newline. Do
this through an authenticated console or an approved secret-management process;
do not put it in source code, Terraform variables, command-line history, or
GitHub repository variables.

Before using real data, scope SQL privileges to the RunnerX database. The v0.1
reference uses one database user for both migrations and the API, so it needs
schema-change privileges on that database. Cloud SQL's default built-in user
privileges may be broader: review and reduce them with your database administrator.
A production hardening step is to use separate migration and application users.

The instance exposes no authorized public IP ranges. Cloud Run mounts the Cloud
SQL Unix socket under `/cloudsql/PROJECT:REGION:INSTANCE`; its runtime account has
`roles/cloudsql.client` and can read only the provisioned password secret. Database
authentication still uses the MySQL username and password.

Copy the Terraform output values to **Settings → Environments → production →
Environment variables** in GitHub. Add one more variable:

| Variable | Value |
| --- | --- |
| `DB_PASSWORD_SECRET_VERSION` | The numeric version just created, for example `1` |

Pinning a numeric version prevents a later secret edit from silently changing a
newly started instance's credential. To rotate credentials, coordinate the MySQL
password change and new secret version, then update this variable and deploy.

## What a cloud release does

After the release gate passes, the deployment script:

1. Authenticates via GitHub OIDC and builds a uniquely tagged Docker image.
2. Pushes the image to the provisioned Artifact Registry repository.
3. Deploys and waits for a one-task Cloud Run migration job running
   `alembic upgrade head`, with automatic retries disabled.
4. Deploys the same image as an IAM-protected Cloud Run service. For an existing
   service it creates a tagged candidate revision with no normal traffic.
5. Calls the candidate's `/ready` with a Google identity token. This checks
   application startup and database connectivity, not the entire business API.
6. Sends traffic to the exact tested revision only if the readiness check succeeds.

On the **first** deployment, the initial revision is available to authorized IAM
callers before the smoke check; there is no previous revision to continue serving.
On subsequent releases a failed candidate readiness check leaves the previous
traffic allocation in place. Database migrations have already run at that point,
so use backward-compatible expand/contract migrations. Do not infer automatic
database rollback or verified zero downtime from the traffic controls.

Runtime configuration uses `APP_ENV=production`, disables API docs, injects
`DB_PASSWORD` through Secret Manager, and sets `DB_UNIX_SOCKET`. No real dataset,
demo API token, or secret is copied into the image. The bundled data is synthetic;
the release workflow does not seed any production data or tenant credentials.
Provision organization/API credentials deliberately using the CLI described in
the main README. Do not enable the publicly documented demo key in production.

Cloud Run IAM authorization and RunnerX's organization API credentials are separate
checks. Send the Google identity token in `X-Serverless-Authorization` and the
RunnerX tenant key in `Authorization`, so the two bearer tokens do not compete
for one header. For example, with an IAM identity token whose audience is the
service URL and a provisioned tenant key already loaded in your shell:

```sh
curl --fail "$SERVICE_URL/api/v1/runners" \
  -H "X-Serverless-Authorization: Bearer $IAM_ID_TOKEN" \
  -H "Authorization: Bearer $TENANT_API_KEY"
```

`/ready` does not require a tenant key; the release script uses only its IAM token
for that route. The deployment account can invoke Cloud Run through its Cloud Run
administrator role, which includes `run.routes.invoke`, and can obtain an ID token
for itself. Grant application consumers only the specific Cloud Run invoker access
they require. See the [Cloud Run authentication guidance](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
and [Cloud Run role permissions](https://docs.cloud.google.com/iam/docs/roles-permissions/run).

## Rollback and operational boundaries

The release log records the previous traffic allocation before deploying a new
revision. To restore a reviewed previous revision, an authorized operator can run:

```sh
gcloud run services update-traffic SERVICE \
  --project=PROJECT --region=REGION \
  --to-revisions=PREVIOUS_REVISION=100
```

This changes traffic only; it does not revert database migrations. Take and test
backups before incompatible changes. Keep prior images and revisions needed for
rollback. The reference caps Cloud Run at three instances; tune its concurrency
and SQL connection limits together using measurements. It does not include a load
test result, an uptime guarantee, monitoring alerts, an automated database restore,
or a production release record.

Cloud SQL and the password secret have deletion protection. Removing those
protections or deleting cloud resources is an explicit operator decision; no
cleanup or destroy command is part of normal application deployment.

## References

- [GitHub service containers](https://docs.github.com/en/actions/tutorials/use-containerized-services)
- [GitHub authentication action](https://github.com/google-github-actions/auth)
- [GCP deployment pipeline federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
- [Cloud SQL built-in users](https://docs.cloud.google.com/sql/docs/mysql/create-manage-users)
- [Cloud SQL Terraform resource](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/sql_database_instance)
- [Cloud Run deploy command](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy)
- [Cloud Run jobs deploy command](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy)
- [Cloud Run authenticated requests](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)
- [Cloud Run traffic migration and rollback](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
