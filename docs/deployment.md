# Optional GCP deployment

Docker Compose is the primary runtime; see the [README](../README.md) and [Docker guide](docker-guide.md). The GCP configuration runs the same image on Cloud Run with Cloud SQL.

## Resources and inputs

[Terraform](../infra/gcp) provisions required APIs, Artifact Registry, a MySQL 8.4 Cloud SQL instance and database, a Secret Manager container, runtime/deployment service accounts, and GitHub Workload Identity Federation. Cloud Run services and migration jobs are managed by [deploy.sh](../infra/gcp/deploy.sh).

Requirements: a GCP project with billing, Terraform >= 1.11, gcloud, and provisioning permissions. Copy `infra/gcp/terraform.tfvars.example` to `infra/gcp/terraform.tfvars` and set:

| Input | Purpose / default |
| --- | --- |
| `project_id` | Target GCP project |
| `github_repository` | Exact `owner/repository` |
| `github_repository_id`, `github_owner_id` | Immutable numeric GitHub IDs |
| `region` | `us-central1` |
| `name_prefix` | `runnerx`; resource prefix |
| `database_name`, `database_user` | `runnerx` |
| `database_tier` | `db-custom-1-3840`; single-zone instance |

```sh
terraform -chdir=infra/gcp init
terraform -chdir=infra/gcp plan -out=deployment.tfplan
terraform -chdir=infra/gcp apply deployment.tfplan
terraform -chdir=infra/gcp output github_environment_variables
```

Keep state and plans outside Git. Use separate projects/state per environment and a locked remote backend for shared operations.

## Credentials and GitHub configuration

Create the configured MySQL user separately, then add its password as a version of the provisioned Secret Manager secret, without a trailing newline. Terraform does not store the password or create the database user. Scope SQL privileges to the application database; the configured user also needs migration privileges.

Copy `github_environment_variables` from Terraform output into the GitHub `production` Environment. Set `DB_PASSWORD_SECRET_VERSION` to the numeric secret version. Federation permits the configured repository IDs, `main`, the deployment workflow and the `production` Environment.

Cloud Run uses `APP_ENV=production`, disables API docs, mounts the Cloud SQL Unix socket and injects the password from Secret Manager. Its runtime account has Cloud SQL client access and access to that password secret. Rotate the MySQL password and pinned secret version together.

## Deploy

```sh
gh workflow run deploy.yml --ref main
```

The manual [workflow](../.github/workflows/deploy.yml) runs CI, authenticates through GitHub OIDC, then:

1. Builds and pushes an image to Artifact Registry.
2. Executes a single-task Alembic migration job with retries disabled.
3. Deploys an IAM-protected candidate revision and checks `/ready`.
4. Routes traffic to that exact revision after the check succeeds.

For an existing service, the candidate receives no normal traffic until readiness succeeds. On initial deployment, IAM callers can reach the first revision before that check. Migrations run before application rollout, so schema changes must remain compatible with the serving revision. The workflow does not seed tenant credentials or data.

## Access and operations

Cloud Run IAM and RunnerX tenant authentication use separate headers:

```sh
curl --fail "$SERVICE_URL/api/v1/runners" \
  -H "X-Serverless-Authorization: Bearer $IAM_ID_TOKEN" \
  -H "Authorization: Bearer $TENANT_API_KEY"
```

The identity token audience is the service URL; callers need Cloud Run invoker access. `/ready` requires only IAM authentication.

Traffic rollback does not undo database migrations:

```sh
gcloud run services update-traffic SERVICE \
  --project=PROJECT --region=REGION --to-revisions=PREVIOUS_REVISION=100
```

Cloud SQL enables backups and deletion protection; the secret has destroy protection. The service scales from zero to three instances with concurrency 20; tune capacity alongside database connection limits. Back up data before incompatible schema changes.
