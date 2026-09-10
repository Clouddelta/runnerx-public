locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "sts.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "${var.name_prefix}-images"
  description   = "RunnerX application images"
  format        = "DOCKER"
  depends_on    = [google_project_service.required]
}

resource "google_sql_database_instance" "database" {
  name                = "${var.name_prefix}-mysql"
  database_version    = "MYSQL_8_4"
  region              = var.region
  deletion_protection = true

  settings {
    tier                        = var.database_tier
    edition                     = "ENTERPRISE"
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    deletion_protection_enabled = true

    backup_configuration {
      enabled            = true
      binary_log_enabled = true
      start_time         = "04:00"
    }

    ip_configuration {
      # No authorized networks: access uses the IAM-authenticated Cloud SQL
      # connector mounted by Cloud Run, then database username/password auth.
      ipv4_enabled = true
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_sql_database" "runnerx" {
  name      = var.database_name
  instance  = google_sql_database_instance.database.name
  charset   = "utf8mb4"
  collation = "utf8mb4_unicode_ci"
}

resource "google_secret_manager_secret" "database_password" {
  secret_id = "${var.name_prefix}-database-password"
  replication {
    auto {}
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}

# Create secret versions and database credentials separately through an
# authenticated operator. No password or secret payload is read by Terraform.
resource "google_service_account" "runtime" {
  account_id   = "${var.name_prefix}-runtime"
  display_name = "RunnerX Cloud Run runtime"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "runtime_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_database_password" {
  secret_id = google_secret_manager_secret.database_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_service_account" "deployer" {
  account_id   = "${var.name_prefix}-deployer"
  display_name = "RunnerX GitHub deployment"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "deployer_run" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# The deployment script impersonates this same account to obtain an ID token
# for the IAM-protected candidate URL. This binding is scoped to that account.
resource "google_service_account_iam_member" "deployer_smoke_token" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "RunnerX GitHub Actions"
  depends_on                = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }
  attribute_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "assertion.repository_id == '${var.github_repository_id}'",
    "assertion.repository_owner_id == '${var.github_owner_id}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.workflow_ref == '${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main'",
    "assertion.sub == 'repo:${var.github_repository}:environment:production'",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}
